# LangAlpha Full Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **⚠️ PREREQUISITE: Complete spike plan first. Review `docs/superpowers/plans/2026-04-14-langalpha-spike-findings.md` before executing any task here. This plan may be revised or discarded based on spike findings.**

**Goal:** Integrate LangAlpha as a self-hosted research layer on the VPS. It receives daily trade exports, maintains compounding context, and produces structured recommendations that Supervisor ingests to complete the TODO LLM hooks already stubbed in supervisor.py.

**Architecture:**
- LangAlpha runs on VPS alongside existing trading system (separate Docker Compose stack)
- `scripts/export_trades.py` queries TimescaleDB nightly → writes CSVs to `~/langalpha-data/`
- LangAlpha scheduled automations trigger daily/weekly/monthly reviews via custom skills
- LangAlpha writes structured JSON recommendations to `~/langalpha-data/results/`
- `scripts/ingest_research.py` parses those files and extracts actionable recommendations
- Supervisor calls ingestion functions in EOD loop (completing supervisor.py:551 and supervisor.py:621–626 TODOs)

**Tech Stack:** LangAlpha (Docker Compose on VPS), TimescaleDB, Redis, existing supervisor.py patterns, new `scripts/export_trades.py` + `scripts/ingest_research.py`

---

## Task 1: Trade history export script

**Files:**
- Create: `scripts/export_trades.py`
- Create: `tests/test_export_trades.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_export_trades.py
import csv
import io
from unittest.mock import MagicMock

from export_trades import export_trades_csv, export_daily_summary_csv


def test_export_trades_csv_writes_expected_columns():
    mock_rows = [
        ("SPY", "sell", 450.0, 10, 25.50, 0.50, "2026-04-14 15:30:00", "rsi_exit")
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value.fetchall.return_value = mock_rows

    buf = io.StringIO()
    export_trades_csv(mock_conn, buf, days=30)
    buf.seek(0)
    rows = list(csv.DictReader(buf))

    assert len(rows) == 1
    assert rows[0]["symbol"] == "SPY"
    assert rows[0]["exit_reason"] == "rsi_exit"
    assert "realized_pnl" in rows[0]


def test_export_daily_summary_csv_writes_expected_columns():
    mock_rows = [
        ("2026-04-14", 5000.0, 5025.0, 25.0, 0.5, 3, 2, 1, 1.50, "RANGING")
    ]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value.fetchall.return_value = mock_rows

    buf = io.StringIO()
    export_daily_summary_csv(mock_conn, buf, days=30)
    buf.seek(0)
    rows = list(csv.DictReader(buf))

    assert rows[0]["regime"] == "RANGING"
    assert rows[0]["daily_pnl"] == "25.0"


def test_export_trades_csv_empty_result():
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value.fetchall.return_value = []

    buf = io.StringIO()
    export_trades_csv(mock_conn, buf, days=30)
    buf.seek(0)
    rows = list(csv.DictReader(buf))
    assert rows == []
```

Run: `PYTHONPATH=scripts python3 -m pytest tests/test_export_trades.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'export_trades'`

- [ ] **Step 2: Implement export script**

Create `scripts/export_trades.py`:

