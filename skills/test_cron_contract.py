"""Contract tests for the deployable system cron file."""
from pathlib import Path


CRON_PATH = Path(__file__).parents[1] / "cron" / "trading-system-cron"


def _job_lines():
    return [
        line.strip()
        for line in CRON_PATH.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "python3 " in line
    ]


def test_crypto_screener_runs_every_four_hours_seven_days_a_week():
    jobs = _job_lines()
    crypto_jobs = [
        line for line in jobs
        if "skills/screener/screener.py --asset-class crypto" in line
    ]

    assert len(crypto_jobs) == 1
    assert crypto_jobs[0].split()[:5] == ["0", "*/4", "*", "*", "*"]


def test_cron_does_not_schedule_reconcile_or_refits():
    cron = CRON_PATH.read_text()
    assert "--reconcile" not in cron
    assert "--refit-thresholds" not in cron
