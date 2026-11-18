"""Read an exact offline snapshot from one explicitly selected frozen evaluator.

The public ``collect`` seam is importable by the reporter. Its ``source_root``
is the explicit frozen checkout's ``scripts`` directory. ``main`` is the
standalone CLI seam; production launches this file with the pinned project
interpreter and ``-I``. CLI JSON is capped at 16 MiB.
"""
from __future__ import annotations

import contextlib
import importlib
import json
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


class SnapshotError(ValueError):
    """Stable public error for malformed snapshot caller configuration."""


_ID = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,63})\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_FROZEN_PACKAGES = ("prospective_evaluation", "prospective_evidence", "decision_lab")
_FROZEN_MODULES = (
    "ai_decision_lab",
    "ai_decision_lab_hermes",
    "collect_prospective_evidence",
    "evaluate_prospective",
    "evaluate_prospective_hermes",
)
_MAX_OUTPUT_BYTES = 16 * 1024 * 1024


def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SnapshotError("invalid snapshot configuration")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and _ID.fullmatch(value) is not None and not value.endswith((".", " "))


def _base_snapshot(
    status: str,
    observed_at: str,
    campaign_id: str,
    registration_hash: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": status,
        "observed_at": observed_at,
        "campaign_id": campaign_id,
        "registration_hash": registration_hash,
        "registered_at": None,
        "report": None,
        "verification": None,
    }


def _frozen_name(name: str) -> bool:
    return any(
        name == module or name.startswith(module + ".") for module in _FROZEN_MODULES
    ) or any(
        name == package or name.startswith(package + ".") for package in _FROZEN_PACKAGES
    )


def _valid_origin(module: Any, source_root: Path) -> bool:
    origin = getattr(module, "__file__", None)
    return (
        isinstance(origin, str)
        and Path(origin).resolve().is_relative_to(source_root)
    )


def _require_source(condition: bool) -> None:
    if not condition:
        raise RuntimeError("selected source unavailable")


@contextmanager
def _selected_public_api(source_root: Path) -> Any:
    source_files = [
        path
        for package in _FROZEN_PACKAGES
        for path in (source_root / package).rglob("*.py")
        if not path.name.startswith("test")
    ]
    source_files.extend(
        source_root / f"{module}.py" for module in _FROZEN_MODULES
    )
    _require_source(bool(source_files) and all(
        path.is_file() and path.resolve().is_relative_to(source_root)
        for path in source_files
    ))

    original_path = list(sys.path)
    prior = {
        name: module
        for name, module in tuple(sys.modules.items())
        if _frozen_name(name)
    }
    for name in prior:
        del sys.modules[name]
    try:
        sys.path[:] = [str(source_root), *[item for item in sys.path if item != str(source_root)]]
        package = importlib.import_module("prospective_evaluation")
        runner = importlib.import_module("prospective_evaluation.runner")
        importlib.import_module("prospective_evaluation.storage")
        for module in _FROZEN_MODULES:
            importlib.import_module(module)
        loaded = {
            name: module for name, module in tuple(sys.modules.items()) if _frozen_name(name)
        }
        expected_roots = {*_FROZEN_PACKAGES, *_FROZEN_MODULES}
        loaded_roots = {name.split(".", 1)[0] for name in loaded}
        _require_source(
            expected_roots <= loaded_roots
            and all(_valid_origin(module, source_root) for module in loaded.values())
        )
        yield (
            package.CampaignBusy,
            package.campaign_lock,
            package.load_campaign,
            runner.verify_campaign,
            runner.report_campaign,
        )
    finally:
        sys.path[:] = original_path
        for name in tuple(sys.modules):
            if _frozen_name(name):
                del sys.modules[name]
        sys.modules.update(prior)


def collect(
    source_root: str | Path,
    campaign: str | Path,
    expected_campaign_id: str,
    expected_registration_hash: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return one sanitized, read-only snapshot from the selected evaluator."""
    try:
        root = Path(source_root)
        campaign_path = Path(campaign)
    except TypeError as exc:
        raise SnapshotError("invalid snapshot configuration") from exc
    if (
        not root.is_dir()
        or not _valid_id(expected_campaign_id)
        or not isinstance(expected_registration_hash, str)
        or _HASH.fullmatch(expected_registration_hash) is None
    ):
        raise SnapshotError("invalid snapshot configuration")
    if now is not None:
        _utc_text(now)

    def observed_snapshot(status: str) -> dict[str, Any]:
        observed_at = _utc_text(now if now is not None else datetime.now(timezone.utc))
        return _base_snapshot(
            status, observed_at, expected_campaign_id, expected_registration_hash,
        )

    CampaignBusy: Any = None
    with open(os.devnull, "w") as sink:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            try:
                with _selected_public_api(root.resolve()) as public_api:
                    CampaignBusy, campaign_lock, load_campaign, verify_campaign, report_campaign = (
                        public_api
                    )
                    with campaign_lock(campaign_path):
                        registration = load_campaign(campaign_path)
                        verification = verify_campaign(campaign_path)
                        report = report_campaign(campaign_path)
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                    raise
                if CampaignBusy is not None and isinstance(exc, CampaignBusy):
                    return observed_snapshot("busy")
                return observed_snapshot("unavailable")

    registered_at = registration.get("registered_at") if isinstance(registration, dict) else None
    valid = (
        isinstance(registered_at, str)
        and isinstance(verification, dict)
        and isinstance(report, dict)
        and verification.get("campaign_id") == expected_campaign_id
        and verification.get("registration_hash") == expected_registration_hash
        and verification.get("verified") is True
        and verification.get("executable") is False
        and report.get("campaign_id") == expected_campaign_id
        and report.get("verified") is True
        and report.get("executable") is False
    )
    if not valid:
        return observed_snapshot("unavailable")
    return {
        **observed_snapshot("verified"),
        "registered_at": registered_at,
        "report": report,
        "verification": verification,
    }


def main(argv: Sequence[str] | None = None, *, now: datetime | None = None) -> int:
    """CLI seam: print one bounded JSON result and return a safe status code."""
    try:
        arguments = list(sys.argv[1:] if argv is None else argv)
        if len(arguments) != 4:
            raise SnapshotError("invalid snapshot configuration")
        result = collect(
            arguments[0],
            arguments[1],
            arguments[2],
            arguments[3],
            now=now,
        )
    except SnapshotError:
        result = {
            "schema_version": 1,
            "status": "unavailable",
            "error": "invalid_configuration",
        }
        code = 2
    else:
        code = 0 if result["status"] in {"verified", "busy"} else 1
    output = json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
    if len(output.encode("utf-8")) > _MAX_OUTPUT_BYTES:
        result = {
            "schema_version": 1,
            "status": "unavailable",
            "error": "output_too_large",
        }
        output = json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
        code = 1
    sys.stdout.write(output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
