"""Vertical public-seam tests for the durable notification outbox."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from research_reporting.outbox import OutboxError, cycle

NOW = datetime.fromisoformat("2026-11-17T21:00:00-06:00")
BEFORE_DAILY = datetime.fromisoformat("2026-11-17T18:00:00-06:00")


def assessment(**changes: Any) -> dict[str, Any]:
    value = {
        "summary": "Research: one checkpoint; two pending horizons. No orders. Billed cost unknown.",
        "event_findings": [],
        "conditions": {},
        "indeterminate_conditions": [],
        "busy": False,
    }
    value.update(changes)
    return value


def accepted_sender(received: list[str]):
    def send(text: str) -> dict[str, Any]:
        received.append(text)
        return {"provider_id": f"provider-{len(received)}", "verified": True}
    return send


def test_daily_report_is_submitted_once_across_restart(tmp_path: Path) -> None:
    submitted: list[str] = []
    send = accepted_sender(submitted)

    first = cycle(tmp_path / "reporter", "study-a", assessment(), send, now=NOW)
    second = cycle(tmp_path / "reporter", "study-a", assessment(), send, now=NOW)

    assert submitted == [assessment()["summary"]]
    assert first == {
        "status": "processed", "executable": False, "accepted_count": 1,
        "in_doubt_count": 0, "pending_count": 0, "expired_count": 0,
        "submitted_this_cycle": 1,
    }
    assert second["accepted_count"] == 1
    assert second["submitted_this_cycle"] == 0


def test_daily_boundary_busy_deferral_and_dst_local_clock(tmp_path: Path) -> None:
    submitted: list[str] = []
    send = accepted_sender(submitted)
    root = tmp_path / "reporter"
    at_1929 = datetime(2026, 3, 8, 19, 29, tzinfo=timezone(timedelta(hours=-5)))
    at_1930 = at_1929 + timedelta(minutes=1)

    assert cycle(root, "study-a", assessment(), send, now=at_1929)["submitted_this_cycle"] == 0
    assert cycle(root, "study-a", assessment(busy=True), send,
                 now=at_1930)["submitted_this_cycle"] == 0
    result = cycle(root, "study-a", assessment(), send, now=at_1930 + timedelta(minutes=5))

    assert result["submitted_this_cycle"] == 1
    assert len(submitted) == 1


def test_failure_events_are_claimed_before_submission_and_aggregated(tmp_path: Path) -> None:
    submitted: list[str] = []
    send = accepted_sender(submitted)
    findings = [
        {"key": "study-a:event:missed-2026-11-17", "text": "Checkpoint was missed."},
        {"key": "study-a:event:collection-7", "text": "Collection failed."},
    ]

    first = cycle(tmp_path / "reporter", "study-a",
                  assessment(summary=None, event_findings=findings), send,
                  now=BEFORE_DAILY)
    second = cycle(tmp_path / "reporter", "study-a",
                   assessment(summary=None, event_findings=findings), send,
                   now=BEFORE_DAILY)

    assert submitted == [
        "Research alert (observed at 2026-11-18T00:00:00Z / "
        "2026-11-17T18:00:00-06:00 America/Chicago):\n"
        "- Checkpoint was missed.\n- Collection failed."
    ]
    assert first["accepted_count"] == 1
    assert second["submitted_this_cycle"] == 0


def test_new_event_does_not_resubmit_earlier_ambiguous_notice(tmp_path: Path) -> None:
    calls: list[str] = []

    def uncertain(text: str) -> dict[str, Any]:
        calls.append(text)
        raise RuntimeError("recipient credentials should never be retained")

    root = tmp_path / "reporter"
    first_finding = [{"key": "study-a:event:one", "text": "First failure."}]
    first = cycle(root, "study-a", assessment(summary=None, event_findings=first_finding),
                  uncertain, now=BEFORE_DAILY)
    second_finding = first_finding + [{"key": "study-a:event:two", "text": "Second failure."}]
    received: list[str] = []
    second = cycle(root, "study-a",
                   assessment(summary=None, event_findings=second_finding),
                   accepted_sender(received), now=BEFORE_DAILY + timedelta(minutes=5))

    assert first["in_doubt_count"] == 1
    assert len(received) == 1
    assert "observed at 2026-11-18T00:05:00Z" in received[0]
    assert received[0].endswith("\n- Second failure.")
    assert second["accepted_count"] == 1
    assert second["in_doubt_count"] == 1
    assert "credentials" not in (root / "ledger.json").read_text()


def test_condition_transitions_recovery_and_busy_indeterminate_preservation(tmp_path: Path) -> None:
    submitted: list[str] = []
    send = accepted_sender(submitted)
    root = tmp_path / "reporter"
    active = assessment(summary=None, conditions={
        "timer_disabled": "Evaluator timer is disabled.",
        "verification_unavailable": "Offline verification is unavailable.",
    })

    cycle(root, "study-a", active, send, now=BEFORE_DAILY)
    cycle(root, "study-a", active, send, now=BEFORE_DAILY + timedelta(minutes=5))
    cycle(root, "study-a", assessment(
        summary=None, busy=True,
        conditions={"timer_disabled": "Evaluator timer is disabled."},
        indeterminate_conditions=["verification_unavailable"],
    ), send, now=BEFORE_DAILY + timedelta(minutes=10))
    cycle(root, "study-a", assessment(summary=None), send,
          now=BEFORE_DAILY + timedelta(minutes=15))

    assert "observed at 2026-11-18T00:00:00Z" in submitted[0]
    assert "2026-11-17T18:00:00-06:00 America/Chicago" in submitted[0]
    assert "Evaluator timer is disabled." in submitted[0]
    assert "observed at 2026-11-18T00:15:00Z" in submitted[1]
    assert "timer disabled cleared." in submitted[1]


def test_busy_defers_events_without_claiming_them(tmp_path: Path) -> None:
    finding = [{"key": "study-a:event:later", "text": "Deferred failure."}]
    submitted: list[str] = []
    root = tmp_path / "reporter"

    cycle(root, "study-a", assessment(summary=None, busy=True, event_findings=finding),
          accepted_sender(submitted), now=BEFORE_DAILY)
    cycle(root, "study-a", assessment(summary=None, event_findings=finding),
          accepted_sender(submitted), now=BEFORE_DAILY + timedelta(minutes=5))

    assert len(submitted) == 1
    assert "observed at 2026-11-18T00:05:00Z" in submitted[0]
    assert submitted[0].endswith("\n- Deferred failure.")


@pytest.mark.parametrize("response", [
    {"provider_id": "provider-1", "verified": False},
    {"provider_id": "provider-1", "verified": 1},
    {"provider_id": "", "verified": True},
    {"provider_id": "provider-1", "verified": True, "extra": "x"},
    None,
])
def test_only_exact_verified_sender_readback_is_accepted_or_resent(
        tmp_path: Path, response: Any) -> None:
    submitted: list[str] = []

    def send(text: str) -> Any:
        submitted.append(text)
        return response

    root = tmp_path / "reporter"
    first = cycle(root, "study-a", assessment(), send, now=NOW)
    second = cycle(root, "study-a", assessment(), send, now=NOW)

    assert len(submitted) == 1
    assert first["accepted_count"] == 0
    assert second["in_doubt_count"] == 1


def test_opaque_provider_and_campaign_identifiers_are_preserved(tmp_path: Path) -> None:
    result = cycle(
        tmp_path / "reporter", "study campaign @ 17", assessment(),
        lambda _: {"provider_id": "Photon message: 17/@home", "verified": True},
        now=NOW,
    )
    assert result["accepted_count"] == 1


def test_keyboard_interrupt_after_send_start_is_not_blindly_resent(tmp_path: Path) -> None:
    calls: list[str] = []

    def interrupted(text: str) -> dict[str, Any]:
        calls.append(text)
        raise KeyboardInterrupt

    root = tmp_path / "reporter"
    with pytest.raises(KeyboardInterrupt):
        cycle(root, "study-a", assessment(), interrupted, now=NOW)
    result = cycle(root, "study-a", assessment(), accepted_sender(calls), now=NOW)

    assert len(calls) == 1
    assert result["in_doubt_count"] == 1
    assert result["submitted_this_cycle"] == 0


def _run_child(code: str, *arguments: Path) -> subprocess.Popen[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[1])
    return subprocess.Popen(
        [sys.executable, "-c", code, *(str(value) for value in arguments)],
        env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _wait_for(path: Path) -> None:
    deadline = time.monotonic() + 10
    while not path.exists():
        if time.monotonic() > deadline:
            pytest.fail(f"timed out waiting for {path.name}")
        time.sleep(0.01)


def test_process_death_after_external_send_is_recorded_in_doubt(tmp_path: Path) -> None:
    root = tmp_path / "reporter"
    receipt = tmp_path / "external-receipt"
    code = """
