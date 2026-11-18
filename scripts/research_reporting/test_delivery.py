"""External Hermes subprocess and local sender-index protocol tests."""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

from research_reporting.delivery import DeliveryError, PhotonSender


STDOUT_LIMIT = 64 * 1024
INDEX_LIMIT = 16 * 1024 * 1024
SAFE_TEXT = "Research channel check. No orders. Billed cost unknown."


def _accepted(message_id: str = "spc-msg-test-1", **extra: Any) -> str:
    return json.dumps(
        {"success": True, "message_id": message_id, **extra},
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _result(stdout: Any, returncode: Any = 0, stderr: str = "PRIVATE STDERR") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["hermes"], returncode, stdout, stderr)


def _write_index(home: Path, value: Any, *, raw: bytes | None = None) -> Path:
    path = home / "state" / "rich_sent_index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if raw is None:
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path.write_bytes(raw)
    return path


def _sized_response(size: int) -> str:
    prefix = '{"success":true,"message_id":"id","padding":"'
    suffix = '"}'
    return prefix + ("x" * (size - len(prefix.encode()) - len(suffix.encode()))) + suffix


def test_real_subprocess_uses_only_existing_photon_home_and_verifies_local_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "human-selected-hermes-home"
    home.mkdir()
    fake = tmp_path / "hermes"
    capture = tmp_path / "capture.json"
    fake.write_text(
        """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
body = sys.stdin.read()
Path(os.environ["CAPTURE"]).write_text(json.dumps({"argv": sys.argv[1:], "stdin": body, "home": os.environ["HERMES_HOME"]}))
root = Path(os.environ["HERMES_HOME"]) / "state"
root.mkdir()
(root / "rich_sent_index.json").write_text(json.dumps({"private-test-home:spc-msg-test-1": {"t": body, "ts": 1}}))
print(json.dumps({"success": True, "message_id": "spc-msg-test-1", "note": "NEVER_EXPOSE_PRIVATE_HOME"}))
"""
    )
    fake.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("CAPTURE", str(capture))

    receipt = PhotonSender(home)(SAFE_TEXT)

    assert receipt == {"provider_id": "spc-msg-test-1", "verified": True}
    assert json.loads(capture.read_text()) == {
        "argv": ["send", "--to", "photon", "--json", "--file", "-"],
        "stdin": SAFE_TEXT,
        "home": str(home),
    }
    assert "NEVER_EXPOSE_PRIVATE_HOME" not in json.dumps(receipt)


def test_external_runner_receives_fixed_safe_projection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    _write_index(home, {"private:id": {"t": SAFE_TEXT}})
    monkeypatch.setenv("HERMES_HOME", "/not/the/selected/home")
    observed: dict[str, Any] = {}

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        observed.update({"argv": argv, **kwargs})
        return _result(_accepted("id"))

    assert PhotonSender(home, runner=runner)(SAFE_TEXT) == {
        "provider_id": "id", "verified": True,
    }
    assert observed["argv"] == ["hermes", "send", "--to", "photon", "--json", "--file", "-"]
    assert observed["input"] == SAFE_TEXT
    assert observed["text"] is True
    assert observed["stdout"] is subprocess.PIPE
    assert observed["stderr"] is subprocess.DEVNULL
    assert observed["timeout"] == 60
    assert observed["check"] is False
    assert observed["env"]["HERMES_HOME"] == str(home)


@pytest.mark.parametrize("text", [None, b"text", "", " \n\t", "x" * 2001, "report MEDIA:/private/file.png", "media:./file"])
def test_invalid_or_attachment_text_is_rejected_before_submission(tmp_path: Path, text: Any) -> None:
    called = False

    def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        nonlocal called
        called = True
        return _result(_accepted())

    with pytest.raises(DeliveryError) as error:
        PhotonSender(tmp_path, runner=runner)(text)
    assert str(error.value) == "invalid_delivery_input"
    assert called is False


