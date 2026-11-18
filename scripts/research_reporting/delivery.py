"""Photon-home delivery with bounded local sender-record verification.

``verified`` means provider acceptance plus one exact local sender record.  It
never means recipient delivery, a read acknowledgement, or rendered-byte
identity.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any, Callable, NoReturn

_MAX_TEXT_CHARS = 2_000
_MAX_STDOUT_BYTES = 64 * 1024
_MAX_INDEX_BYTES = 16 * 1024 * 1024
_MAX_PROVIDER_ID_CHARS = 256
_COMMAND = ["hermes", "send", "--to", "photon", "--json", "--file", "-"]


class DeliveryError(Exception):
    """Sanitized public delivery failure."""


def _fail(category: str) -> NoReturn:
    raise DeliveryError(category) from None


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(value)


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _strict_json(raw: str | bytes) -> Any:
    return json.loads(
        raw,
        object_pairs_hook=_object_without_duplicates,
        parse_constant=_reject_constant,
    )


def _safe_identifier(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= _MAX_PROVIDER_ID_CHARS
        and not any(ord(character) < 32 for character in value)
    )


class PhotonSender:
    """Send through the selected Hermes home's fixed Photon CLI target."""

    def __init__(self, hermes_home: Any, *, runner: Callable[..., Any] | None = None):
        if isinstance(hermes_home, bytes):
            _fail("invalid_delivery_configuration")
        try:
            home = Path(hermes_home)
        except (TypeError, ValueError):
            _fail("invalid_delivery_configuration")
        selected_runner = subprocess.run if runner is None else runner
        if not callable(selected_runner):
            _fail("invalid_delivery_configuration")
        self.home = home
        self.runner = selected_runner

    def __call__(self, text: str) -> dict[str, Any]:
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > _MAX_TEXT_CHARS
            or "MEDIA:" in text.upper()
        ):
            _fail("invalid_delivery_input")

        try:
            response = self.runner(
                list(_COMMAND),
                input=text,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
                env=dict(os.environ, HERMES_HOME=str(self.home)),
            )
        except Exception:
            _fail("delivery_unconfirmed")

        identity = self._accepted_id(response)
        return {
            "provider_id": identity,
            "verified": self._record_matches(identity, text),
        }

    @staticmethod
    def _accepted_id(response: Any) -> str:
        try:
            stdout = response.stdout
            if (
                type(response.returncode) is not int
                or response.returncode != 0
                or not isinstance(stdout, str)
                or len(stdout.encode("utf-8")) > _MAX_STDOUT_BYTES
            ):
                _fail("delivery_unconfirmed")
            value = _strict_json(stdout)
            if (
                not isinstance(value, dict)
                or value.get("success") is not True
                or type(value.get("success")) is not bool
                or ("skipped" in value and type(value["skipped"]) is not bool)
                or value.get("skipped") is True
                or not _safe_identifier(value.get("message_id"))
            ):
                _fail("delivery_unconfirmed")
            return value["message_id"]
        except DeliveryError:
            raise
        except Exception:
            _fail("delivery_unconfirmed")

    def _record_matches(self, identity: str, text: str) -> bool:
        path = self.home / "state" / "rich_sent_index.json"
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            )
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode):
                    return False
                raw = os.read(descriptor, _MAX_INDEX_BYTES + 1)
            finally:
                os.close(descriptor)
            if len(raw) > _MAX_INDEX_BYTES:
                return False
            stored = _strict_json(raw.decode("utf-8-sig"))
            if not isinstance(stored, dict):
                return False
            matches = [
                entry
                for key, entry in stored.items()
                if isinstance(key, str) and key.endswith(":" + identity)
            ]
            return (
                len(matches) == 1
                and isinstance(matches[0], dict)
                and matches[0].get("t") == text
                and isinstance(matches[0].get("t"), str)
            )
        except Exception:
            return False
