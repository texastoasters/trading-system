"""
config.py — Shared configuration for the trading system.

All agents import this for Redis keys, instrument universe defaults,
strategy parameters, and system constants.
"""

import os
import sys
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
MAX_CONCURRENT_POSITIONS = 5  # HOT-RELOADABLE via trading:config
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
RISK_PER_TRADE_PCT = 0.01  # HOT-RELOADABLE via trading:config
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
RSI2_ENTRY_CONSERVATIVE = 10.0  # HOT-RELOADABLE via trading:config
# RSI-2 entry threshold in aggressive (TRENDING) regime. Tighter threshold used
# when ADX > ADX_TREND_THRESHOLD, since trending markets mean-revert less deeply.
RSI2_ENTRY_AGGRESSIVE = 5.0  # HOT-RELOADABLE via trading:config
# Volume filter: skip entry if today's volume < this fraction of the 20-day average daily
# volume (ADV). Adapts per instrument without per-instrument calibration.
MIN_VOLUME_RATIO = 0.5
# RSI-2 exit threshold. Exit signal generated (take-profit) when RSI-2 rises above
# this value on a daily bar, indicating the oversold condition has normalized.
RSI2_EXIT = 60.0  # HOT-RELOADABLE via trading:config
# SMA lookback period (days) for the trend filter. Entries only allowed when
# the instrument's close price > its simple moving average over this period.
RSI2_SMA_PERIOD = 200
# ATR lookback period (days). Used to calculate stop-loss distance and regime-adjusted
# position sizing. Screener populates atr14 in the watchlist on each scan.
RSI2_ATR_PERIOD = 14
# Maximum days to hold a position. If a trade is still open after this many days
# with no RSI-2 exit or stop hit, Watcher generates a time-stop exit signal.
RSI2_MAX_HOLD_DAYS = 5  # HOT-RELOADABLE via trading:config
# Number of trailing calendar days shown in the RSI-2 signal heatmap on the dashboard.
HEATMAP_DAYS = 14
# Lookback window (bars) for bullish RSI-2 divergence detection. Screener checks whether
# the current bar has a lower price low AND a higher RSI-2 low than any bar in this window.
DIVERGENCE_WINDOW = 10

# ── IBS (Internal Bar Strength) Strategy Parameters ─────────

# Second entry path alongside RSI-2. Fires on days RSI-2 misses. Lower max_hold
# because IBS exits on close > prev_high or next-day mean revert rather than
# waiting for RSI-2 > 60.
# Entry: IBS < IBS_ENTRY_THRESHOLD AND close > SMA(200).
IBS_ENTRY_THRESHOLD = 0.15
# Max calendar days to hold an IBS trade before watcher emits a time-stop exit.
IBS_MAX_HOLD_DAYS = 3
# Stop-loss ATR multiple used when IBS entry fills. Same formula as RSI-2 but a
# distinct constant to allow tuning the two strategies independently.
IBS_ATR_MULT = 2.0

# When both RSI-2 and IBS qualify on the same symbol/bar the watcher merges
# them into a single stacked signal. This multiplier bumps the signal
# confidence above the stronger single-strategy value so downstream ranking
# in the Portfolio Manager can prefer stacked entries.
STACKED_CONFIDENCE_BOOST = 1.25

# ── Donchian-BO trend slot (Wave 4 #4) ──────────────────────
# Third entry path. Trend-following, 20-day breakout above the prior N-bar high
# with SMA(200) trend gate. Exits on 10-day low (chandelier), stop_loss, or 30-day
# time-stop. Wider ATR multiple (3.0x) than RSI-2/IBS because breakouts need
# breathing room. Only the curated DONCHIAN_SYMBOLS set is considered — the
# research showed Donchian-BO wins on names where RSI-2 sits idle.
DONCHIAN_ENTRY_LEN = 20
DONCHIAN_EXIT_LEN = 10
DONCHIAN_MAX_HOLD_DAYS = 30
DONCHIAN_ATR_MULT = 3.0
DONCHIAN_SYMBOLS = {"DG", "GOOGL", "NVDA", "AMGN", "SMH", "LIN", "XLY"}

