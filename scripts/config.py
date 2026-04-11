"""
config.py — Shared configuration for the trading system.

All agents import this for Redis keys, instrument universe defaults,
strategy parameters, and system constants.
"""

import os
import json
import redis


def _load_trading_env():
    """Auto-load ~/.trading_env so scripts don't require manual sourcing."""
    env_path = "/home/linuxuser/.trading_env"
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:]
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            os.environ[key] = val


_load_trading_env()

# ── Environment ─────────────────────────────────────────────

ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
PAPER_TRADING = True  # Set False when going live

# ── Capital ─────────────────────────────────────────────────

INITIAL_CAPITAL = 5000.00
MAX_CONCURRENT_POSITIONS = 5
MAX_EQUITY_POSITIONS = 3
MAX_CRYPTO_POSITIONS = 2
EQUITY_ALLOCATION_PCT = 0.70
CRYPTO_ALLOCATION_PCT = 0.30

# ── Risk ────────────────────────────────────────────────────

RISK_PER_TRADE_PCT = 0.01       # 1% of equity
DAILY_LOSS_LIMIT_PCT = 0.03     # 3% of equity
MANUAL_EXIT_REENTRY_DROP_PCT = 0.03  # price must drop 3% below manual-exit price before re-entry
ATR_STOP_MULTIPLIER = 2.0
BTC_FEE_RATE = 0.004            # 0.40% round-trip
BTC_MIN_EXPECTED_GAIN = 0.006   # 0.60% minimum expected gain

# ── Agent Restart Policy ────────────────────────────────────

MAX_AUTO_RESTARTS = 3  # halt and alert after this many consecutive restart attempts

# ── Earnings Avoidance ──────────────────────────────────────

EARNINGS_DAYS_BEFORE = 2   # block entry N days before earnings
EARNINGS_DAYS_AFTER = 1    # block entry N days after earnings

# ── RSI-2 Strategy Parameters ──────────────────────────────

RSI2_ENTRY_CONSERVATIVE = 10.0
RSI2_ENTRY_AGGRESSIVE = 5.0
RSI2_EXIT = 60.0
RSI2_SMA_PERIOD = 200
RSI2_ATR_PERIOD = 14
RSI2_MAX_HOLD_DAYS = 5

# ── Regime ──────────────────────────────────────────────────

ADX_PERIOD = 14
ADX_RANGING_THRESHOLD = 20
ADX_TREND_THRESHOLD = 25

# ── Drawdown Thresholds ─────────────────────────────────────

DRAWDOWN_CAUTION = 5.0
DRAWDOWN_DEFENSIVE = 10.0
DRAWDOWN_CRITICAL = 15.0
DRAWDOWN_HALT = 20.0

# ── Trailing Stop-Loss ──────────────────────────────────────

# Minimum unrealized gain (% from entry price) to activate a trailing stop.
# When gain >= this threshold, the executor cancels the fixed GTC stop and submits
# an Alpaca native trailing stop. Lower tiers get a tighter trigger so smaller
# gains are still locked in (lower-conviction names need faster protection).
TRAILING_TRIGGER_PCT = {
    1: 5.0,   # T1: premium names — give room before locking in
    2: 5.0,   # T2: same as T1
    3: 4.0,   # T3: lower conviction — activate earlier
}

# Trail distance as % below current price (Alpaca trail_percent parameter).
# Wider trails avoid noise-driven shakeouts on more volatile or lower-tier names.
# Must be smaller than the corresponding TRAILING_TRIGGER_PCT entry.
TRAILING_TRAIL_PCT = {
    1: 2.0,   # T1: tight trail — high-conviction names
    2: 2.5,   # T2: medium
    3: 3.0,   # T3: wider
}

# ── Default Universe (before Supervisor populates Redis) ────

DEFAULT_UNIVERSE = {
    "tier1": ["SPY", "QQQ", "NVDA", "XLK", "XLY", "XLI"],
    "tier2": ["GOOGL", "XLF", "META", "TSLA", "XLC", "DIA", "BTC/USD"],
    "tier3": ["V", "XLE", "XLV", "IWM"],
    "disabled": [],
    "archived": [],
    "last_revalidation": None,
}

DEFAULT_TIERS = {}
for tier_num, tier_key in [(1, "tier1"), (2, "tier2"), (3, "tier3")]:
    for sym in DEFAULT_UNIVERSE[tier_key]:
        DEFAULT_TIERS[sym] = tier_num

SECTOR_MAP = {
    "SPY": "broad", "QQQ": "broad", "DIA": "broad", "IWM": "broad",
    "XLK": "tech", "NVDA": "tech", "ON": "tech",
    "GOOGL": "tech", "META": "tech",
    "XLF": "financial", "BK": "financial", "V": "financial",
    "XLI": "industrial",
    "XLY": "consumer_disc", "TSLA": "consumer_disc",
    "XLC": "communications",
    "XLE": "energy", "CEG": "energy",
    "XLV": "healthcare",
    "KGC": "gold",
    "BTC/USD": "crypto",
}

