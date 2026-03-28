# Agent Architecture

This document describes the five-agent architecture, their responsibilities, communication patterns, and how the system evolves over time through the Supervisor's learning loop.

## Architecture Overview

```
┌──────────────┐    watchlist     ┌──────────────┐    signals      ┌──────────────────┐
│   Screener   │ ───────────────► │   Watcher    │ ──────────────► │ Portfolio Manager│
│              │    (Redis)       │              │    (Redis)      │                  │
│  RSI-2 scan  │                  │  Entry/exit  │                 │  Position sizing │
│  Regime      │                  │  detection   │                 │  Tier priority   │
│  News filter │                  │              │                 │  Risk checks     │
└──────────────┘                  └──────────────┘                 └────────┬─────────┘
                                                                           │
                                                                  approved_orders
                                                                     (Redis)
                                                                           │
                                                                           ▼
┌──────────────┐                                                  ┌──────────────────┐
│  Supervisor  │ ◄─── monitors all agents, Redis state ──────────►│    Executor      │
│              │                                                  │                  │
│  Health      │      circuit breakers, EOD review,               │  Alpaca API      │
│  Learning    │      daily/weekly/monthly summaries,             │  Stop-losses     │
│  Universe    │      universe re-validation + discovery          │  Rule 1          │
│  Telegram    │                                                  │  Zero LLM        │
└──────────────┘                                                  └──────────────────┘
```

## Data Flow

1. **Screener** computes RSI-2 across the dynamic instrument universe, determines market regime via ADX on SPY, and publishes a ranked watchlist to Redis.
2. **Watcher** monitors the watchlist for entry conditions and open positions for exit conditions. Publishes signals to `trading:signals`.
3. **Portfolio Manager** evaluates each signal: checks drawdown-based tier access, sizes the position using fixed-fractional risk, validates sector correlation, and publishes approved orders to `trading:approved_orders`.
4. **Executor** validates every order against Rule 1 and safety limits, submits to Alpaca, places server-side stop-losses, updates simulated equity, and sends Telegram trade alerts.
5. **Supervisor** monitors everything: health checks every 15 minutes, end-of-day reviews with LLM analysis, circuit breakers, Telegram summaries, and monthly universe management.

## Communication

All inter-agent communication uses Redis:

| Channel/Key | Publisher | Subscriber | Purpose |
|-------------|-----------|------------|---------|
| `trading:watchlist` | Screener | Watcher | Ranked list of instruments near entry conditions |
| `trading:regime` | Screener | Watcher, PM | ADX regime (RANGING, UPTREND, DOWNTREND) |
| `trading:signals` | Watcher | Portfolio Manager | Entry and exit signals |
| `trading:approved_orders` | Portfolio Manager | Executor | Validated, sized orders |
| `trading:positions` | Executor | PM, Supervisor | Current open positions |
| `trading:simulated_equity` | Executor | All | Virtual $5K capital tracker |
| `trading:universe` | Supervisor | Screener, PM | Dynamic instrument list with tiers |
| `trading:tiers` | Supervisor | PM | Quick tier lookup per symbol |
| `trading:drawdown` | Executor | PM, Supervisor | Current drawdown from peak |
| `trading:system_status` | Supervisor | All | active, halted, daily_halt, etc. |
| `trading:heartbeat:{agent}` | Each agent | Supervisor | Liveness monitoring |
| `trading:rejected_signals` | PM | Supervisor | Logged for EOD review |

---

## Agent 1: Screener

**File**: `screener.py`
**Skill**: `skills/screener/SKILL.md`
**LLM usage**: ~3–5 calls/day (news evaluation only)

### Purpose
Scan the active instrument universe for RSI-2 entry conditions and detect market regime.

### Schedule
- End-of-day scan at 4:15 PM ET (after equity market close)
- BTC/USD check every 4 hours
- News monitoring continuous during market hours (keyword filter in code, LLM for materiality assessment)

### Inputs
- `trading:universe` — which instruments to scan
- Alpaca daily bar data
- Alpaca news WebSocket

### Outputs
- `trading:watchlist` — instruments approaching or at entry conditions, ranked by priority and tier
- `trading:regime` — ADX-based market regime (RANGING, UPTREND, DOWNTREND)

### Key Logic
- Computes RSI(2), SMA(200), ATR(14) for every active instrument
- Classifies each as `strong_signal` (RSI-2 < 5), `signal` (RSI-2 < threshold), or `watch` (RSI-2 < threshold + 5)
- Entry threshold adapts to regime: 10 (conservative/ranging) or 5 (aggressive/uptrend)
- News filter uses keyword matching first, invokes LLM only on matches

---

## Agent 2: Watcher

**File**: `watcher.py`
**Skill**: `skills/watcher/SKILL.md`
**LLM usage**: ~2 calls/day (news materiality only)

### Purpose
Generate RSI-2 entry and exit signals based on the watchlist and open positions.