```python
"""
Export TimescaleDB trade data to CSV for LangAlpha research workspace.
Run daily via cron: 0 17 * * 1-5 (5 PM ET Mon-Fri, after EOD).
Output dir: LANGALPHA_DATA_DIR env var (default: ~/langalpha-data).
"""
import csv
import os
from datetime import datetime
from pathlib import Path

import psycopg2

from config import DB_CONFIG

OUTPUT_DIR = Path(os.environ.get("LANGALPHA_DATA_DIR", Path.home() / "langalpha-data"))

_TRADES_COLUMNS = [
    "symbol", "side", "price", "quantity", "realized_pnl",
    "fees", "trade_time", "exit_reason",
]
_SUMMARY_COLUMNS = [
    "date", "starting_equity", "ending_equity", "daily_pnl",
    "drawdown_pct", "trades_executed", "winning_trades",
    "losing_trades", "total_fees", "regime",
]
_TRADES_SQL = """
    SELECT symbol, side, price, quantity, realized_pnl, fees,
           time AT TIME ZONE 'America/New_York' AS trade_time,
           exit_reason
    FROM trades
    WHERE time >= NOW() - INTERVAL %s
      AND side = 'sell'
    ORDER BY time DESC
"""
_SUMMARY_SQL = """
    SELECT date, starting_equity, ending_equity, daily_pnl,
           drawdown_pct, trades_executed, winning_trades, losing_trades,
           total_fees, regime
    FROM daily_summary
    WHERE date >= CURRENT_DATE - INTERVAL %s
    ORDER BY date DESC
"""


def _write_csv(conn, sql, params, columns, dest):
    if isinstance(dest, (str, Path)):
        with open(dest, "w", newline="") as f:
            _write_csv(conn, sql, params, columns, f)
        return
    writer = csv.DictWriter(dest, fieldnames=columns)
    writer.writeheader()
    with conn.cursor() as cur:
        cur.execute(sql, params)
        for row in cur.fetchall():
            writer.writerow(dict(zip(columns, row)))


def export_trades_csv(conn, dest, days=90):
    _write_csv(conn, _TRADES_SQL, (f"{days} days",), _TRADES_COLUMNS, dest)


def export_daily_summary_csv(conn, dest, days=90):
    _write_csv(conn, _SUMMARY_SQL, (f"{days} days",), _SUMMARY_COLUMNS, dest)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = psycopg2.connect(**DB_CONFIG)
    try:
        stamp = datetime.now().strftime("%Y-%m-%d")
        export_trades_csv(conn, OUTPUT_DIR / f"trades-{stamp}.csv")
        export_daily_summary_csv(conn, OUTPUT_DIR / f"daily-summary-{stamp}.csv")
        print(f"Exported trades and daily-summary for {stamp} to {OUTPUT_DIR}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run tests — verify pass**

```bash
PYTHONPATH=scripts python3 -m pytest tests/test_export_trades.py -v
```
Expected: PASS (3 tests)

- [ ] **Step 4: Commit**

```bash
git add scripts/export_trades.py tests/test_export_trades.py
git commit -m "feat: export_trades.py — nightly CSV export to LangAlpha workspace"
```

---

## Task 2: Custom LangAlpha skills

**Files:**
- Create: `langalpha-skills/daily-trading-review/SKILL.md`
- Create: `langalpha-skills/weekly-strategy-analysis/SKILL.md`
- Create: `langalpha-skills/monthly-tier-revalidation/SKILL.md`

These are checked into the trading-system repo and deployed to the LangAlpha workspace on the VPS.

- [ ] **Step 1: Create daily review skill**

Create `langalpha-skills/daily-trading-review/SKILL.md`:

```markdown
# Daily Trading Review

Analyze today's trading activity and produce a concise EOD report.

## Data
Read the latest trades-YYYY-MM-DD.csv and daily-summary-YYYY-MM-DD.csv from the workspace data/ directory.

## Analysis
1. Today's P&L, win/loss count, fees
2. Trades that stopped out — were entry conditions actually valid?
3. Current drawdown level — which circuit breaker tier are we in?
4. Regime today vs yesterday — any shift?
5. Instruments generating unusual signal frequency (possible threshold miscalibration)?

## Output
Write results/daily-YYYY-MM-DD.json:
{
  "date": "YYYY-MM-DD",
  "summary": "<2-sentence summary>",
  "pnl": <float>,
  "win_rate_today": <float>,
  "regime": "<RANGING|UPTREND|DOWNTREND>",
  "flags": ["<notable observations>"],
  "recommendations": []
}
```

- [ ] **Step 2: Create weekly analysis skill**

Create `langalpha-skills/weekly-strategy-analysis/SKILL.md`:

```markdown
# Weekly Strategy Analysis

Analyze the last 7 days and produce recommendations for strategy adjustments.

## Data
Latest trades CSV and daily-summary CSV. Use last 7 days only.

## Analysis
1. Win rate and profit factor by instrument
2. Win rate and profit factor by exit reason
3. Performance by regime
4. Instruments with PF < 1.0 — tier demotion candidates
5. Instruments with PF > 2.5 and WR > 75% — tier promotion candidates
6. RSI-2 thresholds that appear miscalibrated (too many false entries or stop-outs)

