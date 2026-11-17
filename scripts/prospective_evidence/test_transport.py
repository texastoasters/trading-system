"""Explicit fake-I/O fixtures for the GET-only prospective evidence transport."""
from __future__ import annotations

import hashlib
import io
import json
import runpy
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from prospective_evidence import transport
from prospective_evidence.transport import HttpGetTransport, SshAlpacaTransport, TransportError


BARS = {
    "symbols": "SPY,QQQ",
    "timeframe": "1Day",
    "start": "2026-11-01T00:00:00Z",
    "end": "2026-11-15T23:59:59Z",
    "feed": "iex",
    "adjustment": "raw",
    "sort": "asc",
    "limit": 1000,
    "currency": "USD",
    "asof": "-",
}
NEWS = {
    "symbols": "SPY",
    "start": "2026-11-01T00:00:00Z",
    "end": "2026-11-15T23:59:59Z",
    "sort": "asc",
    "limit": 50,
    "include_content": "false",
    "exclude_contentless": "false",
}
SESSIONS = {"start": "2026-11-01", "end": "2026-11-30"}


class FakeResponse:
    def __init__(self, body=b"{}", *, status=200, headers=None):
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.read_sizes = []
        self.closed = False

    def read(self, size=-1):
        self.read_sizes.append(size)
        return self.body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True


class RecordingOpener:
    def __init__(self, response=None, error=None):
        self.response = response or FakeResponse()
        self.error = error
        self.calls = []

    def open(self, request, *, timeout):
        self.calls.append((request, timeout))
        if self.error:
            raise self.error
        return self.response


def clocks():
    values = iter(
        [
            datetime(2026, 11, 17, 2, 5, tzinfo=timezone.utc),
            datetime(2026, 11, 17, 2, 5, 1, tzinfo=timezone.utc),
        ]
    )
    return lambda: next(values)


def test_http_fetch_uses_fixed_get_url_auth_bounds_clocks_hash_and_safe_rate_limits():
    body = b'{"bars":{"SPY":[{"c":123.4500}]}}'
    response = FakeResponse(
        body,
        headers={
            "X-RateLimit-Limit": "200",
            "X-RateLimit-Remaining": "199",
            "X-RateLimit-Reset": "1790000000",
            "Authorization": "must-not-be-retained",
            "X-RateLimit-Other": "7",
        },
    )
    opener = RecordingOpener(response)

    result = HttpGetTransport("key-value", "secret-value", opener=opener, clock=clocks()).fetch(
        "bars", dict(reversed(list(BARS.items())))
    )

    request, timeout = opener.calls[0]
    assert request.get_method() == "GET"
    assert request.full_url == (
        "https://data.alpaca.markets/v2/stocks/bars?symbols=SPY%2CQQQ&timeframe=1Day"
        "&start=2026-11-01T00%3A00%3A00Z&end=2026-11-15T23%3A59%3A59Z&feed=iex"
        "&adjustment=raw&sort=asc&limit=1000&currency=USD&asof=-"
    )
    assert request.get_header("Apca-api-key-id") == "key-value"
    assert request.get_header("Apca-api-secret-key") == "secret-value"
    assert timeout == 20
    assert response.read_sizes == [2_097_153]
    assert response.closed
    assert result == {
        "payload": {"bars": {"SPY": [{"c": "123.4500"}]}},
        "request_started_at": "2026-11-17T02:05:00+00:00",
        "received_at": "2026-11-17T02:05:01+00:00",
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "rate_limit": {"limit": 200, "remaining": 199, "reset": 1790000000},
    }
    assert "key-value" not in repr(result)
    assert "secret-value" not in repr(result)


def test_bea_fetch_has_no_query_or_credentials_and_returns_ics_text():
    body = b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
    opener = RecordingOpener(FakeResponse(body))

    result = HttpGetTransport("unused-key", None, opener=opener, clock=clocks()).fetch(
        "bea_calendar", {}
    )

    request, _ = opener.calls[0]
    assert request.full_url == transport.BEA_CALENDAR_URL
    assert request.header_items() == []
    assert result["payload"] == body.decode()
    assert result["rate_limit"] == {}


