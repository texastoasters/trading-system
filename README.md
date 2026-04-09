[![Coverage Status](https://coveralls.io/repos/github/texastoasters/trading-system/badge.svg?branch=main)](https://coveralls.io/github/texastoasters/trading-system?branch=main)

# Autonomous Agentic Day Trading System

An autonomous trading system powered by five LLM-orchestrated agents, trading RSI-2 mean reversion across a dynamic universe of 17+ instruments via Alpaca's API.

## Overview

The system runs one proven strategy (RSI-2 mean reversion) across multiple instruments organized into three performance tiers. Five specialized agents handle screening, signal generation, position sizing, order execution, and system oversight. All agents communicate through Redis pub/sub and log to TimescaleDB. The instrument universe is dynamic — the Supervisor Agent discovers new instruments and promotes/demotes existing ones monthly based on rolling performance.

**Key constraints:**
- **Rule 1**: Never expose the account to debt. No shorting, no margin. Enforced in code at every layer.
- **PDT compliance**: All positions are swing trades (held 2–5 days). Zero day trades consumed under normal operation.
- **$5,000 simulated capital**: Paper trading uses a virtual cap tracked in Redis, not Alpaca's $100K paper balance.

**Backtested performance** (3-year real data via Alpaca):
- 17 validated instruments across broad ETFs, sector ETFs, large-cap stocks, and BTC/USD
- Win rates: 65–89% depending on instrument
- Profit factors: 1.35–6.04
- ~125 trades/year (~10.4/month)
- Max drawdown per instrument: sub-3%

## Prerequisites

- **VPS**: Ubuntu 24.04 (tested on Vultr, hostname `openboog`)
- **Python**: 3.12+ (system Python works; avoid 3.14 for alpaca-py compatibility)
- **Docker**: For Redis, TimescaleDB, and the dashboard container
- **Alpaca account**: Free paper trading account at [alpaca.markets](https://alpaca.markets)
- **OpenClaw**: Installed with Node.js 22.16+ for LLM orchestration
- **Telegram bot** (optional): For trade alerts and daily summaries
- **Tailscale**: Installed on the VPS and your devices for private dashboard access

## Quick Start

```bash
# 1. Clone the repo
git clone <your-repo-url> ~/trading-system
cd ~/trading-system

# 2. Install Python dependencies
python3 -m pip install alpaca-py redis psycopg2-binary numpy pytz requests

# 3. Create environment file (used by Python agents and sourced into .env below)
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

# 4. Create .env for Docker Compose (strips `export` from ~/.trading_env)
# .env is gitignored — never committed
grep -E '^export [A-Z_]+=' ~/.trading_env | sed 's/^export //' > ~/trading-system/.env
chmod 600 ~/trading-system/.env

# 5. Start infrastructure
docker compose up -d

# 6. Verify infrastructure
source ~/.trading_env
python3 scripts/verify_alpaca.py

# 7. Link skills to OpenClaw workspace
ln -s ~/trading-system/skills <your-openclaw-workspace>/skills/trading

# 8. Start the system
chmod +x start_trading_system.sh
./start_trading_system.sh
```

## Setup Details

### Alpaca Paper Trading

1. Create a free account at [alpaca.markets](https://alpaca.markets)
2. Generate paper trading API keys from the dashboard
3. Add the keys to `~/.trading_env`
4. Verify with `python3 verify_alpaca.py`

The paper account comes with $100,000 in virtual funds. The system caps itself at $5,000 via `trading:simulated_equity` in Redis. When transitioning to live trading, set `PAPER_TRADING = False` in `config.py` and fund the account with real capital.

### Docker Services

Docker Compose reads `~/trading-system/.env` for variable substitution. This file must exist before running any `docker compose` command. Generate it from `~/.trading_env` with:

```bash
grep -E '^export [A-Z_]+=' ~/.trading_env | sed 's/^export //' > ~/trading-system/.env
chmod 600 ~/trading-system/.env
```

Re-run this whenever you change `~/.trading_env`. The file is gitignored and never committed.

```bash
# Start all services (Redis, TimescaleDB, dashboard)
docker compose up -d

# Rebuild and restart the dashboard after code changes
docker compose up --build -d dashboard

# Check they're running
docker compose ps

# View logs
docker compose logs timescaledb
docker compose logs dashboard

# Redis CLI
redis-cli ping
```

Redis runs on port 6379, TimescaleDB on port 5432. The database schema is automatically created on first startup via `init-db/001_create_schema.sql`.

### Tailscale Setup

The dashboard is served at **port 4000** on your tailnet, leaving port 443 free for OpenClaw. Run these once on the VPS:

```bash
# Serve dashboard at https://openboog.tail233812.ts.net:4000
sudo tailscale serve --bg --https=4000 http://localhost:4000

# OpenClaw stays on the default port (replace 3000 with OpenClaw's local port)
sudo tailscale serve --bg --https=443 http://localhost:3000

# Verify both are configured
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
7. Add both to `~/.trading_env`
8. Test: `python3 scripts/notify.py`

The system sends: trade entry/exit alerts (immediate), daily summaries (4:15 PM ET), weekly summaries (Saturday morning), monthly summaries (1st of month), drawdown alerts, and critical system alerts. If Telegram is not configured, notifications print to console logs instead.

### OpenClaw Integration

The `skills/` directory contains SKILL.md files that define each agent's behavior for OpenClaw. Symlink this directory into your OpenClaw workspace:

```bash
ln -s ~/trading-system/skills <openclaw-workspace>/skills/trading
```

OpenClaw then sees the skills at `skills/trading/screener/SKILL.md`, `skills/trading/executor/SKILL.md`, etc.

## System Operation

### Starting and Stopping

```bash
./start_trading_system.sh              # Start all agents
./start_trading_system.sh --status     # Check what's running
./start_trading_system.sh --stop       # Graceful shutdown
./start_trading_system.sh --restart    # Stop + start
```

Agents start in dependency order: Executor → Supervisor → Portfolio Manager → Screener → Watcher. The startup script verifies infrastructure (Redis, TimescaleDB, Python dependencies), runs the Executor's safety checks (Rule 1, PDT flag, stop-loss verification), then launches each agent as a background daemon with logging to `logs/`.

### Logs

```bash
# Follow all logs
tail -f ~/trading-system/logs/*.log

# Follow a specific agent
tail -f ~/trading-system/logs/executor_$(date +%Y-%m-%d).log
```

Logs rotate automatically — files older than 7 days are deleted on startup.

### Manual Commands

Run from the repo root (`~/trading-system`) after `source ~/.trading_env`.

```bash
# Run a single screener scan (no daemon)
PYTHONPATH=scripts python3 skills/screener/screener.py

# Run a single watcher evaluation cycle
PYTHONPATH=scripts python3 skills/watcher/watcher.py

# Health check only
PYTHONPATH=scripts python3 skills/supervisor/supervisor.py --health

# End-of-day review only
PYTHONPATH=scripts python3 skills/supervisor/supervisor.py --eod

# Reset daily P&L counters (normally automatic at 9:25 AM ET)
PYTHONPATH=scripts python3 skills/supervisor/supervisor.py --reset-daily

# Startup verification only
PYTHONPATH=scripts python3 skills/executor/executor.py --verify
```

### Backtesting and Discovery

```bash
# Backtest RSI-2 on SPY and QQQ (5 years)
python3 scripts/backtest_rsi2.py

# Backtest on sector ETFs and crypto
python3 scripts/backtest_rsi2_expanded.py

# Full 26-instrument universe scan with tier classification
python3 scripts/backtest_rsi2_universe.py

# Discover new instruments from Alpaca's 12,000+ tradeable assets
python3 scripts/discover_universe.py
python3 scripts/discover_universe.py --max-candidates 50
```

## File Structure

```
~/trading-system/
├── .gitignore
├── README.md                        # This file
├── AGENTS.md                        # Agent architecture documentation
├── start_trading_system.sh          # Startup/stop/status/restart
├── docker-compose.yml               # Redis + TimescaleDB
├── init-db/
│   └── 001_create_schema.sql        # Database schema
├── scripts/
│   ├── config.py                    # Shared config, Redis keys, defaults
│   ├── indicators.py                # Technical indicators library
│   ├── notify.py                    # Telegram notification module
│   ├── verify_alpaca.py             # Infrastructure verification
│   ├── backtest_rsi2.py             # RSI-2 backtester (SPY/QQQ)
│   ├── backtest_rsi2_expanded.py    # RSI-2 on sector ETFs + crypto
│   ├── backtest_rsi2_universe.py    # Full universe scanner
│   └── discover_universe.py         # Monthly discovery scanner
├── skills/                          # Agent code + OpenClaw skill definitions
│   ├── screener/
│   │   ├── screener.py              # Screener Agent
│   │   └── SKILL.md
│   ├── watcher/
│   │   ├── watcher.py               # Watcher Agent
│   │   └── SKILL.md
│   ├── portfolio_manager/
│   │   ├── portfolio_manager.py     # Portfolio Manager Agent
│   │   └── SKILL.md
│   ├── executor/
│   │   ├── executor.py              # Trade Executor Agent (zero LLM)
│   │   └── SKILL.md
│   └── supervisor/
│       ├── supervisor.py            # Supervisor Agent
│       └── SKILL.md
└── docs/
    ├── agentic_day_trading_system_report_v2.md
    ├── phase1_market_microstructure_constraints.md
    ├── phase2_strategy_research_backtesting.md
    ├── phase3_signal_engineering_specification.md
    └── phase4_risk_economics_legal.md
```

## Safety Architecture

The system has multiple independent safety layers:

1. **Rule 1 (no debt)**: Enforced in the Executor (caps orders at `account.cash`), the Portfolio Manager (rejects orders exceeding available capital), and the Supervisor (verifies cash ≥ 0 every 15 minutes).

2. **Server-side stop-losses**: Every position gets a GTC stop-loss order on Alpaca's servers immediately after entry. These protect positions even if the entire system goes offline.

3. **Circuit breakers**: Automatic position-size reduction at 10% drawdown, further reduction at 15%, full halt at 20%. Tier-based instrument disabling ensures only the strongest instruments remain active during stress.

4. **PDT protection**: All RSI-2 positions are swing trades (held 2–5 days). The PDT counter should stay at 0 permanently. Day trades are reserved for emergency exits only.

5. **Simulated capital cap**: During paper trading, a $5,000 virtual balance prevents the system from learning behaviors that wouldn't work at the real account size.

## Disclaimer

This is an engineering project for an experimental algorithmic trading system. It is not financial advice. All trading involves risk of loss. The $5,000 seed capital should be considered money you are willing to lose entirely during the learning and validation period. Past backtested performance does not guarantee future results.

v1.0.0
