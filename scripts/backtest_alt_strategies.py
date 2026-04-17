#!/usr/bin/env python3
"""
backtest_alt_strategies.py — Evaluate 12 candidate strategies (RSI-2 baseline +
11 alternates) across Tier 1/2/3 universe.

Universal rules (apply to every strategy):
  - Long-only. Fixed 1% risk, ATR-based stop, max-hold time stop.
  - Entry at open[i+1] after signal bar i (matches live Watcher/Executor flow).
  - Exits: stop_loss checked on low[i], then strategy-specific exits, then
    hold_days >= max_hold.
  - 24h whipsaw cooldown after stop_loss exits.
  - Crypto (BTC/USD) modeled with 0.40% round-trip fee.

Usage:
    export ALPACA_API_KEY=...
    export ALPACA_SECRET_KEY=...
    PYTHONPATH=scripts python3 scripts/backtest_alt_strategies.py --years 2 \
                                                                  --csv data/alt_strategies_results.csv
"""

import os
import sys
import csv
import argparse
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

try:
    from alpaca.data.historical import StockHistoricalDataClient, CryptoHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest, CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame
except ImportError:
    print("ERROR: alpaca-py not installed. pip install alpaca-py")
    sys.exit(1)

from indicators import sma, ema, rsi, atr, adx, macd


# ── Missing indicators ──────────────────────────────────────

def bollinger(close: np.ndarray, period: int = 20, nstd: float = 2.0):
    mid = sma(close, period)
    out_up = np.full_like(close, np.nan)
    out_dn = np.full_like(close, np.nan)
    for i in range(period - 1, len(close)):
        window = close[i - period + 1:i + 1]
        s = np.std(window, ddof=0)
        out_up[i] = mid[i] + nstd * s
        out_dn[i] = mid[i] - nstd * s
    return mid, out_up, out_dn


def stochastic_k(high, low, close, period=14, smooth=3):
    n = len(close)
    raw = np.full(n, np.nan)
    for i in range(period - 1, n):
        hh = np.max(high[i - period + 1:i + 1])
        ll = np.min(low[i - period + 1:i + 1])
        if hh == ll:
            raw[i] = 50.0
        else:
            raw[i] = 100.0 * (close[i] - ll) / (hh - ll)
    out = np.full(n, np.nan)
    for i in range(period + smooth - 2, n):
        out[i] = np.mean(raw[i - smooth + 1:i + 1])
    return out


def williams_r(high, low, close, period=14):
    n = len(close)
    out = np.full(n, np.nan)
    for i in range(period - 1, n):
        hh = np.max(high[i - period + 1:i + 1])
        ll = np.min(low[i - period + 1:i + 1])
        if hh == ll:
            out[i] = -50.0
        else:
            out[i] = -100.0 * (hh - close[i]) / (hh - ll)
    return out


def mfi(high, low, close, volume, period=14):
    n = len(close)
    tp = (high + low + close) / 3.0
    mf = tp * volume
    out = np.full(n, np.nan)
    for i in range(period, n):
        pos_mf = 0.0
        neg_mf = 0.0
        for j in range(i - period + 1, i + 1):
            if tp[j] > tp[j - 1]:
                pos_mf += mf[j]
            elif tp[j] < tp[j - 1]:
                neg_mf += mf[j]
        if neg_mf == 0:
            out[i] = 100.0 if pos_mf > 0 else 50.0
        else:
            ratio = pos_mf / neg_mf
            out[i] = 100.0 - (100.0 / (1.0 + ratio))
    return out


