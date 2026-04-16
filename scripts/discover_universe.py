#!/usr/bin/env python3
"""
discover_universe.py — RSI-2 Universe Discovery Scanner

Scans Alpaca's full asset list for new instruments that might work with RSI-2.
Filters by liquidity, price, and history, then backtests candidates.

This runs as a monthly Supervisor sub-routine to keep the trading universe
fresh and prevent the system from shrinking over time.

Workflow:
  1. Pull all tradeable assets from Alpaca
  2. Filter: liquid (vol > 500K), priced $20-$600, US exchange
  3. Exclude already-known instruments (passed or failed)
  4. Sample candidates and run RSI-2 backtest on each
  5. Report new passes for addition to the universe

Usage:
    export ALPACA_API_KEY="your-paper-key"
    export ALPACA_SECRET_KEY="your-paper-secret"
    python3 discover_universe.py
    python3 discover_universe.py --max-candidates 50   # test more
    python3 discover_universe.py --include-etfs-only    # just ETFs
"""

import os
import sys
import json
import argparse
import random
from datetime import datetime, timedelta
from dataclasses import dataclass, field

import numpy as np

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetAssetsRequest
    from alpaca.trading.enums import AssetClass, AssetStatus, AssetExchange
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
except ImportError:
    print("ERROR: alpaca-py not installed")
    sys.exit(1)

from indicators import rsi, sma, atr
import config  # noqa: F401 — auto-loads /home/linuxuser/.trading_env
from notify import notify, fmt_et


# Known instruments (already tested)
KNOWN_PASSED = {
    "SPY", "QQQ", "DIA", "IWM",
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLC", "XLY",
    "GOOGL", "NVDA", "META", "TSLA", "V",
}

KNOWN_FAILED = {
    "XLP", "XLRE", "XLB", "XLU",
    "AAPL", "MSFT", "AMZN", "JPM", "UNH",
}

KNOWN_ALL = KNOWN_PASSED | KNOWN_FAILED


@dataclass
class ScanResult:
    symbol: str
    name: str
    exchange: str
    asset_type: str  # etf or stock
    total_trades: int = 0
    win_rate: float = 0.0
    avg_trade_pct: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    trades_per_year: float = 0.0
    passed: bool = False
    fail_reasons: list = field(default_factory=list)
    error: str = ""


def get_candidate_assets(trading_client, include_stocks=True, include_etfs=True):
    """
    Pull all tradeable US assets from Alpaca and filter for RSI-2 candidates.
    """
    print("  Fetching asset list from Alpaca...")

    request = GetAssetsRequest(
        asset_class=AssetClass.US_EQUITY,
        status=AssetStatus.ACTIVE,
    )
    assets = trading_client.get_all_assets(request)

    print(f"  Total active US equity assets: {len(assets)}")

    candidates = []
    for asset in assets:
        # Skip known instruments
        if asset.symbol in KNOWN_ALL:
            continue

        # Must be tradable and not OTC
        if not asset.tradable:
            continue

        if asset.exchange in ("OTC",):
            continue

        # Filter by type
        is_etf = hasattr(asset, 'name') and asset.name and (
            'ETF' in asset.name.upper() or
            'FUND' in asset.name.upper() or
            'TRUST' in asset.name.upper() or
            'INDEX' in asset.name.upper()
        )

        if is_etf and not include_etfs:
            continue
        if not is_etf and not include_stocks:
            continue

        candidates.append({
            'symbol': asset.symbol,
            'name': asset.name or "",
            'exchange': str(asset.exchange),
            'is_etf': is_etf,
        })

    print(f"  Candidates after basic filter: {len(candidates)}")
    return candidates


def check_liquidity_and_price(symbol, data_client, min_volume=500000,
                               min_price=20, max_price=600):
    """
    Check if an instrument meets liquidity and price requirements
    using recent daily bars.
    """
    try:
        end = datetime.now() - timedelta(days=1)
        start = end - timedelta(days=30)

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start, end=end,
        )
        bars = data_client.get_stock_bars(request)
        bar_list = bars[symbol]

        if len(bar_list) < 10:
            return False, "Insufficient recent bars"

        avg_volume = np.mean([float(b.volume) for b in bar_list])
        last_price = float(bar_list[-1].close)

        if avg_volume < min_volume:
            return False, f"Volume {avg_volume:.0f} < {min_volume}"

        if last_price < min_price or last_price > max_price:
            return False, f"Price ${last_price:.0f} outside ${min_price}-${max_price}"

        return True, f"Vol {avg_volume:.0f}, Price ${last_price:.2f}"

    except Exception as e:
        return False, str(e)


