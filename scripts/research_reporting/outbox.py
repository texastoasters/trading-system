"""Durable, deduplicated notification intents outside campaign records.

The public interface is :func:`cycle`.  It validates one policy assessment,
serializes access to one campaign's private ledger, advances durable intents,
and invokes the supplied external-I/O sender.  Its result contains only safe
aggregate metadata: ``status``, ``executable``, notice counts, and the number
submitted during this call.  It never returns message text or provider IDs.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import stat
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, NoReturn
from zoneinfo import ZoneInfo

_LOCAL = ZoneInfo("America/Chicago")
_CONDITIONS = frozenset({
    "timer_disabled", "service_failed", "service_overlong", "stale_tick",
    "verification_unavailable",
})
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9+:._/-]{0,255}\Z")
_MAX_TEXT = 2_000
_MAX_FINDINGS = 16_384  # 90 days of 900-second ticks plus cohort events.
_MAX_FRESH_CLAIMS = 100
_MAX_SUBMISSIONS = 2  # Two 60-second sends fit the 270-second service budget.
_MAX_NOTICES = 1_024
_MAX_CLAIMS = 16_384
_MAX_LEDGER = 4_194_304


class OutboxError(Exception):
    """Sanitized failure raised by the public outbox interface."""


def _fail(category: str = "invalid_outbox_input") -> NoReturn:
    raise OutboxError(category)


def _plain_text(value: Any, *, maximum: int = _MAX_TEXT) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        _fail()
    if any(ord(character) < 32 and character not in "\n\t" for character in value):
        _fail()
    return value


def _identifier(value: Any, *, maximum: int = 256) -> str:
    if (not isinstance(value, str) or not value or len(value) > maximum
            or any(ord(character) < 32 for character in value)):
        _fail()
    return value


def _validate_inputs(state_dir: Any, campaign_id: Any, assessment: Any,
                     sender: Any, now: Any) -> tuple[Path, str, dict[str, Any]]:
    if isinstance(state_dir, bytes):
        _fail()
    try:
        root = Path(state_dir)
    except (TypeError, ValueError):
        _fail()
    if "\x00" in str(root):
        _fail()
    _identifier(campaign_id, maximum=128)
    if not callable(sender):
        _fail()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        _fail()
    if not isinstance(assessment, dict) or set(assessment) != {
        "summary", "event_findings", "conditions", "indeterminate_conditions", "busy",
    }:
        _fail()
    if type(assessment["busy"]) is not bool:
        _fail()
    summary = assessment["summary"]
    if summary is not None:
        _plain_text(summary)
    findings = assessment["event_findings"]
    if not isinstance(findings, list) or len(findings) > _MAX_FINDINGS:
        _fail()
    seen: set[str] = set()
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != {"key", "text"}:
            _fail()
        key = finding["key"]
        if not isinstance(key, str) or not _KEY.fullmatch(key) or key in seen:
            _fail()
        seen.add(key)
        _plain_text(finding["text"], maximum=500)
    conditions = assessment["conditions"]
    if not isinstance(conditions, dict) or not set(conditions).issubset(_CONDITIONS):
        _fail()
    for text in conditions.values():
        _plain_text(text, maximum=500)
    indeterminate = assessment["indeterminate_conditions"]
    if (not isinstance(indeterminate, list)
            or any(not isinstance(name, str) or name not in _CONDITIONS for name in indeterminate)
            or len(set(indeterminate)) != len(indeterminate)
            or set(indeterminate) & set(conditions)):
        _fail()
    return root, campaign_id, assessment


def _secure_root(root: Path) -> None:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if (stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()):
        _fail("unsafe_outbox_storage")
    root.chmod(0o700)


def _open_regular(path: Path, flags: int, mode: int = 0o600) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags | nofollow, mode)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        os.close(descriptor)
        _fail("unsafe_outbox_storage")
    os.fchmod(descriptor, 0o600)
    return descriptor


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _fail("invalid_outbox_state")
        value[key] = item
    return value


def _new_state(campaign_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1, "campaign_id": campaign_id, "last_observed_at": None,
        "notices": {}, "claimed_events": {}, "conditions": {},
    }


def _load(path: Path, campaign_id: str) -> dict[str, Any]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return _new_state(campaign_id)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_LEDGER:
        _fail("unsafe_outbox_storage")
    descriptor = _open_regular(path, os.O_RDONLY)
    try:
        raw = os.read(descriptor, _MAX_LEDGER + 1)
    finally:
        os.close(descriptor)
    try:
        state = json.loads(raw.decode("utf-8"), object_pairs_hook=_object_no_duplicates)
    except (UnicodeError, json.JSONDecodeError):
        _fail("invalid_outbox_state")
    try:
        _validate_state(state, campaign_id)
    except OutboxError as error:
        if str(error) == "campaign_identity_mismatch":
            raise
        _fail("invalid_outbox_state")
    return state


def _validate_state(state: Any, campaign_id: str) -> None:
    if (not isinstance(state, dict) or set(state) != {
            "schema_version", "campaign_id", "last_observed_at", "notices",
            "claimed_events", "conditions"}
            or state["schema_version"] != 1 or type(state["schema_version"]) is not int):
        _fail("invalid_outbox_state")
    if not isinstance(state["campaign_id"], str):
        _fail("invalid_outbox_state")
    if state["campaign_id"] != campaign_id:
        _fail("campaign_identity_mismatch")
    last = state["last_observed_at"]
    if last is not None:
        _parse_time(last)
    notices = state["notices"]
    claims = state["claimed_events"]
    conditions = state["conditions"]
    if (not isinstance(notices, dict) or len(notices) > _MAX_NOTICES
            or not isinstance(claims, dict) or len(claims) > _MAX_CLAIMS
            or not isinstance(conditions, dict) or not set(conditions).issubset(_CONDITIONS)):
        _fail("invalid_outbox_state")
    for key, claimed_at in claims.items():
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            _fail("invalid_outbox_state")
        _parse_time(claimed_at)
    for name, value in conditions.items():
        if type(value) is not bool:
            _fail("invalid_outbox_state")
    for key, notice in notices.items():
        if not isinstance(key, str) or not _KEY.fullmatch(key) or not isinstance(notice, dict):
            _fail("invalid_outbox_state")
        required = {"kind", "status", "created_at", "local_date", "text", "provider_id"}
        if (set(notice) != required or not isinstance(notice["kind"], str)
                or notice["kind"] not in {"daily", "alert", "recovery"}):
            _fail("invalid_outbox_state")
        if (not isinstance(notice["status"], str)
                or notice["status"] not in {
                    "pending", "started", "accepted", "in_doubt", "expired"}):
            _fail("invalid_outbox_state")
        _parse_time(notice["created_at"])
        try:
            date.fromisoformat(notice["local_date"])
        except (TypeError, ValueError):
            _fail("invalid_outbox_state")
        _plain_text(notice["text"])
        provider_id = notice["provider_id"]
        if provider_id is not None:
            try:
                _identifier(provider_id)
            except OutboxError:
                _fail("invalid_outbox_state")


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        _fail("invalid_outbox_state")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _fail("invalid_outbox_state")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail("invalid_outbox_state")
    return parsed


def _publish(path: Path, state: dict[str, Any]) -> None:
    encoded = json.dumps(state, sort_keys=True, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    if len(encoded) > _MAX_LEDGER:
        _fail("outbox_state_limit")
    temporary = path.with_name(f".ledger.{os.getpid()}.{os.urandom(8).hex()}.pending")
    try:
        descriptor = _open_regular(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _observation_prefix(kind: str, now: datetime) -> str:
    utc = now.astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds").replace("+00:00", "Z")
    local = now.astimezone(_LOCAL).isoformat(timespec="seconds")
    return f"Research {kind} (observed at {utc} / {local} America/Chicago):"


def _messages(prefix: str, texts: list[str]) -> list[str]:
    messages: list[str] = []
    current = prefix
    for text in texts:
        line = f"\n- {text}"
        if len(current) + len(line) > _MAX_TEXT:
            messages.append(current)
            current = prefix
        current += line
    if current != prefix:
        messages.append(current)
    return messages


def _add_notice(state: dict[str, Any], key: str, kind: str, text: str,
                now: datetime, local_date: str) -> None:
    if len(state["notices"]) >= _MAX_NOTICES:
        _fail("outbox_state_limit")
    state["notices"][key] = {
        "kind": kind, "status": "pending", "created_at": now.isoformat(),
        "local_date": local_date, "text": text, "provider_id": None,
    }


def _prepare(state: dict[str, Any], assessment: dict[str, Any], now: datetime,
             local_date: str, path: Path) -> None:
    changed = False
    for notice in state["notices"].values():
        if notice["status"] == "started":
            notice["status"] = "in_doubt"
            changed = True
        elif (notice["kind"] == "daily" and notice["status"] == "pending"
              and notice["local_date"] != local_date):
            notice["status"] = "expired"
            changed = True

    active = assessment["conditions"]
    indeterminate = set(assessment["indeterminate_conditions"])
    raised: list[str] = []
    recovered: list[str] = []
    for name in sorted(_CONDITIONS):
        previous = state["conditions"].get(name, False)
        if name in active:
            if not previous:
                raised.append(active[name])
            if not previous:
                changed = True
            state["conditions"][name] = True
        elif name not in indeterminate:
            if previous:
                recovered.append(name.replace("_", " ") + " cleared.")
                changed = True
            state["conditions"][name] = False
    stamp = now.isoformat()
    sequence = len(state["notices"])
    for kind, prefix, texts in (
        ("alert", _observation_prefix("alert", now), raised),
        ("recovery", _observation_prefix("recovery", now), recovered),
    ):
        for text in _messages(prefix, texts):
            _add_notice(state, f"notice:{stamp}:{kind}:{sequence}", kind,
                        text, now, local_date)
            changed = True
            sequence += 1

    if not assessment["busy"]:
        fresh = [item for item in assessment["event_findings"]
                 if item["key"] not in state["claimed_events"]][:_MAX_FRESH_CLAIMS]
        if fresh:
            if len(state["claimed_events"]) + len(fresh) > _MAX_CLAIMS:
                _fail("outbox_state_limit")
            for item in fresh:
                state["claimed_events"][item["key"]] = stamp
            for text in _messages(_observation_prefix("alert", now),
                                  [item["text"] for item in fresh]):
                _add_notice(state, f"notice:{stamp}:alert:{sequence}", "alert",
                            text, now, local_date)
                sequence += 1
            changed = True
    if changed:
        _publish(path, state)


def _submit(path: Path, state: dict[str, Any], sender: Callable[[str], Any],
            local_date: str) -> int:
    submitted = 0
    for notice in state["notices"].values():
        if submitted == _MAX_SUBMISSIONS:
            break
        if notice["status"] != "pending":
            continue
        notice["status"] = "started"
        _publish(path, state)
        submitted += 1
        try:
            response = sender(notice["text"])
        except Exception:
            notice["status"] = "in_doubt"
            _publish(path, state)
            continue
        if (isinstance(response, dict) and set(response) == {"provider_id", "verified"}
                and response["verified"] is True and type(response["verified"]) is bool
                and isinstance(response["provider_id"], str)
                and response["provider_id"]
                and len(response["provider_id"]) <= 256
                and not any(ord(character) < 32 for character in response["provider_id"])):
            notice["status"] = "accepted"
            notice["provider_id"] = response["provider_id"]
        else:
            notice["status"] = "in_doubt"
            provider_id = response.get("provider_id") if isinstance(response, dict) else None
            if (isinstance(provider_id, str) and provider_id
                    and len(provider_id) <= 256
                    and not any(ord(character) < 32 for character in provider_id)):
                notice["provider_id"] = provider_id
        _publish(path, state)
    return submitted


def _cycle(state_dir: Any, campaign_id: Any, assessment: Any,
           sender: Callable[[str], Any], *, now: datetime) -> dict[str, Any]:
    """Advance one durable outbox cycle and return sanitized aggregate metadata.

    ``sender(text)`` is the only external-I/O seam.  Acceptance requires exactly
    ``{"provider_id": <nonempty safe str>, "verified": True}``; every other
    return or ordinary exception is recorded in-doubt.  A process-ending
    ``BaseException`` deliberately escapes after durable send-start, so the next
    cycle can expose uncertainty without blindly resending.
    """
    root, campaign_id, assessment = _validate_inputs(
        state_dir, campaign_id, assessment, sender, now)
    _secure_root(root)
    lock_path = root / "outbox.lock"
    if lock_path.is_symlink():
        _fail("unsafe_outbox_storage")
    lock = _open_regular(lock_path, os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = root / "ledger.json"
        state = _load(path, campaign_id)
        previous = state["last_observed_at"]
        if previous is not None and now < _parse_time(previous):
            _fail("clock_regression")
        state["last_observed_at"] = now.isoformat()
        local = now.astimezone(_LOCAL)
        local_date = local.date().isoformat()
        _publish(path, state)
        _prepare(state, assessment, now, local_date, path)
        daily_key = f"daily:{local_date}"
        if (not assessment["busy"] and assessment["summary"] is not None
                and (local.hour, local.minute) >= (19, 30)
                and daily_key not in state["notices"]):
            _add_notice(state, daily_key, "daily", assessment["summary"], now, local_date)
            _publish(path, state)
        submitted = _submit(path, state, sender, local_date)
        notices = list(state["notices"].values())
        return {
            "status": "processed", "executable": False,
            "accepted_count": sum(item["status"] == "accepted" for item in notices),
            "in_doubt_count": sum(item["status"] == "in_doubt" for item in notices),
            "pending_count": sum(item["status"] == "pending" for item in notices),
            "expired_count": sum(item["status"] == "expired" for item in notices),
            "submitted_this_cycle": submitted,
        }
    finally:
        os.close(lock)


def cycle(state_dir: Any, campaign_id: Any, assessment: Any,
          sender: Callable[[str], Any], *, now: datetime) -> dict[str, Any]:
    """Run one cycle, normalizing local storage failures to a public category."""
    try:
        return _cycle(state_dir, campaign_id, assessment, sender, now=now)
    except OutboxError:
        raise
    except OSError:
        _fail("outbox_storage_unavailable")