def connors_rsi(close, rsi_len=3, streak_len=2, roc_len=100):
    r = rsi(close, rsi_len)
    # streak
    n = len(close)
    streak = np.zeros(n)
    for i in range(1, n):
        if close[i] > close[i - 1]:
            streak[i] = streak[i - 1] + 1 if streak[i - 1] >= 0 else 1
        elif close[i] < close[i - 1]:
            streak[i] = streak[i - 1] - 1 if streak[i - 1] <= 0 else -1
        else:
            streak[i] = 0
    r_streak = rsi(streak, streak_len)
    # percent-rank of 1-day ROC over last roc_len bars
    roc = np.full(n, np.nan)
    for i in range(1, n):
        if close[i - 1] != 0:
            roc[i] = (close[i] - close[i - 1]) / close[i - 1] * 100
    pct_rank = np.full(n, np.nan)
    for i in range(roc_len, n):
        window = roc[i - roc_len + 1:i + 1]
        window = window[~np.isnan(window)]
        if len(window) > 0:
            pct_rank[i] = (np.sum(window < roc[i]) / len(window)) * 100
    return (r + r_streak + pct_rank) / 3.0


def donchian(high, low, period=20):
    n = len(high)
    hh = np.full(n, np.nan)
    ll = np.full(n, np.nan)
    for i in range(period - 1, n):
        hh[i] = np.max(high[i - period + 1:i + 1])
        ll[i] = np.min(low[i - period + 1:i + 1])
    return hh, ll


# ── Data fetching ───────────────────────────────────────────

def fetch_stock(symbol, years, client):
    end = datetime.now() - timedelta(days=1)
    start = end - timedelta(days=int(365 * years))
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=start, end=end,
    )
    bars = client.get_stock_bars(request)
    bl = bars[symbol]
    return {
        'symbol': symbol,
        'dates': [b.timestamp.strftime("%Y-%m-%d") for b in bl],
        'open':   np.array([float(b.open) for b in bl]),
        'high':   np.array([float(b.high) for b in bl]),
        'low':    np.array([float(b.low) for b in bl]),
        'close':  np.array([float(b.close) for b in bl]),
        'volume': np.array([float(b.volume) for b in bl]),
    }


def fetch_crypto(symbol, years, client):
    end = datetime.now() - timedelta(hours=1)
    start = end - timedelta(days=int(365 * years))
    all_bars = []
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=90), end)
        request = CryptoBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=chunk_start, end=chunk_end,
        )
        try:
            bars = client.get_crypto_bars(request)
            all_bars.extend(bars[symbol])
        except Exception:
            pass
        chunk_start = chunk_end
    return {
        'symbol': symbol,
        'dates': [b.timestamp.strftime("%Y-%m-%d") for b in all_bars],
        'open':   np.array([float(b.open) for b in all_bars]),
        'high':   np.array([float(b.high) for b in all_bars]),
        'low':    np.array([float(b.low) for b in all_bars]),
        'close':  np.array([float(b.close) for b in all_bars]),
        'volume': np.array([float(b.volume) for b in all_bars]),
    }


# ── Result / Runner ─────────────────────────────────────────

@dataclass
class Trade:
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: float
    pnl_pct: float
    pnl_dollar: float
    hold_days: int
    exit_reason: str


@dataclass
class Result:
    strategy: str
    symbol: str
    trades: list = field(default_factory=list)
    total_return_pct: float = 0.0
    max_dd_pct: float = 0.0

    @property
    def n(self):
        return len(self.trades)

    @property
    def wins(self):
        return sum(1 for t in self.trades if t.pnl_dollar > 0)

    @property
    def win_rate(self):
        return 100 * self.wins / self.n if self.n else 0.0

    @property
    def profit_factor(self):
        g = sum(t.pnl_dollar for t in self.trades if t.pnl_dollar > 0)
        l = -sum(t.pnl_dollar for t in self.trades if t.pnl_dollar < 0)
        if l == 0:
            return float('inf') if g > 0 else 0.0
        return g / l

    @property
    def avg_hold_days(self):
        return np.mean([t.hold_days for t in self.trades]) if self.n else 0.0