import os
from datetime import datetime
from pathlib import Path
from research_reporting.outbox import cycle
root, receipt = map(Path, __import__('sys').argv[1:])
a = {'summary':'Daily safe report.','event_findings':[],'conditions':{},'indeterminate_conditions':[],'busy':False}
def send(text):
    receipt.write_text(text)
    os._exit(23)
cycle(root, 'study-a', a, send, now=datetime.fromisoformat('2026-11-17T21:00:00-06:00'))
"""
    child = _run_child(code, root, receipt)
    assert child.wait(timeout=10) == 23
    assert receipt.read_text() == "Daily safe report."

    submitted: list[str] = []
    result = cycle(root, "study-a", assessment(summary="Daily safe report."),
                   accepted_sender(submitted), now=NOW)

    assert submitted == []
    assert result["in_doubt_count"] == 1


def test_pre_send_pending_resumes_but_started_notice_does_not(tmp_path: Path) -> None:
    root = tmp_path / "reporter"
    started = tmp_path / "started"
    release = tmp_path / "release"
    code = """
import time
from datetime import datetime
from pathlib import Path
from research_reporting.outbox import cycle
root, started, release = map(Path, __import__('sys').argv[1:])
a = {'summary':'Daily pending report.','event_findings':[{'key':'study-a:event:one','text':'First alert.'}],'conditions':{},'indeterminate_conditions':[],'busy':False}
def send(text):
    started.write_text(text)
    while not release.exists(): time.sleep(.01)
    return {'provider_id':'never','verified':True}
