# Alternate Strategies — Beyond RSI-2

Research doc. For each candidate, we list the core thesis, entry rule, exit
rules, why it complements RSI-2, and the parameters used in the companion
backtest (`scripts/backtest_alt_strategies.py`).

**Universal conventions (apply to every strategy below):**

- Equity curve: fixed 1% risk per trade. Stop-loss = `entry − atr_mult × ATR(14)`.
  Shares = `risk_dollars / (entry − stop)`.
- Direction: long-only. Crypto fees modeled at 0.40% round-trip. No short entries.
- Re-entry gate: 24h whipsaw cooldown after stop-loss exits (matches live system).
- Bar-timing: entry is at `open[i+1]` where possible (not `close[i]`), to match the
  live Watcher/Executor flow. Exits evaluated on `close[i]` and `low[i]` each bar
  after entry.
- Max hold days acts as a hard time-stop on every strategy (per-strategy default noted).

Strategies are grouped by regime they target: mean-reversion (MR),
trend-following (TF), or hybrid (H).

---

## 1. RSI-2 (baseline — current system) — MR

**Thesis:** Short-term oversold in an established uptrend reverts within days.

**Entry:** `RSI(2) < 10` AND `close > SMA(200)`.
**Exits (first wins):**
1. `low[i] ≤ stop_price` → stop_loss
2. `RSI(2) > 60` → take_profit
3. `close > high[i-1]` → take_profit (prev-day-high exit)
4. `hold_days ≥ 5` → time_stop

**Why baseline:** Already running in production. Serves as the benchmark.

**Parameters:** `rsi_entry=10, rsi_exit=60, sma_period=200, max_hold=5, atr_mult=2.0`.

---

## 2. Bollinger Band Mean Reversion (BBMR) — MR

**Thesis:** Price touching −2σ of its 20-day distribution tends to revert to the mean.
Smoother, slower variant of RSI-2 — BB width adapts to volatility, so the trigger
tightens automatically in low-vol regimes.

**Entry:** `close < SMA(20) − 2 × stdev(20)` AND `close > SMA(200)`.
**Exits:**
1. `low[i] ≤ stop_price` → stop_loss
2. `close ≥ SMA(20)` → take_profit (mean touched)
3. `hold_days ≥ 10` → time_stop

**Complement to RSI-2:** Fires on different bars. RSI-2 triggers after 2-bar
momentum collapse; BB triggers on volatility-adjusted price extreme. On quiet
days where RSI-2 < 10 doesn't quite fire, BB often still fires.

**Parameters:** `bb_period=20, bb_std=2.0, max_hold=10, atr_mult=2.0`.

---

## 3. Internal Bar Strength (IBS) Scalp — MR

**Thesis:** `IBS = (close − low) / (high − low)` close to 0 means the stock closed
near the low of the day — a classical short-term oversold condition that Larry
Connors documented as profitable across equity indices.

**Entry:** `IBS < 0.15` AND `close > SMA(200)`.
**Exits:**
1. `low[i] ≤ stop_price` → stop_loss
2. `close > high[i-1]` → take_profit (bounce back above prior high)
3. `hold_days ≥ 3` → time_stop

**Complement to RSI-2:** Single-bar signal (RSI-2 needs 2 bars of losses). Often
fires *earlier* than RSI-2 on sharp one-day selloffs. Short horizon (3-day time
stop) keeps capital turning over.

**Parameters:** `ibs_threshold=0.15, max_hold=3, atr_mult=2.0`.

---

## 4. Connors RSI (CRSI) — MR

**Thesis:** Composite of three oversold measures —
`CRSI = (RSI(3) + RSI(streak) + PctRank(ROC(1), 100)) / 3`.
Combines price momentum, directional persistence, and percentile-rank of daily
returns. Less noisy than plain RSI-2.

**Entry:** `CRSI < 10` AND `close > SMA(200)`.
**Exits:**
1. `low[i] ≤ stop_price` → stop_loss
2. `CRSI > 70` → take_profit
3. `hold_days ≥ 5` → time_stop

