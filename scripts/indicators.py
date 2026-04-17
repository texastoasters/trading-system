"""
indicators.py — Technical indicator library for the trading system.
Pure NumPy implementations. No pandas dependency for computation.

All functions take NumPy arrays and return NumPy arrays.
Convention: input arrays are oldest-first (index 0 = earliest bar).
"""

import numpy as np


def sma(close: np.ndarray, period: int) -> np.ndarray:
    """Simple Moving Average."""
    out = np.full_like(close, np.nan)
    if len(close) < period:
        return out
    cumsum = np.cumsum(close)
    cumsum[period:] = cumsum[period:] - cumsum[:-period]
    out[period - 1:] = cumsum[period - 1:] / period
    return out


def ema(close: np.ndarray, period: int) -> np.ndarray:
    """Exponential Moving Average using Wilder-compatible smoothing."""
    out = np.full_like(close, np.nan)
    if len(close) < period:
        return out
    alpha = 2.0 / (period + 1)
    # Seed with SMA
    out[period - 1] = np.mean(close[:period])
    for i in range(period, len(close)):
        out[i] = alpha * close[i] + (1 - alpha) * out[i - 1]
    return out


def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """
    Relative Strength Index using Wilder's smoothing method.
    Returns values 0-100.
    """
    out = np.full_like(close, np.nan)
    if len(close) < period + 1:
        return out

    deltas = np.diff(close)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Seed with simple average
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    if avg_loss == 0:
        out[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[period] = 100.0 - (100.0 / (1.0 + rs))

    # Wilder smoothing
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            out[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i + 1] = 100.0 - (100.0 / (1.0 + rs))

    return out


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
        period: int = 14) -> np.ndarray:
    """
    Average True Range using Wilder's smoothing.
    Returns ATR values (in price units, not percentage).
    """
    out = np.full_like(close, np.nan)
    if len(close) < period + 1:
        return out

    # True Range
    tr = np.zeros(len(close))
    tr[0] = high[0] - low[0]
    for i in range(1, len(close)):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1])
        )

    # Seed with simple average
    out[period] = np.mean(tr[1:period + 1])

    # Wilder smoothing
    for i in range(period + 1, len(close)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period

    return out


def adx(high: np.ndarray, low: np.ndarray, close: np.ndarray,
        period: int = 14) -> tuple:
    """
    Average Directional Index with +DI and -DI.
    Returns (adx_values, plus_di, minus_di) as three NumPy arrays.
    """
    n = len(close)
    adx_out = np.full(n, np.nan)
    pdi_out = np.full(n, np.nan)
    mdi_out = np.full(n, np.nan)

    if n < period * 2 + 1:
        return adx_out, pdi_out, mdi_out

    # True Range
    tr = np.zeros(n)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)

    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1])
        )
        up_move = high[i] - high[i - 1]
        down_move = low[i - 1] - low[i]

        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0

    # Wilder smoothing for TR, +DM, -DM
    atr_smooth = np.mean(tr[1:period + 1])
    pdm_smooth = np.mean(plus_dm[1:period + 1])
    mdm_smooth = np.mean(minus_dm[1:period + 1])

    dx_values = []

    for i in range(period, n):
        if i == period:
            atr_smooth = np.mean(tr[1:period + 1])
            pdm_smooth = np.mean(plus_dm[1:period + 1])
            mdm_smooth = np.mean(minus_dm[1:period + 1])
        else:
            atr_smooth = (atr_smooth * (period - 1) + tr[i]) / period
            pdm_smooth = (pdm_smooth * (period - 1) + plus_dm[i]) / period
            mdm_smooth = (mdm_smooth * (period - 1) + minus_dm[i]) / period

        if atr_smooth == 0:
            pdi_out[i] = 0
            mdi_out[i] = 0
        else:
            pdi_out[i] = 100.0 * pdm_smooth / atr_smooth
            mdi_out[i] = 100.0 * mdm_smooth / atr_smooth

        di_sum = pdi_out[i] + mdi_out[i]
        if di_sum == 0:
            dx = 0.0
        else:
            dx = 100.0 * abs(pdi_out[i] - mdi_out[i]) / di_sum
        dx_values.append(dx)

    # ADX = smoothed DX
    if len(dx_values) >= period:
        adx_out[period * 2 - 1] = np.mean(dx_values[:period])
        for i in range(period, len(dx_values)):
            idx = period + i
            if idx < n:
                adx_out[idx] = (adx_out[idx - 1] * (period - 1) + dx_values[i]) / period

    return adx_out, pdi_out, mdi_out


