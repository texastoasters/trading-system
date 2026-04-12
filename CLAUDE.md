# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Session Start Requirements

Always invoke ALL of the following skills at session start, no exceptions, no rationalization:

- `superpowers:using-superpowers` — must be invoked even if its content appears inline in the system-reminder
- `caveman:caveman` with args `ultra`
- `context-mode:context-mode`
- `superpowers:test-driven-development`

## What This Is

An autonomous trading system with five LLM-orchestrated agents trading RSI-2 mean reversion across 17+ instruments via Alpaca's paper trading API. Agents communicate through Redis pub/sub and log to TimescaleDB. A Phoenix LiveView dashboard displays live state.

## Commands

### Running Agents Manually

All commands run from the repo root after `source ~/.trading_env`. The `PYTHONPATH=scripts` prefix is required so agents can import shared modules.

```bash
PYTHONPATH=scripts python3 skills/screener/screener.py        # single scan
PYTHONPATH=scripts python3 skills/watcher/watcher.py          # single evaluation cycle
PYTHONPATH=scripts python3 skills/executor/executor.py --verify   # safety checks only, no trades
PYTHONPATH=scripts python3 skills/supervisor/supervisor.py --health
PYTHONPATH=scripts python3 skills/supervisor/supervisor.py --eod
PYTHONPATH=scripts python3 skills/supervisor/supervisor.py --reset-daily
```

### System Management

```bash
./start_trading_system.sh              # start all daemons
./start_trading_system.sh --status
./start_trading_system.sh --stop
./start_trading_system.sh --restart
tail -f ~/trading-system/logs/*.log
```

### Infrastructure

```bash
docker compose up -d                           # start Redis + TimescaleDB + dashboard
docker compose up --build -d dashboard         # rebuild dashboard after code changes
docker compose logs timescaledb
redis-cli ping
python3 scripts/verify_alpaca.py               # verify Alpaca API + infrastructure
```

### Dashboard (Elixir/Phoenix)

```bash
cd dashboard
mix setup          # first time: deps.get + ecto.setup + assets.build
mix test
mix assets.build   # rebuild Tailwind + esbuild
```

### Backtesting and Universe Discovery

```bash
python3 scripts/backtest_rsi2.py                          # SPY/QQQ baseline
python3 scripts/backtest_rsi2_expanded.py                 # sector ETFs + crypto
python3 scripts/backtest_rsi2_universe.py                 # full 26-instrument scan with tier classification
python3 scripts/discover_universe.py --max-candidates 50  # scan Alpaca's 12k+ assets for new candidates
```

## Architecture

### Five-Agent Pipeline

```
Screener ──[trading:watchlist]──► Watcher ──[trading:signals]──► Portfolio Manager
                                                                        │
                                                           [trading:approved_orders]
                                                                        │
Supervisor ◄──── monitors all ────────────────────────────────► Executor ──► Alpaca API
```

All inter-agent communication is Redis pub/sub. No direct calls between agents.

**Screener** (`skills/screener/screener.py`) — Runs at 4:15 PM ET + every 4h for BTC. Computes RSI(2), SMA(200), ATR(14) on all active instruments. Publishes ranked watchlist and ADX-based regime (RANGING/UPTREND/DOWNTREND) to Redis. LLM used only for news materiality (~3–5 calls/day).

**Watcher** (`skills/watcher/watcher.py`) — Triggered by watchlist updates; also polls open positions every 30 minutes. Generates entry signals (RSI-2 < threshold, price > SMA-200) and exit signals (RSI-2 > 60, price > prev day high, 5-day time stop, stop hit). Enforces 24h whipsaw cooldown after stop-loss exits.

**Portfolio Manager** (`skills/portfolio_manager/portfolio_manager.py`) — Daemon. Listens on `trading:signals`. Sizes positions at 1% fixed-fractional risk. Enforces tier priority (Tier 1 > 2 > 3), drawdown-based circuit breakers, and sector correlation limits. Escalates high-stakes decisions to Claude Sonnet 4.