**Complement to RSI-2:** Adds streak and percentile-rank components, which filter
out noisy RSI-2 signals that fire on one bad bar after a flat week.

**Parameters:** `rsi_len=3, streak_len=2, roc_len=100, entry=10, exit=70, max_hold=5, atr_mult=2.0`.

---

## 5. Williams %R Oversold — MR

**Thesis:** Williams %R maps current price into the 14-day high-low range. `%R < −90`
means price is in the bottom 10% of its recent range — deeper oversold than RSI-2
threshold of 10 on calm names.

**Entry:** `%R(14) < −90` AND `close > SMA(200)`.
**Exits:**
1. `low[i] ≤ stop_price` → stop_loss
2. `%R(14) > −20` → take_profit
3. `hold_days ≥ 5` → time_stop

**Complement to RSI-2:** Uses high-low range instead of close-to-close
magnitudes. Captures range-bottom touches that RSI-2 would miss on stocks
closing far from the low.

**Parameters:** `wr_period=14, entry=-90, exit=-20, max_hold=5, atr_mult=2.0`.

---

## 6. Stochastic Oscillator Reversal — MR

**Thesis:** `%K(14,3)` below 20 with upward momentum indicates an imminent bounce.
Double-confirmation (below threshold + rising) reduces false triggers common in
raw RSI-2.

**Entry:** `%K(14,3) < 20` AND `%K[i] > %K[i−1]` AND `close > SMA(200)`.
**Exits:**
1. `low[i] ≤ stop_price` → stop_loss
2. `%K(14,3) > 80` → take_profit
3. `hold_days ≥ 5` → time_stop

**Complement to RSI-2:** Smoothed (3-period SMA of raw %K) and requires turn-up
confirmation. Will miss early but enter with higher probability of immediate follow-through.

**Parameters:** `k_period=14, k_smooth=3, entry=20, exit=80, max_hold=5, atr_mult=2.0`.

---

## 7. Money Flow Index (MFI) — MR

**Thesis:** Volume-weighted RSI. Uses typical price × volume so the oversold
signal requires genuine distribution (not just a few low-volume prints).

**Entry:** `MFI(14) < 20` AND `close > SMA(200)`.
**Exits:**
1. `low[i] ≤ stop_price` → stop_loss
2. `MFI(14) > 80` → take_profit
3. `hold_days ≥ 5` → time_stop

**Complement to RSI-2:** Volume gate filters the low-conviction RSI-2 signals
where price dripped down on thin tape. On liquid large-caps (SPY, QQQ, NVDA)
typically fires less often but with higher conviction.

**Parameters:** `mfi_period=14, entry=20, exit=80, max_hold=5, atr_mult=2.0`.

---

## 8. MACD Histogram Reversal — H (momentum / early-trend)

**Thesis:** When MACD histogram crosses from negative to positive, short-term
momentum flipped. Combined with an above-long-MA filter, this catches the *start*
of an up-swing rather than the bottom.

**Entry:** `MACD_hist[i] > 0` AND `MACD_hist[i−1] ≤ 0` AND `close > SMA(200)`.
**Exits:**
1. `low[i] ≤ stop_price` → stop_loss
2. `MACD_hist < 0` (crosses back) → exit
3. `hold_days ≥ 10` → time_stop

**Complement to RSI-2:** Different type of signal. RSI-2 fires at the bottom;
MACD fires slightly *after* the bottom as momentum flips. Will enter at a worse
price but with higher follow-through probability. Long-only filter on SMA(200)
prevents most bear-market false positives.

**Parameters:** `macd_fast=12, macd_slow=26, macd_sig=9, max_hold=10, atr_mult=2.0`.

---

## 9. Donchian Channel Breakout — TF

**Thesis:** Classic Turtle-style breakout. Entry when price makes a new 20-day
high. Exit on 10-day low (chandelier). Pure momentum, no counter-trend logic.

