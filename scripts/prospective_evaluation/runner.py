"""Resumable, research-only prospective evaluation campaign runner.

The three public functions in this module are the orchestration seam.  They do
not place orders, read broker accounts, install timers, or perform networking
while reporting/verifying a campaign.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from zoneinfo import ZoneInfo

from decision_lab import replay_run, run_lab
from decision_lab.core import PROMPT_VERSION, ROLE_CONTRACTS
from prospective_evidence import archive as evidence_archive
from prospective_evidence.normalize import normalize_market
from prospective_evidence.pipeline import build_collection, reconstruct_collection
from prospective_evidence.acquire import collect_sources

from .measurement import baseline_allocations, score_checkpoint
from .observations import ObservationError, observe_market, reconstruct_observation
from .storage import (
    CampaignBusy,
    campaign_lock,
    implementation_fingerprint,
    load_campaign,
    load_records,
    write_record,
)

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
_ACTUAL_TIME_FIELDS = {
    "started_at", "completed_at", "inputs_frozen_at", "frozen_at",
    "recorded_at", "evaluation_at", "observed_at", "registered_at",
}


class RunnerError(ValueError):
    """Stable campaign-runner failure without upstream response details."""


def _clock(clock: Callable[[], datetime] | None) -> datetime:
    if clock is not None and not callable(clock):
        raise RunnerError("invalid_clock")
    value = clock() if clock is not None else datetime.now(UTC)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RunnerError("invalid_clock")
    return value.astimezone(UTC)


def _parse_time(value: Any) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise RunnerError("invalid_campaign_time") from None
    if result.tzinfo is None or result.utcoffset() is None:
        raise RunnerError("invalid_campaign_time")
    return result.astimezone(UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _decision_code_hash() -> str:
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parents[1] / "decision_lab"
    for source in sorted(root.glob("*.py")):
        if not source.name.startswith("test_"):
            digest.update(source.name.encode())
            digest.update(source.read_bytes())
    return digest.hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise RunnerError("invalid_decision_artifact")
        value[key] = item
    return value


def _strict_json(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
            object_pairs_hook=_strict_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise RunnerError("invalid_decision_artifact") from None


def _value(record: dict[str, Any]) -> dict[str, Any]:
    return record["value"]


def _record_id(record: dict[str, Any]) -> str:
    return record["record_id"]


def _tick_id(now: datetime) -> str:
    seconds = int(now.timestamp())
    bucket = datetime.fromtimestamp(seconds - seconds % 900, UTC)
    return bucket.strftime("%Y%m%dT%H%M%SZ")


def _safe_relative(path: Path, campaign: Path) -> str:
    relative = path.resolve().relative_to(campaign.resolve())
    return relative.as_posix()


def _artifact(path: str, campaign: Path, prefix: str) -> Path:
    pure = PurePosixPath(path)
    if pure.is_absolute() or not pure.parts or pure.parts[:2] != ("artifacts", prefix):
        raise RunnerError("unsafe_artifact_reference")
    target = campaign.joinpath(*pure.parts)
    target.resolve().relative_to(campaign.resolve())
    return target


def _protocol(registration: dict[str, Any]) -> dict[str, Any]:
    return registration["protocol"]


def _symbols(protocol: dict[str, Any]) -> list[str]:
    return [item["symbol"] for item in protocol["universe"]]


def _assert_identity(registration: dict[str, Any]) -> None:
    saved = registration.get("implementation_fingerprint")
    if saved != implementation_fingerprint():
        raise RunnerError("implementation_fingerprint_mismatch")


def _actual_times(value: Any) -> list[datetime]:
    result: list[datetime] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _ACTUAL_TIME_FIELDS and isinstance(item, str):
                result.append(_parse_time(item))
            elif isinstance(item, (dict, list)):
                result.extend(_actual_times(item))
    elif isinstance(value, list):
        for item in value:
            result.extend(_actual_times(item))
    return result


def _assert_monotonic(now: datetime, campaign: Path, registration: dict[str, Any]) -> None:
    times = [_parse_time(registration["registered_at"])]
    for kind in ("events", "observations", "checkpoints", "ticks"):
        for record in load_records(campaign, kind):
            times.extend(_actual_times(_value(record)))
    if times and now < max(times):
        raise RunnerError("clock_regression")


def _write_tick(campaign: Path, tick_id: str, status: str, now: datetime, **extra: Any) -> dict[str, Any]:
    receipt = {
        "schema_version": 1,
        "tick_id": tick_id,
        "status": status,
        "completed_at": _iso(now),
        "executable": False,
        **extra,
    }
    write_record(campaign, "ticks", tick_id, receipt)
    return receipt


def _existing_tick(campaign: Path, tick_id: str) -> dict[str, Any] | None:
    for record in load_records(campaign, "ticks"):
        if _record_id(record) == tick_id:
            return _value(record)
    return None


def _events(campaign: Path) -> list[tuple[str, dict[str, Any]]]:
    return [(_record_id(record), _value(record)) for record in load_records(campaign, "events")]


def _event(campaign: Path, record_id: str) -> dict[str, Any] | None:
    for found, value in _events(campaign):
        if found == record_id:
            return value
    return None


def _unfinished_tick_ids(campaign: Path) -> list[str]:
    completed = {_record_id(record) for record in load_records(campaign, "ticks")}
    return sorted(
        record_id.removeprefix("tick-").removesuffix("-started")
        for record_id, _ in _events(campaign)
        if record_id.startswith("tick-") and record_id.endswith("-started")
        and record_id.removeprefix("tick-").removesuffix("-started") not in completed
    )


def _sessions(normalized: dict[str, Any]) -> list[dict[str, Any]]:
    # reconstruct_observation owns the semantic shape and chronology contract.
    return copy.deepcopy(normalized["sessions"])


def _candidate(sessions: list[dict[str, Any]], now: datetime) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
    completed = [item for item in sessions if _parse_time(item["session_close_at"]) < now]
    if not completed:
        return None
    latest = completed[-1]
    position = sessions.index(latest)
    following = sessions[position + 1:position + 6]
    if len(following) != 5:
        return None
    return latest, copy.deepcopy(following)


def _slot_state_count(campaign: Path) -> int:
    return len({
        value["slot_date"] for record_id, value in _events(campaign)
        if record_id.startswith(("reservation-", "missed-"))
    })


def _record_missed_slots(
    campaign: Path,
    sessions: list[dict[str, Any]],
    now: datetime,
    registered_at: datetime,
    max_checkpoints: int,
) -> None:
    existing = {
        value.get("slot_date") for record_id, value in _events(campaign)
        if record_id.startswith(("reservation-", "missed-"))
    }
    remaining = max_checkpoints - _slot_state_count(campaign)
    for index, session in enumerate(sessions[:-5]):
        if remaining <= 0:
            return
        following = sessions[index + 1:index + 6]
        anchor = _parse_time(following[0]["interval_start"])
        close = _parse_time(session["session_close_at"])
        slot = session["date"]
        if registered_at < anchor <= now and close < anchor and slot not in existing:
            write_record(campaign, "events", f"missed-{slot}", {
                "schema_version": 1,
                "event": "checkpoint_missed",
                "slot_date": slot,
                "source_session_close_at": session["session_close_at"],
                "decision_deadline": _iso(anchor - timedelta(microseconds=1)),
                "recorded_at": _iso(now),
                "reason": "anchor_window_elapsed",
                "executable": False,
            })
            existing.add(slot)
            remaining -= 1


def _bar_inputs(collection: dict[str, Any]) -> dict[str, Any]:
    capture = collection["capture"]["source_capture"]
    bundle = collection["bundle"]
    return normalize_market(capture, expires_at=bundle["expires_at"])["bar_inputs"]


def _decision_arm(decision: dict[str, Any], deadline: datetime, completed: datetime) -> dict[str, Any]:
    status = decision.get("status")
    reason = decision.get("reason") or decision.get("failure_type")
    targets = None
    cash_weight = None
    if status == "accepted":
        cio = decision.get("cio")
        if isinstance(cio, dict) and isinstance(cio.get("targets"), list):
            targets = [
                {"symbol": target["symbol"], "weight": target["weight"]}
                for target in cio["targets"]
            ]
            cash_weight = cio.get("cash_weight")
    if completed >= deadline:
        prior_reason = reason if isinstance(reason, str) and reason == "inputs_completed_after_deadline" else "decision_deadline_elapsed"
        status, reason, targets, cash_weight = "unavailable", prior_reason, None, None
    return {
        "status": status if isinstance(status, str) else "failed",
        "reason": reason if isinstance(reason, str) else None,
        "targets": targets,
        "cash_weight": cash_weight,
    }


def _verify_decision_artifact(decision_dir: Path, bundle: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    manifest = _strict_json(decision_dir / "manifest.json")
    expected_keys = {
        "schema_version", "run_id", "mode", "created_at", "code_version",
        "code_hash", "prompt_hash", "input_hash", "model_receipts", "files",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected_keys:
        raise RunnerError("invalid_decision_manifest")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise RunnerError("invalid_decision_manifest")
    expected_id = bundle["run_id"]
    if manifest["run_id"] != expected_id or decision_dir.name != expected_id:
        raise RunnerError("invalid_decision_manifest")
    if (
        manifest["mode"] != bundle["mode"]
        or manifest["code_version"] != PROMPT_VERSION
        or manifest["code_hash"] != _decision_code_hash()
        or manifest["prompt_hash"] != _hash({"version": PROMPT_VERSION, "contracts": ROLE_CONTRACTS})
        or manifest["input_hash"] != _hash(bundle)
        or not isinstance(manifest["model_receipts"], list)
        or not isinstance(manifest["files"], dict)
    ):
        raise RunnerError("invalid_decision_manifest")
    input_bundle = _strict_json(decision_dir / "input.json")
    decision = _strict_json(decision_dir / "decision.json")
    if (
        input_bundle != bundle
        or decision.get("run_id") != expected_id
        or decision.get("mode") != bundle["mode"]
        or decision.get("recorded_at") != manifest["created_at"]
    ):
        raise RunnerError("checkpoint_decision_input_mismatch")
    if manifest["files"].get("input.json") != hashlib.sha256((decision_dir / "input.json").read_bytes()).hexdigest():
        raise RunnerError("invalid_decision_manifest")
    replay = replay_run(decision_dir)
    return decision, manifest["model_receipts"], replay


def _models_match(receipts: list[dict[str, Any]], model: Any) -> bool:
    return bool(receipts) and all(
        isinstance(item, dict) and item.get("model") == model for item in receipts
    )


def _checkpoint_from_inputs(
    inputs: dict[str, Any],
    decision: dict[str, Any],
    receipts: list[dict[str, Any]],
    completed: datetime,
    model: str,
    *,
    in_doubt: bool = False,
) -> dict[str, Any]:
    deadline = _parse_time(inputs["decision_deadline"])
    controls = copy.deepcopy(inputs["controls"])
    if in_doubt:
        ai = {
            "status": "in_doubt", "reason": "unknown_model_completion",
            "targets": None, "cash_weight": None,
        }
        receipts = [{"status": "unknown", "billed_cost": "unknown"}]
    else:
        ai = _decision_arm(decision, deadline, completed)
        if decision.get("status") == "accepted" and not _models_match(receipts, model):
            ai = {
                "status": "failed", "reason": "requested_model_mismatch",
                "targets": None, "cash_weight": None,
            }
    checkpoint = {
        "schema_version": 1,
        "slot_date": inputs["slot_date"],
        "collection_id": inputs["collection_id"],
        "bundle_hash": inputs["bundle_hash"],
        "collection_path": inputs["collection_path"],
        "decision_path": inputs.get("decision_path") if not in_doubt else None,
        "source_session_close_at": inputs["source_session_close_at"],
        "decision_deadline": inputs["decision_deadline"],
        "bundle_as_of": inputs["bundle_as_of"],
        "inputs_frozen_at": inputs["inputs_frozen_at"],
        "frozen_at": _iso(completed),
        "outcome_sessions": copy.deepcopy(inputs["outcome_sessions"]),
        "arms": {**controls, "ai_three_role": ai},
        "model_receipts": copy.deepcopy(receipts),
        "executable": False,
    }
    return checkpoint


def _recover_started(
    campaign: Path,
    tick_id: str,
    protocol: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    inputs = _event(campaign, f"inputs-{tick_id}")
    inference = _event(campaign, f"inference-{tick_id}-started")
    if inputs is None or inference is None:
        return _write_tick(
            campaign, tick_id, "in_doubt", now,
            reason="ambiguous_started_tick", billed_cost="unknown",
        )
    decision_path = inputs["decision_path"]
    checkpoint_id = inputs["slot_date"]
    existing = {
        _record_id(record): _value(record)
        for record in load_records(campaign, "checkpoints")
    }
    if checkpoint_id in existing:
        deadline = _parse_time(existing[checkpoint_id]["decision_deadline"])
        return _write_tick(
            campaign, tick_id, "completed", now, checkpoint_id=checkpoint_id,
            recovered=True, prospective=now < deadline,
        )
    decision_dir = _artifact(decision_path, campaign, "decisions")
    try:
        collection_dir = _artifact(inputs["collection_path"], campaign, "collections")
        collection = reconstruct_collection(collection_dir)
        if _hash(collection["bundle"]) != inputs["bundle_hash"]:
            raise RunnerError("checkpoint_collection_mismatch")
        decision, receipts, _ = _verify_decision_artifact(decision_dir, collection["bundle"])
        checkpoint = _checkpoint_from_inputs(inputs, decision, receipts, now, protocol["model"])
        write_record(campaign, "checkpoints", checkpoint_id, checkpoint)
        return _write_tick(
            campaign, tick_id, "completed", now, checkpoint_id=checkpoint_id,
            recovered=True, prospective=now < _parse_time(inputs["decision_deadline"]),
        )
    except (OSError, json.JSONDecodeError, RunnerError, ValueError, KeyError, TypeError):
        recorded_completion = _parse_time(inputs["inputs_frozen_at"])
        checkpoint = _checkpoint_from_inputs(
            inputs, {}, [], recorded_completion, protocol["model"], in_doubt=True,
        )
        write_record(campaign, "checkpoints", checkpoint_id, checkpoint)
        return _write_tick(
            campaign, tick_id, "in_doubt", now, checkpoint_id=checkpoint_id,
            reason="unknown_model_completion", billed_cost="unknown",
        )


def tick(path, alpaca, public, provider, *, clock=None, execute_lab=None) -> dict[str, Any]:
    """Advance one campaign bucket exactly once under its exclusive lock."""
    campaign = Path(path)
    try:
        lock = campaign_lock(campaign)
        lock.__enter__()
    except CampaignBusy:
        return {
            "schema_version": 1,
            "status": "busy",
            "executable": False,
        }
    try:
        registration = load_campaign(campaign)
        _assert_identity(registration)
        protocol = _protocol(registration)
        now = _clock(clock)
        _assert_monotonic(now, campaign, registration)
        unfinished = _unfinished_tick_ids(campaign)
        if unfinished:
            return _recover_started(campaign, unfinished[0], protocol, now)
        tick_id = _tick_id(now)
        prior = _existing_tick(campaign, tick_id)
        if prior is not None:
            return prior
        write_record(campaign, "events", f"tick-{tick_id}-started", {
            "schema_version": 1, "event": "tick_started", "tick_id": tick_id,
            "started_at": _iso(now), "executable": False,
        })
        registered = _parse_time(registration["registered_at"])
        campaign_end = registered + timedelta(days=90)
        if now >= campaign_end:
            return _write_tick(
                campaign, tick_id, "terminal", now,
                reason="campaign_expired",
            )
        ny_registered = registered.astimezone(NY).date()
        ny_now = now.astimezone(NY).date()
        start_date = ny_registered - timedelta(days=10)
        end_date = min(ny_now + timedelta(days=30), campaign_end.astimezone(NY).date())
        try:
            observation = observe_market(
                _symbols(protocol), start_date.isoformat(), end_date.isoformat(),
                alpaca, clock=clock,
            )
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except BaseException:
            return _write_tick(campaign, tick_id, "unavailable", _clock(clock), reason="observation_capture_failed")
        write_record(campaign, "observations", tick_id, observation)
        try:
            normalized = reconstruct_observation(observation)
            sessions = _sessions(normalized)
        except (ObservationError, RunnerError, ValueError, TypeError):
            return _write_tick(campaign, tick_id, "unavailable", _clock(clock), reason="observation_reconstruction_failed")

        current = _clock(clock)
        observation_completed = _parse_time(observation.get("completed_at"))
        if current < observation_completed:
            raise RunnerError("clock_regression")
        _record_missed_slots(
            campaign, sessions, current, registered,
            protocol["max_checkpoints"],
        )
        candidate = _candidate(sessions, current)
        if candidate is None:
            return _write_tick(campaign, tick_id, "waiting", _clock(clock), reason="no_complete_candidate")
        source_session, outcomes = candidate
        anchor = _parse_time(outcomes[0]["interval_start"])
        slot = source_session["date"]
        if current >= anchor or registered >= anchor or anchor - current <= timedelta(seconds=300):
            closed = "slot_window_closed"
            return _write_tick(campaign, tick_id, "waiting", current, reason=closed)
        if _slot_state_count(campaign) >= protocol["max_checkpoints"]:
            return _write_tick(campaign, tick_id, "observed", _clock(clock), reason="max_checkpoints_reached")
        if _event(campaign, f"reservation-{slot}") is not None:
            return _write_tick(campaign, tick_id, "observed", _clock(clock), reason="slot_already_reserved")

        write_record(campaign, "events", f"reservation-{slot}", {
            "schema_version": 1, "event": "checkpoint_reserved", "tick_id": tick_id,
            "slot_date": slot, "reserved_at": _iso(current), "executable": False,
        })
        collection_id = f"collection-{slot}-{tick_id}"
        experiment = {
            "collection_id": collection_id,
            "book_id": f"book-{slot}",
            "universe": copy.deepcopy(protocol["universe"]),
            "research_cash": protocol["research_cash"],
            "ttl_seconds": 900,
        }
        capture: dict[str, Any] | None = None
        try:
            capture = collect_sources(_symbols(protocol), alpaca, public, clock=clock)
            built = build_collection(capture, experiment)
            collection_dir = evidence_archive.write_collection(
                campaign / "artifacts" / "collections", built["bundle"], built["capture"],
            )
        except Exception:
            if capture is not None and all(source.get("complete") for source in capture.get("sources", {}).values()):
                evidence_archive.write_failed_collection(
                    campaign / "artifacts" / "collections", collection_id,
                    capture, error_code="collection_failed",
                )
            write_record(campaign, "events", f"collection-{tick_id}-failed", {
                "schema_version": 1, "event": "collection_failed", "tick_id": tick_id,
                "slot_date": slot, "recorded_at": _iso(_clock(clock)), "executable": False,
            })
            return _write_tick(campaign, tick_id, "unavailable", _clock(clock), checkpoint_id=slot, reason="collection_failed")

        input_completion = _clock(clock)
        bundle = built["bundle"]
        collection_as_of = _parse_time(bundle["as_of"])
        if input_completion < collection_as_of:
            raise RunnerError("clock_regression")
        deadline = min(
            collection_as_of + timedelta(seconds=300),
            anchor - timedelta(microseconds=1),
        )
        controls = baseline_allocations(protocol, _bar_inputs({"bundle": bundle, "capture": built["capture"]}))
        decision_relative = f"artifacts/decisions/{collection_id}"
        inputs = {
            "schema_version": 1,
            "event": "checkpoint_inputs_frozen",
            "tick_id": tick_id,
            "slot_date": slot,
            "collection_id": collection_id,
            "bundle_hash": _hash(bundle),
            "collection_path": _safe_relative(collection_dir, campaign),
            "decision_path": decision_relative,
            "source_session_close_at": source_session["session_close_at"],
            "decision_deadline": _iso(deadline),
            "bundle_as_of": bundle["as_of"],
            "inputs_frozen_at": _iso(input_completion),
            "outcome_sessions": outcomes,
            "controls": copy.deepcopy(controls),
            "executable": False,
        }
        write_record(campaign, "events", f"inputs-{tick_id}", inputs)
        if input_completion >= deadline:
            checkpoint = _checkpoint_from_inputs(
                inputs,
                {"status": "late", "reason": "inputs_completed_after_deadline"},
                [], input_completion, protocol["model"],
            )
            checkpoint["decision_path"] = None
            write_record(campaign, "checkpoints", slot, checkpoint)
            return _write_tick(campaign, tick_id, "completed", _clock(clock), checkpoint_id=slot, prospective=False)

        inference_started = _clock(clock)
        if inference_started < input_completion:
            raise RunnerError("clock_regression")
        write_record(campaign, "events", f"inference-{tick_id}-started", {
            "schema_version": 1, "event": "inference_started", "tick_id": tick_id,
            "slot_date": slot, "decision_path": decision_relative,
            "started_at": _iso(inference_started), "billed_cost": "unknown", "executable": False,
        })
        decision_root = campaign / "artifacts" / "decisions"
        decision = (
            run_lab(bundle, provider, decision_root, now=clock)
            if execute_lab is None
            else execute_lab(bundle, collection_dir, decision_root)
        )
        completed = _clock(clock)
        if completed < inference_started:
            raise RunnerError("clock_regression")
        decision_dir = campaign / decision_relative
        verified_decision, receipts, _ = _verify_decision_artifact(decision_dir, bundle)
        checkpoint = _checkpoint_from_inputs(
            inputs, verified_decision, receipts, completed, protocol["model"],
        )
        write_record(campaign, "checkpoints", slot, checkpoint)
        publication_witness = _clock(clock)
        if publication_witness < completed:
            raise RunnerError("clock_regression")
        return _write_tick(
            campaign, tick_id, "completed", publication_witness, checkpoint_id=slot,
            prospective=publication_witness < deadline,
        )
    finally:
        lock.__exit__(None, None, None)


def _offline_state(path: Path) -> dict[str, Any]:
    registration = load_campaign(path)
    _assert_identity(registration)
    protocol = _protocol(registration)
    observation_records = load_records(path, "observations")
    observations = [_value(record) for record in observation_records]
    valid_observations: list[dict[str, Any]] = []
    reconstructed_observations = []
    for record, observation in zip(observation_records, observations, strict=True):
        try:
            reconstructed = reconstruct_observation(observation)
        except ObservationError as caught:
            reconstructed_observations.append({
                "record_id": _record_id(record),
                "status": "unavailable",
                "reason": str(caught),
            })
        else:
            valid_observations.append(observation)
            reconstructed_observations.append({
                "record_id": _record_id(record),
                "status": "complete",
                "observation": reconstructed,
            })

    checkpoints = [_value(record) for record in load_records(path, "checkpoints")]
    tick_values = [_value(record) for record in load_records(path, "ticks")]
    verified_checkpoints = []
    for checkpoint in checkpoints:
        collection_dir = _artifact(checkpoint["collection_path"], path, "collections")
        collection = reconstruct_collection(collection_dir)
        if collection.get("status") != "complete" or _hash(collection.get("bundle")) != checkpoint.get("bundle_hash"):
            raise RunnerError("checkpoint_collection_mismatch")
        if checkpoint.get("bundle_as_of") != collection["bundle"]["as_of"]:
            raise RunnerError("checkpoint_collection_mismatch")
        expected = baseline_allocations(protocol, _bar_inputs(collection))
        recorded_controls = {
            name: checkpoint.get("arms", {}).get(name)
            for name in ("cash", "equal_weight", "sma20")
        }
        if recorded_controls != expected:
            raise RunnerError("checkpoint_control_mismatch")
        decision_path = checkpoint.get("decision_path")
        replay = None
        if decision_path is not None:
            decision_dir = _artifact(decision_path, path, "decisions")
            decision, receipts, replay = _verify_decision_artifact(decision_dir, collection["bundle"])
            expected_ai = _decision_arm(
                decision, _parse_time(checkpoint["decision_deadline"]),
                _parse_time(checkpoint["frozen_at"]),
            )
            if decision.get("status") == "accepted" and not _models_match(receipts, protocol["model"]):
                expected_ai = {
                    "status": "failed", "reason": "requested_model_mismatch",
                    "targets": None, "cash_weight": None,
                }
            if checkpoint.get("arms", {}).get("ai_three_role") != expected_ai:
                raise RunnerError("checkpoint_decision_mismatch")
            if checkpoint.get("model_receipts") != receipts:
                raise RunnerError("checkpoint_receipt_mismatch")
        else:
            if checkpoint.get("arms", {}).get("ai_three_role", {}).get("status") not in {"in_doubt", "unavailable"}:
                raise RunnerError("checkpoint_decision_reference_missing")
            receipts = checkpoint["model_receipts"]
            ai = checkpoint["arms"]["ai_three_role"]
            pre_inference_late = (
                ai.get("status") == "unavailable"
                and ai.get("reason") in {"inputs_completed_after_deadline", "decision_deadline_elapsed"}
                and not receipts
            )
            unknown = any(
                isinstance(item, dict)
                and (item.get("billed_cost") == "unknown" or item.get("status") == "unknown")
                for item in receipts
            )
            if not pre_inference_late and not unknown:
                raise RunnerError("checkpoint_receipt_mismatch")
        frozen_at = _parse_time(checkpoint["frozen_at"])
        deadline = _parse_time(checkpoint["decision_deadline"])
        publications = [
            tick for tick in tick_values
            if tick.get("checkpoint_id") == checkpoint["slot_date"]
            and tick.get("status") in {"completed", "in_doubt"}
        ]
        publication = max(
            (_parse_time(tick["completed_at"]) for tick in publications),
            default=None,
        )
        publication_confirmed = publication is not None and all(
            tick.get("prospective") is not False for tick in publications
        )
        if frozen_at >= deadline or publication is None or publication >= deadline or not publication_confirmed:
            reason = "checkpoint_late" if publication is not None and publication >= deadline else "publication_unconfirmed"
            score = {
                "schema_version": 1,
                "slot_date": checkpoint["slot_date"],
                "checkpoint_status": "late",
                "executable": False,
                "economic_claim": None,
                "horizons": [
                    {"horizon": horizon, "status": "unavailable", "reason": reason,
                     "first_observed": None, "latest_observed": None}
                    for horizon in protocol["horizons"]
                ],
                "counts": {
                    "scored": 0, "pending": 0,
                    "unavailable": len(protocol["horizons"]), "economically_scorable": 0,
                },
            }
        else:
            score = score_checkpoint(protocol, checkpoint, valid_observations)
        verified_checkpoints.append({
            "slot_date": checkpoint["slot_date"],
            "checkpoint": copy.deepcopy(checkpoint),
            "decision_replay": replay,
            "score": score,
        })
    verified_checkpoints.sort(key=lambda item: item["slot_date"])
    return {
        "registration": registration,
        "protocol": protocol,
        "observations": observations,
        "valid_observations": valid_observations,
        "reconstructed_observations": reconstructed_observations,
        "checkpoints": verified_checkpoints,
    }


def verify_campaign(path) -> dict[str, Any]:
    """Verify hashes and semantically reconstruct a campaign without I/O adapters."""
    campaign = Path(path)
    state = _offline_state(campaign)
    events = [
        {"record_id": _record_id(record), "value": _value(record)}
        for record in load_records(campaign, "events")
    ]
    ticks = [
        {"record_id": _record_id(record), "value": _value(record)}
        for record in load_records(campaign, "ticks")
    ]
    return {
        "schema_version": 1,
        "campaign_id": state["protocol"]["campaign_id"],
        "verified": True,
        "verification_kind": "offline_semantic_reconstruction",
        "registration_hash": _hash(state["registration"]),
        "observation_count": len(state["observations"]),
        "valid_observation_count": len(state["valid_observations"]),
        "unavailable_observation_count": len(state["observations"]) - len(state["valid_observations"]),
        "checkpoint_count": len(state["checkpoints"]),
        "event_count": len(events),
        "tick_count": len(ticks),
        "checkpoints": state["checkpoints"],
        "events": events,
        "ticks": ticks,
        "executable": False,
    }


def report_campaign(path) -> dict[str, Any]:
    """Return a deterministic, fully offline campaign report."""
    verified = verify_campaign(path)
    events = verified["events"]
    checkpoints = verified["checkpoints"]
    states = {
        "reservations": sum(item["record_id"].startswith("reservation-") for item in events),
        "missed": sum(item["record_id"].startswith("missed-") for item in events),
        "in_doubt": sum(
            item["value"].get("status") == "in_doubt"
            for item in verified["ticks"]
        ),
        "collection_failures": sum(item["record_id"].endswith("-failed") for item in events),
    }
    arm_counts: dict[str, dict[str, int]] = {
        name: {} for name in ("cash", "equal_weight", "sma20", "ai_three_role")
    }
    for item in checkpoints:
        for name, arm in item["checkpoint"]["arms"].items():
            status = arm.get("status", "unknown") if isinstance(arm, dict) else "unknown"
            arm_counts[name][status] = arm_counts[name].get(status, 0) + 1
    return {
        "schema_version": 1,
        "campaign_id": verified["campaign_id"],
        "verified": True,
        "executable": False,
        "counts": {
            "observations": verified["observation_count"],
            "valid_observations": verified["valid_observation_count"],
            "unavailable_observations": verified["unavailable_observation_count"],
            "checkpoints": verified["checkpoint_count"],
            **states,
        },
        "arm_status_counts": arm_counts,
        "checkpoints": [
            {
                "slot_date": item["slot_date"],
                "score": item["score"],
                "model_receipts": item["checkpoint"]["model_receipts"],
                "usage_status": (
                    "known" if item["checkpoint"]["model_receipts"]
                    and all(isinstance(receipt, dict) and isinstance(receipt.get("usage"), dict)
                            for receipt in item["checkpoint"]["model_receipts"])
                    else "missing"
                ),
                "billed_cost": (
                    "unknown" if not any(
                        isinstance(receipt, dict) and "billed_cost" in receipt
                        and receipt["billed_cost"] != "unknown"
                        for receipt in item["checkpoint"]["model_receipts"]
                    ) else [
                        receipt["billed_cost"] for receipt in item["checkpoint"]["model_receipts"]
                        if isinstance(receipt, dict) and receipt.get("billed_cost") not in {None, "unknown"}
                    ]
                ),
            }
            for item in checkpoints
        ],
        "caveats": {
            "price_only": True,
            "corporate_action_status": "unchecked",
            "economically_scorable": False,
            "billed_cost_absent_means": "unknown",
            "overlapping_cohorts_are_not_compounded": True,
        },
    }
