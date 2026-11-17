"""Bounded execution of the pure decision-lab CLI in Hermes' managed runtime."""
from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any, Callable

from ai_decision_lab_hermes import managed_command
from decision_lab import replay_run
from decision_lab.storage import hash_value

MAX_JSON_BYTES = 1_000_000
MAX_JSON_DEPTH = 100
DISCOVERY_TIMEOUT_SECONDS = 60
WORKER_TIMEOUT_SECONDS = 480
_MANIFEST_KEYS = {
    "schema_version",
    "run_id",
    "mode",
    "created_at",
    "code_version",
    "code_hash",
    "prompt_hash",
    "input_hash",
    "model_receipts",
    "files",
}


class LabExecutionError(RuntimeError):
    """Stable, sanitized failure from managed decision-lab execution."""


Runner = Callable[..., Any]


def _raise(code: str) -> None:
    raise LabExecutionError(code)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _strict_json_value(value: Any) -> bool:
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if item is None or isinstance(item, (str, bool, int)):
            continue
        if isinstance(item, float):
            if not math.isfinite(item):
                return False
            continue
        if not isinstance(item, (list, dict)) or depth >= MAX_JSON_DEPTH:
            return False
        children = item.values() if isinstance(item, dict) else item
        stack.extend((child, depth + 1) for child in children)
    return True


