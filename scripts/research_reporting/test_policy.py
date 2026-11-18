from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Inexact, ROUND_DOWN, getcontext, setcontext
from typing import Any

import pytest

from prospective_evaluation import runner
from prospective_evaluation.storage import load_campaign, register_campaign
from research_reporting.policy import ReportingError, assess


NOW = datetime(2026, 11, 18, 3, 32, tzinfo=UTC)
CAMPAIGN = "campaign-fixed"
HASH = "a" * 64


def health(**changes: Any) -> dict[str, Any]:
    value = {
        "timer_active": True,
        "service_active_state": "inactive",
        "service_result": "success",
        "service_exit_status": 0,
        "service_elapsed_seconds": None,
    }
    value.update(changes)
    return value


def observation(response: str = "0.0125") -> dict[str, Any]:
    return {
        "observation_completed_at": "2026-11-17T23:00:00+00:00",
        "raw_response": {
            "kind": "price_only",
            "corporate_action_status": "unchecked",
            "economically_scorable": False,
            "symbols": [],
            "arms": [
                {"arm": "cash", "arm_status": "accepted", "status": "scored", "response": "0"},
                {
                    "arm": "ai_three_role",
                    "arm_status": "accepted",
                    "status": "scored",
                    "response": response,
                },
            ],
            "paired_baseline_differences": [],
        },
    }


def horizon(
    sessions: int,
    *,
    status: str = "pending",
    first: dict[str, Any] | None = None,
    latest: dict[str, Any] | None = None,
    latest_status: str | None = None,
) -> dict[str, Any]:
    return {
        "horizon": sessions,
        "status": status,
        "reason": None if status == "scored" else "awaiting_maturity",
        "first_observed": first,
        "latest_observed": latest,
        "latest_status": latest_status or status,
        "latest_reason": None if (latest_status or status) == "scored" else "mature_data_missing",
        "latest_observation_completed_at": "2026-11-17T23:00:00+00:00",
    }


