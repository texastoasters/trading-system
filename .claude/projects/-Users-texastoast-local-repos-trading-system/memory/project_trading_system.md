---
name: Trading System Project Context
description: Core architecture and current status of the autonomous LLM-powered day trading system
type: project
---

Autonomous day trading system (`~/trading-system`) targeting a $5,000 account via Alpaca's paper trading API.

**Strategy**: RSI-2 mean reversion only. Validated via 3-year real Alpaca historical data. ~125 trades/year, win rates 65–89%, profit factors 1.35–6.04.

**Why:** Engineering project to build a self-improving algorithmic trading system with LLM oversight. $5K simulated cap during paper phase.

**How to apply:** Frame all changes in context of the five-agent pipeline and signal flow. Respect Rule 1 (no debt/shorting/margin) as a hard invariant.

## Current Status
v1.0.2 — Stage 2 (agent development) complete. Directory restructure merged (PR #1). **Next step: integration testing** — run all five agents together on paper trading to verify full signal flow end-to-end.

## Five Agents (files in `skills/*/`)
1. **Screener** (`skills/screener/screener.py`) — EOD scan at 4:15 PM ET, RSI-2 + regime via ADX on SPY, publishes watchlist to Redis. 3–5 LLM calls/day.
2. **Watcher** (`skills/watcher/watcher.py`) — entry/exit signal generation from watchlist + open positions. Reactive to screener + 30-min position checks. ~2 LLM calls/day.
3. **Portfolio Manager** (`skills/portfolio_manager/portfolio_manager.py`) — 1% fixed-fractional sizing, tier priority, sector correlation, drawdown response. 3–5 GPT calls/day, ~2 Claude Sonnet 4 escalations/week.
4. **Executor** (`skills/executor/executor.py`) — **zero LLM**, pure code. Only touches Alpaca API. Validates Rule 1, submits orders, places GTC stop-losses, tracks simulated $5K equity in Redis.
5. **Supervisor** (`skills/supervisor/supervisor.py`) — health checks every 15 min, EOD review (LLM), circuit breakers, Telegram, monthly universe re-validation + discovery.

## Infrastructure
- VPS: Vultr Ubuntu 24.04, hostname `openboog`, user `linuxuser`
- Docker: Redis (6379) + TimescaleDB (5432)
- Credentials: `~/.trading_env` (chmod 600)
- Python: `python3` → Linuxbrew 3.14; **always use `python3 -m pip install`** (bare `pip` targets system 3.12)
- OpenClaw: LLM orchestration layer; `skills/` is symlinked into OpenClaw workspace

## LLM Routing
- GPT-OSS 120B via Together.ai — primary for Screener, Watcher, Portfolio Manager, Supervisor
- Claude Sonnet 4 — PM escalation only (~2 calls/week): order > 20% equity, drawdown > 10%, Tier 3/new instrument signals, contradictory GPT reasoning
- Executor: zero LLM

## Instrument Universe (17 instruments, 3 tiers)
- **Tier 1**: SPY, QQQ, NVDA, XLK, XLY, XLI (~46 trades/year)
- **Tier 2**: GOOGL, XLF, META, TSLA, XLC, DIA, BTC/USD (~53 trades/year)
- **Tier 3**: V, XLE, XLV, IWM (~26 trades/year)
Universe is dynamic — Supervisor manages monthly re-validation (1st) and discovery (15th).

## Safety Layers
- Rule 1: no debt/shorting/margin — enforced in Executor, PM, and Supervisor independently
- Server-side GTC stop-losses placed immediately after every fill
- Circuit breakers: 10% DD → 50% size + Tier 1 only; 15% DD → 25% size + no BTC; 20% DD → full halt
- 3% daily loss → halt until next session
- PDT protection: all positions swing trades (2–5 days)
- Simulated $5K cap enforced in Redis even though paper account has $100K

## Key Redis Keys
`trading:watchlist`, `trading:regime`, `trading:signals`, `trading:approved_orders`, `trading:positions`, `trading:simulated_equity`, `trading:universe`, `trading:tiers`, `trading:drawdown`, `trading:system_status`, `trading:heartbeat:{agent}`, `trading:rejected_signals`

## File Structure
- `skills/*/` — agent source files + SKILL.md for OpenClaw
- `scripts/` — backtesting, config, indicators, notify, verify_alpaca
- `docs/` — research reports (4 phases + v2 summary)
- `init-db/001_create_schema.sql` — TimescaleDB schema
- `start_trading_system.sh` — start/stop/status/restart (starts in order: Executor → Supervisor → PM → Screener → Watcher)