**Entry:** `close[i] > max(high[i−20 : i])`.
**Exits:**
1. `low[i] ≤ stop_price` → stop_loss
2. `close[i] < min(low[i−10 : i])` → exit (breakout failure)
3. `hold_days ≥ 30` → time_stop

**Complement to RSI-2:** Opposite regime. Where RSI-2 wants pullbacks, Donchian
wants continuation. Essentially a second strategy that *only* wins when RSI-2
would lose (trending markets). Wider ATR stop (3.0×) because breakouts need
breathing room.

**Parameters:** `entry_len=20, exit_len=10, max_hold=30, atr_mult=3.0`.

---

## 10. Dual EMA Crossover (10/30) — TF

**Thesis:** When fast EMA crosses above slow EMA, short-term price action has
established upward drift. Noisier than Donchian but catches trends earlier.

**Entry:** `EMA(10)[i] > EMA(30)[i]` AND `EMA(10)[i−1] ≤ EMA(30)[i−1]` AND `close > EMA(100)`.
**Exits:**
1. `low[i] ≤ stop_price` → stop_loss
2. `EMA(10) < EMA(30)` (bearish cross) → exit
3. `hold_days ≥ 20` → time_stop

**Complement to RSI-2:** Trend-following fallback. Broad-market trendy regimes
(post-Fed-pivot rallies, post-earnings runs) tend to starve RSI-2 of entries;
EMA crossover fires here.

**Parameters:** `ema_fast=10, ema_slow=30, ema_trend=100, max_hold=20, atr_mult=2.5`.

---

## 11. Keltner Channel Bounce — MR (volatility-scaled)

**Thesis:** Keltner uses ATR (not stdev) for channel width, which tracks
volatility more smoothly. Touch of the lower band signals oversold in ATR
units, less sensitive to one-bar spikes than Bollinger.

**Entry:** `low[i] < KC_lower[i]` AND `close[i] > SMA(200)` where
`KC_lower = EMA(20) − 2 × ATR(10)`.
**Exits:**
1. `low[i] ≤ stop_price` → stop_loss
2. `close ≥ EMA(20)` (returned to middle band) → take_profit
3. `hold_days ≥ 10` → time_stop

**Complement to RSI-2:** ATR-based channel means signal frequency stays roughly
constant across volatility regimes. BB is biased toward high-vol environments
(wider bands, fewer touches); Keltner compensates.

**Parameters:** `ema_len=20, atr_len=10, atr_mult_band=2.0, max_hold=10, atr_mult=2.0`.

---

## 12. ADX Trend + RSI-14 Pullback — H

**Thesis:** First confirm a strong trend (`ADX > 25`), then wait for a shallow
pullback (`RSI(14) < 40`) while price is still above the medium-term trend
(`close > SMA(50)`). Hybrid of trend and mean-reversion.

**Entry:** `ADX(14) > 25` AND `close > SMA(50)` AND `RSI(14) < 40`.
**Exits:**
1. `low[i] ≤ stop_price` → stop_loss
2. `RSI(14) > 70` → take_profit
3. `close < SMA(50)` → trend_broken
4. `hold_days ≥ 10` → time_stop

**Complement to RSI-2:** Operates in trending markets (RSI-2 works best in
range-bound). The ADX filter ensures the strategy only activates when there's
a trend to pull back within. Together with RSI-2 this covers both the
range-bound and trending halves of the market-regime spectrum.

**Parameters:** `adx_len=14, adx_threshold=25, sma_trend=50, rsi_len=14, entry=40, exit=70, max_hold=10, atr_mult=2.0`.

---

## Backtest plan

All twelve strategies are evaluated against a common universe in
`scripts/backtest_alt_strategies.py`:

- **Tier 1 (6):** SPY, QQQ, NVDA, XLK, XLY, XLI
- **Tier 2 (7):** GOOGL, XLF, META, TSLA, XLC, DIA, BTC/USD
- **Tier 3 (20):** V, XLE, XLV, IWM, NFLX, SHOP, AMGN, SPOT, CSCO, ABBV, ABT,
  LIN, ORCL, SCHW, EMR, SMH, NOW, DG, EA, KMI