def checkpoint(
    slot: str,
    *,
    ai_status: str = "accepted",
    checkpoint_status: str = "eligible",
    horizons: list[dict[str, Any]] | None = None,
    receipts: list[dict[str, Any]] | None = None,
    cash: str | None = "0.4",
    targets: list[dict[str, str]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if horizons is None:
        horizons = [horizon(1), horizon(5)]
    if receipts is None:
        receipts = [
            {
                "role": "research",
                "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            },
            {
                "role": "cio",
                "usage": {"input_tokens": 20, "output_tokens": 3, "total_tokens": 23},
            },
        ]
    if targets is None:
        targets = [{"symbol": "SPY", "weight": "0.6"}]
    verified = {
        "slot_date": slot,
        "checkpoint": {
            "arms": {
                "ai_three_role": {
                    "status": ai_status,
                    "reason": "untrusted model explanation",
                    "targets": targets if ai_status == "accepted" else None,
                    "cash_weight": cash if ai_status == "accepted" else None,
                }
            },
            "model_receipts": deepcopy(receipts),
        },
        "score": {
            "slot_date": slot,
            "checkpoint_status": checkpoint_status,
            "horizons": deepcopy(horizons),
        },
    }
    reported = {
        "slot_date": slot,
        "score": deepcopy(verified["score"]),
        "model_receipts": deepcopy(receipts),
        "usage_status": "known",
        "billed_cost": "unknown",
    }
    return verified, reported


def snapshot(
    *,
    status: str = "verified",
    checkpoints: list[tuple[dict[str, Any], dict[str, Any]]] | None = None,
    events: list[dict[str, Any]] | None = None,
    ticks: list[dict[str, Any]] | None = None,
    registered_at: str | None = "2026-11-18T03:00:00+00:00",
) -> dict[str, Any]:
    if status != "verified":
        report = verification = None
    else:
        pairs = checkpoints or []
        verified_checkpoints = [pair[0] for pair in pairs]
        report_checkpoints = [pair[1] for pair in pairs]
        verification = {
            "schema_version": 1,
            "campaign_id": CAMPAIGN,
            "registration_hash": HASH,
            "verified": True,
            "executable": False,
            "observation_count": 4,
            "valid_observation_count": 3,
            "unavailable_observation_count": 1,
            "checkpoint_count": len(pairs),
            "event_count": len(events or []),
            "tick_count": len(ticks or []),
            "checkpoints": verified_checkpoints,
            "events": events or [],
            "ticks": ticks or [],
        }
        report = {
            "schema_version": 1,
            "campaign_id": CAMPAIGN,
            "verified": True,
            "executable": False,
            "counts": {
                "observations": 4,
                "valid_observations": 3,
                "unavailable_observations": 1,
                "checkpoints": len(pairs),
                "reservations": len(pairs),
                "missed": 0,
                "in_doubt": 0,
                "collection_failures": 0,
            },
            "arm_status_counts": {},
            "checkpoints": report_checkpoints,
            "caveats": {
                "price_only": True,
                "corporate_action_status": "unchecked",
                "economically_scorable": False,
                "billed_cost_absent_means": "unknown",
                "overlapping_cohorts_are_not_compounded": True,
            },
        }
    return {
        "schema_version": 1,
        "status": status,
        "observed_at": "2026-11-18T03:31:00+00:00",
        "campaign_id": CAMPAIGN,
        "registration_hash": HASH,
        "registered_at": registered_at,
        "report": report,
        "verification": verification,
    }


def test_verified_daily_summary_reports_activity_allocation_usage_and_fixed_caveats() -> None:
    pair = checkpoint("2026-11-17")

    result = assess(snapshot(checkpoints=[pair]), health(), now=NOW)

    assert set(result) == {
        "summary", "event_findings", "conditions", "indeterminate_conditions", "busy",
    }
    assert result["busy"] is False
    assert result["conditions"] == {}
    assert result["indeterminate_conditions"] == []
    assert result["event_findings"] == []
    assert result["summary"] == (
        "T² research campaign campaign-fixed\n"
        "As of 2026-11-17 21:31 America/Chicago (2026-11-18 03:31 UTC).\n"
        "Coverage: 1 checkpoint; observations 3 valid, 1 unavailable (4 total); "
        "failures 0 missed, 0 collection, 0 in-doubt.\n"
        "Latest AI 2026-11-17: accepted; SPY 60%, cash 40%.\n"
        "Recorded model receipts 2; usage input 30, output 5, total 35 tokens; billed cost unknown.\n"
        "1-session outcome: pending.\n"
        "5-session outcome: pending.\n"
        "Read-only research; no orders. Raw price responses, not profit or adjusted return; "
        "corporate actions unchecked; billed cost unknown; cohorts are not compounded."
    )
    assert len(result["summary"]) <= 2000


def test_latest_unavailable_tick_sets_condition_and_next_verified_tick_clears_it() -> None:
    failed = {
        "record_id": "failed",
        "value": {
            "tick_id": "failed",
            "status": "unavailable",
            "reason": "observation_capture_failed",
            "completed_at": "2026-11-18T03:30:00+00:00",
        },
    }
    recovered = {
        "record_id": "recovered",
        "value": {
            "tick_id": "recovered",
            "status": "observed",
            "completed_at": "2026-11-18T03:30:30+00:00",
        },
    }

    failed_result = assess(snapshot(ticks=[failed]), health(), now=NOW)
    recovered_result = assess(snapshot(ticks=[failed, recovered]), health(), now=NOW)

    assert failed_result["conditions"] == {
        "verification_unavailable": "Latest evaluator source/collection is unavailable."
    }
    assert failed_result["event_findings"] == [{
        "key": "campaign-fixed:tick:failed:source-unavailable",
        "text": "Research tick failed could not verify source observations.",
    }]
    assert recovered_result["conditions"] == {}
    assert recovered_result["event_findings"] == failed_result["event_findings"]


def test_public_semantic_source_unavailability_is_an_immutable_failure_event() -> None:
    tick = {
        "record_id": "20261118T033000Z",
        "value": {
            "tick_id": "20261118T033000Z",
            "status": "unavailable",
            "reason": "observation_reconstruction_failed",
            "completed_at": "2026-11-18T03:30:00+00:00",
        },
    }

    result = assess(snapshot(ticks=[tick]), health(), now=NOW)

    assert result["event_findings"] == [{
        "key": "campaign-fixed:tick:20261118T033000Z:source-unavailable",
        "text": "Research tick 20261118T033000Z could not verify source observations.",
    }]
    assert result["conditions"] == {
        "verification_unavailable": "Latest evaluator source/collection is unavailable."
    }


def test_frozen_public_report_exposes_source_unavailability_to_policy(tmp_path) -> None:
    class Clock:
        def __call__(self) -> datetime:
            return NOW - timedelta(minutes=2)

    class UnavailableMarket:
        def fetch(self, source: str, query: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("synthetic external source failure")

    class Forbidden:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"unexpected external I/O: {name}")

    campaign_path = register_campaign(
        tmp_path,
        {
            "schema_version": 1,
            "campaign_id": "public-report-source-failure",
            "universe": [{"symbol": "SPY", "asset_class": "etf"}],
            "model": "fixture-model",
            "research_cash": "1000",
            "max_checkpoints": 3,
        },
        clock=Clock(),
    )
    tick = runner.tick(
        campaign_path,
        UnavailableMarket(),
        Forbidden(),
        Forbidden(),
        clock=Clock(),
    )
    verification = runner.verify_campaign(campaign_path)
    public_report = runner.report_campaign(campaign_path)
    registration = load_campaign(campaign_path)
    public_snapshot = {
        "schema_version": 1,
        "status": "verified",
        "observed_at": (NOW - timedelta(minutes=1)).isoformat(),
        "campaign_id": verification["campaign_id"],
        "registration_hash": verification["registration_hash"],
        "registered_at": registration["registered_at"],
        "report": public_report,
        "verification": verification,
    }

    result = assess(public_snapshot, health(), now=NOW)

    assert tick["status"] == "unavailable"
    assert tick["reason"] == "observation_reconstruction_failed"
    assert result["event_findings"] == [{
        "key": (
            "public-report-source-failure:tick:"
            f"{tick['tick_id']}:source-unavailable"
        ),
        "text": f"Research tick {tick['tick_id']} could not verify source observations.",
    }]
    assert result["conditions"] == {
        "verification_unavailable": "Latest evaluator source/collection is unavailable."
    }


@pytest.mark.parametrize(
    "reason",
    [
        "observation_capture_failed",
        "observation_reconstruction_failed",
        "reconstruction_failed",
        "collection_failed",
    ],
)
def test_latest_source_failure_reuses_one_condition_key(reason: str) -> None:
    tick = {
        "record_id": reason,
        "value": {
            "tick_id": reason,
            "status": "unavailable",
            "reason": reason,
            "completed_at": "2026-11-18T03:30:00+00:00",
        },
    }

    result = assess(snapshot(ticks=[tick]), health(), now=NOW)

    assert result["conditions"] == {
        "verification_unavailable": "Latest evaluator source/collection is unavailable."
    }


def test_summary_uses_newest_decision_but_latest_mature_cohort_for_each_horizon() -> None:
    first = observation("0.01")
    latest = observation("0.02")
    old = checkpoint(
        "2026-11-16",
        horizons=[
            horizon(1, status="scored", first=first, latest=latest),
            horizon(5),
        ],
        cash="1",
        targets=[{"symbol": "SPY", "weight": "0"}],
    )
    new = checkpoint("2026-11-17", cash="0.25", targets=[{"symbol": "QQQ", "weight": "0.75"}])

    summary = assess(snapshot(checkpoints=[new, old]), health(), now=NOW)["summary"]

    assert "Latest AI 2026-11-17: accepted; QQQ 75%, cash 25%." in summary
    assert (
        "1-session outcome cohort 2026-11-16: first raw responses cash 0%, AI 1%; "
        "latest cash 0%, AI 2%."
        in summary
    )
    assert "5-session outcome: pending." in summary


def test_latest_unavailable_remains_distinct_from_first_known_response() -> None:
    mature = checkpoint(
        "2026-11-16",
        horizons=[
            horizon(
                1,
                status="scored",
                first=observation("-0.005"),
                latest=None,
                latest_status="unavailable",
            ),
            horizon(5),
        ],
    )

    summary = assess(snapshot(checkpoints=[mature]), health(), now=NOW)["summary"]

    assert (
        "1-session outcome cohort 2026-11-16: first raw responses cash 0%, AI -0.5%; "
        "latest unavailable."
    ) in summary


def test_newer_mature_unavailable_cohort_is_not_hidden_by_older_score() -> None:
    old = checkpoint(
        "2026-11-15",
        horizons=[horizon(1, status="scored", first=observation(), latest=observation()), horizon(5)],
    )
    new = checkpoint(
        "2026-11-16",
        horizons=[horizon(1, status="unavailable"), horizon(5)],
    )

    summary = assess(snapshot(checkpoints=[old, new]), health(), now=NOW)["summary"]

    assert "1-session outcome cohort 2026-11-16: unavailable." in summary
    assert "1-session outcome cohort 2026-11-15" not in summary


def test_usage_is_unknown_when_any_receipt_lacks_exact_integer_usage() -> None:
    pair = checkpoint(
        "2026-11-17",
        receipts=[
            {"role": "research", "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}},
            {"role": "cio", "usage": {"input_tokens": 4, "output_tokens": True}},
            {"role": "critic"},
        ],
    )

    summary = assess(snapshot(checkpoints=[pair]), health(), now=NOW)["summary"]

    assert "Recorded model receipts 3; usage unknown; billed cost unknown." in summary
    assert "usage input 6" not in summary


def test_started_in_doubt_inference_does_not_claim_zero_or_one_total_calls() -> None:
    pair = checkpoint(
        "2026-11-17",
        ai_status="in_doubt",
        receipts=[{"status": "unknown", "billed_cost": "unknown"}],
    )

    summary = assess(snapshot(checkpoints=[pair]), health(), now=NOW)["summary"]

    assert (
        "Recorded model receipts 0; total inference calls unknown; usage unknown; "
        "billed cost unknown."
    ) in summary
    assert "Model calls 0" not in summary
    assert "Model calls 1" not in summary


def test_cash_is_an_accepted_decision_and_not_a_failure() -> None:
    pair = checkpoint(
        "2026-11-17", cash="1", targets=[{"symbol": "SPY", "weight": "0"}],
    )

    result = assess(snapshot(checkpoints=[pair]), health(), now=NOW)

    assert "Latest AI 2026-11-17: accepted; cash 100%." in result["summary"]
    assert result["event_findings"] == []


def test_failure_events_have_stable_keys_and_fixed_bounded_text() -> None:
    failed = checkpoint("2026-11-17", ai_status="failed")
    unavailable = checkpoint("2026-11-16", ai_status="unavailable")
    late = checkpoint("2026-11-15", checkpoint_status="late")
    events = [
        {
            "record_id": "missed-2026-11-14",
            "value": {"event": "checkpoint_missed", "slot_date": "2026-11-14", "reason": "secret"},
        },
        {
            "record_id": "collection-immutable-failed",
            "value": {"event": "collection_failed", "reason": "provider exception with secrets"},
        },
        {"record_id": "ordinary-event", "value": {"event": "checkpoint_reserved"}},
    ]
    ticks = [
        {
            "record_id": "tick-immutable",
            "value": {"tick_id": "tick-immutable", "status": "in_doubt", "reason": "secret"},
        },
        {"record_id": "waiting", "value": {"tick_id": "waiting", "status": "waiting"}},
    ]

    findings = assess(
        snapshot(checkpoints=[failed, unavailable, late], events=events, ticks=ticks),
        health(),
        now=NOW,
    )["event_findings"]

    assert findings == [
        {
            "key": "campaign-fixed:event:missed-2026-11-14",
            "text": "Research checkpoint 2026-11-14 was missed.",
        },
        {
            "key": "campaign-fixed:event:collection-immutable-failed",
            "text": "Research evidence collection failed.",
        },
        {
            "key": "campaign-fixed:tick:tick-immutable",
            "text": "Research tick tick-immutable has an in-doubt completion.",
        },
        {
            "key": "campaign-fixed:ai:2026-11-15:late",
            "text": "AI checkpoint 2026-11-15 completed late.",
        },
        {
            "key": "campaign-fixed:ai:2026-11-16:unavailable",
            "text": "AI checkpoint 2026-11-16 was unavailable.",
        },
        {
            "key": "campaign-fixed:ai:2026-11-17:notaccepted",
            "text": "AI checkpoint 2026-11-17 was not accepted.",
        },
    ]
    assert "secret" not in repr(findings)


@pytest.mark.parametrize("state", ["active", "activating", "deactivating"])
@pytest.mark.parametrize("elapsed,overlong", [(899.9999, False), (900, False), (900.0001, True)])
def test_running_service_runtime_limit_is_strict(
    state: str, elapsed: float, overlong: bool,
) -> None:
    actual = assess(
        snapshot(status="busy"),
        health(service_active_state=state, service_elapsed_seconds=elapsed),
        now=NOW,
    )
    assert ("service_overlong" in actual["conditions"]) is overlong


@pytest.mark.parametrize("state", ["inactive", "reloading"])
def test_nonrunning_successful_oneshot_is_not_reported_as_overlong(state: str) -> None:
    actual = assess(
        snapshot(status="busy"),
        health(service_active_state=state, service_elapsed_seconds=900.0001),
        now=NOW,
    )
    assert "service_overlong" not in actual["conditions"]


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"timer_active": False}, {"timer_disabled": "Evaluator timer is disabled."}),
        (
            {"service_active_state": "failed"},
            {"service_failed": "Evaluator service is failed."},
        ),
        (
            {"service_result": "exit-code", "service_exit_status": 7},
            {"service_failed": "Evaluator service did not exit successfully."},
        ),
        (
            {"service_active_state": "activating", "service_elapsed_seconds": 900.0001},
            {"service_overlong": "Evaluator service has run for more than 900 seconds."},
        ),
    ],
)
def test_health_conditions_are_assessed_independently(
    changes: dict[str, Any], expected: dict[str, str],
) -> None:
    assert assess(snapshot(), health(**changes), now=NOW)["conditions"] == expected


