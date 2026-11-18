"""Isolated subprocess observer for one frozen campaign snapshot."""
from __future__ import annotations

import json
import resource
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, NoReturn

_MAX_STDOUT = 16 * 1024 * 1024
_TIMEOUT = 90
_KEYS = {
    "schema_version", "status", "observed_at", "campaign_id",
    "registration_hash", "registered_at", "report", "verification",
}


class ObservationError(RuntimeError):
    """Sanitized inability to obtain a trustworthy frozen snapshot."""


def _fail() -> NoReturn:
    raise ObservationError("observation_unavailable") from None


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(value)


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _strict_json(raw: bytes) -> Any:
    return json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_object_without_duplicates,
        parse_constant=_reject_constant,
    )


def _aware_time(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _validate_result(value: Any, campaign_id: str, registration_hash: str) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value) != _KEYS
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["status"] not in {"verified", "busy", "unavailable"}
        or value["campaign_id"] != campaign_id
        or value["registration_hash"] != registration_hash
        or not _aware_time(value["observed_at"])
    ):
        _fail()
    registered_at = value["registered_at"]
    if registered_at is not None and not _aware_time(registered_at):
        _fail()
    if value["status"] != "verified":
        if registered_at is not None or value["report"] is not None or value["verification"] is not None:
            _fail()
        return value
    report = value["report"]
    verification = value["verification"]
    if (
        registered_at is None
        or not isinstance(report, dict)
        or not isinstance(verification, dict)
        or report.get("campaign_id") != campaign_id
        or report.get("verified") is not True
        or report.get("executable") is not False
        or verification.get("campaign_id") != campaign_id
        or verification.get("registration_hash") != registration_hash
        or verification.get("verified") is not True
        or verification.get("executable") is not False
    ):
        _fail()
    return value


def _run_bounded(command: list[str], *, timeout: int, stdout_limit: int) -> Any:
    with tempfile.TemporaryFile() as output:
        soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        cap = stdout_limit if hard == resource.RLIM_INFINITY else min(stdout_limit, hard)
        response = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
            preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_FSIZE, (cap, cap)),
        )
        output.seek(0)
        return SimpleProcessResult(response.returncode, output.read(stdout_limit + 1))


class SimpleProcessResult:
    """Minimal result shape shared by the real and injected runner seams."""

    def __init__(self, returncode: int, stdout: bytes):
        self.returncode = returncode
        self.stdout = stdout


class SnapshotObserver:
    """Run the selected frozen reader in an isolated project interpreter."""

    def __init__(self, config: Any, *, runner: Callable[..., Any] | None = None):
        self.config = config
        self.runner = _run_bounded if runner is None else runner

    def __call__(self) -> dict[str, Any]:
        try:
            config = self.config
            required = {
                "schema_version", "campaign_id", "registration_hash", "campaign_path",
                "frozen_scripts_path", "project_python", "hermes_home", "state_dir",
            }
            if not isinstance(config, dict) or set(config) != required:
                _fail()
            command = [
                config["project_python"],
                "-I",
                str(Path(__file__).with_name("read_snapshot.py")),
                config["frozen_scripts_path"],
                config["campaign_path"],
                config["campaign_id"],
                config["registration_hash"],
            ]
            response = self.runner(command, timeout=_TIMEOUT, stdout_limit=_MAX_STDOUT)
            if (
                type(response.returncode) is not int
                or not isinstance(response.stdout, bytes)
                or len(response.stdout) > _MAX_STDOUT
            ):
                _fail()
            value = _strict_json(response.stdout)
            status = value.get("status") if isinstance(value, dict) else None
            expected_code = 0 if status in {"verified", "busy"} else 1 if status == "unavailable" else None
            if response.returncode != expected_code:
                _fail()
            return _validate_result(value, config["campaign_id"], config["registration_hash"])
        except ObservationError:
            raise
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                raise
            _fail()