def _loads_strict(text: str, code: str) -> Any:
    value: Any = None
    try:
        value = json.loads(
            text,
            parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
            object_pairs_hook=_unique_object,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        _raise(code)
    if not _strict_json_value(value):
        _raise(code)
    return value


def _load_json(path: Path, *, unsafe_code: str, invalid_code: str) -> Any:
    try:
        if path.is_symlink() or not path.is_file():
            _raise(unsafe_code)
        size = path.stat().st_size
        if size > MAX_JSON_BYTES:
            _raise(invalid_code)
        return _loads_strict(path.read_text(encoding="utf-8"), invalid_code)
    except LabExecutionError:
        raise
    except (OSError, UnicodeError):
        _raise(invalid_code)


def _completed(response: Any, code: str) -> tuple[int, str, str]:
    returncode = getattr(response, "returncode", None)
    stdout = getattr(response, "stdout", None)
    stderr = getattr(response, "stderr", None)
    if type(returncode) is not int or not isinstance(stdout, str) or not isinstance(stderr, str):
        _raise(code)
    assert type(returncode) is int and isinstance(stdout, str) and isinstance(stderr, str)
    if len(stdout.encode("utf-8")) > MAX_JSON_BYTES or len(stderr.encode("utf-8")) > MAX_JSON_BYTES:
        _raise(code)
    return returncode, stdout, stderr


def _receipt_models(receipts: Any) -> list[str]:
    if not isinstance(receipts, list):
        _raise("invalid_lab_artifacts")
    models: list[str] = []
    for receipt in receipts:
        if not isinstance(receipt, dict):
            _raise("invalid_lab_artifacts")
        model = receipt.get("model")
        if not isinstance(model, str):
            _raise("invalid_lab_artifacts")
        assert isinstance(model, str)
        models.append(model)
    return models


def _verify_receipts(run_dir: Path, manifest: dict[str, Any]) -> None:
    artifacts: list[dict[str, Any]] = []
    files = manifest.get("files")
    if not isinstance(files, dict):
        _raise("invalid_lab_artifacts")
    assert isinstance(files, dict)
    for name in sorted(files):
        if not name.startswith("response-"):
            continue
        artifact = _load_json(
            run_dir / name,
            unsafe_code="invalid_lab_artifacts",
            invalid_code="invalid_lab_artifacts",
        )
        if not isinstance(artifact, dict) or not isinstance(artifact.get("receipt"), dict):
            _raise("invalid_lab_artifacts")
        role = name.removesuffix(".json").split("-", 2)[-1]
        artifacts.append({"role": role, **artifact["receipt"]})
    if hash_value(artifacts) != hash_value(manifest.get("model_receipts")):
        _raise("invalid_lab_artifacts")


class ManagedLabExecutor:
    """Run one archived bundle through the managed pure decision-lab process."""

    def __init__(self, model: str, *, runner: Runner | None = None):
        self.model = model
        self._runner = runner or subprocess.run

    def __call__(self, bundle: dict, collection_path: str | Path, output_dir: str | Path) -> dict:
        collection = Path(collection_path)
        archived_path = collection / "bundle.json"
        archived = _load_json(
            archived_path,
            unsafe_code="unsafe_archived_bundle",
            invalid_code="invalid_archived_bundle",
        )
        if not isinstance(archived, dict):
            _raise("invalid_archived_bundle")
        archived_hash = passed_hash = ""
        try:
            archived_hash = hash_value(archived)
            passed_hash = hash_value(bundle)
        except (TypeError, ValueError, OverflowError, RecursionError):
            _raise("invalid_passed_bundle")
        if archived_hash != passed_hash or archived != bundle:
            _raise("archived_bundle_mismatch")
        run_id = bundle.get("run_id") if isinstance(bundle, dict) else None
        if not isinstance(run_id, str) or archived.get("run_id") != run_id:
            _raise("archived_bundle_mismatch")
        assert isinstance(run_id, str)

        command: list[str] = []
        try:
            discovery = self._runner(
                ["hermes", "--print-runtime-command"],
                capture_output=True,
                text=True,
                timeout=DISCOVERY_TIMEOUT_SECONDS,
                check=False,
            )
            returncode, stdout, _ = _completed(discovery, "managed_runtime_unavailable")
            if returncode != 0:
                _raise("managed_runtime_unavailable")
            runtime_command = _loads_strict(stdout, "managed_runtime_unavailable")
            script = Path(__file__).resolve().parents[1] / "ai_decision_lab.py"
            command = managed_command(
                runtime_command,
                script,
                [
                    "run",
                    str(archived_path),
                    "--model",
                    self.model,
                    "--output",
                    str(Path(output_dir)),
                ],
            )
        except LabExecutionError:
            raise
        except Exception:
            _raise("managed_runtime_unavailable")
        assert command

        worker_returncode = -1
        try:
            worker = self._runner(
                command,
                capture_output=True,
                text=True,
                timeout=WORKER_TIMEOUT_SECONDS,
                check=False,
            )
            worker_returncode, _, _ = _completed(worker, "lab_execution_failed")
        except LabExecutionError:
            raise
        except (subprocess.TimeoutExpired, OSError):
            _raise("lab_execution_failed")
        except Exception:
            _raise("lab_execution_failed")
        if worker_returncode not in {0, 2}:
            _raise("lab_execution_failed")

        run_dir = Path(output_dir) / run_id
        manifest = _load_json(
            run_dir / "manifest.json",
            unsafe_code="invalid_lab_artifacts",
            invalid_code="invalid_lab_artifacts",
        )
        decision = _load_json(
            run_dir / "decision.json",
            unsafe_code="invalid_lab_artifacts",
            invalid_code="invalid_lab_artifacts",
        )
        saved_input = _load_json(
            run_dir / "input.json",
            unsafe_code="invalid_lab_artifacts",
            invalid_code="invalid_lab_artifacts",
        )

        if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS:
            _raise("invalid_lab_artifacts")
        if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
            _raise("invalid_lab_artifacts")
        if not isinstance(decision, dict) or not isinstance(saved_input, dict):
            _raise("invalid_lab_artifacts")
        assert isinstance(manifest, dict) and isinstance(decision, dict) and isinstance(saved_input, dict)
        if (
            manifest.get("run_id") != run_id
            or decision.get("run_id") != run_id
            or run_dir.name != run_id
        ):
            _raise("invalid_lab_artifacts")
        if hash_value(saved_input) != passed_hash or saved_input != bundle:
            _raise("invalid_lab_artifacts")
        if manifest.get("input_hash") != passed_hash:
            _raise("invalid_lab_artifacts")
        status = decision.get("status")
        receipt_models = _receipt_models(manifest.get("model_receipts"))
        permitted_models = {self.model, "unknown"} if status == "failed" else {self.model}
        if any(model not in permitted_models for model in receipt_models):
            _raise("invalid_lab_artifacts")
        _verify_receipts(run_dir, manifest)

        try:
            replay_run(run_dir)
        except Exception:
            _raise("invalid_lab_artifacts")

        expected_returncode = 0 if status == "accepted" else 2
        if worker_returncode != expected_returncode:
            _raise("inconsistent_lab_result")
        return decision
