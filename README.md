[![Coverage Status](https://coveralls.io/repos/github/texastoasters/trading-system/badge.svg?branch=main)](https://coveralls.io/github/texastoasters/trading-system?branch=main)

# Autonomous Agentic Trading System

An autonomous trading system powered by five LLM-orchestrated agents, running three mean-reversion and trend-following strategies across a dynamic universe of 17+ instruments via Alpaca's paper trading API.

## Overview

Five specialized agents handle screening, signal generation, position sizing, order execution, and system oversight. All agents communicate through Redis pub/sub and log to TimescaleDB. A Phoenix LiveView dashboard provides live visibility into every layer of the system.

**Strategies:**
- **RSI-2 mean reversion** — Primary strategy. Entry when RSI(2) < threshold and price > SMA(200). Exit on RSI(2) > 60, price exceeds prior day high, time stop, or stop-loss hit. Per-instrument entry thresholds and time-stop lengths are tuned quarterly via walk-forward sweep and stored in Redis.
- **IBS (Internal Bar Strength)** — Secondary entry path on the same universe. Entry when IBS < 0.15 and price > SMA(200). When RSI-2 and IBS trigger together on the same bar, the Watcher merges them into a single stacked signal with a tighter stop and a 1.25× confidence boost.
- **Donchian-BO (Breakout)** — Trend-following slot for seven curated instruments where RSI-2 stays idle. Entry on Donchian channel breakout (20-day high). Wider hold window (up to 30 days) and larger ATR stop to capture sustained moves.

**Key constraints:**
- **Rule 1 (no debt)**: No shorting, no margin. Enforced independently in the Portfolio Manager, Executor, and Supervisor.
- **PDT compliance**: Positions are held overnight as swing trades. The PDT gate fires only when an order would complete a same-session round-trip for an account with ≥ 3 day trades.
- **$5,000 simulated capital**: Paper trading uses a virtual cap tracked in Redis, not Alpaca's $100K paper balance.

**Backtested performance** (3-year real data via Alpaca):
- 17 validated instruments across broad ETFs, sector ETFs, large-cap stocks, and BTC/USD
- Win rates: 65–89% depending on instrument
- Profit factors: 1.35–6.04
- ~125 trades/year (~10.4/month)
- Max drawdown per instrument: sub-3%

## Prerequisites

- **VPS**: Ubuntu 24.04 (tested on Vultr, hostname `openboog`, user `linuxuser`)
- **Python**: Linuxbrew Python 3.14 (`brew install python`). Always use `python3 -m pip install`, never `pip3`.
- **Docker**: For Redis, TimescaleDB, and the dashboard container
- **cronie**: Required for `CRON_TZ` support (`sudo apt install cronie`; Vixie cron does not support `CRON_TZ`)
- **Alpaca account**: Free paper trading account at [alpaca.markets](https://alpaca.markets)
- **OpenClaw**: Installed with Node.js 22+ for LLM orchestration
- **Telegram bot** (optional): For trade alerts and daily summaries
- **Tailscale**: Installed on the VPS and your devices for private dashboard access

## Quick Start

```bash
# 1. Clone the repo
git clone <your-repo-url> ~/trading-system
cd ~/trading-system

# 2. Install Python dependencies
python3 -m pip install alpaca-py redis psycopg2-binary numpy pytz requests

# 3. Create environment file
cat > ~/.trading_env << 'EOF'
export ALPACA_API_KEY="your-paper-key"
export ALPACA_SECRET_KEY="your-paper-secret"
export TSDB_PASSWORD="changeme_in_env_file"
export TELEGRAM_BOT_TOKEN=""    # optional — see Telegram Setup below
export TELEGRAM_CHAT_ID=""      # optional
export TAILSCALE_HOSTNAME=""    # your Tailscale hostname, e.g. openboog.tail1234.ts.net
export DASHBOARD_SECRET_KEY_BASE=""  # generate with: openssl rand -base64 48
EOF
chmod 600 ~/.trading_env

# 4. Create .env for Docker Compose (.env is gitignored — never committed)
grep -E '^export [A-Z_]+=' ~/.trading_env | sed 's/^export //' > ~/trading-system/.env
chmod 600 ~/trading-system/.env

# 5. Start infrastructure
docker compose up -d

# 6. Verify infrastructure
source ~/.trading_env
python3 scripts/verify_alpaca.py

# 7. Install cron jobs (see Cron Setup below)
# This drives the Screener and Supervisor on their schedules.

# 8. Link skills to OpenClaw workspace
ln -s ~/trading-system/skills <your-openclaw-workspace>/skills/trading

# 9. Start the three daemon agents
chmod +x start_trading_system.sh
./start_trading_system.sh
```

## Setup Details

### Alpaca Paper Trading

1. Create a free account at [alpaca.markets](https://alpaca.markets)
2. Generate paper trading API keys from the dashboard
3. Add the keys to `~/.trading_env`
4. Regenerate `.env`: `grep -E '^export [A-Z_]+=' ~/.trading_env | sed 's/^export //' > ~/trading-system/.env`
5. Verify: `source ~/.trading_env && python3 scripts/verify_alpaca.py`

The paper account comes with $100,000 in virtual funds. The system caps itself at $5,000 via `trading:simulated_equity` in Redis.

### Docker Services

Docker Compose reads `~/trading-system/.env`. Regenerate it from `~/.trading_env` after any credential change:

```bash
grep -E '^export [A-Z_]+=' ~/.trading_env | sed 's/^export //' > ~/trading-system/.env
chmod 600 ~/trading-system/.env
```

```bash
# Start all services (Redis, TimescaleDB, dashboard)
docker compose up -d

# Rebuild dashboard after code changes
docker compose up --build -d dashboard

docker compose ps
docker compose logs timescaledb
docker compose logs dashboard

redis-cli ping
```

Redis runs on port 6379, TimescaleDB on port 5432. The database schema is managed by Ecto migrations in `dashboard/priv/repo/migrations/`. Migrations run automatically each time the dashboard container starts.

### Cron Setup

The Screener and Supervisor run as cron jobs, not daemons. The cron file uses `CRON_TZ=America/New_York`, which requires **cronie** (not Vixie cron):

```bash
# Install cronie if not already installed
sudo apt install cronie
sudo systemctl disable cron && sudo systemctl enable --now crond
```

Install the cron file (owned by root, permissions 0644, no dots in filename):

```bash
sudo cp cron/trading-system-cron /etc/cron.d/trading-system
sudo chown root:root /etc/cron.d/trading-system
sudo chmod 0644 /etc/cron.d/trading-system
```

Verify it installed correctly (cron is silent about bad files):

```bash
# Check ownership and permissions
ls -la /etc/cron.d/trading-system
# Expected: -rw-r--r-- 1 root root ...

# Check the file ends with a newline (cron silently drops the last line without one)
xxd /etc/cron.d/trading-system | tail -1
# Should contain "0a" (newline)

# Check encoding (must be plain ASCII or UTF-8)
file /etc/cron.d/trading-system
```

See `cron/install-trading-cron.sh` for the full step-by-step, including how to verify the first run and how to remove any legacy OpenClaw cron jobs that may conflict.

The cron schedule runs in ET year-round (cronie handles DST automatically):
- **Screener**: 4:15 PM ET weekdays (post-market scan) + every 4 hours for BTC/USD
- **Supervisor health check**: Every 15 minutes during market hours (9:30 AM – 4:00 PM ET weekdays)
- **Supervisor EOD review**: 4:30 PM ET weekdays
- **Supervisor daily reset**: 9:25 AM ET weekdays
- **Universe re-validation**: 1st of each month
- **Universe discovery**: 15th of each month

### Tailscale Setup

The dashboard is served at **port 4000** on your tailnet, leaving port 443 free for OpenClaw. Run these once on the VPS:

```bash
# Serve dashboard at https://openboog.<tailnet>.ts.net:4000
sudo tailscale serve --bg --https=4000 http://localhost:4000

# OpenClaw stays on the default port
sudo tailscale serve --bg --https=443 http://localhost:3000

tailscale serve status
```

Dashboard URL: `https://<your-tailnet-hostname>:4000`

### Telegram Notifications (Optional)

1. Open Telegram, search for `@BotFather`
2. Send `/newbot`, choose a name and username
3. Copy the token BotFather gives you
4. Send any message to your new bot
5. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser
6. Find your `chat_id` in the JSON response
7. Add both to `~/.trading_env` and regenerate `.env`
8. Test: `source ~/.trading_env && python3 scripts/notify.py`

The system sends: trade entry/exit alerts (immediate), daily summaries (4:30 PM ET), weekly summaries (Saturday morning), monthly summaries (1st of month), drawdown alerts, and critical system alerts. If Telegram is not configured, notifications print to console logs instead.

### OpenClaw Integration

The `skills/` directory contains SKILL.md files that define each agent's behavior for OpenClaw. Symlink the directory into your OpenClaw workspace:

```bash
ln -s ~/trading-system/skills <openclaw-workspace>/skills/trading
```

OpenClaw then sees the skills at `skills/trading/screener/SKILL.md`, `skills/trading/executor/SKILL.md`, etc.

## System Operation

### Starting and Stopping

`start_trading_system.sh` manages the three **daemon** agents (Executor, Portfolio Manager, Watcher). The Screener and Supervisor run on their cron schedules independently.

```bash
./start_trading_system.sh              # Start daemon agents
./start_trading_system.sh --status     # Check what's running
./start_trading_system.sh --stop       # Graceful shutdown
./start_trading_system.sh --restart    # Stop + start
./start_trading_system.sh --logs       # Tail all agent logs in tmux
```

Agents start in dependency order: Executor → Portfolio Manager → Watcher. The startup script verifies infrastructure (Redis, TimescaleDB, Python dependencies), runs the Executor's safety checks (Rule 1, PDT flag, stop-loss verification), then launches each agent as a background daemon logging to `~/trading-system/logs/`.

### Logs

```bash
# Follow all agent logs
tail -f ~/trading-system/logs/*.log

# Follow a specific agent
tail -f ~/trading-system/logs/executor_$(date +%Y-%m-%d).log
```

Log files are also streamed live in the dashboard's Logs page. Logs rotate automatically; files older than 7 days are deleted on startup.

### Manual Commands

Run from `~/trading-system` after `source ~/.trading_env`:

```bash
# Single screener scan (no daemon)
PYTHONPATH=scripts python3 skills/screener/screener.py

# Single watcher evaluation cycle
PYTHONPATH=scripts python3 skills/watcher/watcher.py

# Health check only
PYTHONPATH=scripts python3 skills/supervisor/supervisor.py --health

# EOD review only
PYTHONPATH=scripts python3 skills/supervisor/supervisor.py --eod

# Reset daily P&L counters (normally automatic at 9:25 AM ET)
PYTHONPATH=scripts python3 skills/supervisor/supervisor.py --reset-daily

# Re-fit per-instrument RSI-2 thresholds and max-hold days (normally quarterly)
PYTHONPATH=scripts python3 skills/supervisor/supervisor.py --refit-thresholds

# Startup verification only (no trades)
PYTHONPATH=scripts python3 skills/executor/executor.py --verify
```

### Backtesting and Discovery

```bash
# Backtest RSI-2 on SPY and QQQ (5 years)
PYTHONPATH=scripts python3 scripts/backtest_rsi2.py

# Backtest RSI-2 on sector ETFs and crypto
PYTHONPATH=scripts python3 scripts/backtest_rsi2_expanded.py

# Full 26-instrument universe scan with tier classification
PYTHONPATH=scripts python3 scripts/backtest_rsi2_universe.py

# Backtest IBS, Donchian-BO, and other alt strategies
PYTHONPATH=scripts python3 scripts/backtest_alt_strategies.py

# Discover new instruments from Alpaca's 12,000+ tradeable assets
PYTHONPATH=scripts python3 scripts/discover_universe.py
PYTHONPATH=scripts python3 scripts/discover_universe.py --max-candidates 50

# Walk-forward RSI-2 threshold sweep (produces per-instrument optimal thresholds)
PYTHONPATH=scripts python3 scripts/sweep_rsi2_thresholds.py

# Walk-forward max-hold-day sweep
PYTHONPATH=scripts python3 scripts/sweep_rsi2_max_hold.py
```

## Dashboard

The Phoenix LiveView dashboard (port 4000) has six pages:

| Page | URL | What it shows |
|------|-----|---------------|
| **Dashboard** | `/` | Live agent status, open positions, recent signals, equity curve, drawdown gauge, heartbeat grid |
| **Trades** | `/trades` | Paginated trade history with entry/exit prices, P&L, hold time, and exit reason |
| **Universe** | `/universe` | Instrument universe organized by tier; add/remove from blacklist; liquidate positions from UI |
| **Performance** | `/performance` | Equity curve by time range, RSI-2 signal heatmap (instruments × last 14 days), strategy attribution |
| **Logs** | `/logs` | Live-tailing last N lines of each agent's log file; no SSH required |
| **Settings** | `/settings` | Hot-reload 47 strategy constants (RSI thresholds, position limits, drawdown levels, trailing stops, etc.) without restarting agents. Active overrides shown with yellow borders. |

## File Structure

```
~/trading-system/
├── README.md
├── AGENTS.md                             # Agent architecture reference
├── CLAUDE.md                             # Claude Code project instructions
├── VERSION                               # Current version (semver)
├── CHANGELOG.md                          # Release notes
├── start_trading_system.sh               # Manage daemon agents (executor, pm, watcher)
├── docker-compose.yml                    # Redis + TimescaleDB + dashboard
├── pyproject.toml                        # Python project config (pytest, coverage)
├── requirements-dev.txt                  # Dev dependencies (pytest, coverage)
├── trading-system.service                # systemd unit (optional, for auto-start)
├── cron/
│   ├── trading-system-cron               # /etc/cron.d file for screener + supervisor
│   └── install-trading-cron.sh           # Step-by-step cron installation guide
├── scripts/
│   ├── config.py                         # All strategy constants, Redis keys, defaults
│   ├── indicators.py                     # Technical indicators (RSI, SMA, ATR, ADX, Donchian)
│   ├── notify.py                         # Telegram notification module
│   ├── universe.py                       # Universe and tier helpers
│   ├── verify_alpaca.py                  # Infrastructure verification
│   ├── validate_env.py                   # Environment variable checks
│   ├── reconcile.py                      # Redis ↔ Alpaca position reconciliation
│   ├── backup_redis.py                   # Snapshot Redis state to JSON
│   ├── refresh_economic_calendar.py      # Regenerate FOMC/CPI/NFP dates
│   ├── backtest_rsi2.py                  # RSI-2 backtester (SPY/QQQ baseline)
│   ├── backtest_rsi2_expanded.py         # RSI-2 on sector ETFs + crypto
│   ├── backtest_rsi2_universe.py         # Full universe scan with tier classification
│   ├── backtest_alt_strategies.py        # IBS, Donchian-BO, and alt strategy backtests
│   ├── backtest_momentum_gappers.py      # Momentum/gap strategy research
│   ├── discover_universe.py              # Monthly discovery scanner
│   ├── sweep_rsi2_thresholds.py          # Walk-forward RSI-2 threshold sweep
│   └── sweep_rsi2_max_hold.py            # Walk-forward max-hold-day sweep
├── skills/
│   ├── screener/
│   │   ├── screener.py                   # Screener Agent (cron-managed)
│   │   ├── test_screener.py
│   │   └── SKILL.md                      # OpenClaw skill definition
│   ├── watcher/
│   │   ├── watcher.py                    # Watcher Agent (daemon)
│   │   ├── test_watcher.py
│   │   └── SKILL.md
│   ├── portfolio_manager/
│   │   ├── portfolio_manager.py          # Portfolio Manager Agent (daemon)
│   │   ├── test_portfolio_manager.py
│   │   └── SKILL.md
│   ├── executor/
│   │   ├── executor.py                   # Trade Executor Agent (daemon, zero LLM)
│   │   ├── test_executor.py
│   │   └── SKILL.md
│   └── supervisor/
│       ├── supervisor.py                 # Supervisor Agent (cron-managed)
│       ├── test_supervisor.py
│       └── SKILL.md
├── dashboard/                            # Phoenix 1.7 LiveView dashboard
│   ├── Dockerfile                        # Multi-stage build; auto-migrates on start
│   ├── lib/dashboard/release.ex          # Release task: runs Ecto migrations
│   └── priv/repo/migrations/             # Database schema (Ecto-managed)
└── docs/
    ├── FEATURE_WISHLIST.md               # Tracked improvements and future work
    ├── agentic_day_trading_system_report_v2.md
    ├── phase1_market_microstructure_constraints.md
    ├── phase2_strategy_research_backtesting.md
    ├── phase3_signal_engineering_specification.md
    └── phase4_risk_economics_legal.md
```

## Safety Architecture

1. **Rule 1 (no debt)**: Enforced independently in the Portfolio Manager (rejects orders exceeding simulated cash), Executor (caps at `account.cash`), and Supervisor (verifies cash ≥ 0 every 15 minutes during market hours).

2. **Server-side stop-losses**: Every position gets a GTC stop-loss on Alpaca's servers immediately after entry fill. The stop is cancelled only after confirming a sell is fully filled — never before. Protects positions even if the entire system goes offline.

3. **Circuit breakers** (deterministic, no LLM):
   - 10% drawdown → 50% position size, only Tier 1 active
   - 15% drawdown → 25% position size, BTC disabled
   - 20% drawdown → all trading halted, manual approval required
   - 3% daily loss → halted until next session

4. **PDT gate**: Fires only when an order would complete a same-session round-trip for an account with ≥ 3 day trades counted. Overnight exits and new buys on symbols not entered today are allowed regardless of the PDT flag. The Executor is the single enforcement point; pre-rejection in the Watcher was removed.

5. **Simulated capital cap**: `trading:simulated_equity` in Redis is the source of truth for available capital, capped at $5,000. This prevents the system from learning behaviors that wouldn't work at real account size.

## Instrument Universe

Three performance tiers, re-validated monthly via backtesting (rolling 12-month data):

| Tier | Conditions | Instruments |
|------|-----------|-------------|
| **1 — Core** (always active) | PF ≥ 2.0 / WR ≥ 70% / ≥ 8 trades | SPY, QQQ, NVDA, XLK, XLY, XLI |
| **2 — Standard** (disabled at 10%+ drawdown) | PF ≥ 1.5 / WR ≥ 65% / ≥ 5 trades | GOOGL, XLF, META†, TSLA†, XLC, DIA, BTC/USD |
| **3 — Marginal** (active only when higher tiers idle) | PF ≥ 1.3 / WR ≥ 60% / ≥ 5 trades | V, XLE, XLV, IWM + monthly discoveries |

† META and TSLA are in the universe but excluded from order routing — flat/negative across all backtested strategies in the current 2-year window.

**Donchian-BO** runs on a separate curated set of 7 instruments (DG, GOOGL, NVDA, AMGN, SMH, LIN, XLY) where RSI-2 mean reversion is weak but trend-following is productive.

Promotion: max one tier up per month. Demotion: can fall multiple tiers. Disabled for 3+ months → archived.

## Redis Key Reference

| Key | Owner | Purpose |
|-----|-------|---------|
| `trading:watchlist` | Screener | Instruments near entry conditions |
| `trading:regime` | Screener | RANGING / UPTREND / DOWNTREND |
| `trading:heatmap` | Screener | RSI-2 signal grid (instruments × 14 days) |
| `trading:signals` | Watcher | Entry/exit signals |
| `trading:approved_orders` | Portfolio Manager | Validated, sized orders |
| `trading:positions` | Executor | Open positions |
| `trading:simulated_equity` | Executor | Virtual $5K capital tracker |
| `trading:drawdown` | Executor | Current drawdown from peak |
| `trading:closed_today` | Executor | Symbols with a sell fill today (PDT gate) |
| `trading:system_status` | Supervisor | active / halted / daily_halt |
| `trading:universe` | Supervisor | Dynamic instrument list with tiers |
| `trading:tiers` | Supervisor | Quick tier lookup per symbol |
| `trading:rejected_signals` | Portfolio Manager | Logged for EOD review |
| `trading:heartbeat:{agent}` | Each agent | Liveness for Supervisor |
| `trading:thresholds:{symbol}` | Supervisor | Per-instrument RSI-2 entry thresholds |
| `trading:max_hold:{symbol}` | Supervisor | Per-instrument time-stop lengths |
| `trading:whipsaw:{symbol}:{strategy}` | Executor | Per-strategy 24h cooldown after stop-loss |
| `trading:config` | Settings LiveView | Hot-reload strategy constant overrides |
| `trading:pdt:count` | Executor | Day trade counter |

## Disclaimer

This is an engineering project for an experimental algorithmic trading system. It is not financial advice. All trading involves risk of loss. The $5,000 seed capital should be considered money you are willing to lose entirely during the learning and validation period. Past backtested performance does not guarantee future results.
