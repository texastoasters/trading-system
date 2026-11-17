from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import socket
import stat
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from prospective_evaluation.protocol import CampaignError
from prospective_evaluation.storage import (
    CampaignBusy,
    campaign_lock,
    implementation_fingerprint,
    load_campaign,
    load_records,
    register_campaign,
    write_record,
)
from prospective_evaluation.test_protocol import VALID_REQUEST


def _try_lock(path: str, result: Any) -> None:
    try:
        with campaign_lock(path):
            result.put("acquired")
    except CampaignBusy:
        result.put("busy")


def _die_while_locked(path: str, result: Any) -> None:
    with campaign_lock(path):
        result.send("acquired")
        result.close()
        os._exit(0)


def _try_nested_lock(path: str, result: Any) -> None:
    try:
        with campaign_lock(path):
            with campaign_lock(path):
                result.put("unexpected")
    except CampaignBusy:
        result.put("busy")


def _hold_lock(path: str, ready: Any, release: Any) -> None:
    with campaign_lock(path):
        ready.put("locked")
        release.get(timeout=10)


def _die_during_registration(root: str, request: dict[str, Any], ready: Any) -> None:
    def die_after_staging() -> datetime:
        ready.send("staged")
        ready.close()
        os._exit(86)

    register_campaign(root, request, clock=die_after_staging)


def _register_concurrently(
    root: str, request: dict[str, Any], ready: Any, release: Any, result: Any,
) -> None:
    ready.put("ready")
    release.wait(timeout=10)
    try:
        register_campaign(
            root,
            request,
            clock=lambda: datetime(2026, 11, 17, tzinfo=timezone.utc),
        )
        result.put("registered")
    except CampaignError as exc:
        result.put(str(exc))


def _registered(tmp_path: Path) -> Path:
    return register_campaign(
        tmp_path,
        deepcopy(VALID_REQUEST),
        clock=lambda: datetime(2026, 11, 17, 4, 42, 3, 123456, tzinfo=timezone.utc),
    )


def test_register_campaign_creates_exact_durable_layout_and_loads_registration(tmp_path) -> None:
    path = _registered(tmp_path)

    assert path == tmp_path / VALID_REQUEST["campaign_id"]
    assert {item.name for item in tmp_path.iterdir()} == {
        ".campaign-registration.lock", VALID_REQUEST["campaign_id"],
    }
    assert {item.name for item in path.iterdir()} == {
        "registration.json", "manifest.json", "events", "observations",
        "checkpoints", "ticks", "artifacts",
    }
    registration = load_campaign(path)
    assert registration == {
        "schema_version": 1,
        "protocol": registration["protocol"],
        "registered_at": "2026-11-17T04:42:03.123456Z",
        "implementation_fingerprint": implementation_fingerprint(),
    }
    assert registration["protocol"]["model"] == VALID_REQUEST["model"]
    assert len(registration["implementation_fingerprint"]) == 64
    assert bytes.fromhex(registration["implementation_fingerprint"])

    manifest = json.loads((path / "manifest.json").read_text())
    expected_hash = hashlib.sha256((path / "registration.json").read_bytes()).hexdigest()
    assert manifest == {"schema_version": 1, "files": {"registration.json": expected_hash}}

    with pytest.raises(CampaignError, match="^campaign already exists$"):
        register_campaign(tmp_path, deepcopy(VALID_REQUEST))


def test_registration_process_death_leaves_no_final_identity_and_retry_succeeds(tmp_path) -> None:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    request = {**deepcopy(VALID_REQUEST), "campaign_id": "process-death"}
    process = context.Process(
        target=_die_during_registration,
        args=(str(tmp_path), request, child),
    )
    process.start()
    child.close()
    assert parent.recv() == "staged"
    parent.close()
    process.join(10)

    assert process.exitcode == 86
    assert not (tmp_path / "process-death").exists()
    assert all(item.name == ".campaign-registration.lock" or item.name.endswith(".pending")
               for item in tmp_path.iterdir())
    assert register_campaign(tmp_path, request).name == "process-death"