def test_health_boundaries_at_900_and_2700_seconds_are_not_failures() -> None:
    registered = (NOW - timedelta(seconds=2700)).isoformat()

    result = assess(
        snapshot(registered_at=registered),
        health(service_elapsed_seconds=900),
        now=NOW,
    )

    assert result["conditions"] == {}


def test_staleness_uses_latest_completed_tick_then_registration() -> None:
    stale_tick = {
        "record_id": "old",
        "value": {
            "tick_id": "old", "status": "completed",
            "completed_at": (NOW - timedelta(seconds=2700, microseconds=1)).isoformat(),
        },
    }
    recent_tick = {
        "record_id": "new",
        "value": {
            "tick_id": "new", "status": "observed",
            "completed_at": (NOW - timedelta(seconds=30)).isoformat(),
        },
    }

    stale = assess(snapshot(ticks=[stale_tick]), health(), now=NOW)
    recent = assess(snapshot(ticks=[stale_tick, recent_tick]), health(), now=NOW)

    assert stale["conditions"] == {"stale_tick": "No completed evaluator tick for more than 2700 seconds."}
    assert recent["conditions"] == {}


def test_unavailable_is_a_condition_while_busy_defers_policy_outputs() -> None:
    unavailable = assess(snapshot(status="unavailable"), health(), now=NOW)
    busy = assess(
        snapshot(status="busy"),
        health(timer_active=False, service_active_state="activating", service_elapsed_seconds=901),
        now=NOW,
    )

    assert unavailable == {
        "summary": None,
        "event_findings": [],
        "conditions": {"verification_unavailable": "Offline campaign verification is unavailable."},
        "indeterminate_conditions": ["stale_tick"],
        "busy": False,
    }
    assert busy == {
        "summary": None,
        "event_findings": [],
        "conditions": {
            "timer_disabled": "Evaluator timer is disabled.",
            "service_overlong": "Evaluator service has run for more than 900 seconds.",
        },
        "indeterminate_conditions": ["verification_unavailable", "stale_tick"],
        "busy": True,
    }