def macd(close: np.ndarray, fast: int = 12, slow: int = 26,
         signal: int = 9) -> tuple:
    """
    MACD indicator.
    Returns (macd_line, signal_line, histogram) as three NumPy arrays.
    """
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)

    macd_line = ema_fast - ema_slow

    # Signal line = EMA of MACD line (ignoring NaNs)
    signal_line = np.full_like(close, np.nan)
    # Find first valid MACD value
    first_valid = None
    for i in range(len(macd_line)):
        if not np.isnan(macd_line[i]):
            first_valid = i
            break

    if first_valid is not None and len(close) - first_valid >= signal:
        valid_macd = macd_line[first_valid:]
        signal_ema = ema(valid_macd, signal)
        signal_line[first_valid:] = signal_ema

    histogram = macd_line - signal_line

    return macd_line, signal_line, histogram


def vwap(high: np.ndarray, low: np.ndarray, close: np.ndarray,
         volume: np.ndarray) -> np.ndarray:
    """
    Volume-Weighted Average Price (intraday, resets each session).
    For daily bars this is the simple cumulative VWAP.
    """
    typical_price = (high + low + close) / 3.0
    cum_tp_vol = np.cumsum(typical_price * volume)
    cum_vol = np.cumsum(volume)
    # Avoid division by zero
    with np.errstate(divide='ignore', invalid='ignore'):
        out = np.where(cum_vol > 0, cum_tp_vol / cum_vol, np.nan)
    return out


def relative_volume(volume: np.ndarray, period: int = 20) -> np.ndarray:
    """
    Relative volume = current volume / average volume over period.
    Returns ratio (1.0 = average, 2.0 = double average).
    """
    avg_vol = sma(volume.astype(float), period)
    with np.errstate(divide='ignore', invalid='ignore'):
        out = np.where(avg_vol > 0, volume / avg_vol, np.nan)
    return out


def ibs(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """
    Internal Bar Strength = (close - low) / (high - low).
    Returns values 0.0 (close at low) to 1.0 (close at high).
    NaN when high == low (zero range).
    """
    rng = high - low
    with np.errstate(divide='ignore', invalid='ignore'):
        out = np.where(rng > 0, (close - low) / rng, np.nan)
    return out


# ── Convenience: compute all indicators for a daily bar dataset ──

def compute_all_daily(high, low, close, volume):
    """
    Compute all daily indicators needed by the trading system.
    Returns a dict of indicator name → numpy array.
    """
    return {
        'sma200': sma(close, 200),
        'rsi2': rsi(close, 2),
        'rsi14': rsi(close, 14),
        'atr14': atr(high, low, close, 14),
        'adx14': adx(high, low, close, 14),  # returns tuple (adx, +di, -di)
        'macd': macd(close),  # returns tuple (line, signal, histogram)
        'ema9': ema(close, 9),
        'ema50': ema(close, 50),
        'rvol20': relative_volume(volume, 20),
    }


if __name__ == "__main__":  # pragma: no cover
    # Quick self-test with synthetic data
    np.random.seed(42)
    n = 300
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    high = close + np.abs(np.random.randn(n) * 0.3)
    low = close - np.abs(np.random.randn(n) * 0.3)
    volume = np.random.randint(100000, 1000000, n).astype(float)

    print("Testing indicators on synthetic data (n=300)...")
    print(f"  SMA(200) last value: {sma(close, 200)[-1]:.2f}")
    print(f"  RSI(2) last value:   {rsi(close, 2)[-1]:.2f}")
    print(f"  RSI(14) last value:  {rsi(close, 14)[-1]:.2f}")
    print(f"  ATR(14) last value:  {atr(high, low, close, 14)[-1]:.4f}")

    adx_val, pdi, mdi = adx(high, low, close, 14)
    print(f"  ADX(14) last value:  {adx_val[-1]:.2f}")
    print(f"  +DI last value:      {pdi[-1]:.2f}")
    print(f"  -DI last value:      {mdi[-1]:.2f}")

    ml, sl, hist = macd(close)
    print(f"  MACD line last:      {ml[-1]:.4f}")
    print(f"  MACD histogram last: {hist[-1]:.4f}")
    print(f"  RVOL(20) last:       {relative_volume(volume, 20)[-1]:.2f}")

    print("\n✅ All indicators computed successfully.")

# v1.0.0