Lookback: 2 years of daily bars. Metrics: trade count, win rate, profit factor,
total return %, max drawdown %, average hold days.

Output: `data/alt_strategies_results.csv` (per-strategy × per-symbol rows) and a
console summary ranking strategies by profit factor across all symbols.

## Complementarity analysis

After the backtest runs, the expected comparison angles are:

1. **Trade-set overlap:** Do two strategies fire on the same bars? If two
   strategies always fire together, they're redundant. If they fire on
   disjoint days, they're true alternatives.
2. **Regime correlation:** Which strategies win in up-years vs. flat-years vs.
   down-years? (Use the SPY annual return as a proxy regime.)
3. **Tier suitability:** For each tier, which strategy produces the best
   PF × trade-count combination? (High PF with too few trades is noise.)
4. **Risk-adjusted portfolio:** A weighted mix of the best 3 non-correlated
   strategies, each sized to contribute equal risk. Target: Sharpe > baseline
   RSI-2 single-strategy Sharpe.

These analyses are out of scope for the first backtest run but will be
produced from the same CSV as follow-up queries.

---

## Backtest Results (2y, 33 symbols, run 2026-04-17)

Universe: 6 Tier 1 + 7 Tier 2 + 20 Tier 3 = 33 symbols. Data: Alpaca daily bars,
2 years. Produced by `scripts/backtest_alt_strategies.py`. Full per-symbol
breakdown in `data/alt_strategies_results.csv`; aggregate in
`data/alt_strategies_summary.md`.

### Aggregate ranking (across all symbols)

| Rank | Strategy | Trades | WinRate% | PF | AvgRet% | AvgDD% | AvgHoldD |
|-----:|----------|-------:|---------:|---:|--------:|-------:|---------:|
| 1 | **RSI-2 (baseline)** | 347 | **78.4** | **2.44** | **+1.94** | 0.87 | 1.6 |
| 2 | IBS | 656 | 64.3 | 1.43 | +1.61 | 1.82 | 1.9 |
| 3 | Donchian-BO | 187 | 48.1 | 1.21 | +0.56 | 1.90 | 22.2 |
| 4 | Williams%R | 185 | 57.3 | 1.28 | +0.51 | 1.24 | 4.3 |
| 5 | Stoch | 119 | 51.3 | 1.33 | +0.41 | 1.00 | 4.4 |
| 6 | ConnorsRSI | 86 | 66.3 | 1.31 | +0.20 | 0.58 | 2.4 |
| 7 | BB-MR | 126 | 53.2 | 1.09 | +0.13 | 1.11 | 6.1 |
| 8 | MFI | 30 | 53.3 | 1.32 | +0.08 | 0.25 | 4.6 |
| 9 | Keltner | 121 | 52.9 | 1.05 | +0.08 | 1.29 | 4.7 |
| 10 | ADX-Pullback | 3 | 33.3 | 4.81 | +0.03 | 0.01 | 3.7 |
| 11 | EMA-10/30 | 115 | 38.3 | 0.73 | −0.43 | 1.32 | 13.2 |
| 12 | MACD-Hist | 290 | 33.4 | 0.73 | −0.95 | 2.56 | 5.7 |

### Headline findings

- **RSI-2 wins on aggregate.** Highest PF (2.44), highest WR (78.4%), highest
  avg return per symbol (+1.94%), lowest avg DD (0.87%). The current system is
  not broken — exit-rule mechanics are the known leak (see STRATEGY_REVIEW.md).