def test_unobserved_snapshots_leave_verification_and_stale_recovery_indeterminate() -> None:
    for status in ("busy", "unavailable"):
        result = assess(snapshot(status=status), health(), now=NOW)
        assert "stale_tick" in result["indeterminate_conditions"]
        if status == "busy":
            assert "verification_unavailable" in result["indeterminate_conditions"]
        else:
            assert result["conditions"]["verification_unavailable"] == (
                "Offline campaign verification is unavailable."
            )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s, h: s.update(extra=True),
        lambda s, h: h.pop("timer_active"),
        lambda s, h: s.update(schema_version=True),
        lambda s, h: s.update(status="other"),
        lambda s, h: s.update(observed_at=3),
        lambda s, h: s.update(observed_at="2026-11-18T03:31:00"),
        lambda s, h: s.update(observed_at="not-a-time"),
        lambda s, h: s.update(observed_at="2026-11-18T03:33:00+00:00"),
        lambda s, h: h.update(timer_active=1),
        lambda s, h: h.update(service_exit_status=True),
        lambda s, h: h.update(service_elapsed_seconds="1"),
        lambda s, h: h.update(service_elapsed_seconds=float("nan")),
        lambda s, h: h.update(service_elapsed_seconds=-1),
        lambda s, h: h.update(service_active_state=7),
        lambda s, h: s.update(registered_at="2026-11-18T03:32:00.000001+00:00"),
        lambda s, h: s.update(report={}),
        lambda s, h: s.update(report=None),
        lambda s, h: s["report"].update(schema_version=True),
        lambda s, h: s["report"].update(campaign_id="other"),
        lambda s, h: s["report"].update(verified=False),
        lambda s, h: s["verification"].update(registration_hash="other"),
        lambda s, h: s["verification"].update(events={}),
        lambda s, h: s["report"].update(checkpoints=3),
        lambda s, h: s["verification"]["ticks"].append(
            {"record_id": [], "value": {"status": "waiting"}}
        ),
        lambda s, h: s["verification"]["checkpoints"].append(
            {"slot_date": 3, "checkpoint": {}, "score": {}}
        ),
        lambda s, h: s["verification"]["checkpoints"].append(
            {"slot_date": "bad-date", "checkpoint": {}, "score": {}}
        ),
        lambda s, h: s["verification"]["checkpoints"].append(
            {"slot_date": "2026-1-1", "checkpoint": {}, "score": {}}
        ),
    ],
)
def test_malformed_inputs_raise_only_public_reporting_error(mutate) -> None:
    pair = checkpoint("2026-11-17")
    candidate_snapshot = snapshot(checkpoints=[pair])
    candidate_health = health()
    mutate(candidate_snapshot, candidate_health)

    with pytest.raises(ReportingError) as caught:
        assess(candidate_snapshot, candidate_health, now=NOW)

    assert str(caught.value) == "invalid_reporting_input"