## Output
Write results/weekly-YYYY-MM-DD.json:
{
  "week_ending": "YYYY-MM-DD",
  "summary": ["<bullet>", "<bullet>", "<bullet>"],
  "instrument_performance": [
    {"symbol": "<sym>", "pf": <float>, "win_rate": <float>, "trades": <int>}
  ],
  "recommendations": [
    {
      "type": "TIER_CHANGE|THRESHOLD_ADJUST|INVESTIGATE|MONITOR",
      "symbol": "<symbol or ALL>",
      "current_value": "<current setting or null>",
      "suggested_value": "<suggestion or null>",
      "rationale": "<1-2 sentences>",
      "confidence": "HIGH|MEDIUM|LOW"
    }
  ]
}
```

- [ ] **Step 3: Create monthly revalidation skill**

Create `langalpha-skills/monthly-tier-revalidation/SKILL.md`:

```markdown
# Monthly Tier Revalidation

Revalidate tier assignments for all instruments based on rolling 90-day performance.

## Data
Full 90-day trades CSV and daily-summary CSV.

## Tier Criteria
- Tier 1: PF ≥ 2.0 AND WR ≥ 70% AND ≥ 8 trades
- Tier 2: PF ≥ 1.5 AND WR ≥ 65% AND ≥ 5 trades
- Tier 3: PF ≥ 1.3 AND WR ≥ 60% AND ≥ 5 trades
- Below Tier 3: recommend archive

## Rules
- Max one tier promotion per instrument per month
- Demotion can happen immediately if thresholds clearly missed
- < 5 trades: mark INSUFFICIENT_DATA, do not reassign

## Output
Write results/monthly-YYYY-MM.json:
{
  "month": "YYYY-MM",
  "current_universe": {"tier1": [...], "tier2": [...], "tier3": [...]},
  "recommended_universe": {"tier1": [...], "tier2": [...], "tier3": [...], "archive": [...]},
  "changes": [
    {
      "symbol": "<sym>",
      "current_tier": <int>,
      "recommended_tier": <int or "archive">,
      "rationale": "<sentence>",
      "pf_90d": <float>,
      "wr_90d": <float>,
      "trades_90d": <int>
    }
  ]
}
```

- [ ] **Step 4: Commit skills**

```bash
git add langalpha-skills/
git commit -m "feat: LangAlpha custom skills for daily/weekly/monthly trading reviews"
```

---

## Task 3: Research ingestion script

**Files:**
- Create: `scripts/ingest_research.py`
- Create: `tests/test_ingest_research.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ingest_research.py
import json
from ingest_research import (
    Recommendation,
    TierChange,
    parse_weekly_recommendations,
    parse_monthly_tier_changes,
)

WEEKLY_FIXTURE = {
    "week_ending": "2026-04-14",
    "recommendations": [
        {
            "type": "THRESHOLD_ADJUST",
            "symbol": "TSLA",
            "current_value": "10",
            "suggested_value": "8",
            "rationale": "Too many false entries at RSI 10",
            "confidence": "HIGH",
        },
        {
            "type": "MONITOR",
            "symbol": "SPY",
            "current_value": None,
            "suggested_value": None,
            "rationale": "Performance stable",
            "confidence": "LOW",
        },
    ],
}

MONTHLY_FIXTURE = {
    "month": "2026-04",
    "changes": [
        {
            "symbol": "TSLA",
            "current_tier": 2,
            "recommended_tier": 3,
            "rationale": "PF 1.2 over 90d — below Tier 2 threshold",
            "pf_90d": 1.2,
            "wr_90d": 0.58,
            "trades_90d": 8,
        }
    ],
}


def test_parse_weekly_filters_monitor_and_low_confidence():
    recs = parse_weekly_recommendations(WEEKLY_FIXTURE)
    assert len(recs) == 1
    assert recs[0].symbol == "TSLA"
    assert recs[0].rec_type == "THRESHOLD_ADJUST"
    assert recs[0].suggested_value == "8"


def test_parse_monthly_tier_changes():
    changes = parse_monthly_tier_changes(MONTHLY_FIXTURE)
    assert len(changes) == 1
    assert changes[0].symbol == "TSLA"
    assert changes[0].current_tier == 2
    assert changes[0].recommended_tier == 3