# FINRA's Pattern Day Trader rule caps same-day round-trip closes at 3 per
# rolling 5 business days on sub-$25K accounts. Portfolio Manager blocks a
# displacement close when the target was entered today and this cap is hit.
PDT_MAX_DAY_TRADES = 3

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
DRAWDOWN_CAUTION = 5.0  # HOT-RELOADABLE via trading:config
# At DEFENSIVE level (10% drawdown), Tier 2 and Tier 3 entries are disabled.
# Only Tier 1 instruments can receive new entries.
DRAWDOWN_DEFENSIVE = 10.0  # HOT-RELOADABLE via trading:config
# At CRITICAL level (15% drawdown), Tier 2+ are disabled AND the simulated
# equity cap is cut in half to further reduce exposure.
DRAWDOWN_CRITICAL = 15.0  # HOT-RELOADABLE via trading:config
# At HALT level (20% drawdown), ALL new entries are blocked. system_status → halted.
# Only manual intervention or EOD Supervisor reset can clear the halt.
DRAWDOWN_HALT = 20.0  # HOT-RELOADABLE via trading:config

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
    "tier2": ["GOOGL", "XLF", "XLC", "DIA", "BTC/USD"],
    "tier3": ["V", "XLE", "XLV", "IWM"],
    # META, TSLA disabled after Wave 4 alpha review — flat/negative across all
    # backtested strategies in the 2y window. Revisit on next universe re-validation.
    "disabled": ["META", "TSLA"],
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
    # Hash {symbol: close_time_iso} of positions closed today. Used by the
    # executor same-day-round-trip gate: if pdt_count >= 3, reject buys of
    # symbols in this hash (would be the 4th day trade). Cleared by
    # supervisor --reset-daily. Empty hash = no same-day closes.
    CLOSED_TODAY = "trading:closed_today"
    RISK_MULTIPLIER = "trading:risk_multiplier"
    SYSTEM_STATUS = "trading:system_status"
    # Note: disabled instruments are stored in universe["disabled"], not a separate key
    # Note: strategy params are not yet implemented

    RESTART_COUNT = "trading:restart_count"
    CONFIG = "trading:config"  # Hot-reload overrides (JSON). See load_overrides().
    HEATMAP = "trading:heatmap"  # RSI-2 heatmap snapshot. Set by screener on each scan.

    @staticmethod
    def heartbeat(agent: str) -> str:
        return f"trading:heartbeat:{agent}"

    @staticmethod
    def thresholds(symbol: str) -> str:
        """Per-symbol RSI-2 entry threshold + time-stop map written by the
        Wave 4 #2b/#3b supervisor refit job. Value is JSON
        `{"RANGING": int|null, "UPTREND": int|null, "DOWNTREND": int|null,
          "max_hold": int|null, "refit": "YYYY-MM-DD"}`. `max_hold` was
        added in v0.32.6; older payloads (regimes only) still read cleanly
        via the helper fallbacks."""
        return f"trading:thresholds:{symbol}"

    @staticmethod
    def whipsaw(symbol: str, strategy: str = "RSI2") -> str:
        """Per-strategy whipsaw cooldown so an RSI-2 stop-loss whipsaw does not
        block an unrelated IBS entry on the same symbol, and vice versa."""
        return f"trading:whipsaw:{symbol}:{strategy}"

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
    def exited_today(symbol: str) -> str:
        """Set by executor after any sell fill. Watcher blocks re-entry until
        key expires at midnight ET, preventing same-day rebuy and PDT burn."""
        return f"trading:exited_today:{symbol}"

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
    """Return list of all active (non-disabled, non-blacklisted) instruments."""
    universe = json.loads(r.get(Keys.UNIVERSE) or json.dumps(DEFAULT_UNIVERSE))
    blacklisted = set(universe.get("blacklisted") or [])
    disabled = set(universe.get("disabled") or [])
    excluded = blacklisted | disabled
    all_tiers = universe["tier1"] + universe["tier2"] + universe["tier3"]
    return [s for s in all_tiers if s not in excluded]


def get_tier(r: redis.Redis, symbol: str) -> int:
    """Return tier number for a symbol (1, 2, 3, or 99 if unknown)."""
    tiers = json.loads(r.get(Keys.TIERS) or json.dumps(DEFAULT_TIERS))
    return tiers.get(symbol, 99)