def run_backtest(data, strategy_name: str, entry_fn, exit_fn, precomputed,
                 max_hold: int, atr_mult: float, account: float = 5000.0,
                 risk_pct: float = 0.01, fee_rate: float = 0.0,
                 warmup: int = 200) -> Result:
    close = data['close']
    high = data['high']
    low = data['low']
    open_ = data['open']
    dates = data['dates']
    atr14 = precomputed['atr14']

    n = len(close)
    result = Result(strategy=strategy_name, symbol=data['symbol'])

    equity = account
    peak = account
    max_dd = 0.0
    in_pos = False
    entry_price = 0.0
    entry_idx = 0
    stop_price = 0.0
    shares = 0.0
    entry_date = ""
    whipsaw_until = -1  # bar index

    for i in range(warmup, n - 1):  # -1 to allow open[i+1] entry
        if np.isnan(atr14[i]):
            continue

        if in_pos:
            hold_days = i - entry_idx
            exit_signal = False
            exit_price = close[i]
            reason = ""

            if low[i] <= stop_price:
                exit_signal = True
                exit_price = stop_price
                reason = "stop_loss"
                whipsaw_until = i + 1  # simple 1-bar ~ 24h cooldown
            else:
                ok, why = exit_fn(data, i, precomputed, entry_price, entry_idx)
                if ok:
                    exit_signal = True
                    exit_price = close[i]
                    reason = why
                elif hold_days >= max_hold:
                    exit_signal = True
                    exit_price = close[i]
                    reason = f"time_stop({max_hold}d)"

            if exit_signal:
                gross = (exit_price - entry_price) * shares
                fees = fee_rate * (entry_price + exit_price) * shares
                pnl_dollar = gross - fees
                pnl_pct = (exit_price - entry_price) / entry_price * 100 - fee_rate * 100
                result.trades.append(Trade(
                    entry_date=entry_date,
                    exit_date=dates[i],
                    entry_price=entry_price,
                    exit_price=exit_price,
                    shares=shares,
                    pnl_pct=pnl_pct,
                    pnl_dollar=pnl_dollar,
                    hold_days=hold_days,
                    exit_reason=reason,
                ))
                equity += pnl_dollar
                if equity > peak:
                    peak = equity
                dd = (peak - equity) / peak * 100 if peak > 0 else 0
                if dd > max_dd:
                    max_dd = dd
                in_pos = False

        else:
            if i < whipsaw_until:
                continue
            if entry_fn(data, i, precomputed):
                # Enter at open of next bar
                ep = open_[i + 1]
                if np.isnan(ep) or ep <= 0:
                    continue
                stop = ep - atr_mult * atr14[i]
                if stop <= 0 or stop >= ep:
                    continue
                risk_per_share = ep - stop
                risk_dollars = equity * risk_pct
                sh = risk_dollars / risk_per_share
                max_sh_by_cash = equity / ep
                sh = min(sh, max_sh_by_cash)
                if sh <= 0:
                    continue
                in_pos = True
                entry_price = ep
                stop_price = stop
                shares = sh
                entry_date = dates[i + 1]
                entry_idx = i + 1

    result.total_return_pct = (equity - account) / account * 100
    result.max_dd_pct = max_dd
    return result


# ── Precompute indicator bundle ─────────────────────────────

def precompute(data):
    close = data['close']
    high = data['high']
    low = data['low']
    volume = data['volume']

    out = {}
    out['sma20'] = sma(close, 20)
    out['sma50'] = sma(close, 50)
    out['sma200'] = sma(close, 200)
    out['ema10'] = ema(close, 10)
    out['ema20'] = ema(close, 20)
    out['ema30'] = ema(close, 30)
    out['ema100'] = ema(close, 100)
    out['atr14'] = atr(high, low, close, 14)
    out['atr10'] = atr(high, low, close, 10)
    out['rsi2'] = rsi(close, 2)
    out['rsi3'] = rsi(close, 3)
    out['rsi14'] = rsi(close, 14)
    adx14, pdi, mdi = adx(high, low, close, 14)
    out['adx14'] = adx14
    macd_line, macd_sig, macd_hist = macd(close)
    out['macd_hist'] = macd_hist
    bb_mid, bb_up, bb_dn = bollinger(close, 20, 2.0)
    out['bb_mid'] = bb_mid
    out['bb_dn'] = bb_dn
    out['stoch_k'] = stochastic_k(high, low, close, 14, 3)
    out['wr14'] = williams_r(high, low, close, 14)
    out['mfi14'] = mfi(high, low, close, volume, 14)
    out['crsi'] = connors_rsi(close, 3, 2, 100)
    don_hh, don_ll = donchian(high, low, 20)
    out['don_hh20'] = don_hh
    don_hh10, don_ll10 = donchian(high, low, 10)
    out['don_ll10'] = don_ll10
    # IBS
    rng = np.where((high - low) > 0, high - low, np.nan)
    out['ibs'] = (close - low) / rng
    # Keltner
    out['kc_lower'] = out['ema20'] - 2.0 * out['atr10']
    return out


