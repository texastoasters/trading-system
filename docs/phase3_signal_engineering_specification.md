# Phase 3 Research Deliverable: Signal Engineering Specification

## Purpose

This document specifies the exact indicator parameters, combination rules, regime filters, and false-signal protections that the Watcher Agent will implement. It is the "playbook" — the agents' instruction set for translating raw price data into actionable buy and sell signals. Every parameter choice is grounded in backtesting evidence from Phase 2 research and published quantitative analysis.

---

## The Signal Pipeline Architecture

Raw price data flows through four sequential stages before producing a tradeable signal. Each stage filters out noise, and a signal must pass all four stages to generate a trade recommendation.

```
Stage 1: REGIME DETECTION (What kind of market is this?)
    ↓
Stage 2: STRATEGY SELECTION (Which strategy applies right now?)
    ↓
Stage 3: SIGNAL GENERATION (Does a specific entry trigger exist?)
    ↓
Stage 4: SIGNAL VALIDATION (Is the signal confirmed by secondary factors?)
    ↓
OUTPUT: Trade recommendation → Portfolio Manager for sizing and approval
```

---

## Stage 1: Regime Detection

The most important decision the system makes is not *what* to trade but *which strategy to use*. Different market regimes demand different approaches — momentum strategies fail in range-bound markets, and mean-reversion strategies fail in strong trends. The ADX indicator is the primary regime detector.

### ADX Configuration