- **IBS is the strongest complement.** 656 trades (2x RSI-2's count), 64% WR,
  PF 1.43. Trades on days RSI-2 does not fire. Best on broad-index ETFs
  (DIA +6.84%, XLI +5.17%, CSCO +5.01%, XLV +3.68%).
- **Donchian-BO is the trend-follower slot.** PF 1.21 but long holds (22d avg)
  and wins on names where RSI-2 is weak (DG +5.10%, NVDA +3.94%, GOOGL +4.07%).
  Fills the "strong uptrend, never oversold" gap.
- **Eliminate: MACD-Hist and EMA-10/30.** Both negative PF < 1.0 in this
  regime. MACD-Hist's occasional hits (GOOGL +7.44%) are drowned by −0.95% avg.
- **Small-sample winners to shelve, not ship.** ADX-Pullback fired only 3 times;
  MFI only 30; ConnorsRSI 86. Not enough evidence. Keep in the library, revisit
  with looser params or longer data.

### Per-tier average return %

| Strategy | Tier 1 | Tier 2 | Tier 3 |
|----------|-------:|-------:|-------:|
| RSI-2 | +1.86 | +0.40 | **+2.51** |
| IBS | +0.39 | +1.21 | +2.12 |
| Donchian-BO | +1.54 | −0.38 | +0.60 |
| Williams%R | +0.16 | −0.16 | +0.85 |
| Stoch | −0.00 | +0.28 | +0.59 |
| BB-MR | −0.19 | +0.50 | +0.10 |
| Keltner | −0.65 | −0.53 | +0.51 |
| MACD-Hist | −1.60 | −0.27 | −0.99 |

RSI-2 is the top Tier-1 strategy. On Tier 2, IBS beats RSI-2 (+1.21 vs +0.40).
On Tier 3, RSI-2 and IBS are both strong (+2.51 / +2.12).

### Symbols where RSI-2 is NOT the best strategy (20/33)

| Symbol | Best Strategy | Best Ret% | RSI-2 Ret% |
|--------|---------------|----------:|-----------:|
| ABBV | Keltner | +4.88 | +2.66 |
| ABT | IBS | +3.74 | +1.09 |
| AMGN | Donchian-BO | +2.64 | +1.39 |
| BTC/USD | BB-MR | +2.25 | −0.22 |
| CSCO | IBS | +5.01 | +1.84 |
| DG | Donchian-BO | +5.10 | +4.99 |
| DIA | IBS | +6.84 | +1.55 |
| GOOGL | MACD-Hist | +7.44 | +0.22 |
| NVDA | Donchian-BO | +3.94 | +3.52 |
| ORCL | Stoch | +2.78 | −0.32 |
| SHOP | IBS | +2.09 | +1.84 |
| SPOT | IBS | +3.51 | +2.51 |
| V | IBS | +2.36 | +0.74 |
| XLC | Stoch | +1.99 | −2.70 |
| XLI | IBS | +5.17 | +2.42 |
| XLK | Stoch | +1.02 | +0.78 |
| XLV | IBS | +3.68 | +1.22 |
| XLY | Donchian-BO | +1.93 | +0.31 |

(META and TSLA: no strategy produced meaningful return in this window — both
flat to negative across the board; exclude from multi-strategy routing.)

### Proposed multi-strategy routing

Based on per-symbol complementarity, a phased rollout:

1. **Phase 1 (ship next):** Add IBS as a second entry path. Runs alongside RSI-2
   with its own cooldown. Top symbols: DIA, XLI, CSCO, XLV, XLF, EA, ABT, IWM,
   SHOP, V, SPOT. Estimated portfolio trade-count 2-3x current.
2. **Phase 2:** Add Donchian-BO (trend slot) for: DG, GOOGL, NVDA, AMGN, SMH,
   LIN, XLY. Requires wider position-sizing (22d avg hold vs 1.6d RSI-2).
3. **Phase 3 (watchlist only):** Williams%R and BB-MR as screener-level signals
   that promote a symbol but still require RSI-2 or IBS to fire the entry.
   Cheapest additions — just new priority tiers on the watchlist.
4. **Do not ship:** MACD-Hist, EMA-10/30, Keltner (long-only), ADX-Pullback,
   MFI — either negative edge or insufficient trade count in this universe.

Before Phase 1: need to fix the bar-timing leak (STRATEGY_REVIEW.md §2) first,
else IBS inherits the same `close > prev_high` immediate-exit bug.