# ── Strategies ──────────────────────────────────────────────

def s_rsi2():
    def entry(d, i, p):
        return (p['rsi2'][i] < 10 and d['close'][i] > p['sma200'][i])
    def exit_(d, i, p, ep, eidx):
        if not np.isnan(p['rsi2'][i]) and p['rsi2'][i] > 60:
            return True, "rsi2>60"
        if d['close'][i] > d['high'][i - 1]:
            return True, "close>prev_high"
        return False, ""
    return ("RSI-2", entry, exit_, 5, 2.0)


def s_bbmr():
    def entry(d, i, p):
        return (d['close'][i] < p['bb_dn'][i] and d['close'][i] > p['sma200'][i])
    def exit_(d, i, p, ep, eidx):
        if d['close'][i] >= p['bb_mid'][i]:
            return True, "close>=bb_mid"
        return False, ""
    return ("BB-MR", entry, exit_, 10, 2.0)


def s_ibs():
    def entry(d, i, p):
        return (p['ibs'][i] < 0.15 and d['close'][i] > p['sma200'][i])
    def exit_(d, i, p, ep, eidx):
        if d['close'][i] > d['high'][i - 1]:
            return True, "close>prev_high"
        return False, ""
    return ("IBS", entry, exit_, 3, 2.0)


def s_crsi():
    def entry(d, i, p):
        return (not np.isnan(p['crsi'][i]) and p['crsi'][i] < 10 and d['close'][i] > p['sma200'][i])
    def exit_(d, i, p, ep, eidx):
        if not np.isnan(p['crsi'][i]) and p['crsi'][i] > 70:
            return True, "crsi>70"
        return False, ""
    return ("ConnorsRSI", entry, exit_, 5, 2.0)


def s_wr():
    def entry(d, i, p):
        return (p['wr14'][i] < -90 and d['close'][i] > p['sma200'][i])
    def exit_(d, i, p, ep, eidx):
        if not np.isnan(p['wr14'][i]) and p['wr14'][i] > -20:
            return True, "wr>-20"
        return False, ""
    return ("Williams%R", entry, exit_, 5, 2.0)


def s_stoch():
    def entry(d, i, p):
        k = p['stoch_k']
        if np.isnan(k[i]) or np.isnan(k[i - 1]):
            return False
        return (k[i] < 20 and k[i] > k[i - 1] and d['close'][i] > p['sma200'][i])
    def exit_(d, i, p, ep, eidx):
        if not np.isnan(p['stoch_k'][i]) and p['stoch_k'][i] > 80:
            return True, "stoch>80"
        return False, ""
    return ("Stoch", entry, exit_, 5, 2.0)


def s_mfi():
    def entry(d, i, p):
        return (not np.isnan(p['mfi14'][i]) and p['mfi14'][i] < 20 and d['close'][i] > p['sma200'][i])
    def exit_(d, i, p, ep, eidx):
        if not np.isnan(p['mfi14'][i]) and p['mfi14'][i] > 80:
            return True, "mfi>80"
        return False, ""
    return ("MFI", entry, exit_, 5, 2.0)


def s_macd():
    def entry(d, i, p):
        h = p['macd_hist']
        if np.isnan(h[i]) or np.isnan(h[i - 1]):
            return False
        return (h[i] > 0 and h[i - 1] <= 0 and d['close'][i] > p['sma200'][i])
    def exit_(d, i, p, ep, eidx):
        if not np.isnan(p['macd_hist'][i]) and p['macd_hist'][i] < 0:
            return True, "macd_hist<0"
        return False, ""
    return ("MACD-Hist", entry, exit_, 10, 2.0)