def run_rsi2_quick(symbol, data_client, years=3):
    """
    Quick RSI-2 backtest. Returns a ScanResult.
    Uses 3 years to avoid short-window false positives.
    """
    try:
        end = datetime.now() - timedelta(days=1)
        start = end - timedelta(days=365 * years)

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start, end=end,
        )
        bars = data_client.get_stock_bars(request)
        bar_list = bars[symbol]

        if len(bar_list) < 220:
            return None, f"Only {len(bar_list)} bars"

        close = np.array([float(b.close) for b in bar_list])
        high = np.array([float(b.high) for b in bar_list])
        low = np.array([float(b.low) for b in bar_list])

        rsi2 = rsi(close, 2)
        sma200 = sma(close, 200)
        atr14 = atr(high, low, close, 14)

        returns = []
        in_pos = False
        entry_p = entry_i = 0

        for i in range(201, len(close)):
            if np.isnan(rsi2[i]) or np.isnan(sma200[i]) or np.isnan(atr14[i]):
                continue

            if in_pos:
                hold = i - entry_i
                ex = False
                exp = 0.0

                if low[i] <= entry_p - 2.0 * atr14[entry_i]:
                    ex, exp = True, entry_p - 2.0 * atr14[entry_i]
                elif rsi2[i] > 60:
                    ex, exp = True, close[i]
                elif close[i] > high[i-1]:
                    ex, exp = True, close[i]
                elif hold >= 5:
                    ex, exp = True, close[i]

                if ex:
                    returns.append((exp - entry_p) / entry_p * 100)
                    in_pos = False
            else:
                if rsi2[i] < 10 and close[i] > sma200[i]:
                    entry_p = close[i]
                    entry_i = i
                    in_pos = True

        if len(returns) < 5:
            return None, f"Only {len(returns)} trades (need 5+)"

        returns = np.array(returns)
        winners = returns[returns > 0]
        losers = returns[returns <= 0]

        wr = len(winners) / len(returns) * 100
        avg = np.mean(returns)
        gp = np.sum(winners) if len(winners) > 0 else 0
        gl = abs(np.sum(losers)) if len(losers) > 0 else 0.001
        pf = gp / gl
        tpy = len(returns) / max(years, 0.5)

        passed = wr >= 60 and pf >= 1.3
        fails = []
        if wr < 60:
            fails.append(f"WR {wr:.0f}%")
        if pf < 1.3:
            fails.append(f"PF {pf:.2f}")

        return {
            'trades': len(returns),
            'win_rate': wr,
            'avg_trade': avg,
            'profit_factor': pf,
            'trades_per_year': tpy,
            'passed': passed,
            'fail_reasons': fails,
        }, None

    except Exception as e:
        return None, str(e)


def save_to_redis(new_passes, dry_run=False):
    """Save discovered instruments to Redis universe as tier 3."""
    from config import Keys, get_redis, DEFAULT_UNIVERSE, DEFAULT_TIERS, SECTOR_MAP

    r = get_redis()
    universe_raw = r.get(Keys.UNIVERSE)
    universe = json.loads(universe_raw) if universe_raw else dict(DEFAULT_UNIVERSE)
    tiers_raw = r.get(Keys.TIERS)
    tiers = json.loads(tiers_raw) if tiers_raw else dict(DEFAULT_TIERS)

    existing_all = set(universe["tier1"] + universe["tier2"] + universe["tier3"])
    added = []

    for p in new_passes:
        sym = p["symbol"]
        if sym in existing_all:
            continue
        added.append(sym)
        universe["tier3"].append(sym)
        tiers[sym] = 3
        # Auto-detect sector from name heuristics
        if sym not in SECTOR_MAP:
            name_upper = p.get("name", "").upper()
            if p.get("type") == "ETF":
                SECTOR_MAP[sym] = "broad"
            else:
                SECTOR_MAP[sym] = "unknown"

    if not added:
        print("\n  No new instruments to add (all already in universe).")
        return

    universe["last_revalidation"] = datetime.now().isoformat()

    if dry_run:
        print(f"\n  [DRY RUN] Would add {len(added)} instruments to tier 3:")
        for sym in added:
            print(f"    + {sym}")
        print(f"  Universe would grow to {len(existing_all) + len(added)} instruments.")
        return

    r.set(Keys.UNIVERSE, json.dumps(universe))
    r.set(Keys.TIERS, json.dumps(tiers))
    print(f"\n  ✅ Saved {len(added)} new instruments to Redis (tier 3):")
    for sym in added:
        print(f"    + {sym}")
    print(f"  Universe now has {len(existing_all) + len(added)} instruments.")