# ── Redis Keys ──────────────────────────────────────────────

class Keys:
    UNIVERSE = "trading:universe"
    TIERS = "trading:tiers"
    REGIME = "trading:regime"
    WATCHLIST = "trading:watchlist"
    SIGNALS = "trading:signals"
    APPROVED_ORDERS = "trading:approved_orders"
    POSITIONS = "trading:positions"
    SIMULATED_EQUITY = "trading:simulated_equity"
    PEAK_EQUITY = "trading:peak_equity"
    DAILY_PNL = "trading:daily_pnl"
    DRAWDOWN = "trading:drawdown"
    PDT_COUNT = "trading:pdt:count"
    RISK_MULTIPLIER = "trading:risk_multiplier"
    SYSTEM_STATUS = "trading:system_status"
    # Note: disabled instruments are stored in universe["disabled"], not a separate key
    # Note: strategy params are not yet implemented

    RESTART_COUNT = "trading:restart_count"

    @staticmethod
    def heartbeat(agent: str) -> str:
        return f"trading:heartbeat:{agent}"

    @staticmethod
    def whipsaw(symbol: str) -> str:
        return f"trading:whipsaw:{symbol}"

    @staticmethod
    def exit_signaled(symbol: str) -> str:
        """Set when an exit signal is dispatched; cleared on confirmed sell.
        Prevents the same daily-bar condition from re-firing every cycle."""
        return f"trading:exit_signaled:{symbol}"

    @staticmethod
    def manual_exit(symbol: str) -> str:
        """Stores the fill price of a manual dashboard liquidation.
        Watcher blocks re-entry until price drops MANUAL_EXIT_REENTRY_DROP_PCT
        below this value, then clears the key automatically."""
        return f"trading:manual_exit:{symbol}"


# ── Redis Connection ────────────────────────────────────────

def get_redis() -> redis.Redis:
    return redis.Redis(host="localhost", port=6379, decode_responses=True)


def init_redis_state(r: redis.Redis):
    """Initialize Redis with default state if not already set."""
    if not r.exists(Keys.UNIVERSE):
        r.set(Keys.UNIVERSE, json.dumps(DEFAULT_UNIVERSE))
    if not r.exists(Keys.TIERS):
        r.set(Keys.TIERS, json.dumps(DEFAULT_TIERS))
    if not r.exists(Keys.REGIME):
        r.set(Keys.REGIME, json.dumps({"regime": "RANGING", "adx": 20, "initialized_default": True}))
    if not r.exists(Keys.SIMULATED_EQUITY):
        r.set(Keys.SIMULATED_EQUITY, str(INITIAL_CAPITAL))
    if not r.exists(Keys.PEAK_EQUITY):
        r.set(Keys.PEAK_EQUITY, str(INITIAL_CAPITAL))
    if not r.exists(Keys.DAILY_PNL):
        r.set(Keys.DAILY_PNL, "0.0")
    if not r.exists(Keys.DRAWDOWN):
        r.set(Keys.DRAWDOWN, "0.0")
    if not r.exists(Keys.PDT_COUNT):
        r.set(Keys.PDT_COUNT, "0")
    if not r.exists(Keys.RISK_MULTIPLIER):
        r.set(Keys.RISK_MULTIPLIER, "1.0")
    if not r.exists(Keys.SYSTEM_STATUS):
        r.set(Keys.SYSTEM_STATUS, "active")


# ── Helpers ─────────────────────────────────────────────────

def get_active_instruments(r: redis.Redis) -> list:
    """Return list of all active (non-disabled) instruments."""
    universe = json.loads(r.get(Keys.UNIVERSE) or json.dumps(DEFAULT_UNIVERSE))
    return universe["tier1"] + universe["tier2"] + universe["tier3"]


def get_tier(r: redis.Redis, symbol: str) -> int:
    """Return tier number for a symbol (1, 2, 3, or 99 if unknown)."""
    tiers = json.loads(r.get(Keys.TIERS) or json.dumps(DEFAULT_TIERS))
    return tiers.get(symbol, 99)


def get_simulated_equity(r: redis.Redis) -> float:
    return float(r.get(Keys.SIMULATED_EQUITY) or INITIAL_CAPITAL)


def get_drawdown(r: redis.Redis) -> float:
    equity = get_simulated_equity(r)
    peak = float(r.get(Keys.PEAK_EQUITY) or INITIAL_CAPITAL)
    if peak <= 0:
        return 0.0
    return max(0.0, (peak - equity) / peak * 100)


def is_crypto(symbol: str) -> bool:
    return "/" in symbol


def get_sector(symbol: str) -> str:
    return SECTOR_MAP.get(symbol, "unknown")


# v1.0.0