@pytest.mark.parametrize("length", [1, 1999, 2000])
def test_text_character_boundaries_are_sent_as_one_stdin_chunk(tmp_path: Path, length: int) -> None:
    text = "🦎" * length
    _write_index(tmp_path, {"opaque:id": {"t": text}})
    calls: list[str] = []

    def runner(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        calls.append(kwargs["input"])
        return _result(_accepted("id"))

    assert PhotonSender(tmp_path, runner=runner)(text)["verified"] is True
    assert calls == [text]


@pytest.mark.parametrize(
    "response",
    [
        _result(_accepted(), returncode=1),
        _result("not-json"),
        _result("[]"),
        _result('{"success":true,"success":true,"message_id":"id"}'),
        _result('{"success":true,"message_id":"id","value":NaN}'),
        _result('{"success":true,"message_id":"id","value":Infinity}'),
        _result('{"success":true,"message_id":"id","value":-Infinity}'),
        _result(json.dumps({"success": 1, "message_id": "id"})),
        _result(json.dumps({"success": False, "message_id": "id"})),
        _result(json.dumps({"success": True, "skipped": True, "message_id": "id"})),
        _result(json.dumps({"success": True, "skipped": 1, "message_id": "id"})),
        _result(json.dumps({"success": True})),
        _result(json.dumps({"success": True, "message_id": ""})),
        _result(json.dumps({"success": True, "message_id": "   "})),
        _result(json.dumps({"success": True, "message_id": 123})),
        _result(json.dumps({"success": True, "message_id": "bad\nidentifier"})),
        _result(json.dumps({"success": True, "message_id": "x" * 257})),
        _result(b'{"success":true,"message_id":"id"}'),
        _result(_sized_response(STDOUT_LIMIT + 1)),
        _result(_accepted("id", note="🦎" * 20_000)),
    ],
)
def test_unconfirmed_or_malformed_cli_results_raise_only_sanitized_error(
    tmp_path: Path, response: subprocess.CompletedProcess,
) -> None:
    def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        return response

    with pytest.raises(DeliveryError) as error:
        PhotonSender(tmp_path, runner=runner)(SAFE_TEXT)
    assert str(error.value) == "delivery_unconfirmed"
    assert error.value.__cause__ is None
    assert "PRIVATE" not in repr(error.value)


@pytest.mark.parametrize("failure", [OSError("PRIVATE path"), subprocess.TimeoutExpired("PRIVATE command", 60), RuntimeError("PRIVATE body")])
def test_runner_failures_are_sanitized_and_never_retried(tmp_path: Path, failure: Exception) -> None:
    calls = 0

    def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        nonlocal calls
        calls += 1
        raise failure

    with pytest.raises(DeliveryError) as error:
        PhotonSender(tmp_path, runner=runner)(SAFE_TEXT)
    assert str(error.value) == "delivery_unconfirmed"
    assert error.value.__cause__ is None
    assert calls == 1


def test_stdout_byte_limit_accepts_equal_and_rejects_above(tmp_path: Path) -> None:
    _write_index(tmp_path, {"private:id": {"t": SAFE_TEXT}})
    responses = iter([_sized_response(STDOUT_LIMIT), _sized_response(STDOUT_LIMIT + 1)])

    def runner(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        return _result(next(responses))

    sender = PhotonSender(tmp_path, runner=runner)
    assert sender(SAFE_TEXT) == {"provider_id": "id", "verified": True}
    with pytest.raises(DeliveryError, match="^delivery_unconfirmed$"):
        sender(SAFE_TEXT)


@pytest.mark.parametrize(
    ("stored", "verified"),
    [
        ({"private:id": {"t": SAFE_TEXT}}, True),
        ({"private:id": {"t": "different"}}, False),
        ({"private:id": []}, False),
        ({"private:id": {"t": 4}}, False),
        ({"private:id": {"t": SAFE_TEXT}, "other:id": {"t": SAFE_TEXT}}, False),
        ({"private:other": {"t": SAFE_TEXT}}, False),
        ({}, False),
    ],
)
def test_known_acceptance_retains_id_and_requires_one_exact_local_text(
    tmp_path: Path, stored: Any, verified: bool,
) -> None:
    _write_index(tmp_path, stored)
    sender = PhotonSender(tmp_path, runner=lambda *args, **kwargs: _result(_accepted("id")))
    assert sender(SAFE_TEXT) == {"provider_id": "id", "verified": verified}


def test_utf8_bom_index_is_supported(tmp_path: Path) -> None:
    raw = b"\xef\xbb\xbf" + json.dumps({"private:id": {"t": SAFE_TEXT}}).encode()
    _write_index(tmp_path, None, raw=raw)
    sender = PhotonSender(tmp_path, runner=lambda *args, **kwargs: _result(_accepted("id")))
    assert sender(SAFE_TEXT)["verified"] is True


@pytest.mark.parametrize(
    "raw",
    [
        b"not-json",
        b"[]",
        b'{"private:id":{"t":"first"},"private:id":{"t":"second"}}',
        b'{"private:id":{"t":"ok","bad":NaN}}',
        b'{"private:id":{"t":"ok","bad":Infinity}}',
        b'{"private:id":{"t":"ok","bad":-Infinity}}',
        b"\xff",
    ],
)
def test_malformed_local_index_is_safe_unverified_acceptance(tmp_path: Path, raw: bytes) -> None:
    _write_index(tmp_path, None, raw=raw)
    sender = PhotonSender(tmp_path, runner=lambda *args, **kwargs: _result(_accepted("id")))
    assert sender(SAFE_TEXT) == {"provider_id": "id", "verified": False}


def test_missing_symlink_nonregular_and_unreadable_indexes_are_safe_unverified(
    tmp_path: Path,
) -> None:
    accepted = lambda *args, **kwargs: _result(_accepted("id"))

    missing = tmp_path / "missing"
    assert PhotonSender(missing, runner=accepted)(SAFE_TEXT)["verified"] is False

    target_home = tmp_path / "target-home"
    target = _write_index(target_home, {"private:id": {"t": SAFE_TEXT}})
    symlink_home = tmp_path / "symlink-home"
    symlink_path = symlink_home / "state" / "rich_sent_index.json"
    symlink_path.parent.mkdir(parents=True)
    symlink_path.symlink_to(target)
    assert PhotonSender(symlink_home, runner=accepted)(SAFE_TEXT)["verified"] is False

    directory_home = tmp_path / "directory-home"
    (directory_home / "state" / "rich_sent_index.json").mkdir(parents=True)
    assert PhotonSender(directory_home, runner=accepted)(SAFE_TEXT)["verified"] is False

    fifo_home = tmp_path / "fifo-home"
    fifo_path = fifo_home / "state" / "rich_sent_index.json"
    fifo_path.parent.mkdir(parents=True)
    os.mkfifo(fifo_path)
    assert PhotonSender(fifo_home, runner=accepted)(SAFE_TEXT)["verified"] is False

    unreadable_home = tmp_path / "unreadable-home"
    unreadable = _write_index(unreadable_home, {"private:id": {"t": SAFE_TEXT}})
    unreadable.chmod(0)
    try:
        outcome = PhotonSender(unreadable_home, runner=accepted)(SAFE_TEXT)
    finally:
        unreadable.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert outcome["verified"] is False


@pytest.mark.parametrize("size", [INDEX_LIMIT - 1, INDEX_LIMIT])
def test_index_byte_limit_accepts_below_and_equal(tmp_path: Path, size: int) -> None:
    path = _write_index(tmp_path, None, raw=b"{}" + b" " * (size - 2))
    sender = PhotonSender(tmp_path, runner=lambda *args, **kwargs: _result(_accepted("id")))
    assert path.stat().st_size == size
    assert sender(SAFE_TEXT) == {"provider_id": "id", "verified": False}


def test_index_byte_limit_rejects_above(tmp_path: Path) -> None:
    path = _write_index(tmp_path, None, raw=b"{}" + b" " * (INDEX_LIMIT - 2))
    sender = PhotonSender(tmp_path, runner=lambda *args, **kwargs: _result(_accepted("id")))
    path.write_bytes(b"{}" + b" " * (INDEX_LIMIT - 1))
    assert path.stat().st_size == INDEX_LIMIT + 1
    assert sender(SAFE_TEXT) == {"provider_id": "id", "verified": False}


def test_index_limit_counts_utf8_bytes_not_unicode_characters(tmp_path: Path) -> None:
    # Fewer than 16 Mi characters, but more than 16 MiB after UTF-8 expansion.
    raw = ('{"private:id":{"t":"' + ("🦎" * 4_194_300) + '"}}').encode("utf-8")
    assert len(raw) > INDEX_LIMIT
    _write_index(tmp_path, None, raw=raw)
    sender = PhotonSender(tmp_path, runner=lambda *args, **kwargs: _result(_accepted("id")))
    assert sender(SAFE_TEXT) == {"provider_id": "id", "verified": False}


def test_invalid_constructor_values_are_sanitized() -> None:
    class InvalidPath:
        def __fspath__(self) -> str:
            raise ValueError("PRIVATE path")

    invalid_runner: Any = object()

    with pytest.raises(DeliveryError, match="^invalid_delivery_configuration$"):
        PhotonSender(b"not-a-path")
    with pytest.raises(DeliveryError, match="^invalid_delivery_configuration$"):
        PhotonSender(InvalidPath())
    with pytest.raises(DeliveryError, match="^invalid_delivery_configuration$"):
        PhotonSender("/safe", runner=invalid_runner)