def test_malformed_verified_snapshot_with_mismatched_public_shapes_is_rejected() -> None:
    mutations = []

    def duplicate_verified(s: dict[str, Any]) -> None:
        s["verification"]["checkpoints"].append(
            deepcopy(s["verification"]["checkpoints"][0])
        )

    def mismatched_score_slot(s: dict[str, Any]) -> None:
        s["verification"]["checkpoints"][0]["score"]["slot_date"] = "2026-11-16"

    def duplicate_reported(s: dict[str, Any]) -> None:
        s["report"]["checkpoints"].append(deepcopy(s["report"]["checkpoints"][0]))

    def missing_reported(s: dict[str, Any]) -> None:
        s["report"]["checkpoints"].clear()

    mutations.extend([duplicate_verified, mismatched_score_slot, duplicate_reported, missing_reported])
    for mutate in mutations:
        candidate = snapshot(checkpoints=[checkpoint("2026-11-17")])
        mutate(candidate)
        with pytest.raises(ReportingError, match="^invalid_reporting_input$"):
            assess(candidate, health(), now=NOW)


def test_malformed_summary_values_are_rejected() -> None:
    cases = []

    def bad_decimal_type(s: dict[str, Any]) -> None:
        s["verification"]["checkpoints"][0]["checkpoint"]["arms"]["ai_three_role"]["cash_weight"] = 1

    def bad_decimal_text(s: dict[str, Any]) -> None:
        s["verification"]["checkpoints"][0]["checkpoint"]["arms"]["ai_three_role"]["cash_weight"] = "not-decimal"

    def nonfinite_decimal(s: dict[str, Any]) -> None:
        s["verification"]["checkpoints"][0]["checkpoint"]["arms"]["ai_three_role"]["cash_weight"] = "NaN"

    def negative_count(s: dict[str, Any]) -> None:
        s["report"]["counts"]["observations"] = -1

    def missing_ai_response(s: dict[str, Any]) -> None:
        observed = observation()
        observed["raw_response"]["arms"] = []
        horizon_item = s["verification"]["checkpoints"][0]["score"]["horizons"][0]
        horizon_item.update(status="scored", first_observed=observed, latest_observed=observed)

    def null_first_response(s: dict[str, Any]) -> None:
        observed = observation()
        observed["raw_response"]["arms"][-1]["response"] = None
        horizon_item = s["verification"]["checkpoints"][0]["score"]["horizons"][0]
        horizon_item.update(status="scored", first_observed=observed, latest_observed=observed)

    def missing_observation_clock(s: dict[str, Any]) -> None:
        observed = observation()
        observed.pop("observation_completed_at")
        horizon_item = s["verification"]["checkpoints"][0]["score"]["horizons"][0]
        horizon_item.update(status="scored", first_observed=observed, latest_observed=observed)

    cases.extend([
        bad_decimal_type,
        bad_decimal_text,
        nonfinite_decimal,
        negative_count,
        missing_ai_response,
        null_first_response,
        missing_observation_clock,
    ])
    for mutate in cases:
        candidate = snapshot(checkpoints=[checkpoint("2026-11-17")])
        mutate(candidate)
        with pytest.raises(ReportingError, match="^invalid_reporting_input$"):
            assess(candidate, health(), now=NOW)