def get_entry_threshold(r: redis.Redis, symbol: str, regime: str) -> float:
    """Return the RSI-2 entry threshold for (symbol, regime). Reads the
    per-symbol map written by the Wave 4 #2b refit job from
    `trading:thresholds:{symbol}`; falls back to the global live rule when
    the key is missing, the regime slot is absent/null, the payload is
    malformed, or `regime` is outside {RANGING, UPTREND, DOWNTREND}.

    Fallback matches the current live screener logic: UPTREND uses
    `RSI2_ENTRY_AGGRESSIVE`; RANGING and DOWNTREND use
    `RSI2_ENTRY_CONSERVATIVE`. A bad refit payload must never change
    routing — the helper only narrows the threshold when it has a
    validated per-symbol value."""
    fallback = (RSI2_ENTRY_AGGRESSIVE if regime == "UPTREND"
                else RSI2_ENTRY_CONSERVATIVE)
    raw = r.get(Keys.thresholds(symbol))
    if not raw:
        return fallback
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return fallback
    value = payload.get(regime)
    if value is None:
        return fallback
    return value


def get_max_hold_days(r: redis.Redis, symbol: str) -> int:
    """Return the RSI-2 time-stop bar count for `symbol`. Reads the per-symbol
    map written by the Wave 4 #3b refit job from `trading:thresholds:{symbol}`
    (shared key with the entry-threshold helper); falls back to the global
    `RSI2_MAX_HOLD_DAYS` const when the key is missing, the `max_hold` slot is
    absent/null, or the payload is malformed."""
    raw = r.get(Keys.thresholds(symbol))
    if not raw:
        return RSI2_MAX_HOLD_DAYS
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return RSI2_MAX_HOLD_DAYS
    value = payload.get("max_hold")
    if value is None:
        return RSI2_MAX_HOLD_DAYS
    return int(value)


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


