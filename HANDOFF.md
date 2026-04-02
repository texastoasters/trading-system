# HANDOFF: Active Issues and System State

**Date**: 2026-03-31
**Context**: Transferring from Claude.ai conversation to Claude Code for continued development.
**Delete this file when all issues are resolved.**

## Current System State

### Positions
- **XLI**: 3 shares @ $158.19, entry 2026-03-30, Tier 1. STILL HELD on Alpaca. Stop-loss needs to be resubmitted (see Bug 1 below). There may be pending open orders on Alpaca blocking the stop-loss submission — cancel ALL open orders first, then place the stop.
- **GOOGL**: SOLD @ $281.56 on 2026-03-31 (profit +$7.92). No longer on Alpaca but Redis has a garbage 0-quantity entry that needs to be cleaned up.

### Redis State (needs reconciliation)
```
trading:simulated_equity = "5018.54"  # WRONG — should be "5007.92" ($5000 + $7.92 GOOGL profit)
trading:daily_pnl = "18.54"           # WRONG — should be "7.92"
trading:positions = has GOOGL with qty=0  # WRONG — should only have XLI
```

### Alpaca State (source of truth)
- Open positions: XLI 3 shares @ $158.19
- Open orders: ~9 pending GOOGL buy orders (qty=1 each) + 1 pending XLI sell order — ALL need to be cancelled
- No active stop-loss orders for XLI (was cancelled during failed sell attempt)

## IMMEDIATE FIX NEEDED: Reconcile state

Before fixing any code, the live state must be reconciled:

1. Cancel ALL open orders on Alpaca: `tc.cancel_orders()`
2. Wait 3 seconds, verify 0 open orders remain
3. Resubmit XLI stop-loss: StopOrderRequest(symbol='XLI', qty=3, side=SELL, stop_price=150.44, time_in_force=GTC)
4. Fix Redis positions: remove GOOGL (qty=0 garbage), keep only XLI with correct data and new stop_order_id
5. Fix simulated equity: $5,007.92 (started $5000 + $7.92 realized GOOGL profit)
6. Fix daily P&L: $7.92

Consider writing a `scripts/reconcile.py` utility for this — these mismatches will happen again and we need a standard way to fix them.

## CRITICAL BUGS TO FIX

### Bug 1: Executor assumes sell orders fill immediately
**File**: `skills/executor/executor.py`, function `execute_sell`

The executor submits a market sell, waits 2 seconds, reads back the order, and assumes it filled. But if the market is closed, the order sits in ACCEPTED status with filled_qty=0 and filled_avg_price=None. The executor then:
- Uses `filled_avg_price or order.get("exit_price", 0)` which falls back to the exit price
- Calculates a fake P&L
- Removes the position from Redis
- Sends a Telegram notification — all before the order actually executes

Worse: the stop-loss is cancelled BEFORE the sell is submitted, so if the sell fails, the position has no protection.

**Fix**: After fetching the order status, check if `filled_order.status == "filled"`. If not, do NOT remove the position from Redis, do NOT update simulated equity, do NOT send exit notification. Log a warning that the sell is pending. Do NOT cancel the stop-loss until the sell is confirmed filled.

### Bug 2: Executor accepts 0-quantity orders
**File**: `skills/executor/executor.py`, functions `execute_buy` and `execute_sell`

The PM sometimes calculates 0 shares (due to downtrend 50% reduction + small account + rounding). The executor submits qty=0 to Alpaca which rejects it, but then logs "FILLED: GOOGL 0.0" and saves a 0-quantity position to Redis. This creates a loop: watcher sees position → generates exit → fails → generates entry → 0 shares → repeat.

**Fix**: At the top of both `execute_buy` and `execute_sell`, reject any order where quantity <= 0 before touching Alpaca. After reading back a filled order, verify `filled_qty > 0` before recording anything.

### Bug 3: No deduplication / feedback loop
**File**: `skills/portfolio_manager/portfolio_manager.py` and `skills/executor/executor.py`