def test_parse_weekly_empty():
    assert parse_weekly_recommendations({"recommendations": []}) == []


def test_parse_monthly_empty():
    assert parse_monthly_tier_changes({"changes": []}) == []
```

Run: `PYTHONPATH=scripts python3 -m pytest tests/test_ingest_research.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ingest_research'`

- [ ] **Step 2: Implement ingest_research.py**

Create `scripts/ingest_research.py`:

```python
"""
Parse LangAlpha research output files and extract actionable recommendations.
Called by Supervisor EOD loop to feed LLM findings into decision layer.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Recommendation:
    rec_type: str        # THRESHOLD_ADJUST | TIER_CHANGE | INVESTIGATE | MONITOR
    symbol: str
    current_value: Optional[str]
    suggested_value: Optional[str]
    rationale: str
    confidence: str      # HIGH | MEDIUM | LOW


@dataclass
class TierChange:
    symbol: str
    current_tier: int
    recommended_tier: int | str   # int 1-3 or "archive"
    rationale: str
    pf_90d: float
    wr_90d: float
    trades_90d: int


_ACTIONABLE_TYPES = {"THRESHOLD_ADJUST", "TIER_CHANGE", "INVESTIGATE"}
_ACTIONABLE_CONFIDENCE = {"HIGH", "MEDIUM"}


def parse_weekly_recommendations(data: dict) -> list[Recommendation]:
    """Return actionable, non-MONITOR, medium-or-higher-confidence recommendations."""
    return [
        Recommendation(
            rec_type=r["type"],
            symbol=r["symbol"],
            current_value=r.get("current_value"),
            suggested_value=r.get("suggested_value"),
            rationale=r["rationale"],
            confidence=r["confidence"],
        )
        for r in data.get("recommendations", [])
        if r.get("type") in _ACTIONABLE_TYPES
        and r.get("confidence") in _ACTIONABLE_CONFIDENCE
    ]


def parse_monthly_tier_changes(data: dict) -> list[TierChange]:
    """Return all recommended tier changes from monthly revalidation."""
    return [
        TierChange(
            symbol=c["symbol"],
            current_tier=c["current_tier"],
            recommended_tier=c["recommended_tier"],
            rationale=c["rationale"],
            pf_90d=c["pf_90d"],
            wr_90d=c["wr_90d"],
            trades_90d=c["trades_90d"],
        )
        for c in data.get("changes", [])
    ]


def load_latest_weekly(results_dir: Path) -> list[Recommendation]:
    files = sorted(results_dir.glob("weekly-*.json"), reverse=True)
    if not files:
        return []
    return parse_weekly_recommendations(json.loads(files[0].read_text()))


def load_latest_monthly(results_dir: Path) -> list[TierChange]:
    files = sorted(results_dir.glob("monthly-*.json"), reverse=True)
    if not files:
        return []
    return parse_monthly_tier_changes(json.loads(files[0].read_text()))
```

- [ ] **Step 3: Run tests — verify pass**

```bash
PYTHONPATH=scripts python3 -m pytest tests/test_ingest_research.py -v
```
Expected: PASS (4 tests)

- [ ] **Step 4: Commit**

```bash
git add scripts/ingest_research.py tests/test_ingest_research.py
git commit -m "feat: ingest_research.py — parse LangAlpha weekly/monthly research output"
```

---

## Task 4: Wire Supervisor TODO hooks

**Files:**
- Modify: `skills/supervisor/supervisor.py`
- Modify: `tests/supervisor/test_supervisor.py` (confirm path before editing)

> **Read supervisor.py:316–642 first to confirm current line numbers for the TODO hooks before making edits.**

- [ ] **Step 1: Write failing tests**

Find the supervisor test file: `find tests/ -name "*supervisor*"`. Add:

