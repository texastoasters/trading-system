"""Public-seam tests for the research-only campaign runner."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from prospective_evaluation import runner
from prospective_evaluation.storage import campaign_lock, register_campaign, write_record

UTC = timezone.utc
NOW = datetime(2026, 1, 22, 2, 0, tzinfo=UTC)


def request(campaign_id="runner-fixture"):
    return {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "universe": [{"symbol": "SPY", "asset_class": "etf"}],
        "model": "fixture-model",
        "research_cash": "1000",
        "max_checkpoints": 3,
    }


class Clock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class SequenceClock:
    def __init__(self, *values):
        self.values = list(values)
        self.last = values[-1]

    def __call__(self):
        if self.values:
            value = self.values.pop(0)
            if isinstance(value, BaseException):
                raise value
            self.last = value
        return self.last


class Forbidden:
    def __getattr__(self, name):
        raise AssertionError(f"external I/O attempted: {name}")


class MarketTransport:
    """Synthetic GET-only calendar/bars/news adapter."""

    def __init__(self):
        self.calls = []
        first = date(2026, 1, 1)
        self.sessions = []
        self.bars = []
        for index in range(28):
            day = first + timedelta(days=index)
            text = day.isoformat()
            self.sessions.append({
                "date": text, "open": "09:30", "close": "16:00",
                "session_open": "0930", "session_close": "2000",
                "settlement_date": text,
            })
            if index < 21:
                self.bars.append({
                    "t": text + "T05:00:00Z", "o": str(100 + index),
                    "h": str(102 + index), "l": str(99 + index),
                    "c": str(101 + index), "v": 1000 + index,
                })

    def fetch(self, source, query):
        self.calls.append((source, dict(query)))
        sessions = [
            item for item in self.sessions
            if query.get("start", "0000-01-01") <= item["date"] <= query.get("end", "9999-12-31")
        ]
        bar_start = str(query.get("start", "0000-01-01"))[:10]
        bars = [item for item in self.bars if item["t"][:10] >= bar_start]
        payload = {
            "sessions": sessions,
            "bars": {"bars": {"SPY": bars}, "next_page_token": None},
            "news": {"news": [], "next_page_token": None},
        }[source]
        stamp = query.get("end", NOW.isoformat()) if source == "bars" else NOW.isoformat()
        return {
            "payload": payload, "request_started_at": stamp,
            "received_at": stamp, "body_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "rate_limit": {},
        }


class FailingMarketTransport(MarketTransport):
    def __init__(self, source):
        super().__init__()
        self.source = source

    def fetch(self, source, query):
        if source == self.source:
            raise RuntimeError("synthetic source failure")
        return super().fetch(source, query)


class PublicTransport:
    def __init__(self):
        self.calls = []

    def fetch(self, source, query):
        self.calls.append((source, dict(query)))
        payload = "\r\n".join([
            "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SYNTHETIC//EN",
            "BEGIN:VEVENT", "UID:fixture-event", "SUMMARY:SYNTHETIC event",
            "DTSTART:20260123T133000Z", "DTSTAMP:20260101T000000Z",
            "SEQUENCE:0", "END:VEVENT", "END:VCALENDAR", "",
        ])
        stamp = NOW.isoformat()
        return {
            "payload": payload, "request_started_at": stamp,
            "received_at": stamp, "body_sha256": hashlib.sha256(payload.encode()).hexdigest(),
            "rate_limit": {},
        }


class FakeExecutor:
    def __init__(self, provider):
        self.provider = provider
        self.calls = []

    def __call__(self, bundle, collection_path, output_dir):
        self.calls.append((copy.deepcopy(bundle), collection_path, output_dir))
        archived = json.loads((collection_path / "bundle.json").read_text())
        assert archived == bundle
        return runner.run_lab(bundle, self.provider, output_dir, now=NOW)


class Provider:
    def __init__(
        self, *, model="fixture-model", failure=None,
        target_weight="1", cash_weight="0",
    ):
        self.calls = []
        self.model = model
        self.failure = failure
        self.target_weight = target_weight
        self.cash_weight = cash_weight

    def complete(self, role, payload):
        self.calls.append(role)
        if self.failure is not None:
            raise self.failure
        evidence = payload["bundle"]["evidence"]
        applicable = next(
            item for item in evidence
            if "SPY" in item["symbols"] and item["kind"] in {"technical", "algorithmic", "news"}
        )
        evidence_id = applicable["evidence_id"]
        expires = payload["bundle"]["expires_at"]
        outputs = {
            "research": {
                "summary": "Synthetic fixture synthesis.", "candidate_symbols": ["SPY"],
                "evidence_ids": [evidence_id], "counterevidence": [], "missing_data": [],
            },
            "cio": {
                "action": "rebalance",
                "targets": [{
                    "symbol": "SPY", "weight": self.target_weight, "thesis": "Synthetic fixture.",
                    "evidence_ids": [evidence_id], "falsifier": "Fixture changes.",
                }],
                "cash_weight": self.cash_weight, "rationale": "Synthetic fixture allocation.",
                "evidence_ids": [evidence_id], "counterevidence": [], "expires_at": expires,
            },
            "critic": {"verdict": "pass", "issues": [], "evidence_ids": [evidence_id]},
        }
        return {
            "text": json.dumps(outputs[role]), "model": self.model, "usage": {},
            "latency_ms": 1, "request_id": "fixture-request-" + role,
        }


def test_busy_campaign_returns_public_busy_receipt_without_external_calls(tmp_path):
    path = register_campaign(tmp_path, request("busy-fixture"), clock=Clock(NOW - timedelta(hours=1)))

    with campaign_lock(path):
        receipt = runner.tick(path, Forbidden(), Forbidden(), Forbidden(), clock=Clock(NOW))

    assert receipt == {"schema_version": 1, "status": "busy", "executable": False}


def test_clock_contract_and_regression_fail_closed_through_public_tick(tmp_path):
    path = register_campaign(tmp_path, request("clock-contract"), clock=Clock(NOW))

    for clock, code in (
        (object(), "invalid_clock"),
        (Clock(datetime(2026, 1, 22, 2, 0)), "invalid_clock"),
        (Clock(NOW - timedelta(seconds=1)), "clock_regression"),
    ):
        try:
            runner.tick(path, Forbidden(), Forbidden(), Forbidden(), clock=clock)
        except runner.RunnerError as caught:
            assert str(caught) == code
        else:
            raise AssertionError(f"tick accepted {code}")


def test_malformed_persisted_times_fail_closed_offline(tmp_path):
    for campaign_id, value in (
        ("bad-time-text", "not-a-time"),
        ("bad-time-naive", "2026-01-22T02:00:00"),
    ):
        path = register_campaign(tmp_path, request(campaign_id), clock=Clock(NOW))
        write_record(path, "events", "corrupt-time", {
            "schema_version": 1, "event": "fixture", "recorded_at": value, "executable": False,
        })
        try:
            runner.tick(path, Forbidden(), Forbidden(), Forbidden(), clock=Clock(NOW))
        except runner.RunnerError as caught:
            assert str(caught) == "invalid_campaign_time"
        else:
            raise AssertionError("malformed persisted time accepted")


def test_registration_identity_change_fails_tick_and_offline_verification(tmp_path):
    path = register_campaign(tmp_path, request("identity-change"), clock=Clock(NOW))
    registration_path = path / "registration.json"
    manifest_path = path / "manifest.json"
    registration = json.loads(registration_path.read_text())
    registration["implementation_fingerprint"] = "0" * 64
    registration_path.write_text(json.dumps(registration, sort_keys=True, separators=(",", ":")) + "\n")
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["registration.json"] = hashlib.sha256(registration_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")

    for operation in (
        lambda: runner.tick(path, Forbidden(), Forbidden(), Forbidden(), clock=Clock(NOW)),
        lambda: runner.verify_campaign(path),
    ):
        try:
            operation()
        except runner.RunnerError as caught:
            assert str(caught) == "implementation_fingerprint_mismatch"
        else:
            raise AssertionError("runtime identity mismatch accepted")


def test_capture_basefailure_is_sanitized_but_process_controls_propagate(tmp_path):
    class ExoticFailure(BaseException):
        pass

    failed = register_campaign(tmp_path, request("basefailure"), clock=Clock(NOW - timedelta(hours=1)))
    receipt = runner.tick(
        failed, Forbidden(), Forbidden(), Forbidden(),
        clock=SequenceClock(NOW, ExoticFailure("synthetic"), NOW),
    )
    assert receipt["status"] == "unavailable"
    assert receipt["reason"] == "observation_capture_failed"

    for index, control in enumerate((KeyboardInterrupt(), SystemExit(), GeneratorExit())):
        path = register_campaign(tmp_path, request(f"control-{index}"), clock=Clock(NOW - timedelta(hours=1)))
        try:
            runner.tick(path, Forbidden(), Forbidden(), Forbidden(), clock=SequenceClock(NOW, control))
        except type(control):
            pass
        else:
            raise AssertionError("process control was sanitized")


def test_old_bucket_started_tick_is_recovered_before_current_bucket_io(tmp_path):
    path = register_campaign(tmp_path, request("old-bucket"), clock=Clock(NOW - timedelta(hours=1)))
    write_record(path, "events", "tick-20260122T014500Z-started", {
        "schema_version": 1, "event": "tick_started", "tick_id": "20260122T014500Z",
        "started_at": (NOW - timedelta(minutes=15)).isoformat(), "executable": False,
    })

    receipt = runner.tick(path, Forbidden(), Forbidden(), Forbidden(), clock=Clock(NOW))

    assert receipt["tick_id"] == "20260122T014500Z"
    assert receipt["status"] == "in_doubt"
    assert runner.load_records(path, "observations") == []


def test_campaign_expiry_is_terminal_before_any_source_call(tmp_path):
    path = register_campaign(tmp_path, request("expired-campaign"), clock=Clock(NOW - timedelta(days=90)))

    receipt = runner.tick(path, Forbidden(), Forbidden(), Forbidden(), clock=Clock(NOW))

    assert receipt["status"] == "terminal"
    assert receipt["reason"] == "campaign_expired"
    assert runner.load_records(path, "observations") == []


def test_durable_expiry_closes_only_pending_horizons_offline(tmp_path):
    registered = NOW - timedelta(hours=1)
    path = register_campaign(tmp_path, request("expiry-closes-pending"), clock=Clock(registered))
    assert runner.tick(
        path, MarketTransport(), PublicTransport(), Provider(), clock=Clock(NOW),
    )["status"] == "completed"

    observed_at = NOW + timedelta(days=1)

    class FirstHorizonMarket(MarketTransport):
        def __init__(self):
            super().__init__()
            day = date(2026, 1, 22)
            self.bars.append({
                "t": day.isoformat() + "T05:00:00Z", "o": "121", "h": "123",
                "l": "120", "c": "122", "v": 2000,
            })

        def fetch(self, source, query):
            result = super().fetch(source, query)
            result["request_started_at"] = observed_at.isoformat()
            result["received_at"] = observed_at.isoformat()
            return result

    runner.tick(
        path, FirstHorizonMarket(), Forbidden(), Forbidden(), clock=Clock(observed_at),
    )
    before = runner.report_campaign(path)["checkpoints"][0]["score"]
    assert [item["status"] for item in before["horizons"]] == ["scored", "pending"]
    preserved = before["horizons"][0]

    expired_at = registered + timedelta(days=91)
    terminal = runner.tick(
        path, Forbidden(), Forbidden(), Forbidden(), clock=Clock(expired_at),
    )
    report = runner.report_campaign(path)
    verified = runner.verify_campaign(path)
    score = report["checkpoints"][0]["score"]

    assert terminal["status"] == "terminal"
    assert terminal["reason"] == "campaign_expired"
    assert score["horizons"][0] == preserved
    assert score["horizons"][1] == {
        "horizon": 5, "status": "unavailable", "reason": "campaign_expired",
        "first_observed": None, "latest_observed": None,
        "latest_status": "unavailable", "latest_reason": "campaign_expired",
        "latest_observation_completed_at": before["horizons"][1]["latest_observation_completed_at"],
    }
    assert score["counts"] == {
        "scored": 1, "pending": 0, "unavailable": 1, "economically_scorable": 0,
    }
    assert verified["checkpoints"][0]["score"] == score


def test_premature_forged_terminal_tick_does_not_close_pending_horizons(tmp_path):
    registered = NOW - timedelta(hours=1)
    path = register_campaign(tmp_path, request("premature-terminal"), clock=Clock(registered))
    assert runner.tick(
        path, MarketTransport(), PublicTransport(), Provider(), clock=Clock(NOW),
    )["status"] == "completed"
    forged_at = registered + timedelta(days=89)
    forged_id = forged_at.replace(minute=0, second=0, microsecond=0).strftime("%Y%m%dT%H%M%SZ")
    write_record(path, "ticks", forged_id, {
        "schema_version": 1, "tick_id": forged_id, "status": "terminal",
        "completed_at": forged_at.isoformat(), "executable": False,
        "reason": "campaign_expired",
    })

    report = runner.report_campaign(path)
    verified = runner.verify_campaign(path)

    assert [
        item["status"] for item in report["checkpoints"][0]["score"]["horizons"]
    ] == ["pending", "pending"]
    assert verified["checkpoints"][0]["score"] == report["checkpoints"][0]["score"]


def test_noncanonical_terminal_tick_does_not_close_pending_horizons(tmp_path):
    registered = NOW - timedelta(hours=1)
    path = register_campaign(tmp_path, request("noncanonical-terminal"), clock=Clock(registered))
    assert runner.tick(
        path, MarketTransport(), PublicTransport(), Provider(), clock=Clock(NOW),
    )["status"] == "completed"
    forged_at = registered + timedelta(days=91)
    forged_id = forged_at.replace(minute=0, second=0, microsecond=0).strftime("%Y%m%dT%H%M%SZ")
    write_record(path, "ticks", forged_id, {
        "schema_version": 1, "tick_id": forged_id, "status": "terminal",
        "completed_at": forged_at.isoformat(), "executable": False,
        "reason": "campaign_expired", "forged": True,
    })

    report = runner.report_campaign(path)
    verified = runner.verify_campaign(path)

    assert [
        item["status"] for item in report["checkpoints"][0]["score"]["horizons"]
    ] == ["pending", "pending"]
    assert verified["checkpoints"][0]["score"] == report["checkpoints"][0]["score"]


def test_waiting_slots_cover_no_complete_and_insufficient_future_rows(tmp_path):
    for campaign_id, sessions in (
        ("no-complete", []),
        ("too-few-future", MarketTransport().sessions[:22]),
    ):
        path = register_campaign(tmp_path, request(campaign_id), clock=Clock(NOW - timedelta(hours=1)))
        market = MarketTransport()
        market.sessions = sessions
        receipt = runner.tick(path, market, Forbidden(), Forbidden(), clock=Clock(NOW))
        assert receipt["status"] in {"waiting", "unavailable"}


def test_missed_slots_are_published_and_bounded_by_max_checkpoints(tmp_path):
    path = register_campaign(tmp_path, request("missed-visible"), clock=Clock(NOW - timedelta(days=2)))
    market = MarketTransport()
    now = NOW + timedelta(days=1)
    market.bars.append({
        "t": "2026-01-22T05:00:00Z", "o": "121", "h": "123",
        "l": "120", "c": "122", "v": 2000,
    })
    # Match the later observation's availability clock.
    class LaterMarket(MarketTransport):
        def __init__(self):
            self.calls, self.sessions, self.bars = market.calls, market.sessions, market.bars

        def fetch(self, source, query):
            result = super().fetch(source, query)
            result["request_started_at"] = now.isoformat()
            result["received_at"] = now.isoformat()
            return result

    receipt = runner.tick(path, LaterMarket(), Forbidden(), Forbidden(), clock=Clock(now))
    missed = [record for record in runner.load_records(path, "events") if record["record_id"].startswith("missed-")]

    assert receipt["status"] in {"waiting", "unavailable", "completed", "observed"}
    assert 1 <= len(missed) <= request()["max_checkpoints"]
    assert all(record["value"]["reason"] == "anchor_window_elapsed" for record in missed)


def test_missed_slot_after_close_but_before_anchor_is_visible_if_first_tick_is_late(tmp_path):
    registered = datetime(2026, 1, 21, 2, 0, tzinfo=UTC)
    now = datetime(2026, 1, 22, 6, 0, tzinfo=UTC)
    path = register_campaign(tmp_path, request("late-first-tick"), clock=Clock(registered))
    market = MarketTransport()
    market.bars.append({
        "t": "2026-01-22T05:00:00Z", "o": "121", "h": "123",
        "l": "120", "c": "122", "v": 2000,
    })

    class LaterMarket(MarketTransport):
        def __init__(self):
            self.calls, self.sessions, self.bars = market.calls, market.sessions, market.bars

        def fetch(self, source, query):
            result = super().fetch(source, query)
            result["request_started_at"] = now.isoformat()
            result["received_at"] = now.isoformat()
            return result

    runner.tick(path, LaterMarket(), Forbidden(), Forbidden(), clock=Clock(now))
    missed = [record["value"]["slot_date"] for record in runner.load_records(path, "events") if record["record_id"].startswith("missed-")]

    assert "2026-01-21" in missed


def test_reserved_slot_and_max_checkpoint_states_do_not_infer(tmp_path):
    for campaign_id, reservation_id, maximum, reason in (
        ("reserved-current", "reservation-2026-01-21", 3, "slot_already_reserved"),
        ("already-maxed", "reservation-2025-12-31", 1, "max_checkpoints_reached"),
    ):
        declared = request(campaign_id)
        declared["max_checkpoints"] = maximum
        path = register_campaign(tmp_path, declared, clock=Clock(NOW - timedelta(hours=1)))
        write_record(path, "events", reservation_id, {
            "schema_version": 1, "event": "checkpoint_reserved", "tick_id": "prior",
            "slot_date": reservation_id.removeprefix("reservation-"),
            "reserved_at": (NOW - timedelta(minutes=30)).isoformat(), "executable": False,
        })
        receipt = runner.tick(path, MarketTransport(), Forbidden(), Forbidden(), clock=Clock(NOW))
        assert receipt["reason"] == reason


def test_no_completed_session_and_closed_slot_wait_without_inference(tmp_path):
    no_complete_path = register_campaign(tmp_path, request("none-complete"), clock=Clock(NOW - timedelta(hours=1)))
    no_complete = MarketTransport()
    no_complete.sessions = no_complete.sessions[21:]
    no_complete.bars = []
    waiting = runner.tick(no_complete_path, no_complete, Forbidden(), Forbidden(), clock=Clock(NOW))
    assert waiting["reason"] == "no_complete_candidate"


def test_closed_slot_waits_without_collection_or_inference(tmp_path):
    now = NOW + timedelta(hours=2, minutes=56)

    class LaterMarket(MarketTransport):
        def fetch(self, source, query):
            result = super().fetch(source, query)
            result["request_started_at"] = now.isoformat()
            result["received_at"] = now.isoformat()
            return result

    path = register_campaign(tmp_path, request("closed-slot"), clock=Clock(NOW - timedelta(hours=1)))
    receipt = runner.tick(path, LaterMarket(), Forbidden(), Forbidden(), clock=Clock(now))

    assert receipt["status"] == "waiting"
    assert receipt["reason"] == "slot_window_closed"
    assert runner.load_records(path, "checkpoints") == []


def test_repeat_completed_tick_returns_saved_receipt_without_external_calls(tmp_path):
    path = register_campaign(tmp_path, request(), clock=Clock(NOW - timedelta(hours=1)))
    tick_id = "20260122T020000Z"
    receipt = {
        "schema_version": 1, "tick_id": tick_id, "status": "waiting",
        "completed_at": NOW.isoformat(), "executable": False,
        "reason": "no_complete_candidate",
    }
    write_record(path, "ticks", tick_id, receipt)

    assert runner.tick(path, Forbidden(), Forbidden(), Forbidden(), clock=Clock(NOW)) == receipt


def test_ambiguous_started_tick_is_in_doubt_without_retrying_model_or_sources(tmp_path):
    path = register_campaign(tmp_path, request(), clock=Clock(NOW - timedelta(hours=1)))
    write_record(path, "events", "tick-20260122T020000Z-started", {
        "schema_version": 1, "event": "tick_started",
        "tick_id": "20260122T020000Z", "started_at": NOW.isoformat(), "executable": False,
    })

    receipt = runner.tick(path, Forbidden(), Forbidden(), Forbidden(), clock=Clock(NOW))

    assert receipt["status"] == "in_doubt"
    assert receipt["billed_cost"] == "unknown"


def test_post_observation_clock_regression_fails_closed(tmp_path):
    path = register_campaign(tmp_path, request("observation-regression"), clock=Clock(NOW - timedelta(hours=1)))
    clock = SequenceClock(NOW, NOW, NOW, NOW - timedelta(seconds=1))
    try:
        runner.tick(path, MarketTransport(), Forbidden(), Forbidden(), clock=clock)
    except runner.RunnerError as caught:
        assert str(caught) == "clock_regression"
    else:
        raise AssertionError("post-observation clock regression accepted")


def test_complete_tick_freezes_inputs_runs_exact_model_and_offline_reconstructs(tmp_path):
    path = register_campaign(tmp_path, request(), clock=Clock(NOW - timedelta(hours=1)))
    market, public, provider = MarketTransport(), PublicTransport(), Provider()

    receipt = runner.tick(path, market, public, provider, clock=Clock(NOW))
    repeated = runner.tick(path, Forbidden(), Forbidden(), Forbidden(), clock=Clock(NOW))
    report = runner.report_campaign(path)
    verified = runner.verify_campaign(path)

    assert receipt == repeated
    assert receipt["status"] == "completed"
    assert receipt["prospective"] is True
    assert provider.calls == ["research", "cio", "critic"]
    checkpoint = runner.load_records(path, "checkpoints")[0]["value"]
    assert checkpoint["decision_deadline"] == "2026-01-22T02:05:00+00:00"
    assert checkpoint["bundle_as_of"] == NOW.isoformat()
    assert checkpoint["outcome_sessions"][0]["interval_start"] == "2026-01-22T05:00:00+00:00"
    assert checkpoint["inputs_frozen_at"] < checkpoint["decision_deadline"]
    assert checkpoint["arms"]["ai_three_role"]["status"] == "accepted"
    collection = json.loads((path / checkpoint["collection_path"] / "bundle.json").read_text())
    lab_input = json.loads((path / checkpoint["decision_path"] / "input.json").read_text())
    assert lab_input == collection
    assert report["checkpoints"][0]["model_receipts"] == checkpoint["model_receipts"]
    assert report["checkpoints"][0]["usage_status"] == "known"
    assert report["checkpoints"][0]["billed_cost"] == "unknown"
    assert verified["verified"] is True
    assert report["counts"]["checkpoints"] == 1
    assert report["checkpoints"][0]["score"]["counts"]["pending"] == 2


def test_accepted_cio_undersum_uses_residual_cash_without_rewriting_raw_artifact(tmp_path):
    path = register_campaign(tmp_path, request("accepted-undersum"), clock=Clock(NOW - timedelta(hours=1)))
    target_weight = "0.500010000000000000000000000000000001"
    provider = Provider(target_weight=target_weight, cash_weight="0.49995")

    receipt = runner.tick(
        path, MarketTransport(), PublicTransport(), provider, clock=Clock(NOW),
    )
    checkpoint = runner.load_records(path, "checkpoints")[0]["value"]
    decision_path = path / checkpoint["decision_path"] / "decision.json"
    raw_decision = json.loads(decision_path.read_text())

    assert receipt["status"] == "completed"
    assert raw_decision["cio"]["cash_weight"] == "0.49995"
    assert checkpoint["arms"]["ai_three_role"]["targets"] == [
        {"symbol": "SPY", "weight": target_weight},
    ]
    assert checkpoint["arms"]["ai_three_role"]["cash_weight"] == (
        "0.499989999999999999999999999999999999"
    )
    assert runner.report_campaign(path)["checkpoints"][0]["score"]["counts"]["pending"] == 2
    assert runner.verify_campaign(path)["verified"] is True

    observed_at = NOW + timedelta(days=5)

    class MatureMarket(MarketTransport):
        def __init__(self):
            super().__init__()
            for index in range(21, 26):
                day = date(2026, 1, 1) + timedelta(days=index)
                self.bars.append({
                    "t": day.isoformat() + "T05:00:00Z", "o": str(100 + index),
                    "h": str(102 + index), "l": str(99 + index),
                    "c": str(101 + index), "v": 1000 + index,
                })

        def fetch(self, source, query):
            result = super().fetch(source, query)
            result["request_started_at"] = observed_at.isoformat()
            result["received_at"] = observed_at.isoformat()
            return result

    later = runner.tick(
        path, MatureMarket(), Forbidden(), Forbidden(), clock=Clock(observed_at),
    )
    report = runner.report_campaign(path)
    verified = runner.verify_campaign(path)

    assert later["status"] == "waiting"
    assert raw_decision == json.loads(decision_path.read_text())
    first = report["checkpoints"][0]["score"]["horizons"][0]["first_observed"]
    assert first["raw_response"]["arms"][-1] == {
        "arm": "ai_three_role", "arm_status": "accepted", "status": "scored",
        "response": "0.0041323140495867768595041322314049586682",
    }
    assert verified["checkpoints"][0]["score"] == report["checkpoints"][0]["score"]


def test_execute_lab_hook_uses_archived_unchanged_bundle_once_without_provider(tmp_path):
    path = register_campaign(tmp_path, request("executor-hook"), clock=Clock(NOW - timedelta(hours=1)))
    provider = Provider()
    executor = FakeExecutor(provider)

    receipt = runner.tick(
        path, MarketTransport(), PublicTransport(), None,
        clock=Clock(NOW), execute_lab=executor,
    )

    assert receipt["status"] == "completed"
    assert len(executor.calls) == 1
    bundle, collection_path, decision_root = executor.calls[0]
    checkpoint = runner.load_records(path, "checkpoints")[0]["value"]
    assert bundle["run_id"] == checkpoint["collection_id"]
    assert collection_path == path / checkpoint["collection_path"]
    assert decision_root == path / "artifacts" / "decisions"
    assert provider.calls == ["research", "cio", "critic"]


def test_publication_witness_strict_deadline_and_missing_witness_are_unavailable(tmp_path):
    late_path = register_campaign(tmp_path, request("late-witness"), clock=Clock(NOW - timedelta(hours=1)))
    late_clock = SequenceClock(
        NOW, NOW, NOW, NOW, NOW, NOW, NOW,
        NOW + timedelta(seconds=300),
    )
    late = runner.tick(late_path, MarketTransport(), PublicTransport(), Provider(), clock=late_clock)
    late_report = runner.report_campaign(late_path)

    assert late["prospective"] is False
    assert late["completed_at"] == "2026-01-22T02:05:00+00:00"
    assert late_report["checkpoints"][0]["score"]["horizons"][0]["reason"] == "checkpoint_late"

    missing_path = register_campaign(tmp_path, request("missing-witness"), clock=Clock(NOW - timedelta(hours=1)))
    runner.tick(missing_path, MarketTransport(), PublicTransport(), Provider(), clock=Clock(NOW))
    next((missing_path / "ticks").glob("*.json")).unlink()
    missing_report = runner.report_campaign(missing_path)

    assert missing_report["checkpoints"][0]["score"]["horizons"][0]["reason"] == "publication_unconfirmed"


def test_late_input_completion_is_visible_unavailable_and_never_calls_model(tmp_path):
    path = register_campaign(tmp_path, request("late-input"), clock=Clock(NOW - timedelta(hours=1)))
    market, public, provider = MarketTransport(), PublicTransport(), Provider()
    clock = SequenceClock(
        NOW, NOW, NOW, NOW, NOW,
        NOW + timedelta(seconds=300),
        NOW + timedelta(seconds=600),
        NOW + timedelta(seconds=600),
    )

    receipt = runner.tick(path, market, public, provider, clock=clock)
    assert receipt["status"] == "completed", receipt
    checkpoint = runner.load_records(path, "checkpoints")[0]["value"]
    assert checkpoint["bundle_as_of"] <= checkpoint["inputs_frozen_at"]

    assert receipt["status"] == "completed"
    assert receipt["prospective"] is False
    assert provider.calls == []
    assert checkpoint["arms"]["ai_three_role"] == {
        "status": "unavailable", "reason": "inputs_completed_after_deadline",
        "targets": None, "cash_weight": None,
    }
    assert checkpoint["inputs_frozen_at"] == checkpoint["frozen_at"]
    assert runner.report_campaign(path)["checkpoints"][0]["score"]["checkpoint_status"] == "late"


def test_clock_regression_during_and_after_inference_is_not_backdated(tmp_path):
    regressions = 0
    for call in range(5, 16):
        path = register_campaign(tmp_path, request(f"regress-{call}"), clock=Clock(NOW - timedelta(hours=1)))
        clock = SequenceClock(*([NOW] * (call - 1)), NOW - timedelta(seconds=1))
        try:
            runner.tick(path, MarketTransport(), PublicTransport(), Provider(), clock=clock)
        except runner.RunnerError as caught:
            assert str(caught) == "clock_regression"
            regressions += 1
    assert regressions >= 2


def test_crash_after_inference_start_recovers_published_lab_without_model_or_source_calls(tmp_path):
    path = register_campaign(tmp_path, request("recover-fixture"), clock=Clock(NOW - timedelta(hours=1)))
    runner.tick(path, MarketTransport(), PublicTransport(), Provider(), clock=Clock(NOW))
    tick_record = next((path / "ticks").glob("*.json"))
    checkpoint_record = next((path / "checkpoints").glob("*.json"))
    tick_record.unlink()
    checkpoint_record.unlink()

    recovered = runner.tick(path, Forbidden(), Forbidden(), Forbidden(), clock=Clock(NOW))

    assert recovered["status"] == "completed"
    assert recovered["recovered"] is True
    assert runner.load_records(path, "checkpoints")[0]["value"]["arms"]["ai_three_role"]["status"] == "accepted"


def test_recovery_rejects_collection_mismatch_without_retrying_inference(tmp_path):
    path = register_campaign(tmp_path, request("recover-collection-mismatch"), clock=Clock(NOW - timedelta(hours=1)))
    runner.tick(path, MarketTransport(), PublicTransport(), Provider(), clock=Clock(NOW))
    next((path / "ticks").glob("*.json")).unlink()
    next((path / "checkpoints").glob("*.json")).unlink()
    inputs_path = next(path.joinpath("events").glob("inputs-*.json"))
    envelope = json.loads(inputs_path.read_text())
    envelope["value"]["bundle_hash"] = "0" * 64
    canonical = json.dumps(envelope["value"], sort_keys=True, separators=(",", ":")).encode()
    envelope["sha256"] = hashlib.sha256(canonical).hexdigest()
    inputs_path.write_text(json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n")

    recovered = runner.tick(path, Forbidden(), Forbidden(), Forbidden(), clock=Clock(NOW))

    assert recovered["status"] == "in_doubt"
    assert runner.load_records(path, "checkpoints")[0]["value"]["arms"]["ai_three_role"]["status"] == "in_doubt"


def test_recovery_of_already_checkpointed_started_tick_only_finalizes_receipt(tmp_path):
    path = register_campaign(tmp_path, request("recover-checkpoint"), clock=Clock(NOW - timedelta(hours=1)))
    runner.tick(path, MarketTransport(), PublicTransport(), Provider(), clock=Clock(NOW))
    next((path / "ticks").glob("*.json")).unlink()

    recovered = runner.tick(path, Forbidden(), Forbidden(), Forbidden(), clock=Clock(NOW))

    assert recovered["status"] == "completed"
    assert recovered["recovered"] is True
    assert len(runner.load_records(path, "checkpoints")) == 1


def test_started_inference_without_published_lab_becomes_in_doubt_unknown_cost(tmp_path):
    path = register_campaign(tmp_path, request("unknown-charge"), clock=Clock(NOW - timedelta(hours=1)))
    runner.tick(path, MarketTransport(), PublicTransport(), Provider(), clock=Clock(NOW))
    next((path / "ticks").glob("*.json")).unlink()
    next((path / "checkpoints").glob("*.json")).unlink()
    decision = next((path / "artifacts" / "decisions").iterdir())
    for child in decision.iterdir():
        child.unlink()
    decision.rmdir()

    recovered = runner.tick(path, Forbidden(), Forbidden(), Forbidden(), clock=Clock(NOW))
    checkpoint = runner.load_records(path, "checkpoints")[0]["value"]

    assert recovered["status"] == "in_doubt"
    assert recovered["billed_cost"] == "unknown"
    assert checkpoint["arms"]["ai_three_role"]["status"] == "in_doubt"
    assert checkpoint["model_receipts"] == [{"status": "unknown", "billed_cost": "unknown"}]
    assert runner.report_campaign(path)["counts"]["in_doubt"] == 1


def test_in_doubt_checkpoint_requires_unknown_cost_receipt(tmp_path):
    path = register_campaign(tmp_path, request("indoubt-receipt"), clock=Clock(NOW - timedelta(hours=1)))
    runner.tick(path, MarketTransport(), PublicTransport(), Provider(), clock=Clock(NOW))
    next((path / "ticks").glob("*.json")).unlink()
    next((path / "checkpoints").glob("*.json")).unlink()
    decision = next((path / "artifacts" / "decisions").iterdir())
    for child in decision.iterdir():
        child.unlink()
    decision.rmdir()
    runner.tick(path, Forbidden(), Forbidden(), Forbidden(), clock=Clock(NOW))
    record_path = next((path / "checkpoints").glob("*.json"))
    envelope = json.loads(record_path.read_text())
    envelope["value"]["model_receipts"] = []
    canonical = json.dumps(envelope["value"], sort_keys=True, separators=(",", ":")).encode()
    envelope["sha256"] = hashlib.sha256(canonical).hexdigest()
    record_path.write_text(json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n")

    try:
        runner.verify_campaign(path)
    except runner.RunnerError as caught:
        assert str(caught) == "checkpoint_receipt_mismatch"
    else:
        raise AssertionError("in_doubt checkpoint without unknown receipt accepted")


def test_provider_failure_and_model_mismatch_remain_visible_not_cash(tmp_path):
    first = register_campaign(tmp_path, request("provider-failure"), clock=Clock(NOW - timedelta(hours=1)))
    failed_provider = Provider(failure=RuntimeError("synthetic unavailable"))
    failed = runner.tick(first, MarketTransport(), PublicTransport(), failed_provider, clock=Clock(NOW))
    failed_checkpoint = runner.load_records(first, "checkpoints")[0]["value"]

    second = register_campaign(tmp_path, request("model-mismatch"), clock=Clock(NOW - timedelta(hours=1)))
    wrong_provider = Provider(model="wrong-model")
    mismatched = runner.tick(second, MarketTransport(), PublicTransport(), wrong_provider, clock=Clock(NOW))
    mismatch_checkpoint = runner.load_records(second, "checkpoints")[0]["value"]

    assert failed["status"] == mismatched["status"] == "completed"
    assert failed_checkpoint["arms"]["ai_three_role"] == {
        "status": "failed", "reason": "provider_error", "targets": None, "cash_weight": None,
    }
    assert mismatch_checkpoint["arms"]["ai_three_role"] == {
        "status": "failed", "reason": "requested_model_mismatch", "targets": None, "cash_weight": None,
    }
    assert runner.report_campaign(first)["arm_status_counts"]["ai_three_role"] == {"failed": 1}
    assert runner.report_campaign(second)["arm_status_counts"]["ai_three_role"] == {"failed": 1}


def test_failed_observation_is_retained_and_offline_reported_unavailable(tmp_path, monkeypatch):
    path = register_campaign(tmp_path, request("failed-observation"), clock=Clock(NOW - timedelta(hours=1)))
    failed = {
        "schema_version": 1, "symbols": ["SPY"],
        "started_at": NOW.isoformat(), "completed_at": NOW.isoformat(),
        "start_date": "2026-01-12", "end_date": "2026-02-21",
        "policy": {}, "sources": {},
    }
    monkeypatch.setattr(runner, "observe_market", lambda *args, **kwargs: failed)

    receipt = runner.tick(path, Forbidden(), Forbidden(), Forbidden(), clock=Clock(NOW))
    report = runner.report_campaign(path)
    verified = runner.verify_campaign(path)

    assert receipt["status"] == "unavailable"
    assert runner.load_records(path, "observations")[0]["value"] == failed
    assert report["counts"]["unavailable_observations"] == 1
    assert verified["unavailable_observation_count"] == 1


def test_source_and_collection_failures_are_durable_and_never_infer(tmp_path):
    source_path = register_campaign(tmp_path, request("source-failure"), clock=Clock(NOW - timedelta(hours=1)))
    source_receipt = runner.tick(
        source_path, FailingMarketTransport("sessions"), Forbidden(), Forbidden(), clock=Clock(NOW),
    )

    collection_path = register_campaign(tmp_path, request("collection-failure"), clock=Clock(NOW - timedelta(hours=1)))
    incomplete_history = MarketTransport()
    incomplete_history.bars = incomplete_history.bars[:20]
    collection_receipt = runner.tick(
        collection_path, incomplete_history, PublicTransport(), Forbidden(), clock=Clock(NOW),
    )

    assert source_receipt["status"] == "unavailable"
    assert source_receipt["reason"] == "observation_reconstruction_failed"
    assert collection_receipt["status"] == "unavailable"
    assert collection_receipt["reason"] == "collection_failed"
    assert runner.report_campaign(collection_path)["counts"]["collection_failures"] == 1
    failed_root = collection_path / "artifacts" / "collections"
    failed = next(failed_root.iterdir())
    assert json.loads((failed / "manifest.json").read_text())["status"] == "failed"


def test_strict_decision_json_rejects_duplicate_keys_and_invalid_json(tmp_path):
    for campaign_id, target, content in (
        ("duplicate-json", "decision.json", '{"run_id":"x","run_id":"y"}\n'),
        ("invalid-json", "manifest.json", "{not-json}\n"),
        ("nonfinite-json", "manifest.json", '{"value":NaN}\n'),
    ):
        path = register_campaign(tmp_path, request(campaign_id), clock=Clock(NOW - timedelta(hours=1)))
        runner.tick(path, MarketTransport(), PublicTransport(), Provider(), clock=Clock(NOW))
        checkpoint = runner.load_records(path, "checkpoints")[0]["value"]
        artifact = path / checkpoint["decision_path"] / target
        artifact.write_text(content)
        try:
            runner.verify_campaign(path)
        except (runner.RunnerError, ValueError):
            pass
        else:
            raise AssertionError("malformed decision JSON accepted")


def test_decision_and_input_identity_forgery_is_rejected(tmp_path):
    for campaign_id, target, mutate in (
        ("decision-id", "decision.json", lambda value: value.update(run_id="forged")),
        ("input-content", "input.json", lambda value: value["portfolio"].update(snapshot_id="forged")),
    ):
        path = register_campaign(tmp_path, request(campaign_id), clock=Clock(NOW - timedelta(hours=1)))
        runner.tick(path, MarketTransport(), PublicTransport(), Provider(), clock=Clock(NOW))
        checkpoint = runner.load_records(path, "checkpoints")[0]["value"]
        artifact = path / checkpoint["decision_path"] / target
        value = json.loads(artifact.read_text())
        mutate(value)
        artifact.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
        manifest_path = artifact.parent / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"][target] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")
        try:
            runner.verify_campaign(path)
        except (runner.RunnerError, ValueError):
            pass
        else:
            raise AssertionError("forged decision/input identity accepted")


def test_decision_manifest_metadata_forgery_is_rejected_at_campaign_seam(tmp_path):
    mutations = (
        ("extra-field", lambda manifest: manifest.update(forged="value")),
        ("bool-schema", lambda manifest: manifest.update(schema_version=True)),
        ("run-id", lambda manifest: manifest.update(run_id="forged-run")),
        ("directory-id", lambda manifest: None),
        ("mode", lambda manifest: manifest.update(mode="historical_diagnostic")),
        ("code-version", lambda manifest: manifest.update(code_version="forged")),
        ("code-hash", lambda manifest: manifest.update(code_hash="0" * 64)),
        ("prompt-hash", lambda manifest: manifest.update(prompt_hash="0" * 64)),
        ("input-hash", lambda manifest: manifest.update(input_hash="0" * 64)),
        ("input-file-hash", lambda manifest: manifest["files"].update({"input.json": "0" * 64})),
        ("files-shape", lambda manifest: manifest.update(files=[])),
        ("receipts-shape", lambda manifest: manifest.update(model_receipts={})),
    )
    for campaign_id, mutate in mutations:
        path = register_campaign(tmp_path, request("manifest-" + campaign_id), clock=Clock(NOW - timedelta(hours=1)))
        runner.tick(path, MarketTransport(), PublicTransport(), Provider(), clock=Clock(NOW))
        checkpoint = runner.load_records(path, "checkpoints")[0]["value"]
        manifest_path = path / checkpoint["decision_path"] / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        mutate(manifest)
        if campaign_id == "directory-id":
            decision_dir = manifest_path.parent
            forged = decision_dir.with_name("forged-directory")
            decision_dir.rename(forged)
            envelope_path = next((path / "checkpoints").glob("*.json"))
            envelope = json.loads(envelope_path.read_text())
            envelope["value"]["decision_path"] = str(forged.relative_to(path))
            canonical = json.dumps(envelope["value"], sort_keys=True, separators=(",", ":")).encode()
            envelope["sha256"] = hashlib.sha256(canonical).hexdigest()
            envelope_path.write_text(json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n")
            manifest_path = forged / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")

        try:
            runner.verify_campaign(path)
        except (runner.RunnerError, ValueError):
            pass
        else:
            raise AssertionError(f"campaign accepted forged manifest {campaign_id}")


def test_offline_verification_rejects_collection_and_bundle_as_of_mutations(tmp_path):
    for campaign_id, mutate in (
        ("collection-hash-tamper", lambda checkpoint: checkpoint.update(bundle_hash="0" * 64)),
        ("bundle-asof-tamper", lambda checkpoint: checkpoint.update(bundle_as_of="2026-01-22T01:59:00+00:00")),
    ):
        path = register_campaign(tmp_path, request(campaign_id), clock=Clock(NOW - timedelta(hours=1)))
        runner.tick(path, MarketTransport(), PublicTransport(), Provider(), clock=Clock(NOW))
        record_path = next((path / "checkpoints").glob("*.json"))
        envelope = json.loads(record_path.read_text())
        mutate(envelope["value"])
        canonical = json.dumps(envelope["value"], sort_keys=True, separators=(",", ":")).encode()
        envelope["sha256"] = hashlib.sha256(canonical).hexdigest()
        record_path.write_text(json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n")
        try:
            runner.verify_campaign(path)
        except runner.RunnerError as caught:
            assert str(caught) == "checkpoint_collection_mismatch"
        else:
            raise AssertionError("collection mutation accepted")


def test_unsafe_artifact_references_fail_offline_verification(tmp_path):
    for campaign_id, field, value in (
        ("unsafe-collection", "collection_path", "../outside"),
        ("unsafe-decision", "decision_path", "/absolute/outside"),
    ):
        path = register_campaign(tmp_path, request(campaign_id), clock=Clock(NOW - timedelta(hours=1)))
        runner.tick(path, MarketTransport(), PublicTransport(), Provider(), clock=Clock(NOW))
        record_path = next((path / "checkpoints").glob("*.json"))
        envelope = json.loads(record_path.read_text())
        envelope["value"][field] = value
        canonical = json.dumps(envelope["value"], sort_keys=True, separators=(",", ":")).encode()
        envelope["sha256"] = hashlib.sha256(canonical).hexdigest()
        record_path.write_text(json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n")
        try:
            runner.verify_campaign(path)
        except (runner.RunnerError, ValueError):
            pass
        else:
            raise AssertionError("unsafe artifact reference accepted")


def test_offline_verification_rejects_decision_input_divergence(tmp_path):
    path = register_campaign(tmp_path, request("decision-input-tamper"), clock=Clock(NOW - timedelta(hours=1)))
    runner.tick(path, MarketTransport(), PublicTransport(), Provider(), clock=Clock(NOW))
    checkpoint = runner.load_records(path, "checkpoints")[0]["value"]
    input_path = path / checkpoint["decision_path"] / "input.json"
    manifest_path = input_path.parent / "manifest.json"
    value = json.loads(input_path.read_text())
    value["portfolio"]["snapshot_id"] = "different-valid-input"
    input_path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["input.json"] = hashlib.sha256(input_path.read_bytes()).hexdigest()
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    manifest["input_hash"] = hashlib.sha256(canonical).hexdigest()
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n")

    try:
        runner.verify_campaign(path)
    except (runner.RunnerError, ValueError):
        pass
    else:
        raise AssertionError("offline verification accepted divergent lab input")


def test_missing_decision_reference_with_accepted_arm_is_rejected(tmp_path):
    path = register_campaign(tmp_path, request("missing-decision"), clock=Clock(NOW - timedelta(hours=1)))
    runner.tick(path, MarketTransport(), PublicTransport(), Provider(), clock=Clock(NOW))
    record_path = next((path / "checkpoints").glob("*.json"))
    envelope = json.loads(record_path.read_text())
    envelope["value"]["decision_path"] = None
    canonical = json.dumps(envelope["value"], sort_keys=True, separators=(",", ":")).encode()
    envelope["sha256"] = hashlib.sha256(canonical).hexdigest()
    record_path.write_text(json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n")

    try:
        runner.verify_campaign(path)
    except runner.RunnerError as caught:
        assert str(caught) == "checkpoint_decision_reference_missing"
    else:
        raise AssertionError("accepted arm without decision accepted")


def test_offline_verification_rejects_ai_and_receipt_mutations(tmp_path):
    for campaign_id, mutate, expected in (
        ("ai-tamper", lambda checkpoint: checkpoint["arms"]["ai_three_role"].update(cash_weight="1"), "checkpoint_decision_mismatch"),
        ("receipt-tamper", lambda checkpoint: checkpoint.update(model_receipts=[]), "checkpoint_receipt_mismatch"),
    ):
        path = register_campaign(tmp_path, request(campaign_id), clock=Clock(NOW - timedelta(hours=1)))
        runner.tick(path, MarketTransport(), PublicTransport(), Provider(), clock=Clock(NOW))
        record_path = next((path / "checkpoints").glob("*.json"))
        envelope = json.loads(record_path.read_text())
        mutate(envelope["value"])
        canonical = json.dumps(envelope["value"], sort_keys=True, separators=(",", ":")).encode()
        envelope["sha256"] = hashlib.sha256(canonical).hexdigest()
        record_path.write_text(json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n")
        try:
            runner.verify_campaign(path)
        except runner.RunnerError as caught:
            assert str(caught) == expected
        else:
            raise AssertionError("semantic mutation accepted")


def test_offline_report_rejects_recorded_control_mutation(tmp_path):
    path = register_campaign(tmp_path, request("tamper-fixture"), clock=Clock(NOW - timedelta(hours=1)))
    runner.tick(path, MarketTransport(), PublicTransport(), Provider(), clock=Clock(NOW))
    record_path = next((path / "checkpoints").glob("*.json"))
    envelope = json.loads(record_path.read_text())
    envelope["value"]["arms"]["cash"]["cash_weight"] = "0"
    canonical = json.dumps(
        envelope["value"], sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode()
    envelope["sha256"] = hashlib.sha256(canonical).hexdigest()
    record_path.write_text(json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n")

    try:
        runner.verify_campaign(path)
    except runner.RunnerError as caught:
        assert str(caught) == "checkpoint_control_mismatch"
    else:
        raise AssertionError("offline semantic verification trusted mutated controls")


def test_report_and_verify_are_offline_deterministic_for_empty_campaign(tmp_path):
    path = register_campaign(tmp_path, request(), clock=Clock(NOW))

    first = runner.report_campaign(path)
    second = runner.report_campaign(path)
    verified = runner.verify_campaign(path)

    assert first == second
    assert first["counts"] == {
        "observations": 0, "valid_observations": 0, "unavailable_observations": 0,
        "checkpoints": 0, "reservations": 0,
        "missed": 0, "in_doubt": 0, "collection_failures": 0,
    }
    assert verified["verified"] is True
    assert verified["verification_kind"] == "offline_semantic_reconstruction"
    assert first["executable"] is False
