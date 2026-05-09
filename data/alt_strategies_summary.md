# Alt Strategies Backtest Summary

Run date: 2026-05-09 01:48
Universe: 33 symbols  •  Years: 2.0

> **Tail-risk read:** for the 5th-percentile / 95th-percentile distribution under sequence-resampled trades (Monte Carlo bootstrap, N=10k), see [`data/mc_bootstrap_summary.md`](mc_bootstrap_summary.md). Run via `PYTHONPATH=scripts python3 scripts/backtest_mc_bootstrap.py --all`.

## Per-strategy aggregate (across all symbols)

| Strategy | Trades | WinRate% | PF (wins$/loss$) | TotalReturn% (avg) | MaxDD% (avg) | AvgHoldDays | Sharpe (ann) | DSR | p-value | DSR>0.95 |
|----------|-------:|---------:|-----------------:|-------------------:|-------------:|------------:|-------------:|----:|--------:|---------:|
| RSI-2 | 336 | 81.0 | 2.97 | +2.21 | 0.80 | 1.5 | +5.44 | 1.000 | 0.000 | ✓ |
| IBS | 639 | 64.9 | 1.50 | +1.77 | 1.77 | 1.9 | +1.94 | 0.923 | 0.077 | ✗ |
| Williams%R | 174 | 62.6 | 1.57 | +0.87 | 0.99 | 4.3 | +2.71 | 0.721 | 0.279 | ✗ |
| Stoch | 112 | 55.4 | 1.48 | +0.54 | 0.89 | 4.4 | +1.50 | 0.233 | 0.767 | ✗ |
| Donchian-BO | 181 | 47.5 | 1.18 | +0.47 | 1.97 | 21.9 | +1.72 | 0.412 | 0.588 | ✗ |
| ConnorsRSI | 77 | 70.1 | 1.67 | +0.33 | 0.44 | 2.5 | +2.92 | 0.476 | 0.524 | ✗ |
| Keltner | 115 | 56.5 | 1.22 | +0.29 | 1.10 | 4.8 | +0.26 | 0.068 | 0.932 | ✗ |
| BB-MR | 120 | 55.0 | 1.21 | +0.28 | 1.01 | 6.1 | +0.53 | 0.097 | 0.903 | ✗ |
| MFI | 30 | 53.3 | 1.32 | +0.08 | 0.25 | 4.7 | +1.07 | 0.095 | 0.905 | ✗ |
| ADX-Pullback | 3 | 33.3 | 4.81 | +0.03 | 0.01 | 3.7 | +5.14 | 0.090 | 0.910 | ✗ |
| EMA-10/30 | 114 | 39.5 | 0.75 | -0.39 | 1.26 | 13.4 | -1.26 | 0.010 | 0.990 | ✗ |
| MACD-Hist | 293 | 34.1 | 0.78 | -0.75 | 2.53 | 5.6 | -1.27 | 0.002 | 0.998 | ✗ |

## Per-tier × strategy (average total_return %)

| Strategy | TIER1 | TIER2 | TIER3 |
|----------|------:|------:|------:|
| ADX-Pullback | +0.00 | +0.00 | +0.04 |
| BB-MR | +0.31 | +0.79 | +0.10 |
| ConnorsRSI | +0.73 | -0.10 | +0.35 |
| Donchian-BO | +1.87 | -0.37 | +0.34 |
| EMA-10/30 | -0.05 | -0.43 | -0.48 |
| IBS | +0.82 | +1.54 | +2.13 |
| Keltner | +0.07 | -0.34 | +0.58 |
| MACD-Hist | -1.59 | -0.25 | -0.68 |
| MFI | -0.08 | +0.05 | +0.14 |
| RSI-2 | +2.54 | +0.99 | +2.54 |
| Stoch | +0.16 | +0.53 | +0.66 |
| Williams%R | +1.01 | +0.34 | +1.01 |