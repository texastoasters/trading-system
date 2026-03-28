# Phase 2 Research Deliverable: Strategy Research and Backtesting

## Strategy Ranking Summary

After comprehensive research into published backtesting results, academic studies, and community-validated approaches, the four strategy categories from the original plan are ranked below in order of suitability for our system. The ranking accounts for backtested performance, PDT constraint compatibility, implementation complexity, and robustness of the underlying edge.

| Rank | Strategy | Win Rate | Avg Gain/Trade | Profit Factor | Max DD | PDT Impact | Verdict |
|------|----------|----------|----------------|---------------|--------|------------|---------|
| 1 | RSI-2 Mean Reversion (equities) | 75–91% | 0.57–0.82% | 2.3–3.0 | 19–23% | None (swing) | **Primary strategy** |
| 2 | Opening Range Breakout (equities) | 55–75% | 0.27–0.50% | 1.4–2.5 | 12–27% | Uses 1 day trade | **Secondary strategy** (replaces gap-and-go) |
| 3 | Crypto Momentum (BTC/ETH) | 60–77% | 1.2–2.4% | ~1.5–2.0 | 15–30% | None (exempt) | **Tertiary strategy** (fee drag concern) |
| 4 | PEAD Earnings Drift (equities) | ~53–58% | Variable | ~1.3–1.5 | Variable | None (swing) | **Demoted to opportunistic** (weakening edge) |

The original plan's gap-and-go strategy has been replaced by Opening Range Breakout (ORB) based on research showing that simple gap strategies have been largely arbitraged away and no longer produce consistent profits on major indices.

---

## Strategy A: RSI-2 Mean Reversion on SPY/QQQ — PRIMARY

### Why This Is the Strongest Strategy

The RSI-2 mean-reversion strategy has the most robust, longest-running body of backtesting evidence of any strategy we evaluated. It exploits a well-understood behavioral phenomenon: short-term price drops in fundamentally strong markets trigger fear-based selling that overshoots fair value, and the subsequent reversion to the mean generates reliable profits.

### Published Backtesting Results

**On QQQ (Nasdaq 100 ETF)**, QuantifiedStrategies.com reports a backtest from QQQ's inception through 2024 with the following results: CAGR of 12.7% (versus 9% for buy-and-hold), invested only 14% of the time, 232 trades, 75% winners, average winner of 2.4%, average loser of 2.1%, profit factor of 3.0, maximum drawdown of 19.5%, and a Sharpe ratio of 2.85. These numbers are outstanding, particularly the profit factor of 3.0 and the fact that capital is idle 86% of the time — perfectly aligned with our PDT constraints.

**On SPY (S&P 500 ETF)**, the same source reports 75% win rate, 0.57% average gain per trade, profit factor of 2.3, and maximum drawdown of 23%. A more aggressive variant using momentum filters achieved a 91% win rate with 0.82% average gain per trade, though with higher maximum drawdown of 33%.

**On individual large-cap stocks**, a multi-stock approach using RSI-2 with a 200-day SMA trend filter has been consistently profitable. A research piece by Quantitativo analyzed six different RSI-2 entry thresholds (5, 10, 15, 20, 25, 30) and found that the RSI-2 entry at 10 has the best edge over the full 1998–2024 period. However, the optimal threshold shifts over time, and diversifying across multiple thresholds (running several sub-strategies with different parameters) produces more stable performance than optimizing a single threshold.

### Recommended Implementation

**Entry rules**: Buy when RSI-2 closes below 10 AND the closing price is above the 200-day SMA (trend filter ensuring we only buy dips in uptrending markets).

**Exit rules**: Sell when RSI-2 closes above 60, OR after 5 trading days, whichever comes first. The 5-day time stop prevents positions from lingering in unresolved conditions.

**Stop-loss**: 2x ATR(14) below the entry price. This gives the position enough room to absorb normal volatility without being shaken out, while still protecting against outsized losses.

**Position sizing**: Standard 1% risk rule ($50 max risk on $5,000 account). Calculate shares = $50 / (Entry Price - Stop Price).

