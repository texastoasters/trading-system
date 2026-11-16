from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def hash_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    data = canonical_bytes(value) + b"\n"
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def reserve_run(output_dir: str | Path, run_id: str) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    final = root / run_id
    temporary = root / f".{run_id}.pending"
    if final.exists() or temporary.exists():
        raise FileExistsError(f"run already exists: {run_id}")
    temporary.mkdir()
    return temporary, final


def publish_run(temporary: Path, final: Path) -> None:
    if final.exists():
        raise FileExistsError(f"run already exists: {final.name}")
    temporary.rename(final)


def abandon_run(temporary: Path) -> None:
    shutil.rmtree(temporary, ignore_errors=True)