### Schedule
- Evaluates after each Screener scan (triggered by watchlist update)
- Checks open positions for exit conditions every 30 minutes during market hours
- Always checks exits even when system is halted

### Inputs
- `trading:watchlist` — from Screener
- `trading:positions` — current open positions
- `trading:regime` — for threshold selection

### Outputs
- `trading:signals` — published via Redis pub/sub with signal type, indicators, suggested stop, and confidence

### Entry Signal Rules
- **Conservative** (RANGING regime): RSI-2 < 10, Close > SMA-200
- **Aggressive** (UPTREND regime): RSI-2 < 5, Close > SMA-200
- ATR stop multiplier adjusts by regime: 1.5x (ranging), 2.0x (normal), 2.5x (strong trend)

### Exit Signal Rules
- RSI-2 > 60 → take profit
- Close > previous day's high → take profit
- 5 trading days elapsed → time stop
- Price hits stop level → stop loss (sets whipsaw cooldown)

### Validation Filters
- Whipsaw: block re-entry for 24h after entry + stop-loss on same symbol
- BTC fee threshold: reject if expected gain < 0.60%

---

## Agent 3: Portfolio Manager

**File**: `portfolio_manager.py`
**Skill**: `skills/portfolio_manager/SKILL.md`
**LLM usage**: ~3–5 calls/day (GPT-OSS 120B), ~2 calls/week (Claude Sonnet 4 escalation)

### Purpose
Evaluate signals, size positions, enforce risk rules, and approve or reject orders.

### Operating Mode
Reactive — listens on `trading:signals` via Redis pub/sub.

### Position Sizing
Fixed-fractional at 1% risk per trade:
```
max_risk = equity × 0.01 × risk_multiplier × sector_penalty
position_size = max_risk / stop_distance
```
Capped by Rule 1: order value cannot exceed available simulated cash.

### Tier Priority
When multiple signals arrive simultaneously:
1. Tier 1 instruments first (SPY, QQQ, NVDA, XLK, XLY, XLI)
2. Tier 2 second (GOOGL, XLF, META, TSLA, XLC, DIA, BTC/USD)
3. Tier 3 last (V, XLE, XLV, IWM, plus discoveries)

If all 3 position slots are full and a Tier 1 signal arrives, the PM may close the weakest lower-tier position (if at breakeven or profit) to make room.

### Insufficient Funds Handling
- Lower-tier position at breakeven → close it, enter higher-tier signal
- Same/higher tier positions → reject, log for Supervisor review
- Partial position possible → take it if ≥ 50% of target size
- Never queue signals — RSI-2 entries are time-sensitive

### Allocation Limits
- Max 70% equity in stocks, max 30% in BTC/USD
- Max 3 concurrent positions (2 equity + 1 crypto)
- Same-sector penalty: 50% size reduction if 2+ positions in same sector

### Drawdown Response
| Drawdown | Risk Multiplier | Active Tiers |
|----------|----------------|--------------|
| < 5% | 1.0x | All |
| 5–10% | 0.75x | All (Tier 3 reduced) |
| 10–15% | 0.5x | Tier 1 only |
| 15–20% | 0.25x | Tier 1 only, no BTC |
| > 20% | 0x | Halted |

### Escalation to Claude Sonnet 4
- Order exceeds 20% of equity
- Drawdown exceeds 10%
- Signal from Tier 3 or newly discovered instrument
- GPT-OSS 120B reasoning is contradictory

---

## Agent 4: Trade Executor

**File**: `executor.py`
**Skill**: `skills/executor/SKILL.md`
**LLM usage**: Zero. Pure deterministic code.

### Purpose
The only agent that touches the Alpaca API. Validates and executes orders. Manages server-side stop-losses. Tracks simulated equity.

### Safety Validation (every order)
1. Order value ≤ simulated cash (Rule 1)
2. No short selling (Rule 1)
3. Simulated cash ≥ 0 (Rule 1)
4. Daily loss limit not exceeded (3% of equity)
5. Max concurrent positions not exceeded (3)
6. Account not blocked, PDT flag not set

### Order Flow
1. Receive from `trading:approved_orders`
2. Validate against all safety rules
3. Submit to Alpaca (market order for equities, limit for BTC)
4. Wait for fill
5. Submit server-side GTC stop-loss immediately
6. Update `trading:positions` and `trading:simulated_equity` in Redis
7. Send Telegram notification via `notify.py`
8. Log to TimescaleDB

### Simulated Capital Cap
The paper account has $100K but the system enforces a $5K virtual balance:
```python
effective_equity = float(redis.get("trading:simulated_equity"))  # starts at 5000
effective_cash = effective_equity - sum(open_position_values)
```
Updated after every trade close with realized P&L.

### Startup Verification
Runs automatically before any trading begins:
- PDT flag clean
- Account not blocked
- All open positions have active server-side stop-losses
- Simulated equity initialized

---

## Agent 5: Supervisor