def test_sessions_and_news_use_their_only_fixed_origins():
    for endpoint, params, expected in [
        ("sessions", SESSIONS, "https://paper-api.alpaca.markets/v2/calendar?"),
        ("news", NEWS, "https://data.alpaca.markets/v1beta1/news?"),
    ]:
        opener = RecordingOpener()
        HttpGetTransport("key", "secret", opener=opener, clock=clocks()).fetch(endpoint, params)
        request, _ = opener.calls[0]
        assert request.full_url.startswith(expected)
        assert request.get_header("Apca-api-key-id") == "key"


@pytest.mark.parametrize(
    ("endpoint", "params"),
    [
        ("https://attacker.invalid", {}),
        ("BEA_CALENDAR", {}),
        ("bea_calendar", {"start": "2026-01-01"}),
        ("bars", {**BARS, "url": "https://attacker.invalid"}),
        ("bars", {**BARS, "symbols": ["SPY"]}),
        ("bars", {**BARS, "limit": True}),
        ("bars", {**BARS, "page_token": "x" * 2049}),
        ("bars", {**BARS, "feed": "sip"}),
        ("bars", {**BARS, "adjustment": "all"}),
        ("bars", {**BARS, "timeframe": "1Min"}),
        ("bars", {**BARS, "currency": "EUR"}),
        ("bars", {**BARS, "asof": "2026-11-01"}),
        ("bars", {**BARS, "sort": "sideways"}),
        ("bars", {**BARS, "limit": 10001}),
        ("bars", {**BARS, "start": "2026-11-16", "end": "2026-11-15"}),
        ("bars", {key: value for key, value in BARS.items() if key != "start"}),
        ("news", {**NEWS, "include_content": "true"}),
        ("news", {**NEWS, "exclude_contentless": "true"}),
        ("news", {**NEWS, "limit": 51}),
        ("sessions", {"start": "11/01/2026", "end": "2026-11-30"}),
        ("sessions", {"start": "2026-11-01", "end": ""}),
        ("sessions", {1: "not-a-string-key", "start": "2026-11-01", "end": "2026-11-30"}),
    ],
)
def test_routes_and_params_are_rejected_before_any_io(endpoint, params):
    opener = RecordingOpener()
    with pytest.raises(TransportError, match="^(invalid_endpoint|invalid_params)$") as caught:
        HttpGetTransport("key", "secret", opener=opener).fetch(endpoint, params)
    assert caught.value.code in {"invalid_endpoint", "invalid_params"}
    assert caught.value.http_status is None
    assert opener.calls == []


def test_optional_scalars_are_encoded_without_shell_interpretation():
    opener = RecordingOpener()
    params = {**NEWS, "page_token": "opaque;&$ token"}
    HttpGetTransport(opener=opener, clock=clocks()).fetch("news", params)
    assert "page_token=opaque%3B%26%24+token" in opener.calls[0][0].full_url


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"X-RateLimit-Limit": "not-numeric", "X-RateLimit-Remaining": "-1"}, {}),
        ({"X-RateLimit-Limit": str(2**63)}, {}),
        ({"X-RateLimit-Reset": "0007"}, {"reset": 7}),
    ],
)
def test_only_bounded_nonnegative_numeric_rate_headers_survive(headers, expected):
    opener = RecordingOpener(FakeResponse(headers=headers))
    result = HttpGetTransport(opener=opener, clock=clocks()).fetch("bea_calendar", {})
    assert result["rate_limit"] == expected