**Instruments**: SPY and QQQ as primary vehicles. These are highly liquid (zero slippage concern), commission-free on Alpaca, and provide instant diversification. Optionally expand to sector ETFs (XLK, XLF, XLE) for more trade opportunities.

### PDT Impact: Zero

This strategy holds positions for 2–5 days on average. Since positions are opened one day and closed on a subsequent day, no day trades are consumed. This is the single most important reason it is the primary strategy — it operates entirely within our PDT constraints with no friction whatsoever.

### Known Risks and Limitations

The strategy underperforms during strong bear markets when the 200-day SMA filter is breached. During these periods, the strategy correctly stays in cash (no trades trigger when price is below the 200-day SMA), but this means missing recovery opportunities if the reversal happens quickly. Maximum drawdown of 19–23% is significant but within our system's 20% circuit breaker threshold — the Supervisor Agent would halt trading before the strategy's worst historical drawdown fully materializes.

The Sharpe ratio of 2.85 on QQQ should be viewed with caution. Our Phase 1 research established that backtested Sharpe ratios typically degrade by 50% in live trading (per Bailey and López de Prado). A realistic live Sharpe expectation is 1.4–2.0, which is still excellent.

---

## Strategy B: Opening Range Breakout (ORB) — SECONDARY (Replaces Gap-and-Go)

### Why Gap-and-Go Was Replaced

The original plan specified a gap-and-go strategy as the secondary equity approach. Research revealed this to be problematic. QuantifiedStrategies.com tested gap trading on the S&P 500 futures going back 25 years and found that simple gap strategies "no longer yield consistent profits" on major indices. Even after adding multiple filters (gap size restrictions, moving average filters), the best result achieved only 0.06% per trade — essentially zero edge after costs. Their assessment was blunt: gap trading used to be "low hanging fruit but not anymore" — the edge has been arbitraged away by the proliferation of algorithmic trading.

The Opening Range Breakout strategy is a superior alternative that captures similar momentum dynamics (capitalizing on the first directional move of the trading day) with significantly better documented performance.

### Published Backtesting Results

**QuantifiedStrategies.com** reports an ORB strategy on SPY with a daily trend filter: 198 trades, 65% win rate, 0.27% average gain per trade, profit factor of 2.0. They describe this as "pretty good" and have been trading it live.

**Trade That Swing** reports a more refined ORB on MNQ (micro Nasdaq futures): 114 trades over one year, 74.56% win rate, profit factor of 2.512, maximum drawdown of $2,725 (12% of account at time). Only 3 instances of two consecutive losses in the entire sample. No instances of three consecutive losses.

**QuantConnect research** implementing ORB on a universe of liquid US equities with abnormal volume achieved a Sharpe ratio of 2.4 and a beta close to zero — meaning the returns were largely market-independent.

**Option Alpha** backtested 60-minute ORB on SPY (combined bullish and bearish): 89.4% win rate, profit factor of 1.44, with significantly less drawdown than 15 or 30-minute variants.

### Important Caveat: Parameters Must Adapt

Every ORB research source emphasizes that this is not a "set and forget" strategy. The optimal opening range duration (5, 15, 30, or 60 minutes), breakout confirmation filters, and stop/target levels need periodic recalibration based on current market conditions. The Trade That Swing researcher explicitly warns that the exact parameters from the backtest period may not work in the future and should be updated based on running statistics. This is actually an ideal use case for the Supervisor Agent's learning loop — it can track ORB performance on a rolling basis and adjust parameters when metrics deteriorate.

### Recommended Implementation

**Pre-conditions**: Only trade ORB when the Screener Agent identifies the overall market as having directional momentum (ADX > 25 on the daily chart). In ranging markets, ORB produces too many false breakouts.

**Opening range**: Use the first 15 minutes of the trading session (9:30–9:45 AM ET) to establish the range high and range low on SPY or QQQ.