def s_donchian():
    def entry(d, i, p):
        hh = p['don_hh20']
        if np.isnan(hh[i - 1]):
            return False
        return (d['close'][i] > hh[i - 1])
    def exit_(d, i, p, ep, eidx):
        ll = p['don_ll10']
        if not np.isnan(ll[i]) and d['close'][i] < ll[i]:
            return True, "close<don_ll10"
        return False, ""
    return ("Donchian-BO", entry, exit_, 30, 3.0)


def s_ema_cross():
    def entry(d, i, p):
        f = p['ema10']; s = p['ema30']; t = p['ema100']
        if np.isnan(f[i]) or np.isnan(s[i]) or np.isnan(t[i]) or np.isnan(f[i-1]) or np.isnan(s[i-1]):
            return False
        return (f[i] > s[i] and f[i - 1] <= s[i - 1] and d['close'][i] > t[i])
    def exit_(d, i, p, ep, eidx):
        f = p['ema10']; s = p['ema30']
        if not np.isnan(f[i]) and not np.isnan(s[i]) and f[i] < s[i]:
            return True, "ema_cross_down"
        return False, ""
    return ("EMA-10/30", entry, exit_, 20, 2.5)


def s_keltner():
    def entry(d, i, p):
        kl = p['kc_lower']
        if np.isnan(kl[i]):
            return False
        return (d['low'][i] < kl[i] and d['close'][i] > p['sma200'][i])
    def exit_(d, i, p, ep, eidx):
        if not np.isnan(p['ema20'][i]) and d['close'][i] >= p['ema20'][i]:
            return True, "close>=ema20"
        return False, ""
    return ("Keltner", entry, exit_, 10, 2.0)


def s_adx_pullback():
    def entry(d, i, p):
        a = p['adx14'][i]; r = p['rsi14'][i]; s50 = p['sma50'][i]
        if np.isnan(a) or np.isnan(r) or np.isnan(s50):
            return False
        return (a > 25 and d['close'][i] > s50 and r < 40)
    def exit_(d, i, p, ep, eidx):
        r = p['rsi14'][i]; s50 = p['sma50'][i]
        if not np.isnan(r) and r > 70:
            return True, "rsi14>70"
        if not np.isnan(s50) and d['close'][i] < s50:
            return True, "close<sma50"
        return False, ""
    return ("ADX-Pullback", entry, exit_, 10, 2.0)


ALL_STRATEGIES = [
    s_rsi2, s_bbmr, s_ibs, s_crsi, s_wr, s_stoch, s_mfi, s_macd,
    s_donchian, s_ema_cross, s_keltner, s_adx_pullback,
]


# ── Main ────────────────────────────────────────────────────