def test_oversized_body_is_rejected_without_exposing_it():
    secret_body = b"SECRET" + b"x" * 2_097_152
    opener = RecordingOpener(FakeResponse(secret_body))
    with pytest.raises(TransportError, match="^response_too_large$") as caught:
        HttpGetTransport(opener=opener, clock=clocks()).fetch("bea_calendar", {})
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize("body", [b"\xff", b"{bad json", b'{"n":NaN}', b'{"n":Infinity}'])
def test_invalid_utf8_json_and_nonfinite_json_are_sanitized(body):
    opener = RecordingOpener(FakeResponse(body))
    with pytest.raises(TransportError, match="^invalid_source_response$") as caught:
        HttpGetTransport(opener=opener, clock=clocks()).fetch("bars", BARS)
    decoded = body.decode("utf-8", "ignore")
    assert not decoded or decoded not in str(caught.value)


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_any_redirect_response_is_rejected_even_without_following(status):
    opener = RecordingOpener(FakeResponse(b"redirect body", status=status))
    with pytest.raises(TransportError, match="^source_http_error$") as caught:
        HttpGetTransport("key", "secret", opener=opener, clock=clocks()).fetch("bars", BARS)
    assert caught.value.http_status == status


def test_http_error_does_not_read_or_retain_error_body_url_or_credentials():
    error = HTTPError(
        "https://secret:credential@attacker.invalid/path",
        401,
        "body says key-value",
        {},
        io.BytesIO(b"secret error body"),
    )
    opener = RecordingOpener(error=error)
    with pytest.raises(TransportError, match="^source_http_error$") as caught:
        HttpGetTransport("key-value", "secret-value", opener=opener, clock=clocks()).fetch("bars", BARS)
    assert caught.value.http_status == 401
    assert str(caught.value) == "source_http_error"
    assert error.fp.tell() == 0


@pytest.mark.parametrize("error", [URLError("credential detail"), socket.timeout("secret"), OSError("secret")])
def test_network_failures_are_sanitized(error):
    opener = RecordingOpener(error=error)
    with pytest.raises(TransportError, match="^source_unavailable$") as caught:
        HttpGetTransport(opener=opener, clock=clocks()).fetch("bea_calendar", {})
    assert "secret" not in str(caught.value)


def test_unexpected_opener_failure_is_also_sanitized():
    opener = RecordingOpener(error=RuntimeError("secret unexpected detail"))
    with pytest.raises(TransportError, match="^source_unavailable$"):
        HttpGetTransport(opener=opener, clock=clocks()).fetch("bea_calendar", {})


def test_default_opener_has_redirects_disabled(monkeypatch):
    captured = {}

    class FakeBuiltOpener:
        def open(self, request, *, timeout):
            captured["request"] = request
            return FakeResponse(b"calendar")

    def fake_build(handler):
        captured["handler"] = handler
        return FakeBuiltOpener()

    monkeypatch.setattr(transport.request, "build_opener", fake_build)
    HttpGetTransport(clock=clocks()).fetch("bea_calendar", {})
    assert isinstance(captured["handler"], transport._NoRedirectHandler)


def test_clock_must_return_aware_datetime():
    opener = RecordingOpener()
    with pytest.raises(TransportError, match="^invalid_clock$"):
        HttpGetTransport(opener=opener, clock=lambda: datetime(2026, 1, 1)).fetch(
            "bea_calendar", {}
        )


def valid_wire_result():
    return {
        "payload": {"bars": []},
        "request_started_at": "2026-11-17T02:05:00+00:00",
        "received_at": "2026-11-17T02:05:01+00:00",
        "body_sha256": "0" * 64,
        "rate_limit": {"remaining": 199},
    }


class Runner:
    def __init__(self, completed):
        self.completed = completed
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return self.completed