cycle(root, 'study-a', a, send, now=datetime.fromisoformat('2026-11-17T21:00:00-06:00'))
"""
    child = _run_child(code, root, started, release)
    _wait_for(started)
    child.kill()
    child.wait(timeout=10)

    submitted: list[str] = []
    result = cycle(root, "study-a", assessment(summary="Daily pending report."),
                   accepted_sender(submitted), now=NOW)

    assert "observed at 2026-11-18T03:00:00Z" in started.read_text()
    assert started.read_text().endswith("\n- First alert.")
    assert submitted == ["Daily pending report."]
    assert result["accepted_count"] == 1
    assert result["in_doubt_count"] == 1


def test_expired_prior_local_day_pending_daily_is_not_delivered(tmp_path: Path) -> None:
    root = tmp_path / "reporter"
    started = tmp_path / "started"
    release = tmp_path / "release"
    code = """
import time
from datetime import datetime
from pathlib import Path
from research_reporting.outbox import cycle
root, started, release = map(Path, __import__('sys').argv[1:])
a = {'summary':'Old daily.','event_findings':[{'key':'study-a:event:block','text':'Blocking alert.'}],'conditions':{},'indeterminate_conditions':[],'busy':False}
def send(text):
    started.write_text(text)
    while not release.exists(): time.sleep(.01)
    return {'provider_id':'never','verified':True}
cycle(root, 'study-a', a, send, now=datetime.fromisoformat('2026-11-17T21:00:00-06:00'))
"""
    child = _run_child(code, root, started, release)
    _wait_for(started)
    child.kill()
    child.wait(timeout=10)

    submitted: list[str] = []
    result = cycle(root, "study-a", assessment(summary=None), accepted_sender(submitted),
                   now=NOW + timedelta(days=1))

    assert submitted == []
    assert result["expired_count"] == 1
    assert result["in_doubt_count"] == 1


def test_advisory_lock_serializes_real_processes(tmp_path: Path) -> None:
    root = tmp_path / "reporter"
    started = tmp_path / "started"
    release = tmp_path / "release"
    finished = tmp_path / "finished"
    first_code = """
import time
from datetime import datetime
from pathlib import Path
from research_reporting.outbox import cycle
root, started, release = map(Path, __import__('sys').argv[1:])
a={'summary':None,'event_findings':[{'key':'study-a:event:lock','text':'Lock alert.'}],'conditions':{},'indeterminate_conditions':[],'busy':False}
def send(text):
    started.touch()
    while not release.exists(): time.sleep(.01)
    return {'provider_id':'one','verified':True}