def main():
    parser = argparse.ArgumentParser(description="RSI-2 Universe Discovery Scanner")
    parser.add_argument("--max-candidates", type=int, default=30,
                        help="Max candidates to backtest (default: 30)")
    parser.add_argument("--include-etfs-only", action="store_true",
                        help="Only scan ETFs, skip individual stocks")
    parser.add_argument("--include-stocks-only", action="store_true",
                        help="Only scan stocks, skip ETFs")
    parser.add_argument("--min-volume", type=int, default=500000,
                        help="Minimum avg daily volume (default: 500000)")
    parser.add_argument("--save", action="store_true",
                        help="Save passing instruments to Redis universe (tier 3)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what --save would do without writing to Redis")
    args = parser.parse_args()

    api_key = os.environ.get("ALPACA_API_KEY")
    secret_key = os.environ.get("ALPACA_SECRET_KEY")
    if not api_key or not secret_key:
        print("ERROR: Set ALPACA_API_KEY and ALPACA_SECRET_KEY")
        sys.exit(1)

    trading_client = TradingClient(api_key, secret_key, paper=True)
    data_client = StockHistoricalDataClient(api_key, secret_key)

    include_etfs = not args.include_stocks_only
    include_stocks = not args.include_etfs_only

    print("=" * 75)
    print("  RSI-2 UNIVERSE DISCOVERY SCANNER")
    print(f"  Searching for new instruments (excluding {len(KNOWN_ALL)} already tested)")
    print("=" * 75)

    # Step 1: Get candidate assets
    candidates = get_candidate_assets(
        trading_client,
        include_stocks=include_stocks,
        include_etfs=include_etfs,
    )

    if not candidates:
        print("  No candidates found!")
        return

    # Step 2: Shuffle and take a sample (scanning everything takes too long)
    random.shuffle(candidates)
    sample = candidates[:args.max_candidates * 3]  # oversample for liquidity filtering
    print(f"\n  Sampling {len(sample)} candidates for liquidity check...")

    # Step 3: Filter by liquidity and price
    liquid_candidates = []
    for c in sample:
        if len(liquid_candidates) >= args.max_candidates:
            break
        ok, detail = check_liquidity_and_price(
            c['symbol'], data_client, min_volume=args.min_volume
        )
        if ok:
            c['liquidity_detail'] = detail
            liquid_candidates.append(c)

    print(f"  Passed liquidity filter: {len(liquid_candidates)}")

    if not liquid_candidates:
        print("  No candidates passed liquidity filter. Try --min-volume 100000")
        return

    # Step 4: Backtest each candidate
    print(f"\n  Running RSI-2 backtest on {len(liquid_candidates)} candidates...")
    print(f"  {'Symbol':<8} {'Name':<35} {'Result'}")
    print(f"  {'-'*8} {'-'*35} {'-'*40}")

    new_passes = []
    new_fails = []

    for c in liquid_candidates:
        sym = c['symbol']
        name = c['name'][:33]
        atype = "ETF" if c['is_etf'] else "Stock"

        result, error = run_rsi2_quick(sym, data_client)

        if error:
            print(f"  {sym:<8} {name:<35} ⏭️  {error}")
            continue

        if result['passed']:
            print(f"  {sym:<8} {name:<35} ✅ WR {result['win_rate']:.0f}% | "
                  f"PF {result['profit_factor']:.2f} | "
                  f"Avg {result['avg_trade']:+.2f}% | "
                  f"{result['trades']:.0f} trades ({result['trades_per_year']:.0f}/yr)")
            result['symbol'] = sym
            result['name'] = c['name']
            result['type'] = atype
            new_passes.append(result)
        else:
            print(f"  {sym:<8} {name:<35} ❌ {', '.join(result['fail_reasons'])}")
            new_fails.append({'symbol': sym, 'reasons': result['fail_reasons']})

    # Step 5: Report
    print(f"\n{'=' * 75}")
    print(f"  DISCOVERY RESULTS")
    print(f"{'=' * 75}")
    print(f"  Candidates scanned:     {len(liquid_candidates)}")
    print(f"  New passes:             {len(new_passes)}")
    print(f"  New fails:              {len(new_fails)}")

    if new_passes:
        # Sort by profit factor
        new_passes.sort(key=lambda r: r['profit_factor'], reverse=True)

        print(f"\n  NEW INSTRUMENTS TO ADD:")
        print(f"  {'Symbol':<8} {'Type':<6} {'Name':<30} {'Trades':>7} {'Tr/Yr':>6} "
              f"{'WR':>6} {'Avg%':>7} {'PF':>6}")
        print(f"  {'-'*8} {'-'*6} {'-'*30} {'-'*7} {'-'*6} {'-'*6} {'-'*7} {'-'*6}")

        total_new_tpy = 0
        for r in new_passes:
            total_new_tpy += r['trades_per_year']
            print(f"  {r['symbol']:<8} {r['type']:<6} {r['name'][:28]:<30} "
                  f"{r['trades']:>7} {r['trades_per_year']:>6.1f} "
                  f"{r['win_rate']:>5.0f}% {r['avg_trade']:>+6.2f}% "
                  f"{r['profit_factor']:>6.2f}")

        print(f"\n  Additional trades/year from discoveries: {total_new_tpy:.0f}")
        print(f"  Additional trades/month: {total_new_tpy/12:.1f}")

        # Combined with existing universe (query Redis for actual count)
        from config import Keys, get_redis, DEFAULT_UNIVERSE
        _r = get_redis()
        _universe_raw = _r.get(Keys.UNIVERSE)
        _universe = json.loads(_universe_raw) if _universe_raw else DEFAULT_UNIVERSE
        existing_count = len(_universe["tier1"] + _universe["tier2"] + _universe["tier3"])
        print(f"\n  UPDATED UNIVERSE PROJECTION:")
        print(f"    Existing instruments:  {existing_count}")
        print(f"    New discoveries:       {len(new_passes)} ({total_new_tpy:.0f} trades/yr)")
        print(f"    Combined:              {existing_count + len(new_passes)} instruments")
    else:
        print(f"\n  No new instruments found in this scan.")
        print(f"  This is normal — most stocks don't mean-revert cleanly on RSI-2.")
        print(f"  Try running again for a different random sample, or adjust filters.")

    # Save to Redis if requested
    if new_passes and (args.save or args.dry_run):
        save_to_redis(new_passes, dry_run=args.dry_run)
    elif new_passes and not args.save:
        print(f"\n  💡 Run with --save to add these {len(new_passes)} instruments to the Redis universe.")
        print(f"     Use --dry-run to preview changes first.")

    print(f"\n  NOTE: This scanner samples randomly from ~{len(candidates)} candidates.")
    print(f"  Run it multiple times to cover more of the universe.")
    print(f"  The Supervisor should run this monthly to discover new instruments")
    print(f"  and re-validate the existing universe.")

    # Notify on every run so silence is meaningful
    if new_passes:
        pass_lines = []
        for p in new_passes[:10]:  # cap at 10 to keep message readable
            pass_lines.append(
                f"  ✅ <b>{p['symbol']}</b> {p.get('type', '')} "
                f"WR {p['win_rate']:.0f}% | PF {p['profit_factor']:.2f} | "
                f"Avg {p['avg_trade']:+.2f}%"
            )
        if len(new_passes) > 10:
            pass_lines.append(f"  … and {len(new_passes) - 10} more")
        passes_block = "\n".join(pass_lines)
        save_note = "Saved to Redis (tier 3)" if args.save else "⚠️ Run with --save to add to universe"
    else:
        passes_block = "No new instruments passed — normal, most don't mean-revert on RSI-2"
        save_note = ""

    msg = (
        f"🔎 <b>UNIVERSE DISCOVERY — {fmt_et()}</b>\n"
        f"\n"
        f"Candidates scanned: {len(liquid_candidates)}\n"
        f"New passes: {len(new_passes)} | New fails: {len(new_fails)}\n"
        f"\n"
        f"{passes_block}\n"
    )
    if save_note:
        msg += f"\n{save_note}\n"
    notify(msg)


if __name__ == "__main__":
    main()

# v1.0.0