def completed(stdout, returncode=0, stderr="ignored secret stderr"):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def test_ssh_adapter_validates_then_uses_one_fixed_argv_and_bounded_json_stdin():
    wire = json.dumps({"ok": True, "result": valid_wire_result()})
    runner = Runner(completed(wire))

    result = SshAlpacaTransport(runner=runner).fetch("bars", BARS)

    assert result == valid_wire_result()
    argv, kwargs = runner.calls[0]
    assert argv == [
        "ssh",
        "-T",
        "-oBatchMode=yes",
        "-oConnectTimeout=10",
        "openboog",
        "python3",
        "~/.hermes/cache/scratch/t2_evidence_get.py",
    ]
    assert kwargs == {
        "input": json.dumps({"endpoint": "bars", "params": BARS}, separators=(",", ":")),
        "text": True,
        "capture_output": True,
        "timeout": 30,
        "check": False,
    }
    assert ";" not in argv


@pytest.mark.parametrize("endpoint,params", [("bea_calendar", {}), ("bad", {}), ("bars", {**BARS, "url": "x"})])
def test_ssh_rejects_routes_and_params_before_runner(endpoint, params):
    runner = Runner(completed(""))
    with pytest.raises(TransportError, match="^(invalid_endpoint|invalid_params)$"):
        SshAlpacaTransport(runner=runner).fetch(endpoint, params)
    assert runner.calls == []


@pytest.mark.parametrize(
    "result",
    [
        completed("", 255),
        completed("not json"),
        completed(json.dumps({"ok": True, "result": {"payload": "incomplete"}})),
        completed(json.dumps({"ok": False, "code": "remote traceback: secret", "http_status": None})),
        completed("x" * (transport.MAX_SSH_STDOUT_BYTES + 1)),
        completed("\ud800"),
    ],
)
def test_ssh_failures_and_untrusted_remote_output_are_one_sanitized_error(result):
    runner = Runner(result)
    with pytest.raises(TransportError, match="^ssh_source_unavailable$") as caught:
        SshAlpacaTransport(runner=runner).fetch("sessions", SESSIONS)
    assert "secret" not in str(caught.value)


def test_ssh_runner_timeout_or_oserror_is_sanitized():
    for error in [subprocess.TimeoutExpired("ssh", 30, stderr="secret"), OSError("secret")]:
        def broken(*_args, **_kwargs):
            raise error
        with pytest.raises(TransportError, match="^ssh_source_unavailable$"):
            SshAlpacaTransport(runner=broken).fetch("news", NEWS)


def test_allowlisted_zero_status_wire_error_is_recreated_without_remote_text():
    runner = Runner(completed(json.dumps({"ok": False, "code": "source_http_error", "http_status": 429})))
    with pytest.raises(TransportError, match="^source_http_error$") as caught:
        SshAlpacaTransport(runner=runner).fetch("news", NEWS)
    assert caught.value.http_status == 429


def invoke_main(monkeypatch, tmp_path, request_value, *, env_text=None):
    if env_text is not None:
        (tmp_path / ".trading_env").write_text(env_text)
    stdin = io.TextIOWrapper(io.BytesIO(json.dumps(request_value).encode()), encoding="utf-8")
    stdout_bytes = io.BytesIO()
    stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(transport.Path, "home", classmethod(lambda cls: tmp_path))
    code = transport.main([])
    stdout.flush()
    return code, json.loads(stdout_bytes.getvalue())


def test_nonzero_remote_failure_preserves_safe_code_and_status():
    runner = Runner(completed(json.dumps({"ok": False, "code": "source_http_error", "http_status": 403}), returncode=1))
    with pytest.raises(TransportError, match="^source_http_error$") as caught:
        SshAlpacaTransport(runner=runner).fetch("sessions", SESSIONS)
    assert caught.value.http_status == 403


def test_nonzero_remote_process_cannot_claim_success():
    runner = Runner(completed(json.dumps({"ok": True, "result": valid_wire_result()}), returncode=1))
    with pytest.raises(TransportError, match="^ssh_source_unavailable$"):
        SshAlpacaTransport(runner=runner).fetch("sessions", SESSIONS)