**Entry rules**: Go long when a 5-minute candle closes above the opening range high with relative volume greater than 1.5x the 20-bar average. Go short (skip for now given Rule 1 — we don't short) when price closes below the range low. Since we cannot short, this is a long-only strategy that trades bullish breakouts.

**Stop-loss**: Just inside the opposite side of the opening range (below the range low for long trades). This gives a clear invalidation point.

**Take-profit**: Target the opening range height projected from the breakout point (measured move). For example, if the range is $0.50 wide and breakout occurs above the range high, the target is $0.50 above the breakout. After the first target, trail using the 9-period EMA.

**Exit**: Close position by market close if neither stop nor target is hit (this is a day trade, not an overnight hold).

### PDT Impact: Uses 1 Day Trade Per Execution

This is the critical constraint. Each ORB trade opens and closes on the same day, consuming one of our 3 available day trades per rolling 5-day window. The system should limit ORB to a maximum of 2 executions per week, reserving the third day trade for emergency exits on other positions. The Portfolio Manager must check `daytrade_count` before approving any ORB trade.

### Known Risks

False breakouts are the primary risk — price breaks above the opening range, triggers an entry, then reverses back into the range and stops out. Volume confirmation (requiring 1.5x relative volume) helps filter these, but cannot eliminate them entirely. The strategy's edge has been declining over time as more algorithmic traders target the same patterns, which is why continuous parameter recalibration via the Supervisor Agent is essential.

---

## Strategy C: Crypto Momentum on BTC/ETH — TERTIARY

### The Role of Crypto in This System

Crypto serves a specific architectural purpose: it is the only venue where the system can execute unlimited intraday round-trips without PDT constraints. This makes it the "always available" trading channel when equity day trades are exhausted. However, Alpaca's crypto fees (0.15% maker / 0.25% taker, roughly 0.30–0.50% round-trip) create a meaningful performance drag that equities do not face.

### Published Backtesting Results

The evidence base for crypto momentum strategies is less rigorous than for equity strategies, with shorter backtest histories and more variable market conditions. Key findings:

**RSI + MACD combined on BTC**: A backtesting study cited by Spoted Crypto (January 2026) reports that individual indicators achieve 40–60% win rates on their own, but combined RSI + MACD reaches approximately 77% win rate. However, this figure comes from a single source (Gate.io backtest) and should be treated as aspirational rather than guaranteed.

**MACD alone on BTC/ETH**: Multiple sources report 50–55% win rate for MACD crossover strategies on BTC, which is barely profitable after fees. MACD performs well in trending markets but produces frequent false signals in sideways/ranging conditions, which are common in crypto.

**Multi-indicator combination (RSI + MACD + Stochastic) on BTC**: A Python backtest published on Medium achieved 2.43% mean return per trade on BTC and 1.16% on ETH over a limited sample of 5–6 trades. The small sample size makes these numbers unreliable, but the per-trade returns are high enough to overcome Alpaca's fee drag.

**Momentum indicators in general on crypto**: TradingView backtest analysis for 2024–2025 on BTC/USD at 1-hour timeframes showed 60%+ win rates for momentum indicators. Combined approaches (multiple indicators confirming) achieved higher consistency.

### Recommended Implementation

**Primary pair**: BTC/USD. Bitcoin has the deepest liquidity and tightest spreads on Alpaca, minimizing slippage. ETH/USD as secondary.

**Timeframe**: 15-minute charts for signal generation, with 1-hour and 4-hour charts for trend confirmation.

**Entry rules (long only — Rule 1 means no shorting)**: Enter when RSI-14 crosses above 30 from below (oversold bounce) AND MACD histogram flips from negative to positive AND price is above the 50-period EMA on the 1-hour chart. This triple confirmation filters out most false signals. Require above-average volume on the signal candle.

**Exit rules**: Exit when RSI-14 crosses above 70 (overbought), OR when MACD histogram flips from positive to negative, OR after a time stop of 4 hours (prevents positions from lingering). Use a trailing stop at 1.5x ATR(14) once the position is in profit.

**Position sizing**: Same 1% risk rule ($50 max risk), but calculate using the wider stops that crypto's volatility demands. BTC typically requires 1.5–2x wider stops than equities, so position sizes will be proportionally smaller in dollar terms.

**Fee awareness**: Use limit orders whenever possible (0.15% maker vs 0.25% taker). The Portfolio Manager must add 0.30% (best case round-trip) to the minimum expected return before approving a crypto trade. Any signal projecting less than 0.60% gain should be rejected.

**Weekend trading**: Crypto trades 24/7. The system should have a "weekend mode" where the Screener runs at reduced frequency (every 4 hours instead of hourly) and the Watcher monitors open positions. Weekend crypto volume is typically lower, which means wider spreads and more slippage — the agents should increase minimum return thresholds by 50% on weekends.

### PDT Impact: Zero

Crypto is completely exempt from the PDT rule. Unlimited round-trips per day.

### Known Risks

Crypto's volatility is significantly higher than equities. BTC routinely experiences 5–10% daily swings, and flash crashes of 15–20% in minutes have occurred multiple times historically. The March 2020 COVID crash saw BTC drop 50% in a single day. Stop-losses can experience significant slippage during these events because crypto exchanges may have thin order books. The system's 1% risk per trade and 3% daily loss limit provide protection, but the Supervisor Agent should monitor total crypto exposure and reduce position sizes during extreme volatility (VIX equivalent for crypto: the Bitcoin Volatility Index, if available via Alpaca or a supplementary data source).

The fee drag is the largest ongoing concern. At 0.30–0.50% per round-trip, a strategy that trades frequently (say, 3 round-trips per day) would need to generate 0.90–1.50% in daily gross returns just to cover fees — a high bar. The agents should prioritize trade quality over quantity in crypto, taking only the highest-conviction signals.

---

## Strategy D: Post-Earnings Announcement Drift (PEAD) — DEMOTED TO OPPORTUNISTIC

### Why PEAD Was Demoted

The original plan positioned PEAD as a core strategy alongside mean reversion and gap trading. Research reveals that the PEAD edge is significantly weaker and more contested than previously understood, particularly for a system like ours that cannot short stocks.

### The Academic Debate: Is PEAD Still Real?

There is an active academic disagreement about whether PEAD still exists in US markets. A 2025 UCLA Anderson analysis by Subrahmanyam directly addressed this controversy. When including all stocks, the earnings drift showed a t-statistic of 2.18 — barely clearing the threshold of statistical significance. When excluding microcap stocks (the bottom 20% by NYSE market cap), the t-statistic dropped to 1.43, well below meaningful significance. The researcher's conclusion: PEAD in non-microcap stocks may have largely disappeared, and the inclusion of microcap stocks (which have wide bid-ask spreads, low liquidity, and high execution costs) was driving the apparent persistence of the anomaly.

This is directly relevant to our system. We should be trading liquid, large-cap stocks and ETFs to minimize slippage and execution risk. If PEAD primarily exists in illiquid microcap stocks, it is not a practical strategy for us.

### What PEAD Could Still Offer

The PEAD literature is not entirely negative. A 2024 study (Garfinkel, Hribar, and Hsiao) found that a hedge portfolio going long in the top earnings surprise decile and short in the bottom decile generates 5.1% risk-adjusted return over three months (approximately 20% annualized). However, half of this return comes from the short leg, which violates Rule 1. The long-only component would capture roughly 2.5% per quarter — decent but not spectacular.

More promisingly, research using AI and NLP to analyze earnings call transcripts has shown that the linguistic content of the call (not just the earnings numbers) contains predictive information that the market is slow to incorporate. A FinBERT model achieved 57–58% classification accuracy on PEAD direction, and the resulting trading strategy generated meaningful returns. This suggests that our LLM-powered agents could have a genuine advantage in PEAD detection by analyzing earnings call transcripts for sentiment and forward guidance nuances that simpler algorithmic approaches miss.

### Recommended Implementation (Opportunistic, Not Systematic)

Rather than running PEAD as a scheduled strategy, it should be triggered opportunistically when the conditions are right.

**Trigger**: During earnings season (roughly 3 weeks per quarter), the Screener Agent monitors earnings announcements for stocks in its watchlist. When a stock reports earnings that beat analyst estimates by more than 10%, the Screener flags it for PEAD evaluation.

**LLM analysis**: The Portfolio Manager (or a dedicated PEAD sub-routine) uses GPT-OSS 120B at high reasoning effort to read the earnings headline and available summary, assessing whether the surprise is genuinely material and whether forward guidance supports continued drift.

**Entry**: If the LLM assessment is bullish with high confidence, enter a long position the day after the announcement (to avoid the initial volatility spike). This is a swing trade held for 5–10 days.

**Exit**: Exit after 10 trading days or when the drift appears to have stalled (price consolidates for 3+ days without making new highs).

**Position sizing**: Reduced size — 0.5% risk instead of the standard 1% — because the PEAD edge is weaker and less certain than our primary strategies.

### PDT Impact: Zero

PEAD positions are held for 5–10 days, so no day trades are consumed.

### Why This Is the Right Approach

By treating PEAD as opportunistic rather than systematic, we capture the upside when conditions align (a genuinely material earnings surprise in a liquid stock) without depending on an edge that academic research suggests may be fading. The LLM analysis adds genuine value here — it can assess earnings call nuance in ways that pure numerical approaches cannot, potentially giving our system an edge that traditional PEAD strategies lack. This is one of the few places where the cost of an LLM call is clearly justified by the informational advantage it provides.

---

## Cross-Strategy Capital Allocation

Given the performance characteristics and PDT constraints, the recommended capital allocation across strategies is:

**RSI-2 Mean Reversion**: 50% of equity capital ($1,500–$1,750). This is the workhorse that generates the majority of expected returns with no PDT consumption. Given that it is only invested ~14% of the time, the full allocation is rarely deployed simultaneously.

**Opening Range Breakout**: 20% of equity capital ($600–$700 per trade, max 2 per week). This is the controlled day-trading component that uses the limited PDT allowance for high-probability intraday setups.

**Crypto Momentum**: 30% of total capital ($1,500). This is the always-available channel for intraday trading when equity day trades are exhausted. Size trades carefully to account for fee drag.

**PEAD**: No dedicated allocation. Funded from the idle RSI-2 allocation during earnings season when mean-reversion capital is not deployed.

These allocations are starting points. The Supervisor Agent should track relative performance across strategies on a rolling 30-day basis and adjust allocations toward strategies that are performing well in current market conditions. If RSI-2 is in a period of low activity (market consistently trending without pullbacks), temporarily shift more capital to ORB and crypto.

---

## Validation Criteria for Paper Trading

Before proceeding to live trading, each strategy must meet these minimum thresholds during the 6-week paper trading validation:

| Metric | RSI-2 Mean Reversion | Opening Range Breakout | Crypto Momentum |
|--------|---------------------|----------------------|-----------------|
| Minimum trades | 15 | 10 | 30 |
| Win rate | > 60% | > 50% | > 55% |
| Profit factor | > 1.5 | > 1.3 | > 1.2 (after fees) |
| Sharpe ratio | > 1.0 | > 0.7 | > 0.5 |
| Max drawdown | < 20% | < 15% per trade | < 15% of crypto allocation |

If any strategy fails to meet its validation thresholds during paper trading, it should be disabled and its capital reallocated to the strategies that passed. The system can operate profitably on RSI-2 mean reversion alone — the other strategies are diversification, not necessities.

---

## Key Takeaway for the Agents

The most important insight from Phase 2 is that **simplicity wins**. The RSI-2 mean reversion strategy — which uses a single indicator with one filter (200-day SMA) and straightforward entry/exit rules — has the best backtested performance of any strategy we evaluated. More complex approaches (gap trading, PEAD) showed weaker and less reliable edges. The agents' instruction sets should encode this hierarchy clearly: execute the simple, proven strategies consistently, and only layer on complexity (ORB, PEAD) when the simple strategies are idle.

---

*Phase 2 complete. Four strategies evaluated with published backtesting evidence. Gap-and-go replaced by Opening Range Breakout based on evidence that gap trading edges have been arbitraged away. PEAD demoted from core to opportunistic based on academic evidence that the anomaly is weakening in non-microcap stocks. RSI-2 mean reversion confirmed as the strongest, most PDT-compatible strategy. Ready to proceed to Phase 3: Technical Indicator Optimization and Signal Engineering.*

v1.0.0