def test_registration_never_overwrites_existing_destination_and_concurrent_publish_is_create_once(tmp_path) -> None:
    for identity, create in (
        ("preexisting-empty", lambda target: target.mkdir()),
        ("preexisting-file", lambda target: target.write_text("reserved")),
    ):
        destination = tmp_path / identity
        create(destination)
        with pytest.raises(CampaignError, match="^campaign already exists$"):
            register_campaign(
                tmp_path,
                {**deepcopy(VALID_REQUEST), "campaign_id": identity},
            )
        if destination.is_dir():
            assert list(destination.iterdir()) == []
        else:
            assert destination.read_text() == "reserved"

    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    release = context.Event()
    result = context.Queue()
    request = {**deepcopy(VALID_REQUEST), "campaign_id": "concurrent"}
    processes = [
        context.Process(
            target=_register_concurrently,
            args=(str(tmp_path), request, ready, release, result),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    assert [ready.get(timeout=5), ready.get(timeout=5)] == ["ready", "ready"]
    release.set()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0

    assert sorted([result.get(timeout=2), result.get(timeout=2)]) == [
        "campaign already exists", "registered",
    ]
    assert load_campaign(tmp_path / "concurrent")["protocol"]["campaign_id"] == "concurrent"


def test_registration_rejects_unsafe_root_and_naive_clock(tmp_path) -> None:
    unsafe = tmp_path / "unsafe"
    unsafe.write_text("not a directory")
    with pytest.raises(CampaignError, match="^unsafe campaign root$"):
        register_campaign(unsafe, deepcopy(VALID_REQUEST))

    with pytest.raises(CampaignError, match="^clock must return an aware datetime$"):
        register_campaign(tmp_path, {**deepcopy(VALID_REQUEST), "campaign_id": "naive"},
                          clock=lambda: datetime(2026, 1, 1))
    assert not (tmp_path / "naive").exists()

    def broken_clock() -> datetime:
        raise RuntimeError("clock failed")

    with pytest.raises(RuntimeError, match="clock failed"):
        register_campaign(tmp_path, {**deepcopy(VALID_REQUEST), "campaign_id": "clock-error"},
                          clock=broken_clock)
    assert not (tmp_path / "clock-error").exists()


def test_registration_rejects_lock_symlinks_without_publishing_or_altering_outside_file(
    tmp_path,
) -> None:
    outside = tmp_path / "outside-lock"
    outside.write_bytes(b"outside-content")
    root = tmp_path / "regular-link-root"
    root.mkdir()
    (root / ".campaign-registration.lock").symlink_to(outside)

    with pytest.raises(CampaignError, match="^unsafe campaign registration lock$"):
        register_campaign(
            root,
            {**deepcopy(VALID_REQUEST), "campaign_id": "regular-link"},
        )

    assert outside.read_bytes() == b"outside-content"
    assert not (root / "regular-link").exists()

    broken_root = tmp_path / "broken-link-root"
    broken_root.mkdir()
    missing_outside = tmp_path / "missing-outside-lock"
    (broken_root / ".campaign-registration.lock").symlink_to(missing_outside)

    with pytest.raises(CampaignError, match="^unsafe campaign registration lock$"):
        register_campaign(
            broken_root,
            {**deepcopy(VALID_REQUEST), "campaign_id": "broken-link"},
        )

    assert not missing_outside.exists()
    assert not (broken_root / "broken-link").exists()


@pytest.mark.parametrize("lock_kind", ("directory", "fifo", "socket"))
def test_registration_rejects_nonregular_lock_types_without_publishing(tmp_path, lock_kind) -> None:
    root = tmp_path / lock_kind
    root.mkdir()
    lock = root / ".campaign-registration.lock"
    bound_socket = None
    if lock_kind == "directory":
        lock.mkdir()
    elif lock_kind == "fifo":
        os.mkfifo(lock)
    else:
        socket_root = Path(tempfile.mkdtemp(prefix="lk-", dir=os.environ.get("TMPDIR")))
        root = socket_root
        lock = root / ".campaign-registration.lock"
        bound_socket = socket.socket(socket.AF_UNIX)
        bound_socket.bind(str(lock))
    original_mode = lock.lstat().st_mode

    try:
        with pytest.raises(CampaignError, match="^unsafe campaign registration lock$"):
            register_campaign(
                root,
                {**deepcopy(VALID_REQUEST), "campaign_id": f"unsafe-{lock_kind}"},
            )
    finally:
        if bound_socket is not None:
            bound_socket.close()

    assert stat.S_IFMT(lock.lstat().st_mode) == stat.S_IFMT(original_mode)
    assert not (root / f"unsafe-{lock_kind}").exists()
    if bound_socket is not None:
        lock.unlink()
        root.rmdir()


def test_fingerprint_ignores_tests_and_changes_with_production_sources(tmp_path) -> None:
    package = Path(__file__).parent
    before = implementation_fingerprint()
    test_probe = package / "test_fingerprint_probe.py"
    production_probe = package / "fingerprint_probe.py"
    top_level_probe = package.parent / "prospective_fingerprint_probe.py"
    try:
        test_probe.write_text("TEST = True\n")
        assert implementation_fingerprint() == before
        production_probe.write_text("VALUE = 1\n")
        assert implementation_fingerprint() != before
        after_package = implementation_fingerprint()
        top_level_probe.write_text("VALUE = 2\n")
        assert implementation_fingerprint() != after_package
    finally:
        test_probe.unlink(missing_ok=True)
        production_probe.unlink(missing_ok=True)
        top_level_probe.unlink(missing_ok=True)


def test_write_and_load_records_use_exact_sorted_integrity_envelopes(tmp_path) -> None:
    path = _registered(tmp_path)
    value = {"decimal": "1.25", "available": True, "nested": [None, 2], "ratio": 1.5}
    first = write_record(path, "events", "z-last", value)
    second = write_record(path, "events", "a-first", {"event": "started"})

    assert first == {
        "schema_version": 1,
        "kind": "events",
        "record_id": "z-last",
        "value": value,
        "sha256": hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest(),
    }
    assert load_records(path, "events") == [second, first]
    for kind in ("observations", "checkpoints", "ticks"):
        record = write_record(path, kind, "one", {"kind": kind})
        assert load_records(path, kind) == [record]


def test_record_publication_is_create_once_and_strict_json(tmp_path) -> None:
    path = _registered(tmp_path)
    write_record(path, "ticks", "same-id", {"value": 1})

    with pytest.raises(CampaignError, match="^artifact already exists$"):
        write_record(path, "ticks", "same-id", {"value": 2})
    cyclic: list[Any] = []
    cyclic.append(cyclic)
    for kind, record_id, value in (
        ("unknown", "valid", {}),
        ("events", "../escape", {}),
        ("events", "valid", {"bad": float("nan")}),
        ("events", "valid", {"good": 1.25, "bad": float("inf")}),
        ("events", "valid", {1: "non-string-key"}),
        ("events", "valid", {"bad": object()}),
        ("events", "valid", cyclic),
    ):
        with pytest.raises(CampaignError):
            write_record(path, kind, record_id, value)

    deeply_nested: Any = None
    for _ in range(2000):
        deeply_nested = [deeply_nested]
    with pytest.raises(CampaignError, match="^invalid JSON value$"):
        write_record(path, "events", "deep", deeply_nested)

    with pytest.raises(CampaignError, match="^invalid record kind$"):
        load_records(path, "unknown")


def test_load_normalizes_excessive_json_nesting_to_campaign_error(tmp_path) -> None:
    path = _registered(tmp_path)
    (path / "events" / "deep.json").write_text("[" * 2000 + "0" + "]" * 2000)

    with pytest.raises(CampaignError, match="^invalid JSON artifact$"):
        load_records(path, "events")


def _envelope_bytes(record_id: str, value: str) -> bytes:
    canonical_value = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    envelope = {
        "schema_version": 1,
        "kind": "events",
        "record_id": record_id,
        "value": value,
        "sha256": hashlib.sha256(canonical_value).hexdigest(),
    }
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"


def test_record_json_limit_accepts_exactly_four_mb_and_rejects_one_byte_more(tmp_path) -> None:
    path = _registered(tmp_path)
    fixed_size = len(_envelope_bytes("boundary", ""))
    exact_value = "x" * (4_000_000 - fixed_size)
    assert len(_envelope_bytes("boundary", exact_value)) == 4_000_000

    write_record(path, "events", "boundary", exact_value)
    assert load_records(path, "events")[0]["value"] == exact_value
    with pytest.raises(CampaignError, match="^oversized JSON value$"):
        write_record(path, "events", "too-large", "x" * 4_000_000)


def test_load_fails_closed_on_symlink_traversal_layout_and_hash_tampering(tmp_path) -> None:
    path = _registered(tmp_path)
    write_record(path, "events", "safe", {"value": 1})

    (path / "events" / "rogue").write_text("{}")
    with pytest.raises(CampaignError, match="^unsafe record layout$"):
        load_records(path, "events")
    (path / "events" / "rogue").unlink()

    record_path = path / "events" / "safe.json"
    record = json.loads(record_path.read_text())
    record["value"] = {"value": 2}
    record_path.write_text(json.dumps(record))
    with pytest.raises(CampaignError, match="^record integrity mismatch$"):
        load_records(path, "events")

    record_path.unlink()
    record_path.symlink_to(path / "registration.json")
    with pytest.raises(CampaignError, match="^unsafe campaign artifact$"):
        load_records(path, "events")
    record_path.unlink()
    oversized = path / "events" / "oversized.json"
    with oversized.open("wb") as handle:
        handle.seek(4_000_000)
        handle.write(b"x")
    with pytest.raises(CampaignError, match="^oversized JSON artifact$"):
        load_records(path, "events")


def test_load_campaign_rejects_registration_manifest_layout_and_json_tampering(tmp_path) -> None:
    cases = ("registration_hash", "manifest_shape", "manifest_bool", "invalid_registration", "invalid_time", "invalid_time_parse", "invalid_fingerprint", "duplicate_key", "nonfinite", "extra_layout")
    for index, case in enumerate(cases):
        request = {**deepcopy(VALID_REQUEST), "campaign_id": f"tamper-{index}"}
        path = register_campaign(tmp_path, request)
        if case == "registration_hash":
            (path / "registration.json").write_text("{}")
        elif case == "manifest_shape":
            (path / "manifest.json").write_text('{"schema_version":1,"files":{},"extra":true}')
        elif case == "manifest_bool":
            manifest = json.loads((path / "manifest.json").read_text())
            manifest["schema_version"] = True
            (path / "manifest.json").write_text(json.dumps(manifest))
        elif case in {"invalid_registration", "invalid_time", "invalid_time_parse", "invalid_fingerprint"}:
            registration = json.loads((path / "registration.json").read_text())
            if case == "invalid_registration":
                registration["schema_version"] = True
            elif case == "invalid_time":
                registration["registered_at"] = "not-a-time"
            elif case == "invalid_time_parse":
                registration["registered_at"] = "2026-99-99T00:00:00Z"
            else:
                registration["implementation_fingerprint"] = "g" * 64
            registration_bytes = json.dumps(registration, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            (path / "registration.json").write_bytes(registration_bytes)
            manifest = {"schema_version": 1, "files": {
                "registration.json": hashlib.sha256(registration_bytes).hexdigest()
            }}
            (path / "manifest.json").write_text(json.dumps(manifest))
        elif case == "duplicate_key":
            (path / "manifest.json").write_text('{"schema_version":1,"schema_version":1,"files":{}}')
        elif case == "nonfinite":
            (path / "manifest.json").write_text('{"schema_version":NaN,"files":{}}')
        else:
            (path / "unexpected").mkdir()
        with pytest.raises(CampaignError):
            load_campaign(path)


def test_load_rejects_symlinked_campaign_components(tmp_path) -> None:
    cases = ("events", "artifacts", "artifacts/collections", ".lock")
    for index, relative in enumerate(cases):
        path = register_campaign(
            tmp_path,
            {**deepcopy(VALID_REQUEST), "campaign_id": f"symlink-layout-{index}"},
        )
        component = path / relative
        outside = tmp_path / f"outside-{index}"
        if component.exists():
            component.rename(outside)
        else:
            outside.mkdir()
        component.symlink_to(outside, target_is_directory=True)

        expected_error = "unsafe campaign lock" if relative == ".lock" else "unsafe campaign layout"
        with pytest.raises(CampaignError, match=f"^{expected_error}$"):
            load_campaign(path)
        with pytest.raises(CampaignError, match=f"^{expected_error}$"):
            write_record(path, "events", "blocked", {"value": 1})
        assert list(outside.iterdir()) == []


def test_campaign_name_must_match_frozen_campaign_identity(tmp_path) -> None:
    path = _registered(tmp_path)
    renamed = tmp_path / "renamed-campaign"
    path.rename(renamed)

    with pytest.raises(CampaignError, match="^campaign identity mismatch$"):
        load_campaign(renamed)
    with pytest.raises(CampaignError, match="^campaign identity mismatch$"):
        write_record(renamed, "events", "blocked", {})


def test_campaign_lock_is_nonblocking_across_processes_and_released_on_death(tmp_path) -> None:
    path = _registered(tmp_path)
    context = multiprocessing.get_context("spawn")
    result = context.Queue()

    with campaign_lock(path):
        assert load_campaign(path)["protocol"]["campaign_id"] == VALID_REQUEST["campaign_id"]
        contender = context.Process(target=_try_lock, args=(str(path), result))
        contender.start()
        contender.join(10)
        assert contender.exitcode == 0
        assert result.get(timeout=2) == "busy"

    ready = context.Queue()
    release = context.Queue()
    holder = context.Process(target=_hold_lock, args=(str(path), ready, release))
    holder.start()
    assert ready.get(timeout=5) == "locked"
    with pytest.raises(CampaignBusy, match="^campaign is busy$"):
        with campaign_lock(path):
            pass
    release.put("release")
    holder.join(10)
    assert holder.exitcode == 0

    parent_connection, child_connection = context.Pipe(duplex=False)
    owner = context.Process(target=_die_while_locked, args=(str(path), child_connection))
    owner.start()
    child_connection.close()
    assert parent_connection.recv() == "acquired"
    parent_connection.close()
    owner.join(10)
    assert owner.exitcode == 0
    with campaign_lock(path):
        pass

    nested = context.Process(target=_try_nested_lock, args=(str(path), result))
    nested.start()
    nested.join(10)
    assert nested.exitcode == 0
    assert result.get(timeout=2) == "busy"


def test_campaign_lock_rejects_missing_path_and_unsafe_lock_entry(tmp_path) -> None:
    with pytest.raises(CampaignError, match="^unsafe campaign path$"):
        with campaign_lock(tmp_path / "missing"):
            pass

    for index, kind in enumerate(("symlink", "directory")):
        path = register_campaign(
            tmp_path,
            {**deepcopy(VALID_REQUEST), "campaign_id": f"unsafe-lock-{index}"},
        )
        lock = path / ".lock"
        if kind == "symlink":
            lock.symlink_to(path / "registration.json")
        else:
            lock.mkdir()
        with pytest.raises(CampaignError, match="^unsafe campaign lock$"):
            with campaign_lock(path):
                pass