def test_percent_rendering_is_independent_of_ambient_decimal_context() -> None:
    pair = checkpoint(
        "2026-11-17",
        cash="0.876543211",
        targets=[{"symbol": "SPY", "weight": "0.123456789"}],
    )
    candidate = snapshot(checkpoints=[pair])
    baseline = assess(candidate, health(), now=NOW)["summary"]
    original = getcontext().copy()
    try:
        hostile = getcontext()
        hostile.prec = 2
        hostile.rounding = ROUND_DOWN
        hostile.traps[Inexact] = True
        actual = assess(candidate, health(), now=NOW)["summary"]
    finally:
        setcontext(original)

    assert actual == baseline
    assert "SPY 12.3456789%, cash 87.6543211%" in actual


@pytest.mark.parametrize(
    "mutate",
    [
        lambda s: s["verification"]["ticks"][0]["value"].update(
            completed_at="2026-11-18T03:32:00.000001+00:00"
        ),
        lambda s: s["verification"]["ticks"][0]["value"].update(
            completed_at="2026-11-18T03:30:00"
        ),
        lambda s: s["verification"]["checkpoints"][0]["checkpoint"].update(
            frozen_at="2026-11-18T03:32:00.000001+00:00"
        ),
        lambda s: s["verification"]["checkpoints"][0]["score"]["horizons"][0].update(
            latest_observation_completed_at="2026-11-18T03:32:00.000001+00:00"
        ),
        lambda s: s["verification"]["checkpoints"][0]["score"]["horizons"][0][
            "first_observed"
        ].update(observation_completed_at="2026-11-18T03:32:00.000001+00:00"),
    ],
)
def test_future_or_naive_tick_and_cohort_clocks_raise_reporting_error(mutate) -> None:
    scored = horizon(
        1, status="scored", first=observation(), latest=observation(),
    )
    candidate = snapshot(
        checkpoints=[checkpoint("2026-11-17", horizons=[scored, horizon(5)])],
        ticks=[{
            "record_id": "tick",
            "value": {
                "tick_id": "tick",
                "status": "observed",
                "completed_at": "2026-11-18T03:30:00+00:00",
            },
        }],
    )
    mutate(candidate)

    with pytest.raises(ReportingError, match="^invalid_reporting_input$"):
        assess(candidate, health(), now=NOW)