def test_remote_plain_literal_assignments_match_actual_env_format(monkeypatch, tmp_path):
    def fake_fetch(self, endpoint, params):
        assert (self.key, self.secret) == ("fixture-key", "fixture-secret")
        return valid_wire_result()
    monkeypatch.setattr(HttpGetTransport, "fetch", fake_fetch)
    code, result = invoke_main(monkeypatch, tmp_path, {"endpoint": "sessions", "params": SESSIONS},
        env_text="ALPACA_API_KEY=fixture-key\nALPACA_SECRET_KEY=fixture-secret\n")
    assert code == 0
    assert result["ok"] is True
    assert "fixture-secret" not in json.dumps(result)


@pytest.mark.parametrize("code", [[], {}, None])
def test_malformed_remote_error_code_cannot_escape_sanitization(code):
    runner = Runner(completed(json.dumps({"ok": False, "code": code, "http_status": None})))
    with pytest.raises(TransportError, match="^ssh_source_unavailable$"):
        SshAlpacaTransport(runner=runner).fetch("sessions", SESSIONS)


def test_remote_main_validates_route_before_touching_env(monkeypatch, tmp_path):
    code, result = invoke_main(monkeypatch, tmp_path, {"endpoint": "bea_calendar", "params": {}})
    assert code == 1
    assert result == {"ok": False, "code": "invalid_endpoint", "http_status": None}


def test_remote_main_reports_missing_env_without_network(monkeypatch, tmp_path):
    code, result = invoke_main(monkeypatch, tmp_path, {"endpoint": "sessions", "params": SESSIONS})
    assert code == 1
    assert result == {"ok": False, "code": "missing_credentials", "http_status": None}


def test_remote_main_safely_parses_only_literal_required_exports(monkeypatch, tmp_path):
    observed = {}

    def fake_fetch(self, endpoint, params):
        observed.update(key=self.key, secret=self.secret, endpoint=endpoint, params=params)
        return valid_wire_result()

    monkeypatch.setattr(HttpGetTransport, "fetch", fake_fetch)
    env = """
# ignored
export OTHER_KEY=not-used
export ALPACA_API_KEY='literal key'
source /tmp/attacker
export ALPACA_SECRET_KEY="literal secret"
export ALPACA_API_KEY_EXTRA=ignored
"""
    code, result = invoke_main(
        monkeypatch, tmp_path, {"endpoint": "news", "params": NEWS}, env_text=env
    )
    assert code == 0
    assert result == {"ok": True, "result": valid_wire_result()}
    assert observed == {"key": "literal key", "secret": "literal secret", "endpoint": "news", "params": NEWS}


def test_remote_main_sanitizes_transport_failure(monkeypatch, tmp_path):
    def fail(*_args, **_kwargs):
        raise TransportError("source_http_error", http_status=503)

    monkeypatch.setattr(HttpGetTransport, "fetch", fail)
    env = "export ALPACA_API_KEY=k\nexport ALPACA_SECRET_KEY=s\n"
    code, result = invoke_main(
        monkeypatch, tmp_path, {"endpoint": "bars", "params": BARS}, env_text=env
    )
    assert code == 1
    assert result == {"ok": False, "code": "source_http_error", "http_status": 503}


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b'{"endpoint":"sessions","endpoint":"bars","params":{}}',
        b"x" * (transport.MAX_STDIN_BYTES + 1),
    ],
)
def test_remote_main_rejects_invalid_duplicate_or_oversized_stdin(monkeypatch, tmp_path, raw):
    stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
    stdout_bytes = io.BytesIO()
    stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(transport.Path, "home", classmethod(lambda cls: tmp_path))
    assert transport.main([]) == 1
    stdout.flush()
    assert json.loads(stdout_bytes.getvalue()) == {
        "ok": False,
        "code": "invalid_request",
        "http_status": None,
    }


