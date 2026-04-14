# LangAlpha Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate that LangAlpha produces useful, actionable research on this trading system before committing to full integration.

**Architecture:** Clone LangAlpha locally (Mac). Docker sandbox mode — no Daytona, no paid APIs. Feed it a real 30-day trade history CSV + strategy context. Write one custom skill. Run it. Judge output quality.

**Tech Stack:** LangAlpha (Docker Compose local), Claude BYOK via OAuth or `ANTHROPIC_API_KEY`, yfinance MCP (free), TimescaleDB (read-only export)

**This is evaluation, not production code. No tests. No commits to trading-system (except the findings doc at the end). Output is a written assessment.**

---

## Task 1: Stand up LangAlpha locally

**Files:**
- No changes to trading-system repo

- [ ] **Step 1: Clone LangAlpha**

```bash
cd ~/local_repos
git clone https://github.com/ginlix-ai/LangAlpha.git langalpha-spike
cd langalpha-spike
```

- [ ] **Step 2: Configure environment**

```bash
cp .env.example .env
```

Edit `.env` — minimum required settings:
```
# LLM — use existing Anthropic key
ANTHROPIC_API_KEY=<your key from ~/.trading_env>

# Sandbox — Docker local, no Daytona
SANDBOX_PROVIDER=docker
# Leave DAYTONA_API_KEY empty

# Auth — disable for local dev
# Leave SUPABASE_URL empty

# Infrastructure — let Docker Compose spin up its own postgres + redis
COMPOSE_PROFILES=infra
```

Leave everything else at defaults. Yahoo Finance MCP works with no key.

- [ ] **Step 3: Start it**

```bash
docker compose up -d
```

Expected: postgres, redis, backend, frontend containers all start. May take 2–3 min on first run (image pulls + migrations).

- [ ] **Step 4: Verify it's up**

```bash
curl -s http://localhost:8000/health
```

Expected: `{"status":"ok"}` or similar. Open `http://localhost:5173` — should see LangAlpha UI.

---

## Task 2: Export trade history from TimescaleDB

**Files:**
- Create (temp, not committed): `/tmp/spike-trades.csv`, `/tmp/spike-daily-summary.csv`

- [ ] **Step 1: Export trades (run on VPS)**

```bash
ssh linuxuser@openboog
source ~/.trading_env
psql $DATABASE_URL -c "\COPY (
  SELECT symbol, side, price, quantity, realized_pnl, fees,
         time AT TIME ZONE 'America/New_York' AS trade_time,
         exit_reason
  FROM trades
  WHERE time >= NOW() - INTERVAL '30 days'
  ORDER BY time DESC
) TO '/tmp/spike-trades.csv' WITH CSV HEADER"
```

- [ ] **Step 2: Export daily summary (run on VPS)**

```bash
psql $DATABASE_URL -c "\COPY (
  SELECT date, starting_equity, ending_equity, daily_pnl,
         drawdown_pct, trades_executed, winning_trades, losing_trades,
         total_fees, regime
  FROM daily_summary
  WHERE date >= CURRENT_DATE - INTERVAL '30 days'
  ORDER BY date DESC
) TO '/tmp/spike-daily-summary.csv' WITH CSV HEADER"
```

- [ ] **Step 3: Copy to local machine**

```bash
scp linuxuser@openboog:/tmp/spike-trades.csv /tmp/spike-trades.csv
scp linuxuser@openboog:/tmp/spike-daily-summary.csv /tmp/spike-daily-summary.csv
```

---

## Task 3: Create workspace + strategy context

- [ ] **Step 1: Create a workspace in LangAlpha UI**

In the browser at `http://localhost:5173`:
- Create a new workspace named "Trading System Research"

- [ ] **Step 2: Upload trade history CSVs**

Upload `/tmp/spike-trades.csv` and `/tmp/spike-daily-summary.csv` to the workspace `data/` directory.

- [ ] **Step 3: Populate agent.md with strategy context**

In the workspace, open or create `agent.md` and paste:

