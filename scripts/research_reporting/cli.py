"""Deterministic reporter configuration and CLI glue."""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, NoReturn, Sequence

from research_reporting import delivery, outbox, policy, runtime
from research_reporting.observer import ObservationError, SnapshotObserver

_CONFIG_KEYS = {
    "schema_version", "campaign_id", "registration_hash", "campaign_path",
    "frozen_scripts_path", "project_python", "hermes_home", "state_dir",
}
_ID = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,63})\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_MAX_CONFIG = 64 * 1024
_FALLBACK_TEXT = "Reporting verification is unavailable; evaluator state was not inferred."
_OTHER_CONDITIONS = ["timer_disabled", "service_failed", "service_overlong", "stale_tick"]


class ConfigError(ValueError):
    """Sanitized invalid reporter configuration."""


def _config_fail() -> NoReturn:
    raise ConfigError("invalid_configuration") from None


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(value)


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _read_config(path_value: Any) -> dict[str, Any]:
    path = Path(path_value)
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_uid != os.getuid()
                or opened.st_size > _MAX_CONFIG
                or stat.S_IMODE(opened.st_mode) & 0o077
            ):
                _config_fail()
            raw = os.read(descriptor, _MAX_CONFIG + 1)
        finally:
            os.close(descriptor)
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except ConfigError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        _config_fail()
    try:
        return _validate_config(value)
    except ConfigError:
        raise
    except OSError:
        _config_fail()


def _absolute(value: Any) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        _config_fail()
    path = Path(value)
    if not path.is_absolute():
        _config_fail()
    return path


def _contains(parent: Path, child: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _validate_config(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != _CONFIG_KEYS
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or not isinstance(value["campaign_id"], str)
        or _ID.fullmatch(value["campaign_id"]) is None
        or value["campaign_id"].endswith((".", " "))
        or value["campaign_id"].split(".", 1)[0].upper() in _WINDOWS_RESERVED
        or not isinstance(value["registration_hash"], str)
        or _HASH.fullmatch(value["registration_hash"]) is None
    ):
        _config_fail()
    campaign = _absolute(value["campaign_path"])
    scripts = _absolute(value["frozen_scripts_path"])
    python = _absolute(value["project_python"])
    home = _absolute(value["hermes_home"])
    state = _absolute(value["state_dir"])
    campaign_resolved = campaign.resolve(strict=True)
    scripts_resolved = scripts.resolve(strict=True)
    home_resolved = home.resolve(strict=True)
    python_resolved = python.resolve(strict=True)
    state_resolved = state.resolve(strict=False)
    if (
        not campaign_resolved.is_dir()
        or not scripts_resolved.is_dir()
        or scripts_resolved.name != "scripts"
        or not (scripts_resolved / "prospective_evaluation" / "__init__.py").is_file()
        or not (scripts_resolved / "prospective_evaluation" / "runner.py").is_file()
        or not (scripts_resolved / "prospective_evaluation" / "storage.py").is_file()
        or not home_resolved.is_dir()
        or not python_resolved.is_file()
        or _contains(campaign_resolved, state_resolved)
        or _contains(state_resolved, campaign_resolved)
    ):
        _config_fail()
    if state.exists():
        state_info = state.lstat()
        if stat.S_ISLNK(state_info.st_mode) or not stat.S_ISDIR(state_info.st_mode):
            _config_fail()
    return {**value, "frozen_scripts_path": str(scripts_resolved)}


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False, exit_on_error=False)
    parser.add_argument("--config", required=True)
    parser.add_argument("--preview", action="store_true")
    try:
        supplied = list(sys.argv[1:] if argv is None else argv)
        if any(not isinstance(item, str) for item in supplied):
            _config_fail()
        arguments, extras = parser.parse_known_args(
            supplied,
        )
    except (argparse.ArgumentError, SystemExit, TypeError):
        _config_fail()
    if extras:
        _config_fail()
    return arguments


def _fallback_assessment() -> dict[str, Any]:
    return {
        "summary": None,
        "event_findings": [],
        "conditions": {"verification_unavailable": _FALLBACK_TEXT},
        "indeterminate_conditions": list(_OTHER_CONDITIONS),
        "busy": False,
    }


def _unavailable_snapshot(config: dict[str, Any], now: datetime) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "unavailable",
        "observed_at": now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "campaign_id": config["campaign_id"],
        "registration_hash": config["registration_hash"],
        "registered_at": None,
        "report": None,
        "verification": None,
    }


def _emit(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")


def _safe_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status", "reporting_unavailable"),
        "accepted_count": result.get("accepted_count", 0),
        "in_doubt_count": result.get("in_doubt_count", 0),
        "submitted_this_cycle": result.get("submitted_this_cycle", 0),
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
    observer: Callable[[], dict[str, Any]] | None = None,
    health_observer: Callable[[], dict[str, Any]] | None = None,
    sender: Callable[[str], dict[str, Any]] | None = None,
) -> int:
    """Observe, assess, and preview or durably notify through public interfaces."""
    try:
        arguments = _parse(argv)
        config = _read_config(arguments.config)
    except ConfigError:
        _emit({"status": "invalid_configuration"})
        return 2

    observe = SnapshotObserver(config) if observer is None else observer
    observe_health = runtime.collect_health if health_observer is None else health_observer
    current_clock = (lambda: datetime.now(timezone.utc)) if clock is None else clock
    selected_sender = delivery.PhotonSender(config["hermes_home"]) if sender is None else sender

    observed: dict[str, Any] | None = None
    checked_health: dict[str, Any] | None = None
    observation_failed = False
    health_failed = False
    try:
        observed = observe()
    except Exception:
        observation_failed = True
    try:
        checked_health = observe_health()
    except Exception:
        health_failed = True
    try:
        now = current_clock()
    except Exception:
        now = None

    assessment = _fallback_assessment()
    if (
        isinstance(now, datetime)
        and now.tzinfo is not None
        and now.utcoffset() is not None
        and not health_failed
        and checked_health is not None
    ):
        if observation_failed:
            observed = _unavailable_snapshot(config, now)
        try:
            assessment = policy.assess(observed, checked_health, now=now)
        except policy.ReportingError:
            assessment = _fallback_assessment()
    else:
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            now = datetime.now(timezone.utc)

    if arguments.preview:
        _emit({
            "status": "preview",
            "summary": assessment["summary"],
            "conditions": assessment["conditions"],
            "indeterminate_conditions": assessment["indeterminate_conditions"],
            "busy": assessment["busy"],
        })
        return 0 if assessment["busy"] or not assessment["conditions"] else 1

    try:
        result = outbox.cycle(
            config["state_dir"], config["campaign_id"], assessment,
            selected_sender, now=now,
        )
    except (outbox.OutboxError, policy.ReportingError, ObservationError, runtime.HealthError):
        _emit({"status": "reporting_unavailable"})
        return 1
    safe = _safe_result(result)
    _emit(safe)
    return 1 if assessment["conditions"] or safe["in_doubt_count"] > 0 else 0