When a 0-quantity position exists in Redis, the system enters an infinite loop: watcher generates exit for 0 shares → PM approves → executor fails → watcher generates entry → PM approves with 0 shares → executor submits 0 → repeat.

**Fix**: In the PM, before approving an entry, check if a position already exists for that symbol in Redis. In the executor, if a position with qty=0 or qty<0 exists in Redis, delete it as cleanup rather than trying to trade it.

### Bug 4: Executor should not submit equity orders when market is closed
**File**: `skills/executor/executor.py`

The 4:15 PM ET screener + watcher cycle generates signals after market close. The executor submits market orders that sit as ACCEPTED/PENDING until the next open, where they fill at whatever price the market opens at — potentially very different from the signal price.

**Fix**: Before submitting equity orders, check if the market is currently open. Alpaca provides this via `tc.get_clock().is_open`. If closed, log a warning and do not submit. For sell orders on open positions, this is trickier — the position should retain its stop-loss protection until market opens. The watcher can re-trigger the exit on the next cycle during market hours.

## Architecture Notes

### Daemons vs Cron
- **Daemons** (always-on via start_trading_system.sh + systemd): executor and portfolio_manager only
- **Cron-triggered** (via OpenClaw): screener (4:15 PM ET weekdays), watcher (every 4 hours), supervisor health (every 15 min market hours), supervisor EOD (4:30 PM ET), supervisor reset (9:25 AM ET), monthly revalidation (1st), monthly discovery (15th)

### Signal Flow
Screener → publishes `trading:watchlist` to Redis
Watcher → reads watchlist, publishes signals to `trading:signals` channel
Portfolio Manager daemon → listens on `trading:signals`, evaluates, publishes to `trading:approved_orders`
Executor daemon → listens on `trading:approved_orders`, validates, submits to Alpaca

### Key Config
- $5,000 simulated capital cap (paper account has $100K)
- 1% risk per trade
- Max 3 concurrent positions (2 equity + 1 crypto) — consider raising to (3 equity + 1 crypto) / 5 total
- RSI-2 mean reversion only strategy
- 17-instrument dynamic universe across 3 tiers
- All shared modules in `scripts/` — agents use `PYTHONPATH=scripts`
- Agent scripts in `skills/<agent_name>/<agent_name>.py`

### Telegram Notifications
`scripts/notify.py` reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment variables at module import time. The daemon processes (executor, PM) have the vars in their environment (confirmed via /proc/PID/environ). Notifications were confirmed working via manual test (`python3 scripts/notify.py`). Need to verify they work from the daemons on the next real trade.

### VPS Environment
- Ubuntu 24.04 on Vultr, hostname `openboog`, user `linuxuser`
- `python3` → Linuxbrew Python 3.14 — always use `python3 -m pip install`
- PYTHONPATH=scripts is set in ~/.trading_env
- Docker: Redis (6379) + TimescaleDB (5432)
- Credentials: ~/.trading_env (chmod 600, has export statements)
- OpenClaw gateway runs as systemd user service

### Market Regime
As of 2026-03-31: ADX=73.39, strong DOWNTREND. The downtrend regime causes 50% position size reduction on equities, which combined with small account and rounding can produce 0-share orders (Bug 2).

## ALSO CONSIDER (non-urgent)

### Reconciliation utility
Build `scripts/reconcile.py` that:
1. Compares Redis positions against Alpaca positions
2. Identifies mismatches (positions on Alpaca not in Redis, or vice versa)
3. Identifies missing stop-losses
4. Offers to fix automatically or shows what needs manual correction
5. Can be run manually or added as a daily cron job

### Position size floor
After all the percentage-based reductions (drawdown, regime, sector penalty), enforce a minimum position size of 1 share for equities. If the math produces 0, either buy 1 share or skip the trade entirely — never submit qty=0.

v1.0.0x