```python
# In existing supervisor test file
import json
from pathlib import Path
from unittest.mock import MagicMock, patch


def test_apply_weekly_recommendations_sends_telegram(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "weekly-2026-04-14.json").write_text(json.dumps({
        "week_ending": "2026-04-14",
        "recommendations": [{
            "type": "THRESHOLD_ADJUST",
            "symbol": "TSLA",
            "current_value": "10",
            "suggested_value": "8",
            "rationale": "Too many false entries",
            "confidence": "HIGH",
        }]
    }))

    with patch("supervisor.notify") as mock_notify:
        from supervisor import apply_weekly_recommendations
        apply_weekly_recommendations(results_dir)
        mock_notify.assert_called_once()
        msg = mock_notify.call_args[0][0]
        assert "TSLA" in msg
        assert "THRESHOLD_ADJUST" in msg


def test_apply_weekly_recommendations_no_op_when_no_files(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    with patch("supervisor.notify") as mock_notify:
        from supervisor import apply_weekly_recommendations
        apply_weekly_recommendations(results_dir)
        mock_notify.assert_not_called()


def test_apply_monthly_tier_changes_moves_symbol_in_redis(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    (results_dir / "monthly-2026-04.json").write_text(json.dumps({
        "month": "2026-04",
        "changes": [{
            "symbol": "TSLA",
            "current_tier": 2,
            "recommended_tier": 3,
            "rationale": "PF below threshold",
            "pf_90d": 1.2,
            "wr_90d": 0.58,
            "trades_90d": 8,
        }]
    }))

    mock_redis = MagicMock()
    with patch("supervisor.notify"):
        from supervisor import apply_monthly_tier_changes
        apply_monthly_tier_changes(mock_redis, results_dir)
        mock_redis.lrem.assert_any_call("trading:universe:tier2", 0, "TSLA")
        mock_redis.rpush.assert_any_call("trading:universe:tier3", "TSLA")
```

Run: `PYTHONPATH=scripts python3 -m pytest tests/ -k "weekly_recommendations or monthly_tier" -v`
Expected: FAIL — `ImportError: cannot import name 'apply_weekly_recommendations' from 'supervisor'`

- [ ] **Step 2: Add import and constant to supervisor.py**

At the top of `skills/supervisor/supervisor.py`, after existing imports:

```python
import os
from pathlib import Path
from ingest_research import load_latest_weekly, load_latest_monthly

LANGALPHA_RESULTS_DIR = Path(os.environ.get(
    "LANGALPHA_RESULTS_DIR",
    Path.home() / "langalpha-data" / "results"
))
```

- [ ] **Step 3: Add apply functions to supervisor.py**

Add before `run_eod_review`:

```python
def apply_weekly_recommendations(results_dir=None):
    """Ingest LangAlpha weekly recommendations. Notifies via Telegram; does not auto-apply."""
    if results_dir is None:
        results_dir = LANGALPHA_RESULTS_DIR
    recs = load_latest_weekly(Path(results_dir))
    if not recs:
        return
    lines = ["📊 *LangAlpha Weekly Recommendations*"]
    for r in recs:
        line = f"• *{r.rec_type}* {r.symbol}: {r.rationale}"
        if r.suggested_value:
            line += f" → suggest {r.suggested_value}"
        line += f" [{r.confidence}]"
        lines.append(line)
    notify("\n".join(lines))


def apply_monthly_tier_changes(r, results_dir=None):
    """Apply LangAlpha monthly tier revalidation to Redis universe."""
    if results_dir is None:
        results_dir = LANGALPHA_RESULTS_DIR
    changes = load_latest_monthly(Path(results_dir))
    if not changes:
        return
    for change in changes:
        old_key = f"trading:universe:tier{change.current_tier}"
        new_key = (
            f"trading:universe:tier{change.recommended_tier}"
            if isinstance(change.recommended_tier, int)
            else "trading:universe:archived"
        )
        r.lrem(old_key, 0, change.symbol)
        r.rpush(new_key, change.symbol)
        notify(
            f"🔄 Tier change: {change.symbol} "
            f"tier{change.current_tier} → tier{change.recommended_tier} "
            f"(PF={change.pf_90d:.2f}, WR={change.wr_90d:.0%}, "
            f"trades={change.trades_90d})\n{change.rationale}"
        )
```

- [ ] **Step 4: Wire into existing TODO hooks**

In `run_eod_review` where the TODO comment is (~line 551):
```python
# Before: # TODO: LLM analysis
# After:
apply_weekly_recommendations()
```

In monthly revalidation where the TODO comment is (~line 621):
```python
# Before: # TODO: Pass results + current universe to LLM for promotion/demotion decisions
# After:
apply_monthly_tier_changes(r)
```