def test_remote_main_rejects_arguments_and_unsafe_or_oversized_env(monkeypatch, tmp_path):
    code, result = invoke_main(monkeypatch, tmp_path, {"endpoint": "sessions", "params": SESSIONS})
    assert (code, result["code"]) == (1, "missing_credentials")

    for env in [
        "export ALPACA_API_KEY=k\nexport ALPACA_SECRET_KEY='unterminated\n",
        "x" * (transport.MAX_ENV_BYTES + 1),
        "export ALPACA_API_KEY=\nexport ALPACA_SECRET_KEY=s\n",
    ]:
        code, result = invoke_main(
            monkeypatch, tmp_path, {"endpoint": "sessions", "params": SESSIONS}, env_text=env
        )
        assert (code, result["code"]) == (1, "missing_credentials")

    assert transport.main(["unexpected"]) == 1


def test_additional_validation_and_sanitization_edges_do_not_escape_public_errors():
    invalid_cases = [
        ("bars", []),
        ("bars", {**BARS, "symbols": "spy"}),
        ("bars", {**BARS, "symbols": "SPY,SPY"}),
        ("bars", {**BARS, "page_token": "bad\nvalue"}),
        ("bars", {**BARS, "start": "2026-13-01", "end": "2026-11-15"}),
        ("bars", {**BARS, "start": "2026-11-01T00:00:00", "end": "2026-11-15T00:00:00Z"}),
        ("sessions", {**SESSIONS, "end": "2026-99-99"}),
        ("sessions", {**SESSIONS, "date_type": "OTHER"}),
    ]
    for endpoint, params in invalid_cases:
        with pytest.raises(TransportError):
            HttpGetTransport().fetch(endpoint, params)

    for key, secret in [("key", None), ("", "secret"), ("key\n", "secret")]:
        with pytest.raises(TransportError, match="^invalid_credentials$"):
            HttpGetTransport(key, secret).fetch("sessions", SESSIONS)


def test_redirect_handler_clock_failure_and_response_shape_edges_are_sanitized():
    assert transport._NoRedirectHandler().redirect_request(None, None, 302, "", {}, "x") is None

    def broken_clock():
        raise RuntimeError("secret clock detail")

    with pytest.raises(TransportError, match="^invalid_clock$"):
        HttpGetTransport(opener=RecordingOpener(), clock=broken_clock).fetch("bea_calendar", {})

    class GetCodeResponse(FakeResponse):
        status = None

        def getcode(self):
            return 200

    good_getcode = GetCodeResponse()
    good_getcode.status = None
    HttpGetTransport(opener=RecordingOpener(good_getcode), clock=clocks()).fetch(
        "bea_calendar", {}
    )

    class BadGetCodeResponse(FakeResponse):
        def getcode(self):
            raise RuntimeError("secret")

    bad_getcode = BadGetCodeResponse()
    bad_getcode.status = None
    with pytest.raises(TransportError, match="^source_http_error$"):
        HttpGetTransport(opener=RecordingOpener(bad_getcode), clock=clocks()).fetch(
            "bea_calendar", {}
        )

    class NonBytesResponse(FakeResponse):
        def read(self, size=-1):
            return "not bytes"

    with pytest.raises(TransportError, match="^invalid_source_response$"):
        HttpGetTransport(opener=RecordingOpener(NonBytesResponse()), clock=clocks()).fetch(
            "bea_calendar", {}
        )

    class HeaderFailure:
        def get(self, _name):
            raise RuntimeError("secret header")

    result = HttpGetTransport(
        opener=RecordingOpener(FakeResponse(headers=HeaderFailure())), clock=clocks()
    ).fetch("bea_calendar", {})
    assert result["rate_limit"] == {}


