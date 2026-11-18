"""Bounded GET transports for prospective evidence sources.

This module intentionally depends only on the Python standard library.  It can
be copied to a remote host and executed directly as a stdin/stdout JSON helper.
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
import socket
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import NoReturn
from urllib import error, parse, request


BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
SESSIONS_URL = "https://paper-api.alpaca.markets/v2/calendar"
BEA_CALENDAR_URL = (
    "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics"
)

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_STDIN_BYTES = 64 * 1024
MAX_SSH_STDOUT_BYTES = 3 * 1024 * 1024
MAX_ENV_BYTES = 64 * 1024
HTTP_TIMEOUT_SECONDS = 20
SSH_TIMEOUT_SECONDS = 30
MAX_PARAM_STRING = 2048
MAX_RATE_LIMIT_VALUE = (1 << 63) - 1

_ENDPOINTS = {
    "bars": BARS_URL,
    "news": NEWS_URL,
    "sessions": SESSIONS_URL,
    "bea_calendar": BEA_CALENDAR_URL,
}
_ALPACA_ENDPOINTS = frozenset(("bars", "news", "sessions"))
_PARAM_ORDER = {
    "bars": (
        "symbols",
        "timeframe",
        "start",
        "end",
        "feed",
        "adjustment",
        "sort",
        "limit",
        "currency",
        "asof",
        "page_token",
    ),
    "news": (
        "symbols",
        "start",
        "end",
        "sort",
        "limit",
        "include_content",
        "exclude_contentless",
        "page_token",
    ),
    "sessions": ("start", "end", "date_type"),
    "bea_calendar": (),
}
_REQUIRED = {
    "bars": frozenset(
        (
            "symbols",
            "timeframe",
            "start",
            "end",
            "feed",
            "adjustment",
            "sort",
            "limit",
            "currency",
            "asof",
        )
    ),
    "news": frozenset(
        (
            "symbols",
            "start",
            "end",
            "sort",
            "limit",
            "include_content",
            "exclude_contentless",
        )
    ),
    "sessions": frozenset(("start", "end")),
    "bea_calendar": frozenset(),
}
_ERROR_CODES = frozenset(
    (
        "invalid_endpoint",
        "invalid_params",
        "invalid_credentials",
        "invalid_clock",
        "response_too_large",
        "source_http_error",
        "source_unavailable",
        "invalid_source_response",
        "ssh_source_unavailable",
        "invalid_request",
        "missing_credentials",
    )
)
_REMOTE_ERROR_CODES = frozenset(
    (
        "missing_credentials",
        "source_http_error",
        "source_unavailable",
        "invalid_source_response",
        "response_too_large",
        "invalid_clock",
        "invalid_credentials",
    )
)
_SYMBOLS_RE = re.compile(r"[A-Z0-9][A-Z0-9./-]*(?:,[A-Z0-9][A-Z0-9./-]*)*")
_HEX_256_RE = re.compile(r"[0-9a-f]{64}")
_SSH_ARGV = [
    "ssh",
    "-T",
    "-oBatchMode=yes",
    "-oConnectTimeout=10",
    "openboog",
    "python3",
    "~/.local/share/t2-research/t2_evidence_get.py",
]


class TransportError(ValueError):
    """A deliberately small, sanitized transport failure."""

    def __init__(self, code: str, *, http_status: int | None = None):
        safe_code = code if isinstance(code, str) and code in _ERROR_CODES else "source_unavailable"
        self.code = safe_code
        self.http_status = (
            http_status
            if isinstance(http_status, int)
            and not isinstance(http_status, bool)
            and 100 <= http_status <= 599
            else None
        )
        super().__init__(safe_code)


class _NoRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fail(code: str, *, status: int | None = None) -> NoReturn:
    raise TransportError(code, http_status=status)


def _strict_json(text: str):
    def reject_constant(_value):
        raise ValueError("non-finite JSON number")

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    return json.loads(
        text,
        parse_float=str,
        parse_constant=reject_constant,
        object_pairs_hook=unique_object,
    )


def _validate_string(value, *, nonempty=True):
    if not isinstance(value, str) or len(value) > MAX_PARAM_STRING:
        _fail("invalid_params")
    if nonempty and not value:
        _fail("invalid_params")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _fail("invalid_params")


def _parse_window_value(value: str):
    _validate_string(value)
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return datetime.combine(date.fromisoformat(value), datetime.min.time(), timezone.utc)
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        _fail("invalid_params")


def _validate_window(params, *, dates_only=False):
    start = params["start"]
    end = params["end"]
    if dates_only:
        _validate_string(start)
        _validate_string(end)
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start):
                raise ValueError
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
                raise ValueError
            start_value = date.fromisoformat(start)
            end_value = date.fromisoformat(end)
        except (TypeError, ValueError):
            _fail("invalid_params")
    else:
        start_value = _parse_window_value(start)
        end_value = _parse_window_value(end)
    if start_value > end_value:
        _fail("invalid_params")


def _validate_symbols(value):
    _validate_string(value)
    if not _SYMBOLS_RE.fullmatch(value):
        _fail("invalid_params")
    symbols = value.split(",")
    if len(symbols) != len(set(symbols)):
        _fail("invalid_params")


def _validate_params(endpoint, params, *, ssh=False):
    allowed_endpoints = _ALPACA_ENDPOINTS if ssh else _ENDPOINTS
    if not isinstance(endpoint, str) or endpoint not in allowed_endpoints:
        _fail("invalid_endpoint")
    if not isinstance(params, dict):
        _fail("invalid_params")
    if any(not isinstance(key, str) for key in params):
        _fail("invalid_params")
    allowed = frozenset(_PARAM_ORDER[endpoint])
    keys = frozenset(params)
    if not _REQUIRED[endpoint].issubset(keys) or not keys.issubset(allowed):
        _fail("invalid_params")
    for value in params.values():
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            _fail("invalid_params")
        if isinstance(value, str):
            _validate_string(value)
    if endpoint == "bea_calendar":
        return {}
    if endpoint in ("bars", "news"):
        _validate_symbols(params["symbols"])
        _validate_window(params)
        if params["sort"] not in ("asc", "desc"):
            _fail("invalid_params")
        if "page_token" in params:
            _validate_string(params["page_token"])
    if endpoint == "bars":
        if (
            params["timeframe"] != "1Day"
            or params["feed"] != "iex"
            or params["adjustment"] != "raw"
            or params["currency"] != "USD"
            or params["asof"] != "-"
        ):
            _fail("invalid_params")
        if not isinstance(params["limit"], int) or not 1 <= params["limit"] <= 10_000:
            _fail("invalid_params")
    elif endpoint == "news":
        if (
            params["include_content"] != "false"
            or params["exclude_contentless"] != "false"
        ):
            _fail("invalid_params")
        if not isinstance(params["limit"], int) or not 1 <= params["limit"] <= 50:
            _fail("invalid_params")
    elif endpoint == "sessions":
        _validate_window(params, dates_only=True)
        if "date_type" in params and params["date_type"] not in ("TRADING", "SETTLEMENT"):
            _fail("invalid_params")
    return {name: params[name] for name in _PARAM_ORDER[endpoint] if name in params}


def _validate_credentials(key, secret):
    if key is None and secret is None:
        return
    if not isinstance(key, str) or not isinstance(secret, str):
        _fail("invalid_credentials")
    if not key or not secret or len(key) > 4096 or len(secret) > 4096:
        _fail("invalid_credentials")
    if "\r" in key or "\n" in key or "\r" in secret or "\n" in secret:
        _fail("invalid_credentials")


def _clock_iso(clock):
    try:
        value = clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            _fail("invalid_clock")
        return value.astimezone(timezone.utc).isoformat()
    except TransportError:
        raise
    except Exception:
        _fail("invalid_clock")


def _status_of(response):
    status = getattr(response, "status", None)
    if status is None:
        try:
            status = response.getcode()
        except Exception:
            return None
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def _safe_rate_limit(headers):
    result = {}
    if headers is None:
        return result
    for output_name, header_name in (
        ("limit", "X-RateLimit-Limit"),
        ("remaining", "X-RateLimit-Remaining"),
        ("reset", "X-RateLimit-Reset"),
    ):
        try:
            raw = headers.get(header_name)
        except Exception:
            raw = None
        if isinstance(raw, str) and raw.isascii() and raw.isdecimal():
            value = int(raw)
            if value <= MAX_RATE_LIMIT_VALUE:
                result[output_name] = value
    return result


class HttpGetTransport:
    """GET a named source without accepting caller-controlled URLs or methods."""

    def __init__(self, key=None, secret=None, *, opener=None, clock=None):
        self.key = key
        self.secret = secret
        self._opener = opener
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def fetch(self, endpoint, params):
        ordered_params = _validate_params(endpoint, params)
        if endpoint in _ALPACA_ENDPOINTS:
            _validate_credentials(self.key, self.secret)
        url = _ENDPOINTS[endpoint]
        if ordered_params:
            url += "?" + parse.urlencode(ordered_params)
        headers = {}
        if endpoint in _ALPACA_ENDPOINTS and self.key is not None:
            headers = {
                "APCA-API-KEY-ID": self.key,
                "APCA-API-SECRET-KEY": self.secret,
            }
        outgoing = request.Request(url, headers=headers, method="GET")
        started_at = _clock_iso(self._clock)
        opener = self._opener or request.build_opener(_NoRedirectHandler())
        try:
            open_method = getattr(opener, "open", opener)
            with open_method(outgoing, timeout=HTTP_TIMEOUT_SECONDS) as response:
                status = _status_of(response)
                if status is None or not 200 <= status <= 299:
                    _fail("source_http_error", status=status)
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if not isinstance(body, bytes):
                    _fail("invalid_source_response")
                if len(body) > MAX_RESPONSE_BYTES:
                    _fail("response_too_large")
                rate_limit = _safe_rate_limit(getattr(response, "headers", None))
        except TransportError:
            raise
        except error.HTTPError as caught:
            _fail("source_http_error", status=getattr(caught, "code", None))
        except (error.URLError, socket.timeout, TimeoutError, OSError):
            _fail("source_unavailable")
        except Exception:
            _fail("source_unavailable")
        received_at = _clock_iso(self._clock)
        try:
            text = body.decode("utf-8")
            payload = text if endpoint == "bea_calendar" else _strict_json(text)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            _fail("invalid_source_response")
        return {
            "payload": payload,
            "request_started_at": started_at,
            "received_at": received_at,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "rate_limit": rate_limit,
        }


def _valid_timestamp(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _validate_wire_result(value):
    if not isinstance(value, dict) or set(value) != {
        "payload",
        "request_started_at",
        "received_at",
        "body_sha256",
        "rate_limit",
    }:
        return False
    if not _valid_timestamp(value["request_started_at"]) or not _valid_timestamp(
        value["received_at"]
    ):
        return False
    if not isinstance(value["body_sha256"], str) or not _HEX_256_RE.fullmatch(
        value["body_sha256"]
    ):
        return False
    limits = value["rate_limit"]
    if not isinstance(limits, dict) or not set(limits).issubset(
        {"limit", "remaining", "reset"}
    ):
        return False
    return all(
        isinstance(item, int)
        and not isinstance(item, bool)
        and 0 <= item <= MAX_RATE_LIMIT_VALUE
        for item in limits.values()
    )


class SshAlpacaTransport:
    """Invoke the fixed helper and bound availability by the local SSH call.

    Public request/receipt times describe the caller-local SSH call lifetime,
    not exact upstream HTTP dispatch, completion, or source publication.
    """

    def __init__(self, *, runner=None, clock=None):
        self._runner = runner or subprocess.run
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def fetch(self, endpoint, params):
        ordered_params = _validate_params(endpoint, params, ssh=True)
        wire_input = json.dumps(
            {"endpoint": endpoint, "params": ordered_params}, separators=(",", ":")
        )
        started_at = _clock_iso(self._clock)
        try:
            completed = self._runner(
                list(_SSH_ARGV),
                input=wire_input,
                text=True,
                capture_output=True,
                timeout=SSH_TIMEOUT_SECONDS,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError):
            _fail("ssh_source_unavailable")
        returncode = getattr(completed, "returncode", None)
        if type(returncode) is not int or returncode not in (0, 1):
            _fail("ssh_source_unavailable")
        stdout = getattr(completed, "stdout", None)
        try:
            stdout_size = len(stdout.encode("utf-8")) if isinstance(stdout, str) else None
        except UnicodeError:
            _fail("ssh_source_unavailable")
        if stdout_size is None or stdout_size > MAX_SSH_STDOUT_BYTES:
            _fail("ssh_source_unavailable")
        try:
            envelope = _strict_json(stdout)
        except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            _fail("ssh_source_unavailable")
        if not isinstance(envelope, dict) or type(envelope.get("ok")) is not bool:
            _fail("ssh_source_unavailable")
        if envelope["ok"] is True:
            if returncode != 0:
                _fail("ssh_source_unavailable")
            if set(envelope) != {"ok", "result"} or not _validate_wire_result(
                envelope["result"]
            ):
                _fail("ssh_source_unavailable")
            received_at = _clock_iso(self._clock)
            if _parse_window_value(received_at) < _parse_window_value(started_at):
                _fail("invalid_clock")
            return {
                **envelope["result"],
                "request_started_at": started_at,
                "received_at": received_at,
            }
        if set(envelope) != {"ok", "code", "http_status"}:
            _fail("ssh_source_unavailable")
        code = envelope["code"]
        status = envelope["http_status"]
        if not isinstance(code, str) or code not in _REMOTE_ERROR_CODES:
            _fail("ssh_source_unavailable")
        if status is not None and (
            not isinstance(status, int)
            or isinstance(status, bool)
            or not 100 <= status <= 599
        ):
            _fail("ssh_source_unavailable")
        raise TransportError(code, http_status=status)


def _read_remote_request():
    try:
        raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
        if len(raw) > MAX_STDIN_BYTES:
            raise ValueError
        value = _strict_json(raw.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        _fail("invalid_request")
    if not isinstance(value, dict) or set(value) != {"endpoint", "params"}:
        _fail("invalid_request")
    params = _validate_params(value["endpoint"], value["params"], ssh=True)
    return value["endpoint"], params


def _load_remote_credentials():
    path = Path.home() / ".trading_env"
    try:
        if path.stat().st_size > MAX_ENV_BYTES or not path.is_file():
            _fail("missing_credentials")
        raw = path.read_bytes()
        if len(raw) > MAX_ENV_BYTES:
            _fail("missing_credentials")
        text = raw.decode("utf-8")
    except TransportError:
        raise
    except (OSError, UnicodeError):
        _fail("missing_credentials")
    found = {}
    malformed_required = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            parts = shlex.split(stripped, comments=True, posix=True)
        except ValueError:
            if "ALPACA_API_KEY" in stripped or "ALPACA_SECRET_KEY" in stripped:
                malformed_required = True
            continue
        if len(parts) == 2 and parts[0] == "export":
            parts = parts[1:]
        if len(parts) != 1 or "=" not in parts[0]:
            continue
        name, value = parts[0].split("=", 1)
        if name in ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"):
            if name in found or not value or len(value) > 4096:
                malformed_required = True
            else:
                found[name] = value
    if malformed_required or set(found) != {"ALPACA_API_KEY", "ALPACA_SECRET_KEY"}:
        _fail("missing_credentials")
    _validate_credentials(found["ALPACA_API_KEY"], found["ALPACA_SECRET_KEY"])
    return found["ALPACA_API_KEY"], found["ALPACA_SECRET_KEY"]


def _write_envelope(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n")
    sys.stdout.flush()


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        _write_envelope({"ok": False, "code": "invalid_request", "http_status": None})
        return 1
    try:
        endpoint, params = _read_remote_request()
        key, secret = _load_remote_credentials()
        result = HttpGetTransport(key, secret).fetch(endpoint, params)
        _write_envelope({"ok": True, "result": result})
        return 0
    except TransportError as caught:
        code = caught.code if caught.code in _ERROR_CODES else "source_unavailable"
        _write_envelope({"ok": False, "code": code, "http_status": caught.http_status})
        return 1
    except Exception:
        _write_envelope({"ok": False, "code": "source_unavailable", "http_status": None})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