- [ ] **Step 5: Run all tests — verify pass**

```bash
PYTHONPATH=scripts python3 -m pytest tests/ -v
```
Expected: PASS (all existing + 3 new tests)

- [ ] **Step 6: Commit**

```bash
git add skills/supervisor/supervisor.py tests/
git commit -m "feat: supervisor ingests LangAlpha weekly/monthly research via EOD + monthly hooks"
```

---

## Task 5: VPS deployment + cron

> **Infrastructure task. Execute on VPS via SSH. No trading-system code changes.**

- [ ] **Step 1: Clone and configure LangAlpha on VPS**

```bash
ssh linuxuser@openboog
cd ~
git clone https://github.com/ginlix-ai/LangAlpha.git langalpha
cd langalpha
cp .env.example .env
```

Edit `~/langalpha/.env`:
```
ANTHROPIC_API_KEY=<from ~/.trading_env>
SANDBOX_PROVIDER=docker
COMPOSE_PROFILES=infra
# Leave DAYTONA_API_KEY empty
# Leave SUPABASE_URL empty
```

```bash
docker compose up -d
curl -s http://localhost:8000/health
```

- [ ] **Step 2: Create workspace + populate agent.md**

Open LangAlpha UI (via Tailscale to openboog:5173):
- Create workspace "Trading System Research"
- Upload skills from `~/trading-system/langalpha-skills/` to workspace
- Populate `agent.md` with strategy context (copy from spike, refined based on findings)

- [ ] **Step 3: Set up data dir and cron**

```bash
mkdir -p ~/langalpha-data/results
```

```bash
crontab -e
```

Add:
```
# Export trade data to LangAlpha workspace (5 PM ET Mon-Fri)
0 17 * * 1-5 source ~/.trading_env && PYTHONPATH=/home/linuxuser/trading-system/scripts python3 /home/linuxuser/trading-system/scripts/export_trades.py >> /home/linuxuser/trading-system/logs/export_trades.log 2>&1
```

LangAlpha scheduled automations (configure in LangAlpha UI under Automations):
- Daily (Mon-Fri 5:15 PM ET): `/daily-trading-review`
- Weekly (Friday 4:45 PM ET): `/weekly-strategy-analysis`
- Monthly (1st of month 5:00 PM ET): `/monthly-tier-revalidation`

- [ ] **Step 4: Verify end-to-end**

Trigger manual export and review:

```bash
# Run export manually
source ~/.trading_env && PYTHONPATH=/home/linuxuser/trading-system/scripts \
  python3 /home/linuxuser/trading-system/scripts/export_trades.py

# Confirm files created
ls ~/langalpha-data/

# Manually trigger weekly review from LangAlpha UI, then:
ls ~/langalpha-data/results/

# Confirm Supervisor can read output
PYTHONPATH=/home/linuxuser/trading-system/scripts python3 -c "
from pathlib import Path
from ingest_research import load_latest_weekly
recs = load_latest_weekly(Path.home() / 'langalpha-data/results')
print(f'{len(recs)} actionable recommendations found')
for r in recs:
    print(f'  {r.rec_type} {r.symbol}: {r.rationale}')
"
```

---

## Verification Checklist

- [ ] `~/langalpha-data/trades-YYYY-MM-DD.csv` exists after 5 PM Mon-Fri
- [ ] `~/langalpha-data/results/daily-YYYY-MM-DD.json` written after daily review
- [ ] `~/langalpha-data/results/weekly-YYYY-MM-DD.json` written after weekly review
- [ ] Telegram message received after Supervisor EOD loop runs with weekly recommendations
- [ ] After monthly run: `redis-cli lrange trading:universe:tier2 0 -1` reflects any tier changes
- [ ] All tests pass: `PYTHONPATH=scripts python3 -m pytest tests/ -v`

---

*Key open questions resolved by spike findings:*
- *Does LangAlpha PTC correctly parse our CSV structure without manual coaching?*
- *Does weekly-strategy-analysis output match the JSON schema expected by ingest_research.py, or does the schema need adjustment?*
- *Does agent.md compounding add meaningful value across weekly review sessions?*
