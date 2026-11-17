"""Create-once campaign storage with integrity verification and process locks."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .protocol import CampaignError, prepare_protocol, validate_protocol


class CampaignBusy(CampaignError):
    """Raised when another process holds the campaign lock."""


KINDS = ("events", "observations", "checkpoints", "ticks")
MAX_JSON_BYTES = 4_000_000
_REGISTRATION = "registration.json"
_MANIFEST = "manifest.json"
MAX_JSON_DEPTH = 100


def _strict_json_value(value: Any) -> bool:
    active: set[int] = set()
    stack: list[tuple[Any, int, bool]] = [(value, 0, False)]
    while stack:
        item, depth, exiting = stack.pop()
        if exiting:
            active.remove(id(item))
            continue
        if item is None or isinstance(item, (str, bool, int)):
            continue
        if isinstance(item, float):
            if item != item or item in (float("inf"), float("-inf")):
                return False
            continue
        if not isinstance(item, (list, dict)) or depth >= MAX_JSON_DEPTH:
            return False
        identity = id(item)
        if identity in active:
            return False
        active.add(identity)
        stack.append((item, depth, True))
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                return False
            children = item.values()
        else:
            children = item
        stack.extend((child, depth + 1, False) for child in children)
    return True


def _canonical_bytes(value: Any) -> bytes:
    if not _strict_json_value(value):
        raise CampaignError("invalid JSON value")
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_regular_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _safe_directory(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def _load_json(path: Path) -> Any:
    if not _safe_regular_file(path):
        raise CampaignError("unsafe campaign artifact")
    size = path.stat().st_size
    if size > MAX_JSON_BYTES:
        raise CampaignError("oversized JSON artifact")
    try:
        text = path.read_text(encoding="utf-8")
        value = json.loads(
            text,
            parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
            object_pairs_hook=_unique_object,
        )
        if not _strict_json_value(value):
            raise ValueError("invalid JSON value")
        return value
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise CampaignError("invalid JSON artifact") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _exclusive_write(path: Path, value: Any) -> None:
    data = _canonical_bytes(value) + b"\n"
    if len(data) > MAX_JSON_BYTES:
        raise CampaignError("oversized JSON value")
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise CampaignError("artifact already exists") from exc
        _fsync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CampaignError("clock must return an aware datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def implementation_fingerprint() -> str:
    """Hash production evaluation/evidence/lab Python sources, excluding tests."""
    scripts = Path(__file__).resolve().parents[1]
    roots = (scripts / "prospective_evaluation", scripts / "prospective_evidence", scripts / "decision_lab")
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files.extend(path for path in root.rglob("*.py") if not path.name.startswith("test"))
    files.extend(
        path for path in scripts.glob("*.py")
        if not path.name.startswith("test")
        and ("prospective" in path.stem or "decision_lab" in path.stem)
    )
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(scripts).as_posix()):
        relative = path.relative_to(scripts).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        data = path.read_bytes()
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _remove_tree(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    for child in sorted(path.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        child.rmdir() if child.is_dir() and not child.is_symlink() else child.unlink(missing_ok=True)
    path.rmdir()


@contextmanager
def _registration_lock(base: Path) -> Iterator[None]:
    lock = base / ".campaign-registration.lock"
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(lock, flags, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("registration lock is not a regular file")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise CampaignError("unsafe campaign registration lock") from exc
    try:
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def register_campaign(
    root: str | Path,
    request: Any,
    *,
    clock: Callable[[], datetime] | None = None,
) -> Path:
    """Create and atomically publish a campaign once at ``root/<campaign_id>``."""
    protocol = prepare_protocol(request)
    base = Path(root)
    if base.exists() and not _safe_directory(base):
        raise CampaignError("unsafe campaign root")
    base.mkdir(parents=True, exist_ok=True)
    path = base / protocol["campaign_id"]
    staging = Path(tempfile.mkdtemp(prefix=f".{protocol['campaign_id']}.", suffix=".pending", dir=base))
    try:
        staging.chmod(0o700)
        for name in (*KINDS, "artifacts"):
            (staging / name).mkdir(mode=0o700)
        now = (clock or (lambda: datetime.now(timezone.utc)))()
        registration = {
            "schema_version": 1,
            "protocol": protocol,
            "registered_at": _utc_text(now),
            "implementation_fingerprint": implementation_fingerprint(),
        }
        _exclusive_write(staging / _REGISTRATION, registration)
        manifest = {
            "schema_version": 1,
            "files": {_REGISTRATION: _hash_file(staging / _REGISTRATION)},
        }
        _exclusive_write(staging / _MANIFEST, manifest)
        _fsync_directory(staging)
        with _registration_lock(base):
            if path.exists() or path.is_symlink():
                raise CampaignError("campaign already exists")
            os.rename(staging, path)
            _fsync_directory(base)
        return path
    finally:
        _remove_tree(staging)


def _verify_layout(path: Path) -> None:
    expected = {_REGISTRATION, _MANIFEST, *KINDS, "artifacts"}
    names = {child.name for child in path.iterdir()} if _safe_directory(path) else set()
    allowed = expected | ({".lock"} if ".lock" in names else set())
    if not _safe_directory(path) or names != allowed:
        raise CampaignError("unsafe campaign layout")
    for name in (*KINDS, "artifacts"):
        if not _safe_directory(path / name):
            raise CampaignError("unsafe campaign layout")
    artifacts = path / "artifacts"
    for child in artifacts.rglob("*"):
        if child.is_symlink():
            raise CampaignError("unsafe campaign layout")
    lock = path / ".lock"
    if lock.exists() or lock.is_symlink():
        if not stat.S_ISREG(lock.lstat().st_mode):
            raise CampaignError("unsafe campaign lock")


def _valid_registered_at(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def load_campaign(path: str | Path) -> dict[str, Any]:
    """Verify and load an exact campaign registration envelope."""
    campaign = Path(path)
    _verify_layout(campaign)
    manifest = _load_json(campaign / _MANIFEST)
    if (not isinstance(manifest, dict) or set(manifest) != {"schema_version", "files"}
            or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
            or manifest["files"] != {_REGISTRATION: _hash_file(campaign / _REGISTRATION)}):
        raise CampaignError("campaign manifest mismatch")
    registration = _load_json(campaign / _REGISTRATION)
    if (not isinstance(registration, dict)
            or set(registration) != {"schema_version", "protocol", "registered_at", "implementation_fingerprint"}
            or type(registration["schema_version"]) is not int or registration["schema_version"] != 1
            or not _valid_registered_at(registration["registered_at"])
            or not isinstance(registration["implementation_fingerprint"], str)
            or re.fullmatch(r"[0-9a-f]{64}", registration["implementation_fingerprint"]) is None):
        raise CampaignError("invalid campaign registration")
    validate_protocol(registration["protocol"])
    if campaign.name != registration["protocol"]["campaign_id"]:
        raise CampaignError("campaign identity mismatch")
    return registration


def _safe_id(value: Any) -> bool:
    from .protocol import _portable_id
    return _portable_id(value)


def write_record(path: str | Path, kind: str, record_id: str, value: Any) -> dict[str, Any]:
    """Publish one create-once canonical record; callers manage the campaign lock."""
    campaign = Path(path)
    _verify_layout(campaign)
    load_campaign(campaign)
    if kind not in KINDS or not _safe_id(record_id):
        raise CampaignError("invalid record identity")
    envelope = {
        "schema_version": 1,
        "kind": kind,
        "record_id": record_id,
        "value": value,
        "sha256": _sha256(value),
    }
    _exclusive_write(campaign / kind / f"{record_id}.json", envelope)
    return envelope


def load_records(path: str | Path, kind: str) -> list[dict[str, Any]]:
    """Load and integrity-check all records of one exact kind in ID order."""
    campaign = Path(path)
    _verify_layout(campaign)
    load_campaign(campaign)
    if kind not in KINDS:
        raise CampaignError("invalid record kind")
    directory = campaign / kind
    records: list[dict[str, Any]] = []
    for file_path in sorted(directory.iterdir(), key=lambda item: item.name):
        if not file_path.name.endswith(".json") or not _safe_id(file_path.name[:-5]):
            raise CampaignError("unsafe record layout")
        record = _load_json(file_path)
        if (not isinstance(record, dict)
                or set(record) != {"schema_version", "kind", "record_id", "value", "sha256"}
                or type(record["schema_version"]) is not int or record["schema_version"] != 1
                or record["kind"] != kind or record["record_id"] != file_path.name[:-5]
                or record["sha256"] != _sha256(record["value"])):
            raise CampaignError("record integrity mismatch")
        records.append(record)
    return records


@contextmanager
def campaign_lock(path: str | Path) -> Iterator[None]:
    """Hold a nonblocking POSIX advisory lock for the whole campaign tick."""
    campaign = Path(path)
    if not _safe_directory(campaign):
        raise CampaignError("unsafe campaign path")
    lock_path = campaign / ".lock"
    if lock_path.exists() or lock_path.is_symlink():
        if not stat.S_ISREG(lock_path.lstat().st_mode):
            raise CampaignError("unsafe campaign lock")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CampaignBusy("campaign is busy") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
