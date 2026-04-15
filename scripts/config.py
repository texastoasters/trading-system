"""
config.py — Shared configuration for the trading system.

All agents import this for Redis keys, instrument universe defaults,
strategy parameters, and system constants.
"""

import os
import json
import redis
from datetime import date, timedelta


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

# Alpaca API credentials loaded from ~/.trading_env via _load_trading_env().
# Both must be set before any agent starts. Keys are read-only at import time.
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
# When True, all orders go to Alpaca's paper trading environment. Set False for live.
PAPER_TRADING = True

# ── Capital ─────────────────────────────────────────────────

# Total virtual capital ($). NOT Alpaca's $100K paper balance. The system enforces
# this cap via trading:simulated_equity in Redis. Position sizing and Rule 1 are
# both based on this number, not Alpaca's reported equity.
INITIAL_CAPITAL = 5000.00
# Maximum simultaneous open positions across all tiers and asset classes.
MAX_CONCURRENT_POSITIONS = 5
# Maximum open positions in equity instruments (stocks, ETFs).
MAX_EQUITY_POSITIONS = 3
# Maximum open positions in crypto instruments (BTC/USD, etc.).
MAX_CRYPTO_POSITIONS = 2
# Fraction of INITIAL_CAPITAL allocated to equities. Portfolio Manager uses this
# for per-asset-class exposure limits.
EQUITY_ALLOCATION_PCT = 0.70
# Fraction of INITIAL_CAPITAL allocated to crypto. Must sum to 1.0 with EQUITY_ALLOCATION_PCT.
CRYPTO_ALLOCATION_PCT = 0.30

# ── Risk ────────────────────────────────────────────────────

# Risk per trade as a fraction of current simulated equity (1%). Portfolio Manager
# sizes every position so that a stop-loss hit equals exactly this loss in dollar terms:
#   qty = (equity * RISK_PER_TRADE_PCT) / (entry_price - stop_price)
RISK_PER_TRADE_PCT = 0.01
# Maximum daily loss as a fraction of simulated equity (3%). When the daily P&L
# reaches -(equity × DAILY_LOSS_LIMIT_PCT), Executor blocks new buys and Supervisor
# sets system_status → daily_halt until the next trading day reset.
DAILY_LOSS_LIMIT_PCT = 0.03
# Maximum lookback for drawdown attribution queries. Prevents unbounded DB scans
# during prolonged drawdowns where peak_equity_date may be months old.
ATTRIBUTION_MAX_LOOKBACK_DAYS = 90
# After a manual dashboard exit, entry for that symbol is blocked until its price
# drops this % below the manual-exit fill price. Prevents immediate re-entry into
# a position we just decided to close.
MANUAL_EXIT_REENTRY_DROP_PCT = 0.03
# ATR(14) multiplier used to compute the initial stop-loss distance from entry.
# stop_price = entry_price - (ATR_STOP_MULTIPLIER × ATR14).
# This multiplier is adjusted per-regime in Watcher: 1.5× in downtrends, 2.5× in uptrends.
ATR_STOP_MULTIPLIER = 2.0
# BTC/USD estimated round-trip fee rate (0.40%). Deducted from realized P&L on all
# crypto exits (buy fee + sell fee combined).
BTC_FEE_RATE = 0.004
# Minimum expected gain on a BTC/USD trade (0.60%). Entry signals below this threshold
# are filtered in Portfolio Manager to avoid fee-eating micro-gains.
BTC_MIN_EXPECTED_GAIN = 0.006

# ── Agent Restart Policy ────────────────────────────────────

# After this many consecutive automatic restarts, the agent halts and fires a
# critical Telegram alert. Prevents infinite crash-restart loops from generating noise.
MAX_AUTO_RESTARTS = 3

# ── Earnings Avoidance ──────────────────────────────────────

# Block new entries this many calendar days before a scheduled earnings release.
# RSI-2 mean reversion signals ahead of earnings carry outsized binary risk.
EARNINGS_DAYS_BEFORE = 2
# Block new entries this many calendar days after a scheduled earnings release.
# Post-earnings gaps can invalidate the SMA-200 trend filter temporarily.
EARNINGS_DAYS_AFTER = 1

# ── RSI-2 Strategy Parameters ──────────────────────────────

# RSI-2 entry threshold in conservative (RANGING) regime. Entry signal requires
# RSI-2 < this value AND price > SMA(RSI2_SMA_PERIOD).
RSI2_ENTRY_CONSERVATIVE = 10.0
# RSI-2 entry threshold in aggressive (TRENDING) regime. Tighter threshold used
# when ADX > ADX_TREND_THRESHOLD, since trending markets mean-revert less deeply.
RSI2_ENTRY_AGGRESSIVE = 5.0
# Volume filter: skip entry if today's volume < this fraction of the 20-day average daily
# volume (ADV). Adapts per instrument without per-instrument calibration.
MIN_VOLUME_RATIO = 0.5
# RSI-2 exit threshold. Exit signal generated (take-profit) when RSI-2 rises above
# this value on a daily bar, indicating the oversold condition has normalized.
RSI2_EXIT = 60.0
# SMA lookback period (days) for the trend filter. Entries only allowed when
# the instrument's close price > its simple moving average over this period.
RSI2_SMA_PERIOD = 200
# ATR lookback period (days). Used to calculate stop-loss distance and regime-adjusted
# position sizing. Screener populates atr14 in the watchlist on each scan.
RSI2_ATR_PERIOD = 14
# Maximum days to hold a position. If a trade is still open after this many days
# with no RSI-2 exit or stop hit, Watcher generates a time-stop exit signal.
RSI2_MAX_HOLD_DAYS = 5