TIER1 = ["SPY", "QQQ", "NVDA", "XLK", "XLY", "XLI"]
TIER2 = ["GOOGL", "XLF", "META", "TSLA", "XLC", "DIA", "BTC/USD"]
TIER3 = ["V", "XLE", "XLV", "IWM", "NFLX", "SHOP", "AMGN", "SPOT", "CSCO",
         "ABBV", "ABT", "LIN", "ORCL", "SCHW", "EMR", "SMH", "NOW", "DG",
         "EA", "KMI"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=2.0)
    ap.add_argument("--csv", default="data/alt_strategies_results.csv")
    ap.add_argument("--summary", default="data/alt_strategies_summary.md")
    args = ap.parse_args()

    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    if not api_key or not secret_key:
        print("ERROR: ALPACA_API_KEY and ALPACA_SECRET_KEY must be set")
        sys.exit(1)

    stock = StockHistoricalDataClient(api_key, secret_key)
    crypto = CryptoHistoricalDataClient(api_key, secret_key)

    symbols = []
    for t, syms in [("tier1", TIER1), ("tier2", TIER2), ("tier3", TIER3)]:
        for s in syms:
            symbols.append((s, t))

    # Fetch
    print(f"Fetching {len(symbols)} symbols ({args.years}y)...")
    datasets = {}
    for sym, tier in symbols:
        try:
            if "/" in sym:
                d = fetch_crypto(sym, args.years, crypto)
            else:
                d = fetch_stock(sym, args.years, stock)
            if len(d['close']) < 250:
                print(f"  {sym}: SKIP (only {len(d['close'])} bars)")
                continue
            datasets[sym] = (d, tier)
            print(f"  {sym}: {len(d['close'])} bars")
        except Exception as e:
            print(f"  {sym}: ERROR {e}")

    # Run
    os.makedirs(os.path.dirname(args.csv) or ".", exist_ok=True)
    rows = []
    strat_totals = {}  # name → list of (pf, wr, tret, dd, n)

    for sym, (d, tier) in datasets.items():
        p = precompute(d)
        fee = 0.004 if "/" in sym else 0.0
        for factory in ALL_STRATEGIES:
            name, entry, exit_fn, mh, am = factory()
            res = run_backtest(d, name, entry, exit_fn, p, mh, am, fee_rate=fee)
            rows.append({
                'strategy': name, 'symbol': sym, 'tier': tier,
                'trades': res.n,
                'wins': res.wins,
                'win_rate': round(res.win_rate, 2),
                'profit_factor': round(res.profit_factor, 3) if res.profit_factor != float('inf') else 99.0,
                'total_return_pct': round(res.total_return_pct, 2),
                'max_dd_pct': round(res.max_dd_pct, 2),
                'avg_hold_days': round(res.avg_hold_days, 1),
            })
            strat_totals.setdefault(name, []).append(res)

    # Write CSV
    with open(args.csv, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"\nCSV → {args.csv}")

    # Summary
    lines = ["# Alt Strategies Backtest Summary", "",
             f"Run date: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
             f"Universe: {len(datasets)} symbols  •  Years: {args.years}",
             "", "## Per-strategy aggregate (across all symbols)", ""]
    lines.append("| Strategy | Trades | WinRate% | PF (wins$/loss$) | TotalReturn% (avg) | MaxDD% (avg) | AvgHoldDays |")
    lines.append("|----------|-------:|---------:|-----------------:|-------------------:|-------------:|------------:|")
    for name, results in sorted(strat_totals.items(),
                                key=lambda kv: -sum(r.total_return_pct for r in kv[1]) / max(1, len(kv[1]))):
        n_tr = sum(r.n for r in results)
        wins = sum(r.wins for r in results)
        wr = 100 * wins / n_tr if n_tr else 0
        g = sum(sum(t.pnl_dollar for t in r.trades if t.pnl_dollar > 0) for r in results)
        l = -sum(sum(t.pnl_dollar for t in r.trades if t.pnl_dollar < 0) for r in results)
        pf = (g / l) if l > 0 else (99.0 if g > 0 else 0.0)
        avg_ret = np.mean([r.total_return_pct for r in results])
        avg_dd = np.mean([r.max_dd_pct for r in results])
        avg_hold = np.mean([r.avg_hold_days for r in results if r.n])
        lines.append(f"| {name} | {n_tr} | {wr:.1f} | {pf:.2f} | {avg_ret:+.2f} | {avg_dd:.2f} | {avg_hold:.1f} |")

    # Per-tier breakdown
    lines += ["", "## Per-tier × strategy (average total_return %)", ""]
    tiers = ["tier1", "tier2", "tier3"]
    lines.append("| Strategy | " + " | ".join(t.upper() for t in tiers) + " |")
    lines.append("|----------|" + "|".join("------:" for _ in tiers) + "|")
    for name in sorted(strat_totals.keys()):
        cells = [name]
        for t in tiers:
            rs = [r for r in strat_totals[name] if any(d[1] == t for s, d in datasets.items() if s == r.symbol)]
            if rs:
                cells.append(f"{np.mean([r.total_return_pct for r in rs]):+.2f}")
            else:
                cells.append("-")
        lines.append("| " + " | ".join(cells) + " |")

    with open(args.summary, 'w') as f:
        f.write("\n".join(lines))
    print(f"Summary → {args.summary}")
    print("\n" + "\n".join(lines[:40]))


if __name__ == "__main__":
    main()