def test_unavailable_outcome_and_no_checkpoint_summary_are_explicit() -> None:
    unavailable_pair = checkpoint(
        "2026-11-17",
        horizons=[horizon(1, status="unavailable"), horizon(5)],
    )

    unavailable_summary = assess(
        snapshot(checkpoints=[unavailable_pair]), health(), now=NOW,
    )["summary"]
    empty_summary = assess(snapshot(), health(), now=NOW)["summary"]

    assert "1-session outcome cohort 2026-11-17: unavailable." in unavailable_summary
    assert "Latest AI: no checkpoint." in empty_summary
    assert "Recorded model receipts 0; usage unknown; billed cost unknown." in empty_summary


def test_nonaccepted_latest_decision_uses_fixed_status_label() -> None:
    pair = checkpoint("2026-11-17", ai_status="in_doubt")

    summary = assess(snapshot(checkpoints=[pair]), health(), now=NOW)["summary"]

    assert "Latest AI 2026-11-17: in doubt." in summary
    assert "untrusted model explanation" not in summary


@pytest.mark.parametrize(
    "bad_now",
    ["2026-11-18T03:32:00+00:00", datetime(2026, 11, 18, 3, 32), float("inf")],
)
def test_malformed_now_raises_public_reporting_error(bad_now: Any) -> None:
    with pytest.raises(ReportingError, match="^invalid_reporting_input$"):
        assess(snapshot(), health(), now=bad_now)