**Indicator**: Average Directional Index (ADX), 14-period (Wilder's original default). This period captures roughly two weeks of daily data, smoothing daily noise while remaining responsive to genuine regime changes. The 14-period setting is standard across the industry, meaning the threshold levels (20, 25) are calibrated to this period — changing the period would require recalibrating thresholds.

**Regime classification**:

**ADX below 20 — Ranging/Choppy market.** No clear trend exists. Trend-following strategies (ORB, momentum) should be suppressed. Mean-reversion strategies (RSI-2) are the primary approach. The system should reduce overall position frequency because ranging markets produce more false signals across all strategy types. When ADX stays below 20 for more than 5 consecutive trading days, the Supervisor Agent should flag this as a "low-opportunity environment" and potentially reduce capital allocation to equities temporarily.

**ADX between 20 and 25 — Emerging trend.** This is the "watch zone." A trend may be forming but is not yet confirmed. The system can run mean-reversion strategies normally and begin watching for ORB setups, but should not increase position sizes or frequency. When ADX crosses above 20 and is rising, this often signals the beginning of a new trend — the system should be alert for ORB opportunities in the direction of the emerging trend.

**ADX above 25 — Strong trend confirmed.** Trend-following strategies (ORB) have their highest probability of success. Mean-reversion strategies still work (stocks can become oversold even in strong trends) but should use tighter entry thresholds (RSI-2 below 5 instead of below 10) because the "snap-back" from oversold conditions is faster and smaller in trending markets. The Watcher Agent should give priority to ORB signals when ADX is above 25.

**ADX above 50 — Extreme trend.** The trend is very strong but may be approaching exhaustion. ORB trades should use tighter profit targets (1:1 instead of 2:1) because the strongest part of the move may already be over. Mean-reversion signals should be treated with extra caution because "oversold in an extreme trend" can get much more oversold before reversing.

**ADX declining from high levels — Trend fading.** When ADX peaks above 40 and begins falling, the current trend is losing momentum. This often precedes a consolidation period or reversal. The system should begin taking profits on trend-following positions and prepare to shift back to mean-reversion mode.

### Directional Movement (+DI / -DI) for Trend Direction

ADX tells you *how strong* the trend is but not *which direction*. The Positive Directional Indicator (+DI) and Negative Directional Indicator (-DI) provide direction:

When +DI is above -DI, the prevailing trend is up. ORB trades should be long-only (which aligns with Rule 1 anyway since we cannot short).

When -DI is above +DI, the prevailing trend is down. ORB trades should be suppressed entirely (since we are long-only and going long in a confirmed downtrend is counterproductive). Mean-reversion trades can still work but should use reduced position sizes.

### Implementation

```python
# Regime detection — runs once per daily bar on SPY
adx_14 = compute_adx(close, high, low, period=14)
plus_di = compute_plus_di(close, high, low, period=14)
minus_di = compute_minus_di(close, high, low, period=14)

if adx_14 < 20:
    regime = "RANGING"
    allowed_strategies = ["RSI2_MEAN_REVERSION", "CRYPTO_MOMENTUM"]
    orb_enabled = False
elif adx_14 < 25:
    regime = "EMERGING_TREND"
    allowed_strategies = ["RSI2_MEAN_REVERSION", "CRYPTO_MOMENTUM"]
    orb_enabled = True  # but reduced confidence
elif adx_14 >= 25 and plus_di > minus_di:
    regime = "STRONG_UPTREND"
    allowed_strategies = ["RSI2_MEAN_REVERSION", "ORB", "CRYPTO_MOMENTUM"]
    orb_enabled = True  # full confidence
elif adx_14 >= 25 and minus_di > plus_di:
    regime = "STRONG_DOWNTREND"
    allowed_strategies = ["RSI2_MEAN_REVERSION", "CRYPTO_MOMENTUM"]
    orb_enabled = False  # no long ORB in downtrend
```

---

## Stage 2: Strategy-Specific Signal Generation

### Strategy A: RSI-2 Mean Reversion

**Primary indicator**: RSI with a 2-period lookback (RSI-2). This ultra-short period makes the indicator extremely sensitive to recent price drops, which is the point — we want to detect short-term oversold extremes within longer-term uptrends.

**Trend filter**: 200-day Simple Moving Average (SMA). Only take long entries when the closing price is above the 200-day SMA. This single filter ensures we are buying dips in uptrending markets rather than catching falling knives in downtrends. The 200-day SMA is the most widely watched trend indicator in institutional finance, so it also functions as a self-fulfilling prophecy — price tends to respect it because so many participants are watching it.

**Entry parameters**:

The entry threshold for RSI-2 has been tested at multiple levels. Research by Quantitativo across the 1998–2024 period found that RSI-2 below 10 has the best edge over the full history. A backtested variant using RSI-2 below 5 as the entry threshold produced the highest per-trade returns but with fewer trade opportunities. A Medium article analyzing "Dip Bonanza" strategy uses RSI-2 below 5 with the 5-period MA as the exit, reporting win rates above 60% on SPY and large-cap stocks.

**Recommended configuration — Conservative (default)**:
- Entry: RSI-2 closes below 10 AND Close > 200-day SMA
- Exit: RSI-2 closes above 60 OR Close > Previous day's High OR after 5 trading days
- Stop-loss: 2x ATR(14) below entry price

**Recommended configuration — Aggressive (when ADX > 25 suggests strong uptrend)**:
- Entry: RSI-2 closes below 5 AND Close > 200-day SMA
- Exit: Close > 5-period SMA (faster exit to capture quick bounce)
- Stop-loss: 2x ATR(14) below entry price

The aggressive variant trades less frequently but captures the most extreme oversold conditions, which tend to have the sharpest rebounds.

**Exit logic detail**: The three exit conditions serve different purposes. RSI-2 above 60 means the oversold condition has been resolved and the mean-reversion move is likely complete — taking profit here captures the bulk of the rebound. Close above the previous day's high serves as a simple trailing stop mechanism that exits once a new short-term high is made. The 5-day time stop prevents positions from lingering in unresolved conditions that could tie up capital.

### Strategy B: Opening Range Breakout (ORB)

**Pre-conditions**: ADX(14) must be above 20 (preferably above 25) and +DI must be above -DI (confirming uptrend direction since we are long-only). The Screener Agent must identify the overall market as having directional momentum before ORB is activated.

**Opening range definition**: The high and low of SPY or QQQ during the first 15 minutes of regular trading (9:30–9:45 AM Eastern). The 15-minute range is a good balance — 5-minute ranges capture more signals but with more noise, while 30 and 60-minute ranges are more reliable but give up too much of the initial move for a day trading strategy that needs to exit by close.

**Entry signal**: A 5-minute candle closes above the opening range high (for long entry) with:
- Relative volume greater than 1.5x the 20-bar volume average (confirms genuine momentum behind the breakout, not a low-volume fake-out)
- Price is above VWAP (Volume-Weighted Average Price) at the time of breakout (confirms the breakout is occurring above the day's average traded price, a sign of bullish momentum)

**Stop-loss**: Just below the opening range low (for long trades). This is typically 0.3–0.5% on SPY/QQQ, which translates to a tight dollar risk that allows good position sizing within the 1% risk rule.

**Take-profit / Exit**:
- Target 1: Opening range height projected above the breakout point (measured move). Take 50% of the position here.
- Target 2: Trail the remaining 50% using the 9-period EMA on 5-minute bars. Exit when price closes below the 9 EMA.
- Time stop: Close any remaining position by 3:45 PM ET (15 minutes before market close) to avoid overnight risk.

**Critical note on PDT impact**: Every ORB trade consumes one day trade. The Trade Executor must verify `daytrade_count < 2` before approving any ORB entry (reserving 1 day trade for emergency exits).

### Strategy C: Crypto Momentum (BTC/ETH)

**Timeframe**: 15-minute charts for signal generation. 1-hour and 4-hour charts for trend confirmation.

**Trend confirmation (higher timeframe)**: Price must be above the 50-period EMA on the 1-hour chart. This ensures we are only entering long positions in the direction of the intermediate trend. Since we cannot short (Rule 1), all crypto entries are long-only.

**Entry signal (triple confirmation on 15-minute chart)**:
1. RSI(14) crosses above 30 from below (oversold bounce) — the security was recently in oversold territory and momentum is now turning up
2. MACD histogram flips from negative to positive — confirming that short-term momentum is accelerating upward
3. Volume on the signal candle is above the 20-bar volume average — confirming participation behind the move

All three conditions must be met simultaneously. This triple-confirmation approach filters out the majority of false signals that plague single-indicator crypto strategies. Research shows MACD alone achieves only 50–55% accuracy on BTC, but combined with RSI filters, accuracy improves to approximately 60–65%, and with volume confirmation, to roughly 65–75%.

**Exit rules**:
- RSI(14) crosses above 70 (overbought — take profit)
- OR MACD histogram flips from positive to negative (momentum fading)
- OR trailing stop at 1.5x ATR(14) on 15-minute bars is hit
- OR time stop: exit after 4 hours if no other exit triggered (prevents positions from lingering through low-volume periods)

**Fee-adjusted minimum return threshold**: The Portfolio Manager must reject any crypto signal where the expected gain (based on the distance to target versus distance to stop) does not exceed 0.60% (to cover the 0.30–0.50% round-trip fee plus a small buffer).

---

## Stage 3: ATR-Based Stop-Loss Framework

All strategies use ATR-based stop-losses rather than fixed-percentage stops. ATR stops adapt to current market volatility — wider when the market is volatile (giving trades room to breathe), tighter when the market is calm (protecting against unnecessary losses).

### ATR Configuration by Strategy

| Strategy | ATR Period | Multiplier | Rationale |
|----------|-----------|------------|-----------|
| RSI-2 Mean Reversion | 14 days | 2.0x | Swing trade (2–5 day hold). 2x ATR provides enough room for the mean-reversion move to develop without being stopped out by normal daily fluctuations. |
| ORB | 14 periods on 5-min chart | N/A (uses range low) | The opening range low is a natural invalidation point that is typically tighter than an ATR-based stop, making it preferable for this strategy. |
| Crypto Momentum | 14 periods on 15-min chart | 1.5x | Crypto is more volatile than equities, so a slightly tighter multiplier (1.5x instead of 2x) on the 15-minute timeframe provides adequate room while keeping risk per trade within the 1% budget. BTC's ATR on 15-minute bars is typically 0.3–0.8% of price, so 1.5x ATR = 0.45–1.2% risk. |

### Trailing Stop Rules

For RSI-2 mean reversion: no trailing stop. The strategy uses a fixed exit (RSI-2 above 60 or close above previous day's high) rather than a trailing mechanism. The holding period is short enough (2–5 days) that a trailing stop would add complexity without meaningful benefit.

For ORB: after Target 1 is hit (50% of position closed), trail the remaining 50% using the 9-period EMA on 5-minute bars. This locks in profit while allowing the trend to continue.

For crypto: trail at 1.5x ATR(14) on 15-minute bars once the position is in profit by at least 0.5% (after fees). The trail only moves in the direction of profit — it never moves back toward the entry.

### Dynamic ATR Adjustment

The system should adjust ATR multipliers based on the current regime:

When ADX < 20 (ranging): tighten equity ATR multiplier from 2.0x to 1.5x. In ranging markets, price swings are smaller and stops can be tighter.

When ADX > 40 (strong trend): widen equity ATR multiplier from 2.0x to 2.5x. Strong trends produce larger pullbacks that look like stop-triggers but are actually continuation opportunities. The wider stop prevents being shaken out.

For crypto, when Bitcoin Volatility Index (if available) is above its 30-day average: widen ATR multiplier from 1.5x to 2.0x and reduce position sizes proportionally to keep dollar risk constant.

---

## Stage 4: Signal Validation and False-Signal Filters

Even after a signal passes Stages 1–3, the following validation checks must pass before a trade recommendation is sent to the Portfolio Manager.

### Volume Confirmation

Every entry signal must be accompanied by above-average volume. The specific thresholds:

For RSI-2 mean reversion: no explicit volume requirement (the RSI-2 reading itself is sufficient, and the strategy buys into selling — which naturally occurs on higher volume).

For ORB: relative volume must be ≥ 1.5x the 20-bar average at the time of breakout. Breakouts on low volume are statistically much more likely to fail and reverse back into the range. This is one of the single most impactful false-signal filters.

For crypto: volume on the signal candle must be above the 20-bar average on 15-minute bars. Below-average volume suggests the RSI/MACD crossover is noise rather than genuine momentum.

### Multi-Timeframe Confirmation

For ORB: the 15-minute trend (using 20-period EMA) must agree with the breakout direction. If the 5-minute chart shows a breakout above the opening range high but the 15-minute EMA is declining, the signal is downgraded to "low confidence" and should only be taken if the Portfolio Manager determines other factors are supportive.

For crypto: the 1-hour chart trend (50-period EMA) must be up for long entries. The 4-hour chart provides additional context — if the 4-hour chart shows a bearish regime (price below 50 EMA on 4-hour), even a valid 15-minute signal should be treated with caution and position size reduced by 50%.

### News Filter

The Watcher Agent checks for pending high-impact news events (Federal Reserve announcements, CPI releases, employment reports) within the next 2 hours. If a high-impact event is pending, all new equity entries are suppressed until 30 minutes after the event (to let the initial volatility spike settle). This prevents the system from entering a position just before a news-driven whipsaw that invalidates the technical setup.

For crypto, major events like Bitcoin ETF decisions, regulatory announcements, or exchange incidents should similarly suppress new entries. The Screener Agent's news stream provides these events.

### Whipsaw Detection

If the same symbol has produced a buy signal and then a stop-loss exit within the last 24 hours, the system should not re-enter for 24 hours on that symbol. This prevents the "whipsaw trap" where a stock bounces around a technical level, repeatedly triggering entries and stops.

---

## Indicator Computation Reference

All indicators are computed using standard formulas. The Watcher Agent should use NumPy for computation (pure Python is too slow for real-time monitoring of multiple symbols). For reference:

**RSI-2**: Standard Wilder RSI formula with period=2. Uses exponential smoothing of average gains and average losses over the lookback period.

**200-day SMA**: Arithmetic mean of the last 200 daily closing prices. Recomputed once per day after market close.

**ADX(14)**: Average Directional Index, 14-period, using Wilder's smoothing method. Computed from +DI and -DI, which are themselves derived from Directional Movement (+DM and -DM). Updated once per daily bar.

**ATR(14)**: Average True Range, 14-period. True Range = max(High - Low, |High - Previous Close|, |Low - Previous Close|). ATR is the exponentially smoothed average of True Range over 14 periods. The timeframe of ATR matches the strategy's operating timeframe (daily for RSI-2, 5-minute for ORB, 15-minute for crypto).

**MACD**: Standard MACD with parameters (12, 26, 9). MACD Line = 12-period EMA - 26-period EMA. Signal Line = 9-period EMA of MACD Line. Histogram = MACD Line - Signal Line. The histogram flip from negative to positive is the primary signal used in the crypto strategy.

**RSI(14)**: Standard Wilder RSI with period=14. Used in the crypto strategy for overbought/oversold detection (entry below 30, exit above 70). Not to be confused with RSI-2, which uses period=2 for the equity mean-reversion strategy.

**VWAP**: Volume-Weighted Average Price, computed intraday only. VWAP = Cumulative(Price × Volume) / Cumulative(Volume). Resets at the start of each trading session. Used in the ORB strategy as a momentum filter.

**EMA**: Exponential Moving Average. Weight = 2 / (Period + 1). The 9-period EMA (used for ORB trailing stops) and 50-period EMA (used for crypto trend confirmation) are standard implementations.

---

## Parameter Summary Table

| Parameter | Strategy A (RSI-2) | Strategy B (ORB) | Strategy C (Crypto) |
|-----------|-------------------|-------------------|---------------------|
| **Timeframe** | Daily bars | 5-min bars (15-min opening range) | 15-min bars |
| **Trend filter** | Close > 200-day SMA | ADX > 20, +DI > -DI | Price > 50 EMA (1-hour) |
| **Entry trigger** | RSI-2 < 10 (conservative) or < 5 (aggressive) | 5-min close above opening range high | RSI-14 crosses above 30 + MACD histogram flips positive |
| **Volume filter** | None required | Relative volume ≥ 1.5x 20-bar avg | Above 20-bar average |
| **Stop-loss** | 2x ATR(14) below entry | Below opening range low | 1.5x ATR(14) on 15-min |
| **Take-profit** | RSI-2 > 60 or Close > Prev High | Measured move (range height), then trail 9 EMA | RSI-14 > 70 or MACD histogram flips negative |
| **Time stop** | 5 trading days | 3:45 PM ET | 4 hours |
| **PDT impact** | None (swing trade) | Uses 1 day trade | None (crypto exempt) |
| **Typical hold** | 2–5 days | 30 min – 6 hours | 30 min – 4 hours |
| **Expected win rate** | 75–91% | 55–75% | 60–70% |
| **Expected avg gain** | 0.57–0.82% per trade | 0.27–0.50% per trade | 1.0–2.0% per trade (before fees) |

---

## What the Supervisor Agent Should Monitor and Tune

The signal parameters above are starting points, not permanent settings. The Supervisor Agent's end-of-day review should track these metrics on a rolling 30-day basis and recommend adjustments when performance degrades:

**For RSI-2**: Track the win rate and average gain per trade. If the win rate drops below 60% or the average gain drops below 0.3% over a 30-day window, consider tightening the entry threshold from RSI-2 < 10 to RSI-2 < 5 (being more selective). If too few signals are generated (fewer than 2 per month), consider loosening to RSI-2 < 15.

**For ORB**: Track the win rate, average R-multiple (gain in units of risk), and false breakout rate (trades stopped out within the first 30 minutes). If the false breakout rate exceeds 50%, increase the volume confirmation threshold from 1.5x to 2.0x. If the strategy has produced losses for 3 consecutive weeks, disable it for 2 weeks and reassess market conditions.

**For Crypto**: Track the win rate after fees (net of the 0.30–0.50% round-trip cost). If the net win rate drops below 50% over 30 days, the strategy is not profitable after fees and should be paused. Track which time-of-day produces the best results and adjust the Watcher's monitoring schedule accordingly.

**For regime detection**: Track how often the ADX-based regime classification was correct (did the market actually trend when ADX said it was trending?). If the regime filter is generating too many false signals, consider raising the trend threshold from 25 to 30 for ORB activation.

---

*Phase 3 complete. This specification provides the Watcher Agent with exact indicator parameters, entry/exit rules, regime classification logic, and validation filters for all three active strategies. The parameters are evidence-based starting points that the Supervisor Agent will refine through ongoing performance monitoring. Ready to proceed to Phase 4: Risk Management, Economics, and Legal Framework.*

v1.0.0