```markdown
# Trading System Research Workspace

## System Overview
Autonomous RSI-2 mean reversion trading system. 5 agents (Screener, Watcher, Portfolio Manager, Executor, Supervisor). Trades 17 instruments across 3 tiers via Alpaca paper trading API. ~$5,000 simulated capital.

## Strategy
- **Entry**: RSI(2) < threshold (varies by instrument, default ~10), price > SMA(200), volume > 50% of 20d ADV
- **Exit**: RSI(2) > 60, price > previous day high, 5-day time stop, stop-loss hit
- **Stop-loss**: Server-side GTC, ~2× ATR below entry
- **Trailing stop**: Activates after N% gain (tier-dependent)
- **Whipsaw protection**: 24h cooldown after stop-loss exit

## Tiers
- Tier 1 (always active): SPY, QQQ, NVDA, XLK, XLY, XLI — requires PF ≥ 2.0, WR ≥ 70%
- Tier 2 (disabled at 10%+ drawdown): GOOGL, XLF, META, TSLA, XLC, DIA, BTC/USD — requires PF ≥ 1.5, WR ≥ 65%
- Tier 3 (active only when Tier 1+2 idle): V, XLE, XLV, IWM — requires PF ≥ 1.3, WR ≥ 60%

## Circuit Breakers
- 10% drawdown: disable Tier 2+
- 15% drawdown: disable Tier 2+ AND reduce sizes
- 20% drawdown: halt all trading

## Data Files (in workspace data/)
- spike-trades.csv: last 30 days of closed trades (symbol, side, price, qty, realized_pnl, fees, trade_time, exit_reason)
- spike-daily-summary.csv: last 30 days of daily summaries (equity, P&L, drawdown, regime, trade counts)

## Research Goals
1. Identify patterns in losing trades — common entry conditions or exit reasons?
2. Assess whether current RSI-2 thresholds are appropriate per instrument
3. Identify any tier assignment changes that seem warranted
4. Flag instruments consistently underperforming
5. Suggest strategy adjustments worth backtesting
```

---

## Task 4: Write and run custom skill

- [ ] **Step 1: Create skill in workspace**

In the LangAlpha workspace, create `skills/weekly-trading-review/SKILL.md`:

```markdown
# Weekly Trading System Review

Analyze the trading system's last 30 days of performance and produce an actionable weekly review.

## Data Available
- `spike-trades.csv`: closed trades — symbol, side, price, quantity, realized_pnl, fees, trade_time, exit_reason
- `spike-daily-summary.csv`: daily equity, P&L, drawdown, regime, win/loss counts

## Analysis Steps
1. Compute overall: total trades, win rate, profit factor, total P&L, total fees
2. Break down P&L by instrument — identify best and worst performers
3. Break down P&L by exit reason (rsi_exit, time_stop, stop_loss, prev_high) — which are most/least profitable?
4. Break down by regime — does performance differ in RANGING vs UPTREND vs DOWNTREND?
5. Identify instruments where win rate < 50% or profit factor < 1.0
6. Flag any instrument where RSI-2 threshold may be miscalibrated (few signals vs many stop-outs)
7. Check if Tier 3 instruments are adding value or diluting attention

## Output Format
Produce a structured report with:
- Executive summary (3-5 bullets)
- Performance table by instrument
- Exit reason analysis
- Regime analysis
- Specific recommendations: each as CHANGE / MONITOR / INVESTIGATE + rationale + suggested action
- Open questions that need more data
```

- [ ] **Step 2: Activate and run the skill**

In the LangAlpha workspace chat, type: `/weekly-trading-review`

Let it run. Observe and note:
- Does it read and parse the CSV files correctly?
- Does it write analysis code (PTC) or dump raw data into LLM context?
- Does the output address all 7 analysis steps?
- How long does it take?
- Does `agent.md` get updated with findings after the run?

- [ ] **Step 3: Document findings**

Create `docs/superpowers/plans/2026-04-14-langalpha-spike-findings.md` in the trading-system repo with:

```markdown
# LangAlpha Spike Findings

Date: 2026-04-14

## Setup
- LangAlpha version: <git sha>
- Sandbox: docker (local)
- LLM: <model used>

## Output Quality
- Accurate vs data: <yes/no/partially — any hallucinations?>
- Actionable recommendations: <yes/no — were they specific and implementable?>
- Addressed all 7 analysis steps: <yes/no — which were skipped or weak?>

## PTC Behavior
- Wrote analysis Python: <yes/no>
- Code quality: <notes>
- Token efficiency vs raw dump: <observations>

## Workspace Persistence
- agent.md updated after run: <yes/no>
- Would compound meaningfully across weekly runs: <yes/no/maybe>

## Gaps
- Context it asked for or got wrong: <list>
- Output format issues: <any JSON/structure problems>

## Verdict
[ ] Proceed to full integration (Plan B)
[ ] Proceed with modifications (list them)
[ ] Discard — build native Analyst agent instead

## Notes
<free-form observations>
```

- [ ] **Step 4: Commit findings doc**

```bash
git add docs/superpowers/plans/2026-04-14-langalpha-spike-findings.md
git commit -m "docs: LangAlpha spike findings and integration verdict"
```