def test_no_registration_or_ticks_has_no_stale_condition() -> None:
    result = assess(snapshot(registered_at=None), health(), now=NOW)

    assert result["conditions"] == {}


def test_unavailable_or_busy_with_public_payload_is_rejected() -> None:
    for status in ("busy", "unavailable"):
        candidate = snapshot()
        candidate["status"] = status
        with pytest.raises(ReportingError, match="^invalid_reporting_input$"):
            assess(candidate, health(), now=NOW)


def test_zero_cash_with_positive_target_omits_cash_line() -> None:
    pair = checkpoint(
        "2026-11-17", cash="0", targets=[{"symbol": "SPY", "weight": "1"}],
    )

    summary = assess(snapshot(checkpoints=[pair]), health(), now=NOW)["summary"]

    assert "Latest AI 2026-11-17: accepted; SPY 100%." in summary
    assert "SPY 100%, cash" not in summary


def test_unexpected_container_failure_is_sanitized() -> None:
    class ExplodingDict(dict):
        def get(self, key, default=None):
            raise KeyError("private detail")

    candidate = snapshot(checkpoints=[checkpoint("2026-11-17")])
    candidate["report"]["counts"] = ExplodingDict(candidate["report"]["counts"])

    with pytest.raises(ReportingError, match="^invalid_reporting_input$"):
        assess(candidate, health(), now=NOW)


def test_large_valid_summary_remains_bounded_without_losing_safety_caveats() -> None:
    targets = [
        {"symbol": f"S{i:03d}" + "X" * 20, "weight": "0.001"}
        for i in range(300)
    ]
    pair = checkpoint("2026-11-17", cash="0.7", targets=targets)

    summary = assess(snapshot(checkpoints=[pair]), health(), now=NOW)["summary"]

    assert len(summary) <= 2000
    assert summary.endswith(
        "Read-only research; no orders. Raw price responses, not profit or adjusted return; "
        "corporate actions unchecked; billed cost unknown; cohorts are not compounded."
    )