**File**: `supervisor.py`
**Skill**: `skills/supervisor/SKILL.md`
**LLM usage**: ~8–10 calls/week

### Purpose
System oversight, circuit breakers, performance learning, Telegram notifications, and universe management.

### Schedule

| Task | Frequency | Type |
|------|-----------|------|
| Health check | Every 15 min (market hours) | Code |
| Circuit breakers | Every 15 min | Code |
| Daily P&L reset | 9:25 AM ET | Code |
| End-of-day review | 4:15 PM ET | LLM |
| Weekly review | Saturday morning | LLM |
| Universe re-validation | 1st of month | Code + LLM |
| Universe discovery | 15th of month | Code + LLM |

### Circuit Breakers
Deterministic, code-enforced, no LLM involvement:
- 10% drawdown → 50% position size, only Tier 1 active
- 15% drawdown → 25% position size, BTC disabled
- 20% drawdown → all trading halted, manual approval required
- 3% daily loss → halted until next session
- Negative cash → immediate halt + critical alert
- PDT flag → immediate halt + critical alert

### End-of-Day Review (The Learning Loop)
1. Gather today's trades, signals, and rejections from Redis + TimescaleDB
2. Compute rolling 30-day per-instrument performance
3. LLM analyzes what worked, what failed, and recommends parameter adjustments
4. Apply approved changes (RSI-2 thresholds, tier promotions/demotions)
5. Send daily summary via Telegram
6. Report capital constraint warnings if Tier 1 signals were rejected

### Telegram Notifications

| Event | Timing |
|-------|--------|
| Trade entry | Immediate (via Executor) |
| Trade exit with P&L | Immediate (via Executor) |
| Daily summary | 4:15 PM ET |
| Weekly summary | Saturday morning |
| Monthly summary | 1st of month |
| Drawdown alert | Immediate |
| Critical system alert | Immediate |
| Universe changes | After monthly jobs |
| Capital constraint warning | End of day |

### Monthly Job 1: Universe Re-Validation (1st of month)
Re-runs `backtest_rsi2_universe.py` on all instruments (active, disabled, and previously failed) using rolling 12-month data. Applies tier thresholds:

| Tier | Profit Factor | Win Rate | Min Trades |
|------|--------------|----------|------------|
| 1 (Core) | ≥ 2.0 | ≥ 70% | ≥ 8 |
| 2 (Standard) | ≥ 1.5 | ≥ 65% | ≥ 5 |
| 3 (Marginal) | ≥ 1.3 | ≥ 60% | ≥ 5 |

Promotion: max one tier up per month. Demotion: can fall multiple tiers. Disabled for 3+ months → archived.

### Monthly Job 2: Universe Discovery (15th of month)
Runs `discover_universe.py` to scan random samples from Alpaca's 12,000+ tradeable assets. Stricter filters than re-validation: ≥ 10 trades, avg trade > 0.30%, WR ≥ 65%, PF ≥ 1.5. Excludes leveraged/inverse ETFs, bond ETFs with tiny moves, and SPACs. New passes enter as Tier 3 (probation), capped at 5 additions per month. Checks sector diversification before adding.

---

## LLM Model Routing

| Agent | Primary Model | Escalation | Calls/Day |
|-------|--------------|------------|-----------|
| Screener | GPT-OSS 120B (Together.ai) | — | 3–5 |
| Watcher | GPT-OSS 120B | — | ~2 |
| Portfolio Manager | GPT-OSS 120B (high effort) | Claude Sonnet 4 | 3–5 |
| Executor | None (zero LLM) | — | 0 |
| Supervisor | GPT-OSS 120B (high effort) | — | 1–2 |

Claude Sonnet 4 is reserved as an escalation path for the Portfolio Manager (~2 calls/week) when decisions involve high stakes, contradictory reasoning, or newly discovered instruments.

---

## Dynamic Universe

The instrument universe is not static. It evolves through three mechanisms:

1. **Daily tuning**: The Supervisor's EOD review can adjust RSI-2 entry thresholds per instrument and temporarily pause underperformers.

2. **Monthly re-validation**: Re-backtests everything. Instruments that have improved get promoted. Instruments that have degraded get demoted or disabled.

3. **Monthly discovery**: Scans new candidates from the broader market. Finds instruments we'd never think to check — gold miners, semiconductor companies, niche ETFs — and adds them on probation.

The universe starts with 17 instruments producing ~125 trades/year. Over time it grows organically toward its natural ceiling as the discovery scanner finds new instruments that mean-revert cleanly on RSI-2.

### Current Universe (v1.0.0)

**Tier 1 — Core** (always active, ~46 trades/year):
SPY, QQQ, NVDA, XLK, XLY, XLI

**Tier 2 — Standard** (disabled during 10%+ drawdowns, ~53 trades/year):
GOOGL, XLF, META, TSLA, XLC, DIA, BTC/USD

**Tier 3 — Marginal** (active only when higher tiers idle, ~26 trades/year):
V, XLE, XLV, IWM

v1.0.0