def load_overrides(r: redis.Redis) -> None:
    """
    Read trading:config from Redis and apply valid overrides to module globals.

    Called at the top of each agent's main cycle. Missing key = no-op.
    Invalid type or out-of-range value: log warning, skip that key.
    This is the only supported mechanism for runtime parameter changes.
    """
    try:
        raw = r.get(Keys.CONFIG)
    except Exception:
        return  # Redis unavailable — continue with module defaults
    if not raw:
        return

    try:
        overrides = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        print("[config] WARNING: trading:config contains invalid JSON, skipping overrides")
        return

    def _trail_cast(v):
        if not isinstance(v, dict):
            raise ValueError("expected dict")
        return {int(k): float(x) for k, x in v.items()}

    def _trail_check(d):
        return set(d.keys()) == {1, 2, 3} and all(0 < x <= 50 for x in d.values())

    def _daemon_cast(v):
        if not isinstance(v, dict):
            raise ValueError("expected dict")
        return {str(k): int(x) for k, x in v.items()}

    def _daemon_check(d):
        return (set(d.keys()) == {"executor", "portfolio_manager", "watcher"}
                and all(1 <= x <= 1440 for x in d.values()))

    _SPEC = {
        "RSI2_ENTRY_CONSERVATIVE":       (float, lambda v: 0 < v <= 30),
        "RSI2_ENTRY_AGGRESSIVE":         (float, lambda v: 0 < v <= 20),
        "RSI2_EXIT":                     (float, lambda v: 50 <= v <= 95),
        "RSI2_MAX_HOLD_DAYS":            (int,   lambda v: 1 <= v <= 30),
        "RSI2_SMA_PERIOD":               (int,   lambda v: 20 <= v <= 500),
        "RSI2_ATR_PERIOD":               (int,   lambda v: 2 <= v <= 60),
        "HEATMAP_DAYS":                  (int,   lambda v: 1 <= v <= 120),
        "DIVERGENCE_WINDOW":             (int,   lambda v: 2 <= v <= 60),
        "MIN_VOLUME_RATIO":              (float, lambda v: 0 <= v <= 5.0),
        "RISK_PER_TRADE_PCT":            (float, lambda v: 0 < v <= 0.05),
        "MAX_CONCURRENT_POSITIONS":      (int,   lambda v: 1 <= v <= 20),
        "MAX_EQUITY_POSITIONS":          (int,   lambda v: 1 <= v <= 20),
        "MAX_CRYPTO_POSITIONS":          (int,   lambda v: 0 <= v <= 10),
        "EQUITY_ALLOCATION_PCT":         (float, lambda v: 0 < v <= 1.0),
        "CRYPTO_ALLOCATION_PCT":         (float, lambda v: 0 <= v < 1.0),
        "ATR_STOP_MULTIPLIER":           (float, lambda v: 0.5 <= v <= 10.0),
        "DAILY_LOSS_LIMIT_PCT":          (float, lambda v: 0 < v <= 0.20),
        "MANUAL_EXIT_REENTRY_DROP_PCT":  (float, lambda v: 0 <= v <= 0.50),
        "ATTRIBUTION_MAX_LOOKBACK_DAYS": (int,   lambda v: 7 <= v <= 365),
        "IBS_ENTRY_THRESHOLD":           (float, lambda v: 0 < v < 1),
        "IBS_MAX_HOLD_DAYS":             (int,   lambda v: 1 <= v <= 30),
        "IBS_ATR_MULT":                  (float, lambda v: 0.5 <= v <= 10.0),
        "STACKED_CONFIDENCE_BOOST":      (float, lambda v: 1.0 <= v <= 5.0),
        "DONCHIAN_ENTRY_LEN":            (int,   lambda v: 5 <= v <= 120),
        "DONCHIAN_EXIT_LEN":             (int,   lambda v: 3 <= v <= 120),
        "DONCHIAN_MAX_HOLD_DAYS":        (int,   lambda v: 1 <= v <= 120),
        "DONCHIAN_ATR_MULT":             (float, lambda v: 0.5 <= v <= 10.0),
        "ADX_PERIOD":                    (int,   lambda v: 5 <= v <= 60),
        "ADX_RANGING_THRESHOLD":         (int,   lambda v: 5 <= v <= 50),
        "ADX_TREND_THRESHOLD":           (int,   lambda v: 10 <= v <= 60),
        "BTC_FEE_RATE":                  (float, lambda v: 0 <= v <= 0.05),
        "BTC_MIN_EXPECTED_GAIN":         (float, lambda v: 0 < v <= 0.10),
        "EARNINGS_DAYS_BEFORE":          (int,   lambda v: 0 <= v <= 14),
        "EARNINGS_DAYS_AFTER":           (int,   lambda v: 0 <= v <= 14),
        "DRAWDOWN_CAUTION":              (float, lambda v: 0 < v < 100),
        "DRAWDOWN_DEFENSIVE":            (float, lambda v: 0 < v < 100),
        "DRAWDOWN_CRITICAL":             (float, lambda v: 0 < v < 100),
        "DRAWDOWN_HALT":                 (float, lambda v: 0 < v < 100),
        "TRAILING_TRIGGER_PCT":          (_trail_cast,  _trail_check),
        "TRAILING_TRAIL_PCT":            (_trail_cast,  _trail_check),
        "DAEMON_STALE_THRESHOLDS":       (_daemon_cast, _daemon_check),
    }

    validated = {}
    for key, (cast, check) in _SPEC.items():
        if key not in overrides:
            continue
        try:
            val = cast(overrides[key])
            if not check(val):
                raise ValueError(f"{val} out of range")
        except (TypeError, ValueError) as e:
            print(f"[config] WARNING: override {key}={overrides[key]!r} invalid ({e}), skipping")
            continue
        validated[key] = val

    # Cross-check: AGGRESSIVE must be < CONSERVATIVE (use effective value after override)
    if "RSI2_ENTRY_AGGRESSIVE" in validated:
        effective_conservative = validated.get(
            "RSI2_ENTRY_CONSERVATIVE", RSI2_ENTRY_CONSERVATIVE
        )
        if validated["RSI2_ENTRY_AGGRESSIVE"] >= effective_conservative:
            print(
                f"[config] WARNING: RSI2_ENTRY_AGGRESSIVE="
                f"{validated['RSI2_ENTRY_AGGRESSIVE']} >= "
                f"RSI2_ENTRY_CONSERVATIVE={effective_conservative}, skipping aggressive override"
            )
            del validated["RSI2_ENTRY_AGGRESSIVE"]

    # Cross-check: drawdown thresholds must be strictly ascending
    _dd_keys = ["DRAWDOWN_CAUTION", "DRAWDOWN_DEFENSIVE", "DRAWDOWN_CRITICAL", "DRAWDOWN_HALT"]
    _mod = sys.modules[__name__]
    _dd_vals = [validated.get(k, getattr(_mod, k)) for k in _dd_keys]
    for i in range(len(_dd_vals) - 1):
        if _dd_vals[i] >= _dd_vals[i + 1]:
            print(
                "[config] WARNING: drawdown thresholds out of order after overrides, "
                "skipping all drawdown overrides"
            )
            for k in _dd_keys:
                validated.pop(k, None)
            break

    # Cross-check: ADX_RANGING_THRESHOLD must be < ADX_TREND_THRESHOLD
    if "ADX_RANGING_THRESHOLD" in validated or "ADX_TREND_THRESHOLD" in validated:
        r_val = validated.get("ADX_RANGING_THRESHOLD", getattr(_mod, "ADX_RANGING_THRESHOLD"))
        t_val = validated.get("ADX_TREND_THRESHOLD",   getattr(_mod, "ADX_TREND_THRESHOLD"))
        if r_val >= t_val:
            print(
                f"[config] WARNING: ADX_RANGING_THRESHOLD={r_val} >= "
                f"ADX_TREND_THRESHOLD={t_val}, skipping both ADX threshold overrides"
            )
            validated.pop("ADX_RANGING_THRESHOLD", None)
            validated.pop("ADX_TREND_THRESHOLD", None)

    # Cross-check: DONCHIAN_EXIT_LEN must be < DONCHIAN_ENTRY_LEN
    if "DONCHIAN_ENTRY_LEN" in validated or "DONCHIAN_EXIT_LEN" in validated:
        e_val = validated.get("DONCHIAN_ENTRY_LEN", getattr(_mod, "DONCHIAN_ENTRY_LEN"))
        x_val = validated.get("DONCHIAN_EXIT_LEN",  getattr(_mod, "DONCHIAN_EXIT_LEN"))
        if x_val >= e_val:
            print(
                f"[config] WARNING: DONCHIAN_EXIT_LEN={x_val} >= "
                f"DONCHIAN_ENTRY_LEN={e_val}, skipping both Donchian length overrides"
            )
            validated.pop("DONCHIAN_ENTRY_LEN", None)
            validated.pop("DONCHIAN_EXIT_LEN", None)

    # Cross-check: EQUITY_ALLOCATION_PCT + CRYPTO_ALLOCATION_PCT must sum to 1.0
    if "EQUITY_ALLOCATION_PCT" in validated or "CRYPTO_ALLOCATION_PCT" in validated:
        eq = validated.get("EQUITY_ALLOCATION_PCT", getattr(_mod, "EQUITY_ALLOCATION_PCT"))
        cr = validated.get("CRYPTO_ALLOCATION_PCT", getattr(_mod, "CRYPTO_ALLOCATION_PCT"))
        if abs((eq + cr) - 1.0) > 1e-9:
            print(
                f"[config] WARNING: EQUITY_ALLOCATION_PCT={eq} + CRYPTO_ALLOCATION_PCT={cr} "
                f"!= 1.0, skipping both allocation overrides"
            )
            validated.pop("EQUITY_ALLOCATION_PCT", None)
            validated.pop("CRYPTO_ALLOCATION_PCT", None)

    # Cross-check: TRAILING_TRAIL_PCT[tier] must be < TRAILING_TRIGGER_PCT[tier] per tier
    if "TRAILING_TRIGGER_PCT" in validated or "TRAILING_TRAIL_PCT" in validated:
        trig = validated.get("TRAILING_TRIGGER_PCT", getattr(_mod, "TRAILING_TRIGGER_PCT"))
        trail = validated.get("TRAILING_TRAIL_PCT",  getattr(_mod, "TRAILING_TRAIL_PCT"))
        bad = [t for t in (1, 2, 3) if trail[t] >= trig[t]]
        if bad:
            print(
                f"[config] WARNING: TRAILING_TRAIL_PCT >= TRAILING_TRIGGER_PCT for tier(s) {bad}, "
                f"skipping both trailing-stop overrides"
            )
            validated.pop("TRAILING_TRIGGER_PCT", None)
            validated.pop("TRAILING_TRAIL_PCT", None)

    # Apply validated overrides to module globals
    for key, val in validated.items():
        setattr(_mod, key, val)


# v1.0.0