cycle(root,'study-a',a,send,now=datetime.fromisoformat('2026-11-17T18:00:00-06:00'))
"""
    second_code = """
from datetime import datetime
from pathlib import Path
from research_reporting.outbox import cycle
root, finished = map(Path, __import__('sys').argv[1:])
a={'summary':None,'event_findings':[],'conditions':{},'indeterminate_conditions':[],'busy':False}
cycle(root,'study-a',a,lambda text:{'provider_id':'two','verified':True},now=datetime.fromisoformat('2026-11-17T18:05:00-06:00'))
finished.touch()
"""
    first = _run_child(first_code, root, started, release)
    _wait_for(started)
    second = _run_child(second_code, root, finished)
    time.sleep(0.2)
    assert not finished.exists()
    release.touch()
    assert first.wait(timeout=10) == 0
    assert second.wait(timeout=10) == 0
    assert finished.exists()


def test_clock_regression_and_campaign_identity_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "reporter"
    cycle(root, "study-a", assessment(summary=None), lambda _: None, now=NOW)

    with pytest.raises(OutboxError, match="^clock_regression$"):
        cycle(root, "study-a", assessment(summary=None), lambda _: None,
              now=NOW - timedelta(seconds=1))
    with pytest.raises(OutboxError, match="^campaign_identity_mismatch$"):
        cycle(root, "study-b", assessment(summary=None), lambda _: None, now=NOW)


def test_existing_ledger_without_prior_observation_is_valid(tmp_path: Path) -> None:
    root = tmp_path / "reporter"
    cycle(root, "study-a", assessment(summary=None), lambda _: None, now=NOW)
    ledger = json.loads((root / "ledger.json").read_text())
    ledger["last_observed_at"] = None
    (root / "ledger.json").write_text(json.dumps(ledger))

    result = cycle(root, "study-a", assessment(summary=None), lambda _: None,
                   now=NOW - timedelta(seconds=1))

    assert result["status"] == "processed"


@pytest.mark.parametrize("mutation", [
    lambda a: {**a, "extra": None},
    lambda a: {**a, "busy": 1},
    lambda a: {**a, "summary": ""},
    lambda a: {**a, "event_findings": "bad"},
    lambda a: {**a, "event_findings": [{"key": "bad key", "text": "x"}]},
    lambda a: {**a, "event_findings": [
        {"key": "same", "text": "one"}, {"key": "same", "text": "two"}]},
    lambda a: {**a, "event_findings": [{"key": "safe", "text": "\x00"}]},
    lambda a: {**a, "conditions": {"unknown": "x"}},
    lambda a: {**a, "indeterminate_conditions": ["timer_disabled", "timer_disabled"]},
    lambda a: {**a, "conditions": {"timer_disabled": "x"},
               "indeterminate_conditions": ["timer_disabled"]},
])
def test_malformed_assessment_raises_sanitized_public_error(
        tmp_path: Path, mutation: Any) -> None:
    with pytest.raises(OutboxError, match="^invalid_outbox_input$"):
        cycle(tmp_path / "reporter", "study-a", mutation(assessment()),
              lambda _: None, now=NOW)


@pytest.mark.parametrize(("campaign", "now", "sender"), [
    ("bad\n campaign", NOW, lambda _: None),
    ("study-a", datetime(2026, 11, 17), lambda _: None),
    ("study-a", NOW, None),
])
def test_other_malformed_public_arguments_raise_sanitized_error(
        tmp_path: Path, campaign: Any, now: Any, sender: Any) -> None:
    with pytest.raises(OutboxError, match="^invalid_outbox_input$"):
        cycle(tmp_path / "reporter", campaign, assessment(), sender, now=now)


def test_invalid_path_arguments_and_storage_errors_are_sanitized(tmp_path: Path) -> None:
    class BadPath:
        def __fspath__(self) -> str:
            raise ValueError("private path detail")

    for path in (b"bytes", BadPath()):
        with pytest.raises(OutboxError, match="^invalid_outbox_input$"):
            cycle(path, "study-a", assessment(), lambda _: None, now=NOW)
    with pytest.raises(OutboxError, match="^outbox_storage_unavailable$"):
        cycle(Path("/dev/null/child"), "study-a", assessment(), lambda _: None, now=NOW)


def test_malformed_finding_shape_is_rejected(tmp_path: Path) -> None:
    malformed = assessment(event_findings=[{"key": "only"}])
    with pytest.raises(OutboxError, match="^invalid_outbox_input$"):
        cycle(tmp_path / "reporter", "study-a", malformed, lambda _: None, now=NOW)


def test_findings_are_split_into_bounded_messages(tmp_path: Path) -> None:
    findings = [
        {"key": f"study-a:event:{number}", "text": f"{number}:" + "x" * 480}
        for number in range(9)
    ]
    submitted: list[str] = []

    root = tmp_path / "reporter"
    first = cycle(root, "study-a", assessment(summary=None, event_findings=findings),
                  accepted_sender(submitted), now=BEFORE_DAILY)
    second = cycle(root, "study-a", assessment(summary=None, event_findings=findings),
                   accepted_sender(submitted), now=BEFORE_DAILY + timedelta(minutes=5))

    assert len(submitted) == 3
    assert all(len(message) <= 2_000 for message in submitted)
    assert first["submitted_this_cycle"] == 2
    assert first["pending_count"] == 1
    assert second["submitted_this_cycle"] == 1


def test_private_directory_and_records_have_restrictive_modes(tmp_path: Path) -> None:
    root = tmp_path / "reporter"
    root.mkdir(mode=0o777)
    os.chmod(root, 0o777)

    cycle(root, "study-a", assessment(), accepted_sender([]), now=NOW)

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "ledger.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((root / "outbox.lock").stat().st_mode) == 0o600


@pytest.mark.parametrize("target", ["root", "lock", "ledger"])
def test_symlink_storage_is_rejected(tmp_path: Path, target: str) -> None:
    real = tmp_path / "real"
    real.mkdir()
    root = tmp_path / "reporter"
    if target == "root":
        root.symlink_to(real, target_is_directory=True)
    else:
        root.mkdir()
        destination = tmp_path / f"{target}-target"
        destination.write_text("{}")
        (root / ("outbox.lock" if target == "lock" else "ledger.json")).symlink_to(destination)

    with pytest.raises(OutboxError, match="^unsafe_outbox_storage$"):
        cycle(root, "study-a", assessment(), lambda _: None, now=NOW)


def test_nonregular_ledger_and_malformed_json_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "reporter"
    root.mkdir()
    (root / "ledger.json").mkdir()
    with pytest.raises(OutboxError, match="^unsafe_outbox_storage$"):
        cycle(root, "study-a", assessment(), lambda _: None, now=NOW)

    (root / "ledger.json").rmdir()
    (root / "ledger.json").write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(OutboxError, match="^invalid_outbox_state$"):
        cycle(root, "study-a", assessment(), lambda _: None, now=NOW)


def _ledger(root: Path) -> dict[str, Any]:
    return json.loads((root / "ledger.json").read_text())


def _write_ledger(root: Path, value: Any) -> None:
    (root / "ledger.json").write_text(json.dumps(value))


def test_nonregular_lock_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "reporter"
    root.mkdir()
    os.mkfifo(root / "outbox.lock")
    with pytest.raises(OutboxError, match="^unsafe_outbox_storage$"):
        cycle(root, "study-a", assessment(), lambda _: None, now=NOW)


def test_non_utf8_ledger_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "reporter"
    root.mkdir()
    (root / "ledger.json").write_bytes(b"\xff")
    with pytest.raises(OutboxError, match="^invalid_outbox_state$"):
        cycle(root, "study-a", assessment(), lambda _: None, now=NOW)


@pytest.mark.parametrize("mutation", [
    lambda s: [],
    lambda s: {**s, "schema_version": True},
    lambda s: {**s, "campaign_id": []},
    lambda s: {**s, "notices": []},
    lambda s: {**s, "claimed_events": {"bad key": NOW.isoformat()}},
    lambda s: {**s, "conditions": {"timer_disabled": 1}},
    lambda s: {**s, "notices": {"bad key": {}}},
    lambda s: {**s, "notices": {"safe": {"kind": "bad"}}},
    lambda s: {**s, "notices": {"safe": {
        "kind": [], "status": "accepted", "created_at": NOW.isoformat(),
        "local_date": "2026-11-17", "text": "x", "provider_id": None}}},
    lambda s: {**s, "notices": {"safe": {
        "kind": "daily", "status": {}, "created_at": NOW.isoformat(),
        "local_date": "2026-11-17", "text": "x", "provider_id": None}}},
    lambda s: {**s, "notices": {"safe": {
        "kind": "daily", "status": "bad", "created_at": NOW.isoformat(),
        "local_date": "2026-11-17", "text": "x", "provider_id": None}}},
    lambda s: {**s, "notices": {"safe": {
        "kind": "daily", "status": "accepted", "created_at": NOW.isoformat(),
        "local_date": 1, "text": "x", "provider_id": None}}},
    lambda s: {**s, "notices": {"safe": {
        "kind": "daily", "status": "accepted", "created_at": NOW.isoformat(),
        "local_date": "not-a-date", "text": "x", "provider_id": None}}},
    lambda s: {**s, "notices": {"safe": {
        "kind": "daily", "status": "accepted", "created_at": NOW.isoformat(),
        "local_date": "2026-11-17", "text": "x", "provider_id": "bad\nid"}}},
    lambda s: {**s, "notices": {"safe": {
        "kind": "daily", "status": "accepted", "created_at": NOW.isoformat(),
        "local_date": "2026-11-17", "text": [], "provider_id": None}}},
    lambda s: {**s, "last_observed_at": 1},
    lambda s: {**s, "last_observed_at": "not-a-time"},
    lambda s: {**s, "last_observed_at": "2026-11-17T21:00:00"},
])
def test_strict_ledger_types_are_rejected(tmp_path: Path, mutation: Any) -> None:
    root = tmp_path / "reporter"
    cycle(root, "study-a", assessment(summary=None), lambda _: None, now=NOW)
    _write_ledger(root, mutation(_ledger(root)))
    with pytest.raises(OutboxError, match="^invalid_outbox_state$"):
        cycle(root, "study-a", assessment(summary=None), lambda _: None,
              now=NOW + timedelta(minutes=5))


def test_claim_limit_is_enforced_through_public_cycles(tmp_path: Path) -> None:
    root = tmp_path / "reporter"
    send = accepted_sender([])
    moment = BEFORE_DAILY
    for batch in range(163):
        findings = [
            {"key": f"e:{batch:02x}:{item:02x}", "text": "x"}
            for item in range(100)
        ]
        cycle(root, "study-a", assessment(summary=None, event_findings=findings),
              send, now=moment)
        moment += timedelta(seconds=1)
    overflow = [{"key": f"overflow:{item}", "text": "x"} for item in range(85)]
    with pytest.raises(OutboxError, match="^outbox_state_limit$"):
        cycle(root, "study-a", assessment(summary=None, event_findings=overflow),
              send, now=moment)


def test_serialized_ledger_size_is_bounded(tmp_path: Path) -> None:
    root = tmp_path / "reporter"
    moment = BEFORE_DAILY
    reached_limit = False
    for batch in range(164):
        findings = [
            {"key": f"long:{batch:02x}:{item:02x}:" + "k" * 220, "text": "x"}
            for item in range(100)
        ]
        try:
            cycle(root, "study-a", assessment(summary=None, event_findings=findings),
                  accepted_sender([]), now=moment)
        except OutboxError as error:
            assert str(error) == "outbox_state_limit"
            reached_limit = True
            break
        moment += timedelta(seconds=1)
    assert reached_limit


def test_notice_count_is_bounded_through_public_cycles(tmp_path: Path) -> None:
    root = tmp_path / "reporter"
    moment = BEFORE_DAILY
    for _ in range(512):
        cycle(root, "study-a", assessment(
            summary=None, conditions={"timer_disabled": "Timer disabled."}),
            accepted_sender([]), now=moment)
        moment += timedelta(seconds=1)
        cycle(root, "study-a", assessment(summary=None), accepted_sender([]), now=moment)
        moment += timedelta(seconds=1)
    with pytest.raises(OutboxError, match="^outbox_state_limit$"):
        cycle(root, "study-a", assessment(
            summary=None, conditions={"timer_disabled": "Timer disabled."}),
            accepted_sender([]), now=moment)


@pytest.mark.parametrize(("finding_count", "accepted"), [
    (16_383, True),
    (16_384, True),
    (16_385, False),
])
def test_historical_finding_input_bound_is_literal(
        tmp_path: Path, finding_count: int, accepted: bool) -> None:
    findings = [
        {"key": f"history:{number}", "text": "Historical failure."}
        for number in range(finding_count)
    ]
    if accepted:
        result = cycle(tmp_path / "reporter", "study-a",
                       assessment(summary=None, event_findings=findings, busy=True),
                       lambda _: None, now=BEFORE_DAILY)
        assert result["submitted_this_cycle"] == 0
    else:
        with pytest.raises(OutboxError, match="^invalid_outbox_input$"):
            cycle(tmp_path / "reporter", "study-a",
                  assessment(summary=None, event_findings=findings, busy=True),
                  lambda _: None, now=BEFORE_DAILY)


def test_historical_findings_over_old_batch_limit_remain_valid_after_claim(
        tmp_path: Path) -> None:
    root = tmp_path / "reporter"
    findings = [
        {"key": f"history:{number}", "text": f"Failure {number}."}
        for number in range(101)
    ]
    submitted: list[str] = []

    first = cycle(root, "study-a", assessment(summary=None, event_findings=findings),
                  accepted_sender(submitted), now=BEFORE_DAILY)
    second = cycle(root, "study-a", assessment(summary=None, event_findings=findings),
                   accepted_sender(submitted), now=BEFORE_DAILY + timedelta(minutes=5))

    assert first["submitted_this_cycle"] == 1
    assert second["submitted_this_cycle"] == 1
    assert "Failure 100." in submitted[-1]


@pytest.mark.parametrize(("group_count", "expected_first", "expected_pending"), [
    (0, 1, 0),
    (1, 2, 1),
    (2, 2, 2),
])
def test_external_attempt_budget_below_equal_and_above_two(
        tmp_path: Path, group_count: int, expected_first: int,
        expected_pending: int) -> None:
    findings = [
        {"key": f"group:{number}", "text": f"{number}:" + "x" * 490}
        for number in range(group_count * 4)
    ]
    submitted: list[str] = []
    result = cycle(tmp_path / "reporter", "study-a",
                   assessment(event_findings=findings), accepted_sender(submitted), now=NOW)

    assert result["submitted_this_cycle"] == expected_first
    assert result["pending_count"] == expected_pending
    assert len(submitted) == expected_first


def test_three_alert_groups_plus_daily_drain_two_per_cycle(tmp_path: Path) -> None:
    root = tmp_path / "reporter"
    findings = [
        {"key": f"group:{number}", "text": f"{number}:" + "x" * 490}
        for number in range(7)
    ]
    submitted: list[str] = []
    moments = [NOW, NOW + timedelta(minutes=5), NOW + timedelta(minutes=10)]

    results = [
        cycle(root, "study-a", assessment(event_findings=findings),
              accepted_sender(submitted), now=moment)
        for moment in moments
    ]

    assert [result["submitted_this_cycle"] for result in results] == [2, 2, 0]
    assert [result["pending_count"] for result in results] == [2, 0, 0]
    assert len(submitted) == 4
    assert assessment()["summary"] in submitted
    alerts = [message for message in submitted
              if message.startswith("Research alert (observed at ")]
    assert len(alerts) == 3
    assert all("observed at 2026-11-18T03:00:00Z" in message for message in alerts)
    assert all("2026-11-17T21:00:00-06:00 America/Chicago" in message
               for message in alerts)


def test_queued_recovery_keeps_its_observation_time_until_later_delivery(
        tmp_path: Path) -> None:
    root = tmp_path / "reporter"
    findings = [
        {"key": f"group:{number}", "text": f"{number}:" + "x" * 490}
        for number in range(7)
    ]
    submitted: list[str] = []
    send = accepted_sender(submitted)

    first = cycle(root, "study-a", assessment(
        summary=None, event_findings=findings,
        conditions={"timer_disabled": "Evaluator timer is disabled."}),
        send, now=BEFORE_DAILY)
    recovered_at = BEFORE_DAILY + timedelta(minutes=5)
    second = cycle(root, "study-a", assessment(summary=None), send, now=recovered_at)
    delivered_at = recovered_at + timedelta(minutes=5)
    third = cycle(root, "study-a", assessment(summary=None), send, now=delivered_at)

    assert [first["submitted_this_cycle"], second["submitted_this_cycle"],
            third["submitted_this_cycle"]] == [2, 2, 1]
    recovery = submitted[-1]
    assert recovery.startswith(
        "Research recovery (observed at 2026-11-18T00:05:00Z / "
        "2026-11-17T18:05:00-06:00 America/Chicago):")
    assert "2026-11-18T00:10:00Z" not in recovery
    assert recovery.endswith("\n- timer disabled cleared.")