# ── Regime ──────────────────────────────────────────────────

# ADX indicator lookback period (days). ADX measures trend strength regardless of
# direction. Screener computes ADX on each scan and publishes the regime to Redis.
ADX_PERIOD = 14
# ADX below this threshold → RANGING regime. Standard RSI-2 entry threshold
# (RSI2_ENTRY_CONSERVATIVE) applies. Most conducive to mean reversion.
ADX_RANGING_THRESHOLD = 20
# ADX above this threshold → TRENDING regime. Aggressive entry threshold
# (RSI2_ENTRY_AGGRESSIVE) applies and stop distance is widened by 2.5× ATR.
ADX_TREND_THRESHOLD = 25

# ── Drawdown Thresholds ─────────────────────────────────────

# At CAUTION level (5% drawdown from peak), Supervisor halves position sizes.
DRAWDOWN_CAUTION = 5.0
# At DEFENSIVE level (10% drawdown), Tier 2 and Tier 3 entries are disabled.
# Only Tier 1 instruments can receive new entries.
DRAWDOWN_DEFENSIVE = 10.0
# At CRITICAL level (15% drawdown), Tier 2+ are disabled AND the simulated
# equity cap is cut in half to further reduce exposure.
DRAWDOWN_CRITICAL = 15.0
# At HALT level (20% drawdown), ALL new entries are blocked. system_status → halted.
# Only manual intervention or EOD Supervisor reset can clear the halt.
DRAWDOWN_HALT = 20.0

# ── Trailing Stop-Loss ──────────────────────────────────────

# Minimum unrealized gain (% from entry price) to activate a trailing stop.
# When gain >= this threshold, the executor cancels the fixed GTC stop and submits
# an Alpaca native trailing stop. Lower tiers get a tighter trigger so smaller
# gains are still locked in (lower-conviction names need faster protection).
TRAILING_TRIGGER_PCT = {
    1: 5.0,   # T1: premium names — give room before locking in
    2: 5.0,   # T2: same threshold — volatility profile doesn't justify a tighter trigger
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

# NOTE: both dicts only contain tiers 1, 2, 3. Callers must guard against
# unknown tier values (e.g. use pos.get("tier", 3)) before indexing.

# ── Daemon Heartbeat Stale Thresholds ──────────────────────
# Maximum acceptable heartbeat age (minutes) before the supervisor declares a daemon crashed.
# Executor and PM use a tight Redis pub/sub loop (heartbeat every ~60s) → 5 min is sufficient.
# Watcher sleeps 30 min between cycles off-hours → threshold must exceed 30 min or the
# supervisor fires a false alarm at market open when the off-hours sleep overlaps the check.
DAEMON_STALE_THRESHOLDS = {
    "executor": 5,
    "portfolio_manager": 5,
    "watcher": 35,
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
    PEAK_EQUITY_DATE = "trading:peak_equity_date"
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

    @staticmethod
    def age_alert(symbol: str) -> str:
        """Set when a position age nudge has been sent for this symbol today.
        24h TTL prevents repeat alerts within the same calendar day."""
        return f"trading:age_alert:{symbol}"


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
    if not r.exists(Keys.PEAK_EQUITY_DATE):
        r.set(Keys.PEAK_EQUITY_DATE, date.today().isoformat())
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


def get_drawdown_attribution(r: redis.Redis, conn) -> list:
    """
    Returns per-instrument drawdown contribution since peak date.
    List of dicts: {symbol, realized_pnl, unrealized_pnl, total_pnl}
    Sorted by total_pnl ascending (worst first). Only non-zero totals included.
    Degrades gracefully: DB failure → unrealized only.
    """
    peak_date_str = r.get(Keys.PEAK_EQUITY_DATE)
    if peak_date_str:
        peak_date = date.fromisoformat(peak_date_str)
    else:
        peak_date = date.today() - timedelta(days=30)
    max_lookback = date.today() - timedelta(days=ATTRIBUTION_MAX_LOOKBACK_DAYS)
    if peak_date < max_lookback:
        peak_date = max_lookback

    # Realized: query trades closed since peak date
    realized: dict = {}
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT symbol, SUM(realized_pnl) FROM trades "
            "WHERE side = 'sell' AND realized_pnl IS NOT NULL AND time >= %s "
            "GROUP BY symbol",
            (peak_date,),
        )
        for symbol, pnl in cur.fetchall():
            realized[symbol] = float(pnl or 0)
    except Exception:
        pass  # degrade to unrealized-only

    # Unrealized: open positions from Redis
    unrealized: dict = {}
    try:
        positions = json.loads(r.get(Keys.POSITIONS) or "{}")
        for symbol, pos in positions.items():
            entry = float(pos["entry_price"])
            qty = float(pos["quantity"])
            pct = float(pos["unrealized_pnl_pct"])
            unrealized[symbol] = entry * qty * pct / 100
    except Exception:
        pass

    # Merge by symbol
    all_symbols = set(realized) | set(unrealized)
    rows = []
    for symbol in all_symbols:
        r_pnl = realized.get(symbol, 0.0)
        u_pnl = unrealized.get(symbol, 0.0)
        total = r_pnl + u_pnl
        if total != 0.0:
            rows.append({
                "symbol": symbol,
                "realized_pnl": r_pnl,
                "unrealized_pnl": u_pnl,
                "total_pnl": total,
            })

    rows.sort(key=lambda x: x["total_pnl"])
    return rows


def is_crypto(symbol: str) -> bool:
    return "/" in symbol


def get_sector(symbol: str) -> str:
    return SECTOR_MAP.get(symbol, "unknown")


# v1.0.0