def test_ssh_strictly_validates_every_success_and_failure_wire_field():
    invalid_successes = []
    for field, replacement in [
        ("request_started_at", 1),
        ("received_at", "not-a-date"),
        ("body_sha256", "xyz"),
        ("rate_limit", {"other": 1}),
        ("rate_limit", {"limit": True}),
    ]:
        value = valid_wire_result()
        value[field] = replacement
        invalid_successes.append(completed(json.dumps({"ok": True, "result": value})))
    invalid_successes.extend(
        [
            completed(json.dumps([])),
            completed(json.dumps({"ok": "true"})),
            completed(json.dumps({"ok": False, "code": "source_unavailable"})),
            completed(json.dumps({"ok": False, "code": "source_unavailable", "http_status": True})),
        ]
    )
    for wire in invalid_successes:
        with pytest.raises(TransportError, match="^ssh_source_unavailable$"):
            SshAlpacaTransport(runner=Runner(wire)).fetch("sessions", SESSIONS)


def test_remote_request_shape_env_io_and_unexpected_failure_are_sanitized(monkeypatch, tmp_path):
    code, result = invoke_main(
        monkeypatch,
        tmp_path,
        {"endpoint": "sessions", "params": SESSIONS, "extra": True},
    )
    assert (code, result["code"]) == (1, "invalid_request")

    env_path = tmp_path / ".trading_env"
    env_path.write_text("x" * (transport.MAX_ENV_BYTES + 1))
    code, result = invoke_main(monkeypatch, tmp_path, {"endpoint": "sessions", "params": SESSIONS})
    assert (code, result["code"]) == (1, "missing_credentials")

    monkeypatch.setattr(transport, "_read_remote_request", lambda: (_ for _ in ()).throw(RuntimeError("secret")))
    code, result = invoke_main(monkeypatch, tmp_path, {"endpoint": "sessions", "params": SESSIONS})
    assert (code, result) == (
        1,
        {"ok": False, "code": "source_unavailable", "http_status": None},
    )


def test_remaining_bounded_input_branches_are_covered(monkeypatch, tmp_path):
    with pytest.raises(TransportError, match="^invalid_params$"):
        HttpGetTransport().fetch("sessions", {"start": "2026-11-01", "end": "bad"})
    assert transport._safe_rate_limit(None) == {}

    monkeypatch.setattr(transport, "MAX_STDIN_BYTES", 1)
    with pytest.raises(TransportError, match="^invalid_params$"):
        SshAlpacaTransport(runner=Runner(completed(""))).fetch("sessions", SESSIONS)

    monkeypatch.setattr(transport, "MAX_STDIN_BYTES", 64 * 1024)
    monkeypatch.setattr(
        transport,
        "_validate_params",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TransportError("invalid_clock")),
    )
    stdin = io.TextIOWrapper(
        io.BytesIO(json.dumps({"endpoint": "sessions", "params": SESSIONS}).encode()),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "stdin", stdin)
    with pytest.raises(TransportError, match="^invalid_request$"):
        transport._read_remote_request()

    monkeypatch.undo()

    class FakeEnvPath:
        def stat(self):
            return type("S", (), {"st_size": 0})()

        def is_file(self):
            return True

        def read_bytes(self):
            return b"x" * (transport.MAX_ENV_BYTES + 1)

    class FakeHome:
        def __truediv__(self, _name):
            return FakeEnvPath()

    monkeypatch.setattr(transport.Path, "home", classmethod(lambda cls: FakeHome()))
    with pytest.raises(TransportError, match="^missing_credentials$"):
        transport._load_remote_credentials()


def test_standalone_main_guard_emits_sanitized_invalid_request(monkeypatch):
    stdin = io.TextIOWrapper(io.BytesIO(b"{}"), encoding="utf-8")
    stdout_bytes = io.BytesIO()
    stdout = io.TextIOWrapper(stdout_bytes, encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    with pytest.raises(SystemExit) as caught:
        runpy.run_path(str(Path(transport.__file__)), run_name="__main__")
    stdout.flush()
    assert caught.value.code == 1
    assert json.loads(stdout_bytes.getvalue())["code"] == "invalid_request"