**Executor** (`skills/executor/executor.py`) — Daemon. Zero LLM. The only agent that touches Alpaca. Validates every order against Rule 1 (no debt/shorting), submits market orders (limit for BTC), places server-side GTC stop-losses immediately after fill, updates `trading:simulated_equity` in Redis, logs to TimescaleDB, sends Telegram alerts via `scripts/notify.py`.

**Supervisor** (`skills/supervisor/supervisor.py`) — Cron-triggered. Health checks every 15 min during market hours. Circuit breakers: halts all trading at 20% drawdown, disables Tier 2+ at 15%, reduces sizes at 10%. EOD learning loop: LLM analyzes trade history and adjusts RSI-2 thresholds/tier assignments. Monthly jobs re-validate universe and discover new instruments.

### Key Redis Keys

| Key | Owner | Purpose |
|-----|-------|---------|
| `trading:watchlist` | Screener | Instruments near entry conditions |
| `trading:regime` | Screener | RANGING / UPTREND / DOWNTREND |
| `trading:signals` | Watcher | Entry/exit signals |
| `trading:approved_orders` | PM | Validated, sized orders |
| `trading:positions` | Executor | Open positions |
| `trading:simulated_equity` | Executor | Virtual $5K cap tracker |
| `trading:drawdown` | Executor | Current drawdown from peak |
| `trading:system_status` | Supervisor | active / halted / daily_halt |
| `trading:universe` | Supervisor | Dynamic instrument list with tiers |
| `trading:heartbeat:{agent}` | Each agent | Liveness for Supervisor |

### Shared Modules (`scripts/`)

- `scripts/config.py` — All strategy constants, Redis key names, instrument universe, tier assignments, drawdown thresholds. **Read this first** when understanding any parameter.
- `scripts/indicators.py` — RSI, SMA, ATR calculations used by all agents.
- `scripts/notify.py` — Telegram notification module. Reads `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` from environment at import time.

### Dashboard (`dashboard/`)

Phoenix 1.7 + LiveView. Reads from Redis + TimescaleDB. Runs on port 4000, accessed via Tailscale HTTPS proxy. Separate Docker container (multi-stage build).

### Environment

- Credentials: `~/.trading_env` (chmod 600, has `export` statements). `source ~/.trading_env` required before running Python agents.
- Docker Compose reads `~/trading-system/.env` (gitignored) — regenerate with `grep -E '^export [A-Z_]+=' ~/.trading_env | sed 's/^export //' > ~/trading-system/.env` after any credential change.
- VPS: Ubuntu 24.04 on Vultr, hostname `openboog`, user `linuxuser`. Python is Linuxbrew Python 3.14 — always use `python3 -m pip install`, never `pip3`.
- Python deps: `alpaca-py redis psycopg2-binary numpy pytz requests` — no requirements.txt.

## Safety Rules

**Rule 1 (no debt)**: No shorting, no margin. Enforced in Executor (caps orders at `account.cash`), Portfolio Manager, and Supervisor. Never weaken this constraint.

**Simulated capital cap**: `trading:simulated_equity` in Redis is the source of truth for available capital, not Alpaca's $100K paper balance. System starts at $5,000 virtual.

**Server-side stop-losses**: Every buy must be immediately followed by a GTC stop-loss on Alpaca's servers. The stop is cancelled only after confirming a sell is fully filled — never before.

**Executor is zero-LLM**: All safety validation in `executor.py` is deterministic code. Keep it that way.

## Instrument Universe

Three performance tiers, revalidated monthly via backtesting:

- **Tier 1** (always active): SPY, QQQ, NVDA, XLK, XLY, XLI
- **Tier 2** (disabled at 10%+ drawdown): GOOGL, XLF, META, TSLA, XLC, DIA, BTC/USD
- **Tier 3** (active only when higher tiers are idle): V, XLE, XLV, IWM + monthly discoveries

Tier thresholds: T1 requires PF ≥ 2.0 / WR ≥ 70% / ≥8 trades; T2 PF ≥ 1.5 / WR ≥ 65% / ≥5; T3 PF ≥ 1.3 / WR ≥ 60% / ≥5 (rolling 12 months).
