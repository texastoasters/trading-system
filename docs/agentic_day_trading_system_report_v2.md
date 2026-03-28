# Building an Autonomous Agentic Day Trading System

## A Complete Research Report, Architecture Plan, and Implementation Roadmap

**Starting Capital: $5,000 | 1x Margin Account (No Leverage, No Shorting) | No Debt Exposure**

---

## Executive Summary

An LLM-powered multi-agent trading system is technically feasible on $5,000 seed capital and can be designed so that 90% of operations run on pure code with LLMs reserved for judgment calls that algorithms cannot handle. The economics at $5,000 are considerably more forgiving than at $1,000: a budget-optimized system costs $2–$8/month to run, requiring just 0.16% monthly returns to break even — well within reach of even modest algorithmic strategies. The system can be orchestrated entirely through OpenClaw, leveraging its skills system, multi-agent session routing, cron scheduling, and cross-agent communication to coordinate five specialized trading agents. Together.ai's GPT-OSS 120B model at $0.15/$0.60 per million tokens provides the best balance of capability and cost for most agent tasks — its configurable reasoning depth (low effort for quick classification, high effort for complex analysis) means a single model can serve all agents at different quality levels, with Claude Sonnet 4 reserved for the handful of high-stakes portfolio decisions where superior reasoning justifies the premium.

This report delivers three detailed plans — research, architecture, and implementation — along with explanations of every tool and framework referenced, and a comprehensive glossary of financial and technical terms.

---

## Table of Contents

1. The Honest Context: Why Most Day Traders Lose Money
2. Plan 1: The Research Plan
3. Plan 2: The Architecture Plan
4. Plan 3: The Implementation Plan
5. Account Constraints: 1x Margin, PDT Rule, and How They Shape Everything
6. The Indicator Combinations That Actually Work
7. Risk Management That Preserves the $5,000
8. Which LLM Models to Use and Why
9. OpenClaw as the Orchestration Layer
10. Broker Recommendation: Why Alpaca
11. The Economics Must Work or Nothing Else Matters
12. Legal and Regulatory Considerations
13. Common Failure Modes This System Must Survive
14. Tool and Framework Reference Guide
15. Glossary of Financial and Technical Terms

---

## 1. The Honest Context: Why Most Day Traders Lose Money

Before anything else, this context matters for calibrating expectations. A landmark Brazilian study tracked all 19,646 individuals who began day trading between 2013 and 2015. Of those who persisted for 300 or more days, 97% lost money. Only 1.1% earned more than minimum wage. The top performer averaged $310/day with a standard deviation of $2,560/day, meaning even the single best trader was barely profitable on a risk-adjusted basis. A separate 15-year study of the Taiwan stock market found less than 1% of day traders could reliably earn positive returns net of fees.

These statistics are for *human* day traders. An algorithmic approach eliminates emotional biases (fear, greed, the disposition effect where traders hold losers too long and sell winners too early), but introduces new risks: overfitting, strategy decay, and the fundamental challenge of competing against institutional quantitative firms with vastly superior data, infrastructure, and Ph.D.-staffed research teams.

The design of this system accounts for this reality. The first several months are explicitly a learning period where the Supervisor Agent's ability to analyze mistakes and improve the system is more valuable than any trading profits generated. The $5,000 seed capital should be considered tuition money that also happens to be working toward future profitability.

---

## 2. Plan 1: The Research Plan

The research phase must precede any code. Trading systems built on assumptions rather than evidence are the primary reason retail algo traders fail. The plan is organized into four sequential phases, where each phase produces deliverables that feed the next. Since I (Claude) will be conducting this research through deep web search, analysis, and synthesis, the timeline is measured in working sessions rather than calendar weeks. The calendar time constraint comes later, during paper trading validation, where the system needs to observe real market conditions over several weeks — that cannot be compressed.

### Phase 1: Market Microstructure and Constraints (1–2 sessions)

This phase establishes the hard boundaries of the system. The research covers Alpaca's account structure (all accounts are margin accounts — there are no cash accounts), the Pattern Day Trader rule (the binding constraint for equity trading under $25,000), and crypto's exemption from PDT rules as a parallel trading channel. We will document exactly how Alpaca's API reports buying power, cash, and day trade counts, and how the broker's built-in PDT protection (which rejects orders with HTTP 403 if they would trigger PDT designation) serves as a safety net underneath our own tracking.

We will build a PDT-aware capital management model that tracks day trades in the rolling 5-business-day window, reserves emergency exit capacity, and routes capital between equities (PDT-limited) and crypto (unlimited day trades but subject to 0.15%/0.25% maker/taker fees).

**Deliverable**: A constraints document specifying exactly how capital flows through the system given PDT rules and crypto fee structures, and an algorithm specification for PDT tracking and Rule 1 enforcement (preventing any use of margin leverage) that the Trade Executor agent will implement.

### Phase 2: Strategy Research and Backtesting (2–3 sessions) — COMPLETED

This phase investigated four strategy categories through published research and then validated them with real backtesting against Alpaca historical data. The backtesting results significantly changed the system design.

**Strategy A — RSI-2 Mean Reversion (NOW THE SOLE STRATEGY)**: Validated on real data across 7 instruments over 3–5 years. Results on equities: SPY 78% win rate, +0.58% avg trade, 2.75 profit factor; QQQ 84% win rate, +0.77% avg trade, 3.38 PF; XLK 84% win rate, +0.94% avg trade, 3.46 PF; XLF 79% WR, 1.90 PF; XLI 71% WR, 2.00 PF. All equity instruments passed validation with sub-2% max drawdowns. BTC/USD daily bars with the 200-day SMA trend filter also passed: 75% net win rate (after 0.40% fees), 1.52 PF, +0.41% avg net trade. Across all validated instruments, the system generates approximately 12–14 trades per month. Zero PDT consumption (all positions held 2–5 days). Two RSI-2 configurations: conservative (entry < 10, exit > 60) as default, aggressive (entry < 5, exit > SMA-5) for confirmed strong uptrends.

**Strategy B — Opening Range Breakout: ELIMINATED after backtesting.** Published research showed strong results on futures with leverage, but backtesting on SPY with proper regime filtering (ADX uptrend only + minimum range size + volume + VWAP) produced only 3 trades in 12 months, with average gains of +0.14%. SPY's opening ranges average only 0.20–0.25% of price — insufficient for meaningful returns at our account size, and each trade consumes a scarce PDT day trade.

**Strategy C — Crypto 15-minute Momentum: ELIMINATED after backtesting.** Triple-confirmation strategy (RSI + MACD + volume) on 15-minute BTC bars produced a 3.2% win rate with -18.4% total return. Root causes: Alpaca's 0.40% round-trip fees exceed the average gross trade (+0.32%), the volume data is too thin for meaningful confirmation, and the trailing stop (1.5x ATR) is too tight for crypto volatility. The strategy contradiction — requiring oversold conditions AND an uptrend simultaneously on 15-minute bars — meant the v2 trend filter eliminated all signals entirely.

**Strategy D — PEAD: Remains OPPORTUNISTIC.** Not backtested (requires earnings calendar integration). Retained as a future overlay when the LLM detects genuinely material earnings surprises.

**ETH/USD: ELIMINATED.** Daily RSI-2 backtest showed 57% win rate with -11% stop-losses destroying profitability even when winners averaged +5.5%. Too volatile for mean reversion at our risk parameters.

**Final validated instrument universe**: 17 instruments across three tiers. Tier 1 (core, always active): SPY, QQQ, NVDA, XLK, XLY, XLI. Tier 2 (standard, disabled during 10%+ drawdowns): GOOGL, XLF, META, TSLA, XLC, DIA, BTC/USD. Tier 3 (marginal, active only when higher tiers idle): V, XLE, XLV, IWM. Combined: ~125 trades/year (10.4/month). The universe is dynamic — the Supervisor Agent runs monthly re-validation and discovery scans to promote, demote, and discover instruments.

### Phase 3: Technical Indicator Optimization and Signal Engineering (1–2 sessions) — COMPLETED

Research produced a four-stage signal pipeline: Regime Detection → Instrument Selection → Signal Generation → Signal Validation. The pipeline simplifies dramatically with only one strategy (RSI-2) applied across multiple instruments. ADX(14) serves as the regime switch — below 20 (ranging), use conservative RSI-2 entry (below 10); above 25 with +DI > -DI (strong uptrend), switch to aggressive entry (below 5) for faster re-entry on deeper dips. Two RSI-2 configurations: conservative (entry below 10, exit RSI-2 above 60 or close > previous high, 5-day time stop) and aggressive (entry below 5, exit close > 5-period SMA). ATR-based stops at 2x ATR(14) for all instruments. For BTC/USD, the 200-day SMA trend filter is used (backtesting showed it outperforms the 50-period EMA, producing 75% net win rate vs 62%). A news filter suppresses new entries within 2 hours of high-impact events.

**Deliverable**: A signal engineering specification with exact indicator parameters for RSI-2 across equities and BTC/USD daily bars, regime classification logic, and tuning parameters for the Supervisor Agent's learning loop.

### Phase 4: Risk Management, Economics, and Legal Framework (1–2 sessions) — COMPLETED

Research confirmed the economic model is highly favorable: at $3–$5/month operating costs, the system needs only 0.06–0.10% monthly return to break even — a single successful RSI-2 trade generates 5–10x this amount. Position sizing uses the fixed-fractional method at 1% risk per trade ($50 on $5,000), which naturally caps positions below Rule 1 limits on expensive instruments. A tiered drawdown protocol halts trading at 20% drawdown ($1,000 loss from peak), with intermediate reductions at 10% and 15%.

Critical tax finding: starting with the 2025 tax year, cryptocurrency is now subject to wash sale rules (previously exempt). This makes the Section 475(f) mark-to-market election effectively mandatory for this system — it eliminates wash sale complications entirely. However, under Revenue Procedure 2025-23, the election now has a five-year lock-in period. The election deadline for 2026 trading is April 15, 2026, filed with the 2025 tax return. Operational risk mitigations include server-side stop-loss orders (protecting positions during connectivity loss), LLM-free fallback mode, and comprehensive startup verification.

**Deliverable**: A complete economic model, tiered drawdown protocol, tax strategy with 475(f) election guidance, and operational risk mitigation plan.

---

## 3. Plan 2: The Architecture Plan

### The Fundamental Design Principle

LLMs should handle strategic thinking while pure code handles tactical execution. LLM inference takes 1–30 seconds per call, making it useless for sub-second trading decisions. But LLMs excel at interpreting earnings reports, synthesizing news sentiment, reasoning about whether a portfolio is overexposed to a particular sector, and evaluating ambiguous market conditions — tasks where traditional rule-based algorithms struggle.

Research into production multi-agent trading systems validates this approach. The TradingAgents framework (UCLA/MIT, 2024) uses 7–8 specialized agents orchestrated by LangGraph. The AgenticTrading project (Open-Finance-Lab) uses MCP/A2A protocols with a DAG Planner that constructs task-flow graphs dynamically. Both demonstrate that specialized agents with clearly bounded responsibilities, communicating through structured data, outperform monolithic approaches.

### Five-Agent Architecture with Supervisor

The system uses five specialized agents, each running as an OpenClaw session with its own skills, model configuration, and communication channels. The Supervisor Agent serves as the orchestration layer.

**Agent 1: Screener Agent** — Discovers what to trade. Runs an end-of-day scan at 4:15 PM ET computing RSI-2, SMA-200, and ATR-14 for every instrument in the active universe (read dynamically from Redis `trading:universe` — not hardcoded). Checks BTC/USD every 4 hours. Also monitors Alpaca's news WebSocket for material events. News evaluation is the only LLM task — keyword filtering catches headlines, then the LLM assesses whether the news invalidates a pending RSI-2 entry. Publishes a ranked watchlist of instruments approaching entry conditions, tagged with their tier level. Expected LLM usage: 3–5 calls per day.

**Agent 2: Watcher Agent** — Monitors for entries and exits. Evaluates RSI-2 signals once per day after market close (daily bar driven — not intraday). Monitors open positions continuously for stop-loss hits. Applies the same RSI-2 rules across all instruments: conservative entry (RSI-2 < 10) in ranging markets, aggressive entry (RSI-2 < 5) in confirmed uptrends. Exits on RSI-2 > 60, close > previous high, 5-day time stop, or 2x ATR stop-loss. For BTC/USD, includes a fee-adjusted flag so the Portfolio Manager deducts 0.40%. LLM invoked only for news materiality assessment (~2 calls/day).

**Agent 3: Portfolio Manager Agent** — Decides position sizes and priorities. Reads the tier system from Redis and prioritizes Tier 1 instruments over Tier 2 over Tier 3 when multiple signals arrive. Uses fixed-fractional sizing at 1% risk ($50 on $5,000), capped by Rule 1 (never exceed cash balance). Checks sector correlation to avoid overconcentration. May close a weak Tier 3 position to make room for a Tier 1 signal. Applies drawdown-based tier disabling (Tier 3 off at 5% DD, Tier 2 off at 10% DD). Expected LLM usage: 3–5 calls per day (GPT-OSS 120B), with Claude Sonnet 4 escalation ~2 calls/week.

**Agent 4: Trade Executor Agent** — Executes trades. Pure code, zero LLM. Validates every order against Rule 1, PDT limits, daily loss limit, and max concurrent positions. Submits server-side GTC stop-loss orders immediately after every entry fill. Under normal operation, no day trades are consumed (all positions are swing trades held 2–5 days). PDT counter maintained as a safety net for emergency exits only.

**Agent 5: Supervisor Agent** — Watches everything, learns, and manages the universe. Code-based health checks every 15 minutes. End-of-day LLM review analyzes trades, adjusts parameters, and promotes/demotes instruments between tiers. Two monthly jobs: re-validation (1st — re-backtests all instruments on rolling 12-month data) and discovery (15th — scans new candidates from Alpaca's 12,000+ assets, adds up to 5 new Tier 3 instruments per month). Circuit breakers halt trading at 20% drawdown, with intermediate tier-based disabling at 10% and 15%. Expected LLM usage: ~8–10 calls per week.

### Communication and Data Flow

All agents communicate through a shared state store (Redis), not through conversational back-and-forth. The Screener publishes a watchlist. The Watcher publishes alerts. The Portfolio Manager publishes approved orders. The Executor publishes fill confirmations, PDT counter updates, and position status. The Supervisor reads everything and publishes system-level directives (pause trading, reduce size, update strategy weights). This event-driven architecture minimizes unnecessary LLM calls — agents only invoke the LLM when they encounter a situation that pure code cannot resolve.

The data pipeline flows through three tiers. The Bronze tier ingests raw daily price data (OHLCV bars) from Alpaca's API into Redis. The Silver tier computes technical indicators (RSI-2, SMA-200, ATR-14, ADX-14) using pure Python and NumPy via the validated `indicators.py` library. The Gold tier generates RSI-2 signals by applying the entry/exit rules across the dynamic instrument universe, with selective LLM augmentation for news materiality assessment. Decisions flow from the Portfolio Manager to the Executor, and every action is logged to TimescaleDB for audit, tax reporting, and the Supervisor's learning process.

---

## 4. Plan 3: The Implementation Plan

### Stage 0: Infrastructure Setup (Day 1–2)

Open an Alpaca paper trading account (free, no minimum deposit, provides $100,000 in virtual funds for testing). Install the `alpaca-py` Python SDK. Set up OpenClaw on a local machine or VPS with Node.js 22.16+. Configure five OpenClaw agent sessions, each with its own skills directory and model assignment. Set up Redis (for shared state and pub/sub messaging between agents) and TimescaleDB (for historical storage) via Docker. Create API accounts for Anthropic, OpenAI, and Together.ai. Create a Finnhub free account for supplemental market data (60 API calls per minute on the free tier). Contact Alpaca support to request 1x margin multiplier configuration (no leverage), and verify that `shorting_enabled` is false.

Build the PDT tracking module and Rule 1 enforcement module first — these are the foundation that prevents regulatory violations and debt exposure, and must be bulletproof before any trading begins. Verify that Alpaca's paper trading environment enforces PDT rules (it does — rejected orders return HTTP 403) so the paper trading validation phase accurately reflects live constraints.

### Stage 1: Strategy Implementation and Backtesting (Days 3–10) — COMPLETED

RSI-2 mean reversion has been implemented and backtested against real Alpaca historical data across 26 candidate instruments. The backtesting validated RSI-2 on 17 instruments, eliminated 3 alternative strategies, and established a three-tier universe with an automated discovery mechanism for ongoing expansion.

**RSI-2 Mean Reversion across all instruments**: One strategy, multiple instruments, one set of rules. Buy when RSI-2 drops below 10 (conservative) or below 5 (aggressive, in strong uptrends) and price is above the 200-day SMA. Exit when RSI-2 rises above 60, or close exceeds the previous day's high, or after 5 days. Stop-loss at 2x ATR(14) below entry. All positions held 2–5 days, zero PDT consumption. BTC/USD uses the 200-day SMA trend filter and deducts 0.40% round-trip fees.

**Tier 1 — Core (always active, PF ≥ 2.0, WR ≥ 70%)**:

| Instrument | Win Rate | Avg Trade | Profit Factor | Max DD | Trades/Year |
|-----------|----------|-----------|---------------|--------|-------------|
| SPY | 89% | +0.86% | 6.04 | 1.6% | 9 |
| QQQ | 88% | +1.01% | 5.06 | 1.0% | 9 |
| NVDA | 82% | +3.07% | 4.29 | 1.0% | 4 |
| XLK | 84% | +0.94% | 3.46 | 1.0% | 6 |
| XLY | 81% | +0.62% | 2.10 | 1.2% | 9 |
| XLI | 71% | +0.42% | 2.00 | 1.9% | 9 |

**Tier 2 — Standard (active unless drawdown > 10%, PF ≥ 1.5)**:

| Instrument | Win Rate | Avg Trade | Profit Factor | Max DD | Trades/Year |
|-----------|----------|-----------|---------------|--------|-------------|
| GOOGL | 82% | +0.67% | 1.94 | 1.0% | 7 |
| XLF | 79% | +0.41% | 1.90 | 2.3% | 8 |
| META | 85% | +0.58% | 1.85 | 1.0% | 7 |
| TSLA | 65% | +1.27% | 1.77 | 2.8% | 7 |
| XLC | 75% | +0.36% | 1.66 | 1.7% | 9 |
| DIA | 67% | +0.20% | 1.56 | 1.6% | 9 |
| BTC/USD | 75% net | +0.41% net | 1.52 | 1.5% | 6 |

**Tier 3 — Marginal (active only when Tier 1+2 idle, PF ≥ 1.3)**:

| Instrument | Win Rate | Avg Trade | Profit Factor | Max DD | Trades/Year |
|-----------|----------|-----------|---------------|--------|-------------|
| V | 73% | +0.35% | 1.48 | 1.6% | 7 |
| XLE | 73% | +0.28% | 1.47 | 1.0% | 4 |
| XLV | 74% | +0.16% | 1.44 | 1.3% | 8 |
| IWM | 70% | +0.24% | 1.35 | 1.8% | 8 |

**Combined validated universe**: 17 instruments producing approximately 125 trades per year (10.4 per month). Tier 1+2 alone produces 99 trades/year (8.2/month). A discovery scan of 29 random candidates from Alpaca's 12,338+ tradeable assets found 18 additional instruments that pass RSI-2 validation, confirming the universe can grow organically over time through the Supervisor's monthly discovery process.

**Dynamic universe management**: The instrument list is not hardcoded. The Supervisor Agent runs two monthly jobs: re-validation (1st of each month — re-backtests all instruments on rolling 12-month data, promotes/demotes between tiers) and discovery (15th of each month — scans random samples of new instruments, adds passes as Tier 3 probationary). New discoveries must meet stricter thresholds (≥ 10 trades, WR ≥ 65%, PF ≥ 1.5, avg trade > 0.30%) and are capped at 5 additions per month. All agents read their instrument list from Redis rather than hardcoded lists.

**Eliminated after backtesting**: Opening Range Breakout (3 trades in 12 months on SPY — insufficient), crypto 15-minute momentum (3% win rate, fees exceed returns), ETH/USD daily RSI-2 (stop-losses of -11% destroy profitability), and 9 individual instruments that failed validation thresholds (AAPL, MSFT, AMZN, JPM, UNH, XLP, XLRE, XLB, XLU).

### Stage 2: Agent Development (Days 11–25)

Build agents incrementally, testing each in isolation before integration.

**Days 11–14**: Build the Trade Executor Agent (pure code). This is the most critical agent because it touches real money and must enforce all safety constraints. Implement order submission, fill monitoring, partial fill handling, the PDT day-trade counter, Rule 1 enforcement (capping orders at cash balance, never using margin buying power), and 403 PDT rejection handling (graceful fallback to overnight hold). Test extensively on Alpaca paper trading, which fully simulates PDT constraints. This agent has zero LLM dependency.

**Days 15–18**: Build the Screener and Watcher Agents. Implement the technical screening pipeline as pure code. Add the LLM layer for news evaluation using structured JSON-output prompts. Configure as OpenClaw skills with cron scheduling for the Screener and event-driven triggers for the Watcher.

**Days 19–22**: Build the Portfolio Manager Agent. This agent reads the active instrument universe and tier assignments from Redis (managed by the Supervisor). Its system prompt encodes: position sizing rules ($50 max risk per trade on $5,000), Rule 1 enforcement (never exceed cash balance), tier-based signal priority (Tier 1 > Tier 2 > Tier 3), capital allocation (max 70% equities, max 30% BTC/USD, max 3 concurrent positions), sector correlation checks, drawdown-based tier disabling, and BTC fee awareness. Use few-shot examples of correct decision-making embedded in the prompt.

**Days 23–25**: Build the Supervisor Agent and integrate all five agents through OpenClaw's multi-session architecture. Implement circuit breakers with tier-based disabling, the daily end-of-day review process, and the learning loop. Implement the two monthly jobs: universe re-validation (re-backtest all instruments on rolling 12-month data, promote/demote between tiers) and universe discovery (scan new candidates from Alpaca's asset list, add passes as Tier 3 probationary). Populate the initial `trading:universe` Redis key with the 17 validated instruments and their tier assignments.

### Stage 3: Paper Trading Validation (Weeks 4–10, approximately 6 calendar weeks)

This is the long pole in the schedule and it cannot be compressed. The complete system must run on Alpaca paper trading for a minimum of 6 weeks of market exposure. **Critical: the system simulates the $5,000 capital constraint** even though the paper account has $100,000. The Trade Executor maintains a `trading:simulated_equity` value in Redis starting at $5,000, and all position sizing, risk calculations, and drawdown thresholds are computed against this virtual balance — not the paper account's actual equity. This ensures paper trading results accurately reflect live performance.

Track every metric: win rate per instrument, average win/loss ratio, Sharpe ratio, maximum drawdown, per-instrument profit factor, LLM API costs, and cross-instrument correlation (are signals clustering in the same sectors). The paper trading period must include at least one volatile market event to test the circuit breakers.

**Telegram notifications** are active during paper trading — Dan receives real-time trade alerts on every entry and exit, daily summaries at 4:15 PM ET, weekly reports Saturday mornings, and critical alerts immediately. This gives Dan full visibility into the system's behavior without needing to check dashboards.

Expected performance degradation from backtest to paper trading is 25–50%. A backtested Sharpe of 1.5 should produce a paper trading Sharpe of 0.75–1.1 in live market conditions. If paper trading results fall below 50% of backtest expectations, return to Stage 1 and revise strategies.

During this period, measure actual LLM token consumption and costs. Optimize prompts to reduce token usage without sacrificing decision quality. This is also when the Supervisor Agent begins building its library of learned patterns — what kinds of market conditions cause specific strategies to fail, which news events are actually material versus noise, and how PDT constraints affect strategy selection throughout the week.

### Stage 4: Live Deployment with Real Capital (Weeks 11–14)

Fund the Alpaca account with $5,000. Remove the `SIMULATED_CAPITAL` cap in the Trade Executor — `account.equity` becomes the source of truth. Begin live trading at 50% of paper-trading position sizes for the first two weeks to account for the difference between simulated and real execution. Telegram notifications continue identically — trade alerts, daily/weekly/monthly summaries, and critical alerts.

The capital management strategy with $5,000 in a 1x margin account (no leverage) is simpler than originally planned because the system runs a single strategy (RSI-2) that never consumes day trades. The full $5,000 of buying power is available at all times for new positions (minus the value of currently held positions). All 3 PDT day trades are reserved for emergency exits — the system should never need them under normal operation since all RSI-2 positions are swing trades held 2–5 days. A typical week allocates up to $3,500 to equity positions (from the 17-instrument universe — max 2 concurrent equity positions) and up to $1,500 to BTC/USD (max 1 position). BTC round-trips cost 0.30–0.50% in fees, so the system only enters BTC when the expected swing comfortably exceeds this threshold.

### Stage 5: Scaling and Optimization (Months 4–12+)

As the account grows, the system evolves. From $5,000 to $10,000, run 2–3 trades per day with $50–$100 risk per trade, focusing on consistency over aggression. From $10,000 to $25,000, increase to 3–4 simultaneous positions, add sector rotation signals, and upgrade the Portfolio Manager from the budget model to Claude Sonnet for better reasoning (the additional cost is justified by better decision quality at this capital level). Above $25,000, consider adding defined-risk options (buying calls or puts, never selling naked options), increase LLM sophistication by adding bull/bear debate between sub-agents, and explore additional strategies.

**Realistic timeline to $25,000**: At a 2% monthly compound return (ambitious but achievable for a well-validated system), $5,000 reaches $25,000 in approximately 17 months. At 3% monthly, approximately 12 months. At 1% monthly, approximately 27 months. These returns are not guaranteed.

---

## 5. Account Constraints: 1x Margin, PDT Rule, and How They Shape Everything

### The Critical Discovery: Alpaca Has No Cash Accounts

Alpaca does not offer cash accounts. All Alpaca accounts are margin accounts. This single fact, discovered during Phase 1 research, dramatically changes the constraint model — mostly for the better.

With $5,000 deposited, Alpaca provides a regular margin account with 2x overnight buying power by default. This means the broker would let you buy $10,000 worth of stock, borrowing $5,000. If those stocks dropped 60%, you would owe money — violating Rule 1. The solution is to configure the account to behave like a cash account while retaining the benefits of a margin account: set the margin multiplier to 1x (either through Alpaca support or by enforcing it in code) and disable shorting. This gives you the debt protection of a cash account with the settlement flexibility of a margin account.

### Why This Is Better Than a Cash Account

In a margin account, even at 1x buying power, the broker handles T+1 settlement gaps as normal business. You can sell Stock X at 10:00 AM and immediately use those proceeds to buy Stock Y at 10:05 AM, even though X's sale will not settle until tomorrow. The broker covers the one-day gap. This means good faith violations and free-riding violations — which are cash-account concepts — simply do not apply.

### The Constraint That Actually Matters: Pattern Day Trader Rule

With settlement eliminated as a concern, the binding constraint for equity trading becomes the Pattern Day Trader (PDT) rule. An account is flagged as PDT if it executes 4 or more day trades within any rolling 5-business-day window. A "day trade" is defined as opening and closing the same position within the same calendar day. Once flagged, the account must maintain $25,000 minimum equity or face trading restrictions.

With $5,000 in the account (well below $25,000), being flagged as PDT would effectively freeze equity trading. Therefore, the system must stay at or below 3 day trades per rolling 5-day window. Alpaca provides built-in PDT protection that rejects orders with HTTP status 403 if they would trigger PDT designation — this broker-level enforcement serves as a safety net underneath the Trade Executor's own tracking.

The practical impact: the system can execute unlimited swing trades (positions held overnight or longer) on equities, but only 2–3 same-day round-trips per week. The recommended approach is to reserve 1 day trade at all times for emergency stop-loss exits, leaving all 3 day trades reserved for emergency stop-loss exits. Since the system's sole strategy (RSI-2 mean reversion) holds positions for 2–5 days, day trades should never be consumed under normal operation.

### Crypto Is PDT-Exempt but Not Fee-Free

Cryptocurrency is not classified as a security and is not subject to the PDT rule. The system can execute unlimited crypto round-trips per day. This makes crypto the primary venue for intraday trading while equity strategies focus on swing trades.

However, Alpaca charges crypto trading fees: 0.15% for maker orders (limit orders that add liquidity) and 0.25% for taker orders (market orders that remove liquidity). A round-trip costs approximately 0.30–0.50% depending on order type. This means crypto strategies need to generate at least 0.50% return per round-trip to be profitable after fees. The Portfolio Manager must include this fee drag in every crypto trade's risk/reward calculation, and the Trade Executor should prefer limit orders over market orders whenever timing permits.

### How Capital Flows in Practice

With $5,000 in a 1x margin account, the full balance is available for trading at all times (minus the value of open positions). There is no waiting for settlement. A typical week with 17 instruments might look like this: Monday evening, RSI-2 triggers on QQQ and GOOGL simultaneously — the Portfolio Manager approves QQQ (Tier 1, higher priority) and GOOGL (Tier 2) for $1,500 and $1,200 respectively. Tuesday, RSI-2 triggers on BTC/USD — enter $1,300 (the remaining cash after two equity positions). Wednesday, QQQ's RSI-2 crosses above 60 — exit for +0.9%. Thursday, NVDA's RSI-2 drops below 5 (aggressive entry zone in an uptrend) — enter $1,500 with the freed capital. Friday, GOOGL closes above previous day's high — exit for +0.6%. BTC held through the weekend. Monday, BTC RSI-2 > 60 — exit for +1.8% gross, +1.4% net after fees. NVDA still held. Zero day trades consumed. With 10+ trades per month across the universe, the system stays active and the Supervisor's learning loop has continuous data to learn from.

### Rule 1 Enforcement: Defense in Depth

Rule 1 (never expose Dan to debt) is enforced at two levels. First, request 1x margin configuration from Alpaca support during account setup, preventing the broker from extending leverage. Second, the Trade Executor enforces it in code by using `float(account.cash)` as the maximum order value rather than `float(account.buying_power)`, which with 2x margin could be double the actual cash. The Supervisor Agent verifies on every startup that cash is not negative and that no positions were opened with borrowed funds. Even if the broker configuration fails, the code-level enforcement prevents margin usage.

---

## 6. The Signal Pipeline: From Raw Data to Trade Decisions

The system runs one strategy (RSI-2 mean reversion) across a dynamic, tiered universe of 17+ instruments. The instrument list is managed by the Supervisor Agent through monthly re-validation and discovery scans, stored in Redis, and read by all other agents on every cycle. This simplification — driven by backtesting results that eliminated ORB, crypto momentum, and ETH/USD — produces a clean, auditable pipeline that scales by adding instruments rather than strategies.

**Stage 1 — Regime Detection via ADX(14) on SPY.** ADX below 20: ranging market, use conservative RSI-2 entry threshold (below 10). ADX above 25 with +DI > -DI: confirmed uptrend, switch to aggressive RSI-2 entry (below 5) for faster re-entry on deeper dips. ADX above 25 with -DI > +DI: confirmed downtrend, reduce position sizes by 50% on all equity instruments (mean reversion still works in downtrends but with reduced edge). ADX declining from above 40: trend fading, tighten stops to 1.5x ATR.

**Stage 2 — Signal Generation (identical rules across all instruments).** Conservative (default): entry when RSI-2 closes below 10 and Close > 200-day SMA; exit when RSI-2 > 60 or Close > previous day's high or after 5 trading days. Aggressive (when ADX > 25 uptrend): entry when RSI-2 closes below 5; exit when Close > 5-period SMA. Stop-loss: 2x ATR(14) below entry for all instruments. For BTC/USD: same rules, same 200-day SMA trend filter, but the Portfolio Manager deducts 0.40% round-trip fees when evaluating expected returns.

**Stage 3 — ATR-Based Stops.** All instruments use 2x ATR(14) on daily bars. ATR multipliers adjust dynamically with regime: tighten to 1.5x when ADX < 20 (ranging), widen to 2.5x when ADX > 40 (strong trend).

**Stage 4 — Validation.** News filter: suppress new equity entries within 2 hours of high-impact events (Fed, CPI, employment). Whipsaw detection: if a symbol had an entry + stop-loss within 24 hours, block re-entry for 24 hours. Correlation check: no more than 2 positions in the same sector simultaneously. BTC/USD fee threshold: reject BTC signals where the expected gain is less than 0.60% (0.40% fees + 0.20% buffer). Tier priority: when multiple instruments signal simultaneously, Tier 1 instruments take priority over Tier 2 over Tier 3. If all position slots are filled with lower-tier holdings and a Tier 1 signal arrives, the weakest lower-tier position may be closed to make room.

The Supervisor Agent monitors rolling 30-day performance per instrument. Instruments are assigned to three tiers based on backtested and live performance: Tier 1 (core, PF ≥ 2.0, WR ≥ 70% — always active), Tier 2 (standard, PF ≥ 1.5 — disabled during 10%+ drawdowns), Tier 3 (marginal, PF ≥ 1.3 — active only when higher tiers are idle). Instruments falling below Tier 3 thresholds are disabled. The Supervisor runs two monthly jobs to keep the universe healthy: re-validation (1st of month — re-backtests everything on rolling 12-month data, promotes/demotes between tiers) and discovery (15th of month — scans new candidates from Alpaca's 12,000+ tradeable assets, adds passes as Tier 3 probationary, capped at 5 per month).

---

## 7. Risk Management That Preserves the $5,000

At $5,000, the math of drawdowns is still punishing but significantly more workable than at $1,000. A 20% drawdown ($1,000 loss) requires a 25% gain to recover. A 50% drawdown requires 100%. The system must make catastrophic loss essentially impossible through layered defenses.

The fixed-fractional position sizing method at 1% risk per trade means risking $50 maximum per trade. With a $2 stop-loss distance on a stock, that allows 25 shares. With a $5 stop-loss distance, 10 shares. This feels conservative, and it is intentionally so — it means surviving 50 consecutive losing trades before losing half the account, which provides an enormous runway for the system to learn and improve. Note that on expensive instruments like SPY (~$550), Rule 1 (never exceed cash balance) naturally caps position sizes below what the 1% risk formula would allow, providing an additional safety margin.

The daily loss limit is set at 3% of the account ($150 on $5,000). After hitting this limit, the system halts for the remainder of the trading day. Maximum simultaneous positions: 3 at this account size, ideally diversified across asset classes (no more than 2 equity and 1 crypto at any time). The Portfolio Manager checks whether a proposed new position is highly correlated with existing holdings — if both existing positions are in tech, a new tech entry gets its size reduced by 50%.

The tiered drawdown response protocol integrates with the instrument tier system. At 5% drawdown ($250 loss from peak), continue trading normally but the Supervisor flags underperforming instruments for review and reduces Tier 3 position sizes. At 10% drawdown ($500), reduce all position sizes by 50% (from 1% to 0.5% risk per trade), disable all Tier 2 and Tier 3 instruments — only the 6 Tier 1 instruments remain active (SPY, QQQ, NVDA, XLK, XLY, XLI). At 15% drawdown ($750), cut to 25% of normal position sizes on the remaining Tier 1 instruments and disable BTC/USD. At 20% drawdown ($1,000 loss from peak), halt all trading — no new positions opened until Dan manually approves. These thresholds are enforced by the Supervisor Agent in deterministic code — never left to LLM judgment.

**Operational safeguards**: All positions receive server-side stop-loss orders via Alpaca's API immediately upon entry, ensuring the stop executes even if the system loses connectivity. If the LLM API (Together.ai or Anthropic) becomes unreachable, the system falls back to code-only operation — the Trade Executor and its risk rules are entirely code-based and never depend on LLM availability. On every startup and every 15 minutes during trading hours, the Trade Executor verifies: cash ≥ 0, `pattern_day_trader == false`, `trading_blocked == false`, and no unrecognized positions exist. Any check failure triggers an immediate halt and alert via Telegram.

---

## 8. Which LLM Models to Use and Why

Model selection is one of the most consequential economic decisions in this system. The agents collectively make approximately 15–25 LLM calls per day, each with varying complexity requirements. The goal is to use the cheapest model that can reliably perform each task, escalating to more expensive models only when the decision quality justifiably demands it.

### The Available Models Across Your Three Providers

**Anthropic** offers Claude Haiku 4.5 at approximately $0.80 per million input tokens and $4.00 per million output tokens — a strong mid-tier option with good reasoning capabilities. Claude Sonnet 4 at $3.00/$15.00 is excellent for complex reasoning tasks. Claude Opus 4 at $15.00/$75.00 is the most capable but prohibitively expensive for routine trading operations.

**OpenAI** offers GPT-4o-mini at $0.15 per million input tokens and $0.60 per million output tokens — the cheapest viable option from a major provider. GPT-4o at $2.50/$10.00 provides strong general-purpose capability. GPT-4.1 at $2.00/$8.00 is a newer and slightly cheaper alternative to 4o with a million-token context window.

**Together.ai** provides access to open-weight models at extremely competitive prices. GPT-OSS 120B is the standout option — it is OpenAI's open-weight 117-billion parameter Mixture-of-Experts model that activates only 5.1 billion parameters per forward pass, priced at just $0.15 per million input tokens and $0.60 per million output tokens. It supports native chain-of-thought reasoning with configurable effort levels (low, medium, high), native function calling/tool use, and a 128K context window. This makes it ideal for trading agents: the Screener can run with low reasoning effort for quick news classification while the Portfolio Manager can use high effort for complex decisions — all on the same model at the same price. DeepSeek V3.1 is also available at competitive rates for bulk processing, and at the absolute budget end, Gemma 3n E4B costs just $0.03 per million tokens but is only suitable for the simplest classification tasks.

### Recommended Model Assignment by Agent

**Trade Executor Agent**: No LLM needed. Pure code. Cost: $0.

**Screener Agent** (news catalyst evaluation, 7 calls/day): Use **GPT-OSS 120B via Together.ai** at $0.15/$0.60. This task requires reading a news headline or summary and outputting a structured JSON assessment of whether it is a material catalyst. GPT-OSS 120B handles this reliably with its native function calling and structured output support, at a fraction of the cost of proprietary models. Run with low reasoning effort to minimize token usage on these straightforward classification tasks. Estimated daily cost: approximately $0.02.

**Watcher Agent** (news materiality assessment, 2–5 calls/day): Use **GPT-OSS 120B via Together.ai**. Similar task profile to the Screener's LLM calls, also at low reasoning effort. Estimated daily cost: approximately $0.01.

**Portfolio Manager Agent** (position sizing and portfolio reasoning, 3–8 calls/day): This is the one agent where model quality directly impacts financial outcomes. Use **GPT-OSS 120B via Together.ai with high reasoning effort** as the primary model. The configurable chain-of-thought reasoning at high effort gives this model strong analytical capability for evaluating correlations between positions, assessing sector exposure, and making nuanced risk/reward judgments — at a tiny fraction of what Claude Sonnet would cost. For particularly high-stakes decisions (involving more than 20% of the account or during extreme volatility), escalate to **Claude Sonnet 4** for its superior reasoning. Estimated daily cost: approximately $0.05–$0.15 (with occasional Sonnet escalation adding $0.10–$0.30 on some days).

**Supervisor Agent** (end-of-day review, 1–2 calls/day): Use **GPT-OSS 120B via Together.ai with high reasoning effort** for end-of-day analysis where it evaluates the full day's performance and generates learning insights. The chain-of-thought reasoning is particularly valuable here as the Supervisor needs to reason through what worked and what did not. Estimated daily cost: approximately $0.03–$0.05.

### Total Monthly LLM Cost Estimate

| Agent | Model | Calls/Day | Monthly Cost |
|-------|-------|-----------|-------------|
| Screener | GPT-OSS 120B (Together, low effort) | ~7 | ~$0.60 |
| Watcher | GPT-OSS 120B (Together, low effort) | ~3 | ~$0.30 |
| Portfolio Manager | GPT-OSS 120B (Together, high effort) | ~5 | ~$2.50 |
| Supervisor | GPT-OSS 120B (Together, high effort) | ~2 | ~$1.00 |
| Sonnet escalation | Claude Sonnet 4 (Anthropic, ~2x/week) | ~0.3 | ~$2.00 |
| **Total** | | **~17** | **~$6.40** |

This is dramatically cheaper than the previous estimate because GPT-OSS 120B's pricing ($0.15/$0.60) undercuts most alternatives while delivering strong reasoning through its configurable chain-of-thought system. With prompt caching, actual costs could drop to approximately **$3–$5/month**. At $5,000 capital, this requires just **0.06–0.10% monthly return** to cover — essentially negligible. This economic headroom means the system can afford to make more LLM calls for better decision quality without worrying about eating into profits.

### The Budget Alternative

If you want to go even cheaper, running GPT-OSS 120B at low reasoning effort for *all* agent tasks (including Portfolio Manager and Supervisor) drops the total to approximately **$1.50–$2.50/month**. The tradeoff is that the Portfolio Manager and Supervisor lose the deeper chain-of-thought reasoning that the high effort setting provides, which may lead to more superficial analysis on complex decisions. However, even at low effort, GPT-OSS 120B with its native function calling is a capable model for structured trading tasks.

Alternatively, GPT-4o-mini ($0.15/$0.60) from OpenAI directly offers nearly identical pricing to GPT-OSS 120B but without the configurable reasoning depth. It is a viable fallback if Together.ai experiences availability issues.

### When to Escalate Models

The system should include logic to escalate from GPT-OSS 120B (high effort) to Claude Sonnet 4 for specific Portfolio Manager decisions. Escalation triggers include decisions involving more than 20% of the account balance, decisions during extreme market volatility (VIX above 30), decisions where the GPT-OSS 120B response has low confidence or internal reasoning contradictions, and the first trade in a new asset class or strategy. This selective escalation might add $2–$4/month but could prevent the single bad decision that costs hundreds. The Supervisor Agent's end-of-day review should also track whether Sonnet-escalated decisions outperform GPT-OSS-only decisions — if the difference is negligible, reduce escalation frequency to save costs; if it is significant, consider escalating more often.

---

## 9. OpenClaw as the Orchestration Layer

OpenClaw is not just a viable option for orchestrating this system — it is arguably the ideal fit, better suited than LangGraph, CrewAI, or a custom-built solution. Here is why, and how it works.

### What OpenClaw Is

OpenClaw is a free, open-source (MIT license) AI agent framework created by Peter Steinberger and released in late 2025. It is designed to build, run, and orchestrate autonomous agents that can take real actions — not just chat. The architecture splits into two layers: the Gateway (a WebSocket control plane handling sessions, presence, configuration, cron jobs, webhooks, and channel routing) and Pi (the underlying agent runtime that provides core tools like read, write, edit, and bash). OpenClaw runs locally on your machine or a VPS, connects to any LLM provider (Anthropic, OpenAI, Together.ai, DeepSeek, local models via Ollama), and is controlled via messaging apps (Telegram, Discord, Slack, WhatsApp) or API.

### Why OpenClaw Fits This Project

**Multi-agent routing is built in.** OpenClaw supports routing different channels, accounts, and peers to isolated agents, each with their own workspace and session. Each of our five trading agents runs as a separate OpenClaw session with its own model configuration, thinking level, and skills. This is not a bolted-on feature — it is core to the architecture.

**Cross-agent communication works natively.** OpenClaw provides `sessions_list` (discover active sessions and their metadata), `sessions_history` (fetch transcript logs from another session), and `sessions_send` (message another session and optionally wait for a reply). This means the Screener Agent can publish its watchlist to the shared state and notify the Watcher Agent directly, the Portfolio Manager can request the current PDT counter and cash balance from the Executor, and the Supervisor can query any agent's recent activity log.

**The skills system maps perfectly to trading knowledge.** Each agent's trading knowledge (indicator rules, risk parameters, strategy specifications) lives in SKILL.md files within that agent's workspace. When the Supervisor Agent identifies a needed improvement — say, tightening the RSI threshold from 10 to 8 based on recent performance data — it can literally edit the relevant agent's skill file, and the change takes effect on the next invocation. This is the mechanism by which the system "learns from mistakes" in a persistent, auditable way.

**Cron scheduling is native.** The Screener Agent's end-of-day scan at 4:15 PM Eastern and the BTC/USD check every 4 hours are configured directly in OpenClaw's cron system. The Supervisor's monthly re-validation (1st) and discovery (15th) jobs use the same scheduling. No external scheduler needed.

**The messaging interface provides human oversight.** You can monitor and intervene via Telegram or Discord. Send a message to check current positions, override a trade, pause the system, or ask the Supervisor for a performance summary. This is the "kill switch" and the human-in-the-loop interface.

**Sandboxing for safety.** OpenClaw supports running non-main sessions inside per-session Docker containers with whitelisted tools and restricted filesystem access. Given that these agents interact with real money via broker APIs, sandboxing prevents a misbehaving agent from accessing credentials or resources it should not touch. Nvidia's NemoClaw add-on provides additional enterprise-grade security (prompt injection scanning, data leakage prevention) for production deployments.

### What OpenClaw Cannot Do (and What Fills the Gaps)

OpenClaw is the orchestration and agent management layer, but it does not replace the need for a data pipeline, a state store, or a trading engine. Redis still handles the shared state (positions, signals, PDT counter, agent status) because OpenClaw's session state is conversational, not structured-data-optimized. The Alpaca SDK handles actual order submission and market data streaming. TimescaleDB handles historical data storage. NumPy and pandas handle indicator computation. OpenClaw ties all of these together by giving each agent the ability to invoke Python scripts, query Redis, call the Alpaca API, and coordinate with other agents — all through its skills system and bash/code execution tools.

### OpenClaw vs. LangGraph, CrewAI, or Custom Build

LangGraph is the strongest pure-code alternative and would be the choice if you wanted a Python-native system without the messaging-app interface. Its deterministic flow control and state machine abstraction are well-suited to financial applications. However, it requires building your own scheduling, monitoring, UI, and deployment infrastructure. CrewAI is simpler but less flexible — its role-based agent model works for straightforward workflows but lacks OpenClaw's session branching and dynamic tool creation capabilities. A custom build gives maximum control but maximum development effort.

OpenClaw wins for this project because it provides the orchestration, scheduling, communication, monitoring, and human interface out of the box, letting you focus on the trading-specific logic rather than infrastructure plumbing. The skills system means the agents' trading knowledge is stored as editable, version-controlled documents — exactly what the Supervisor needs to implement its learning loop.

---

## 10. Broker Recommendation: Why Alpaca

Alpaca is the clear choice at this capital level and for this use case. It charges zero commissions on stocks, ETFs, and options. There is no account minimum. It provides excellent paper trading with real-time market data at no cost, including full PDT rule simulation (rejected orders return HTTP 403, exactly as in live trading). It supports fractional shares from $1 via API, which matters when $50 of risk might only buy 3 shares of a $100 stock. It supports crypto trading on the same API (though subject to 0.15%/0.25% maker/taker fees). The account API provides `cash`, `buying_power`, `daytrade_count`, and `pattern_day_trader` fields that give the Trade Executor everything it needs for PDT tracking and Rule 1 enforcement. It also provides a real-time news WebSocket stream on the free plan, which the Screener Agent can use for catalyst detection. The Python SDK (`alpaca-py`) is well-documented and actively maintained. Alpaca was named "Best Broker for Algorithmic Trading in 2026" by BrokerChooser and was built specifically for API-first algorithmic trading.

Interactive Brokers is the stronger platform for larger accounts (above $25,000) but introduces complexity (a Java gateway is required for the full TWS API) and data subscription costs ($10–$30/month) that are disproportionate to a $5,000 account. The TD Ameritrade API was permanently shut down in May 2024 when Schwab completed the migration, and Schwab's replacement API has a cumbersome approval process. Webull launched an official API in 2025 but its ecosystem is immature. Tradier is strong for options trading but lacks crypto support.

For dedicated crypto trading alongside Alpaca equities, Kraken offers the best fee-to-quality ratio at 0.16% maker and 0.26% taker fees with a robust API. Coinbase Advanced Trade is the safest US option but charges 0.60% maker and 0.80% taker at low volumes — too expensive for frequent small-account trading.

---

## 11. The Economics Must Work or Nothing Else Matters

The entire system lives or dies on this equation: monthly trading profit must exceed monthly operating costs. Here is the realistic cost breakdown at different levels of system sophistication.

**Budget Setup**: LLM API costs of $1.50–$2.50/month (using GPT-OSS 120B at low reasoning effort for everything), $0 for Alpaca commissions and data, $0 for compute if running locally. Total: approximately $1.50–$2.50/month. At $5,000 capital, this requires 0.03–0.05% monthly return to break even. That is less than $2.50 per month — virtually nothing.

**Recommended Setup**: LLM API costs of $3–$6/month (GPT-OSS 120B with mixed reasoning effort levels as described in the model section, with occasional Claude Sonnet escalation), $0 for Alpaca, $0 for local compute or $5–$10 for a small VPS if you want it running 24/7. Total: approximately $3–$16/month. This requires 0.06–0.32% monthly return, which is extremely modest.

**Full-Featured Setup** (likely appropriate once the account exceeds $15,000): LLM API costs of $10–$25/month (more Sonnet escalation calls, higher call frequency, perhaps adding a second Screener pass for crypto), $9 for Alpaca SIP real-time data feed, $10–$20 for a VPS. Total: approximately $29–$54/month. This requires 0.58–1.08% monthly return on $5,000, or much less as the account grows — at $15,000 it is only 0.19–0.36%.

The critical insight: the system should start at the budget level and upgrade incrementally as it proves profitability. There is no reason to spend $50/month on infrastructure for a system that has not yet demonstrated it can generate $50/month in profit.

---

## 12. Legal and Regulatory Considerations

There is no law, regulation, or SEC/FINRA rule prohibiting individual retail investors from using AI, LLMs, or autonomous agents to make trading decisions. FINRA's 2025 Annual Regulatory Oversight Report confirms that technologically neutral rules apply to AI in the same manner as any other technology. Alpaca explicitly supports and encourages API-based algorithmic trading — it is their core business model. No registration, license, or filing is required for personal algorithmic trading.

The system must avoid three categories of potential violations. **Market manipulation** (spoofing, layering, wash trading) can occur accidentally if an algorithm rapidly places and cancels orders or trades the same security back and forth. Prevention: never place orders without execution intent, maintain reasonable order-to-trade ratios, and code explicit blocks against wash trading. **Pattern Day Trader designation** is the most relevant day-to-day regulatory risk — the system must stay below 4 day trades per rolling 5-business-day window. Alpaca's built-in PDT protection (403 rejection) provides a broker-level safety net, and the Trade Executor maintains its own counter as the primary defense. **Tax compliance** is the most complex area.

The **wash sale rule** can devastate frequent traders. If you sell a security at a loss and repurchase the same or a "substantially identical" security within 30 days before or after the sale, the loss is disallowed for tax purposes — it gets added to the cost basis of the replacement shares instead. For an algorithmic system trading the same few symbols repeatedly, wash sales can cascade: a real-world case showed a trader with $30,000 in actual net profit owing taxes on $195,000 because of disallowed wash sale losses across thousands of trades.

**Critical 2025 change**: Starting with the 2025 tax year, the Infrastructure Investment and Jobs Act extended wash sale rules to digital assets including cryptocurrency. Prior to 2025, crypto was exempt because the IRS classified it as property rather than securities. This means our BTC/USD trading is now subject to the same wash sale complications as our equity strategies — repeatedly trading BTC at a loss and re-entering within 30 days triggers wash sales. Many older resources incorrectly state that crypto is wash-sale-exempt; this is no longer true.

The **Section 475(f) Mark-to-Market election** is effectively mandatory for this system. It allows active traders to treat all positions as if they were sold at fair market value on the last business day of the year, converting all gains and losses to ordinary income/loss. This completely eliminates wash sale tracking (IRC Section 1091 does not apply to traders using mark-to-market accounting), removes the $3,000 annual capital loss limitation, and enables full business expense deductions (API costs, data subscriptions, software). The downside — losing preferential long-term capital gains tax rates — is irrelevant for a system that primarily generates short-term gains (short-term gains are already taxed at ordinary rates).

**475(f) election timeline and lock-in**: For 2026 trading, the election must be filed by April 15, 2026, attached to the 2025 tax return or extension. This is a hard deadline — filing a tax return extension does NOT extend the election deadline. Under Revenue Procedure 2025-23 (issued in 2025), once the election is made, revocation is subject to a five-year lock-in period. Revoking within five years requires IRS consent, the non-automatic change procedures, and a user fee of approximately $13,225. This means the election should be considered carefully — but for an automated trading system that trades the same symbols repeatedly and generates exclusively short-term gains, the benefits (eliminating wash sales, unlimited loss deductions) overwhelmingly outweigh the costs.

**Tax action items**: (1) Consult a tax professional experienced in trader taxation before live trading begins to confirm Trader Tax Status qualification and file the 475(f) election on time. (2) The system must log every trade with complete details (date, symbol, quantity, prices, P&L) in TimescaleDB for IRS record-keeping requirements. (3) On December 31, all open positions are treated as if sold at fair market value — the system logs these "deemed sales" automatically. (4) The Supervisor Agent tracks cumulative annual P&L and alerts Dan when estimated quarterly tax payments may be owed (due April 15, June 15, September 15, January 15).

---

## 13. Common Failure Modes This System Must Survive

**Overfitting** is the primary killer of algorithmic trading systems. In a study of 888 algorithmic strategies, common backtest performance metrics had almost zero predictive value for live performance (R-squared below 0.025). Testing just seven strategy variations can produce one with an annualized Sharpe above 1.0 even when the true expected return is zero — purely by chance. The system combats this through walk-forward optimization (testing on data the model was not trained on), out-of-sample validation, and the principle that every strategy must have a logical economic hypothesis explaining why it works, not just a historical pattern.

**The backtest-to-live performance gap** is typically 1.5 to 2 times higher drawdowns and 50–75% of backtested returns in live trading. A strategy showing Sharpe 2.0 in backtesting will likely deliver Sharpe 1.0–1.5 live. Plan for this degradation from the start.

**Strategy decay** is inevitable. Markets evolve, new competitors enter, and the signals that worked last year may not work next year. The Supervisor Agent's end-of-day review process is the detection mechanism: when a strategy's rolling 30-day Sharpe drops below 0.5, flag it for review; below 0.0, automatically reduce its allocation. The system must treat strategies as having limited lifespans and continuously research replacements.

**Slippage** is the difference between the price you expected to get and the price you actually received. For liquid large-cap stocks and ETFs, slippage is typically 0.01–0.05% per trade. For smaller or less liquid stocks, it can be much higher. Every backtest must include realistic slippage assumptions, and the Supervisor should track actual vs. expected slippage to detect when execution quality degrades.

---

## 14. Tool and Framework Reference Guide

This section explains what every tool, framework, and platform referenced in this report actually is and does.

**Alpaca** is a technology company that provides a brokerage API specifically designed for algorithmic trading. Think of it as a broker that was built for robots rather than humans — instead of a website with charts and buttons, it provides a programming interface where your code can submit buy/sell orders, check account balances, stream real-time price data, and manage positions. It is commission-free for stocks, ETFs, and options, and also supports crypto trading. Their paper trading mode gives you a fake $100,000 account that behaves exactly like a real one, letting you test strategies without risking money.

**alpaca-py** is Alpaca's official Python SDK (Software Development Kit). It is a Python library that wraps Alpaca's REST and WebSocket APIs into convenient Python functions, so instead of making raw HTTP requests, you can write `trading_client.submit_order(...)` and it handles all the network communication details.

**Redis** is an in-memory data store that acts as an extremely fast shared scratchpad. In this system, it serves two purposes: first, as a key-value store where agents read and write shared data (the current watchlist, open positions, PDT counter, agent status); second, as a pub/sub message broker where agents can publish events ("new buy signal for AAPL") and other agents can subscribe to receive those events instantly. Because Redis stores everything in RAM rather than on disk, reads and writes happen in microseconds rather than milliseconds.

**TimescaleDB** is a time-series database built as an extension on top of PostgreSQL (the most popular open-source relational database). It is optimized for storing and querying data that is indexed by time — exactly the shape of trading data. Every trade, every price point, every agent decision goes into TimescaleDB with a timestamp, making it easy to query things like "show me all trades from last week where Strategy A was used and the result was a loss."

**QuantConnect/LEAN** is a cloud-based algorithmic trading platform (QuantConnect) built on an open-source trading engine (LEAN). LEAN is written in C# and can run locally or in the cloud. It provides backtesting infrastructure — meaning you feed it historical market data and your strategy code, and it simulates how your strategy would have performed over that historical period, accounting for things like transaction costs and slippage. QuantConnect adds a web IDE, community strategy library, and connections to 20+ brokerages for live trading.

**FreqTrade** is an open-source cryptocurrency trading bot written in Python. It provides a framework for defining trading strategies using technical indicators, backtesting those strategies against historical data, optimizing strategy parameters through hyperparameter search (trying thousands of parameter combinations to find the best ones), and executing live trades on crypto exchanges. It has a large community that shares and validates strategies.

**OpenClaw** is the open-source AI agent orchestration framework described in detail in Section 9. In brief: it lets you run autonomous AI agents on your computer that can take real actions (execute code, manage files, call APIs), coordinated through messaging apps, with built-in multi-agent routing, scheduling, and a skills system.

**LangGraph** is a Python framework (built by the LangChain team) for creating stateful, multi-agent applications using LLMs. It models agent workflows as directed graphs where nodes are agent actions and edges are decision points. It is mentioned in this report as the primary alternative to OpenClaw — LangGraph provides finer-grained programmatic control over agent orchestration but requires you to build your own scheduling, monitoring, and human interface.

**NumPy** is the fundamental Python library for numerical computing. In this system, it handles all the mathematical heavy lifting of computing technical indicators (RSI, MACD, Bollinger Bands, etc.) from raw price data. It is extremely fast because the actual computation happens in optimized C code underneath the Python interface.

**Docker** is a platform for running applications in isolated containers — lightweight virtual environments that bundle an application with all its dependencies. In this system, Docker is used to run Redis and TimescaleDB in containers (so you do not need to install them directly on your machine), and optionally to sandbox OpenClaw agent sessions for security.

**WebSocket** is a communication protocol that provides a persistent, two-way connection between a client (your code) and a server (Alpaca's market data feed). Unlike regular HTTP requests where you ask for data and get a response, a WebSocket connection stays open and the server pushes new data to you the instant it is available. This is how the Watcher Agent receives real-time price updates with minimal latency.

**Finnhub** is a financial data API provider that offers stock prices, company fundamentals, SEC filings, earnings calendars, and news in a developer-friendly REST API. The free tier provides 60 API calls per minute, which is sufficient for the Screener Agent's news and earnings calendar lookups.

---

## 15. Glossary of Financial and Technical Terms

**ATR (Average True Range)**: A measure of how much a security's price typically moves over a given period, accounting for gaps between trading sessions. If a stock has a 14-day ATR of $2.50, that means it typically moves about $2.50 per day. ATR is used in this system primarily for setting stop-loss distances — a stop set at 2x ATR below entry means the stop is placed at twice the stock's normal daily range below your purchase price, giving it enough room to absorb normal fluctuations without getting triggered by noise.

**Backtesting**: The process of testing a trading strategy against historical market data to see how it would have performed in the past. You feed the strategy's rules to a backtesting engine along with years of price data, and it simulates every trade the strategy would have made, tracking performance metrics. Backtesting is essential but dangerous — strategies that look great on historical data frequently fail in live markets due to overfitting, look-ahead bias, and the backtest-to-live performance gap.

**Bollinger Bands**: A technical indicator consisting of three lines plotted on a price chart: a middle line (typically the 20-day simple moving average), an upper band (middle line plus 2 standard deviations), and a lower band (middle line minus 2 standard deviations). When price touches or breaks below the lower band, the security may be oversold (potential buy signal). When it touches or breaks above the upper band, it may be overbought (potential sell signal). The width of the bands reflects volatility — wide bands indicate high volatility, narrow bands indicate low volatility.

**Circuit Breaker** (in this system's context): An automatic safety mechanism that halts or restricts trading when certain risk thresholds are exceeded. For example, "halt all trading if daily loss exceeds 3% of account value." Named by analogy to electrical circuit breakers that cut power to prevent damage.

**Drawdown**: The peak-to-trough decline in account value, usually expressed as a percentage. If your account grows from $5,000 to $6,000 and then falls to $5,400, the drawdown is $600/$6,000 = 10%. Maximum drawdown is the largest such decline over a given period and is one of the most important risk metrics — it tells you the worst pain you would have experienced.

**EMA (Exponential Moving Average)**: A type of moving average that gives more weight to recent prices, making it more responsive to new information than a simple moving average. A 20-day EMA responds faster to price changes than a 20-day SMA. Traders use EMAs to identify trend direction and potential support/resistance levels.

**Free-Riding Violation**: A specific type of cash account violation that occurs when you buy securities without having sufficient settled funds to cover the purchase, and then sell those same securities to generate the funds needed to pay for the original purchase. This is essentially using the proceeds of a sale to retroactively fund the purchase that generated those proceeds — a circular dependency that regulators prohibit. *Note: This does not apply to our system because Alpaca only offers margin accounts, not cash accounts. In a margin account, the broker handles settlement gaps as normal business, eliminating free-riding risk.*

**Gap (Gap Up / Gap Down)**: When a security opens at a significantly different price than its previous close, creating a visible gap on the price chart. A stock that closed at $50 and opens at $54 has "gapped up" 8%. Gaps often occur due to overnight news (earnings announcements, FDA decisions, geopolitical events). *The "gap-and-go" strategy (trading in the direction of the gap) was evaluated during Phase 2 research and found to have been largely arbitraged away — backtests on major indices showed near-zero edge after costs. It was replaced in this system by the Opening Range Breakout (ORB) strategy, which captures similar intraday momentum dynamics with significantly better documented performance.*

**GFV (Good Faith Violation)**: A specific type of cash account violation that occurs when you buy securities using unsettled funds (proceeds from a sale that has not yet settled) and then sell those newly purchased securities before the funds you used to buy them have settled. Three GFVs within a twelve-month period typically triggers a 90-day restriction to settled-cash-only trading. *Note: This does not apply to our system because Alpaca only offers margin accounts. In a margin account, the broker handles the settlement gap, so GFVs cannot occur. The relevant constraint for our system is the Pattern Day Trader rule instead.*

**Kelly Criterion**: A mathematical formula for determining the optimal bet size to maximize long-term growth rate. The formula is: Kelly % = W - [(1-W)/R], where W is the win rate and R is the win/loss ratio. In practice, traders typically use a fraction of the Kelly amount (half-Kelly or quarter-Kelly) because the full Kelly bet size produces uncomfortably large drawdowns.

**MACD (Moving Average Convergence Divergence)**: A trend-following momentum indicator calculated by subtracting the 26-period EMA from the 12-period EMA. The result is the "MACD line." A "signal line" (9-period EMA of the MACD line) is plotted on top. When the MACD line crosses above the signal line, it is considered a bullish signal; when it crosses below, bearish. MACD alone has a poor win rate (around 40%), but combined with RSI it achieves approximately 77%.

**Margin Account**: A brokerage account where the broker can lend you money to buy securities, using your existing holdings as collateral. This amplifies both gains and losses and can result in a margin call — a demand from the broker to deposit more money or sell positions — if your holdings decline in value. *In our system, we use a margin account (since Alpaca only offers margin accounts) but configure it with a 1x multiplier and enforce in code that orders never exceed actual cash balance. This gives us the settlement flexibility of a margin account (no GFVs, no free-riding violations) while the code-level enforcement prevents any actual margin borrowing — satisfying Rule 1 (no debt exposure). The PDT rule applies to all margin accounts under $25,000.*

**Mark-to-Market (Section 475(f))**: A tax election available to active traders that requires treating all positions as if sold at fair market value on the last day of the tax year. This eliminates wash sale complications, removes the $3,000 capital loss deduction limit, and converts all trading gains/losses to ordinary income/loss. For the 2026 tax year, the election must be filed by April 15, 2026, with the 2025 tax return. *Under Revenue Procedure 2025-23, the election is now subject to a five-year lock-in period — revocation within five years requires IRS consent and a fee of approximately $13,225. For our system, which generates exclusively short-term gains and trades the same symbols repeatedly (making wash sale avoidance impossible without the election), this lock-in is a non-issue.*

**Mean Reversion**: A trading strategy based on the statistical tendency of prices to return to their average over time. When a stock drops significantly below its average price, a mean-reversion strategy buys it, betting that it will bounce back toward the mean. The RSI-2 strategy used in this system is a classic mean-reversion approach — it buys when RSI-2 is extremely low (price is far below recent average) and sells when it normalizes.

**Momentum Trading**: A strategy that buys securities that are rising and sells securities that are falling, based on the empirical observation that price trends tend to persist in the short to medium term. *Momentum-based strategies (Opening Range Breakout, crypto 15-minute momentum) were evaluated and backtested during the strategy validation phase but eliminated from the system due to poor real-data performance. The system uses mean-reversion (RSI-2) exclusively, which is the opposite approach — buying when prices have fallen and selling when they recover.*

**Overfitting**: When a trading strategy is tuned so precisely to historical data that it captures noise (random fluctuations) rather than genuine signal (repeatable patterns). An overfitted strategy looks spectacular in backtesting but fails in live trading because the specific noise patterns it learned will not repeat. The most common cause is testing too many parameter combinations and cherry-picking the best result.

**Pattern Day Trader (PDT) Rule**: A FINRA regulation that designates an account as a "Pattern Day Trader" if it executes 4 or more day trades within any rolling 5-business-day window. A day trade is defined as opening and closing the same security position within the same calendar day. Once flagged, the account must maintain a minimum of $25,000 in equity or face trading restrictions. With our $5,000 account, being flagged as PDT would effectively freeze equity trading. The system manages this by limiting itself to 3 day trades per 5-day window, reserving one for emergency exits. Crypto trades are completely exempt from the PDT rule because cryptocurrency is not classified as a security. Alpaca provides built-in PDT protection that rejects orders (HTTP 403) before they would trigger PDT designation, serving as a broker-level safety net.

**PEAD (Post-Earnings Announcement Drift)**: An academically documented market anomaly where stocks that report positive earnings surprises tend to continue rising for days or weeks after the announcement, and stocks with negative surprises continue falling. The drift appears to persist because the market underreacts to earnings information initially and takes time to fully incorporate it.

**Position Sizing**: The process of determining how many shares or how much capital to allocate to a single trade. The fixed-fractional method used in this system sizes each position so that the maximum possible loss (entry price minus stop-loss price, multiplied by number of shares) equals a fixed percentage of the account (1% in this system, meaning $50 on a $5,000 account).

**Profit Factor**: The ratio of gross profits to gross losses over a series of trades. A profit factor of 1.5 means the strategy earned $1.50 for every $1.00 it lost. Anything above 1.0 is profitable; below 1.0 is unprofitable. Values above 3.0 in backtesting are suspicious and may indicate overfitting.

**RSI (Relative Strength Index)**: A momentum oscillator that measures the speed and magnitude of recent price changes on a scale from 0 to 100. Traditionally, RSI above 70 indicates overbought conditions (potential sell signal) and below 30 indicates oversold (potential buy signal). RSI-2 is a variant using a 2-period lookback that is more sensitive and is the basis of the mean-reversion strategy in this system — it triggers buy signals at extreme readings below 10 rather than the traditional 30.

**Settlement (T+1)**: The process of officially transferring ownership of securities and payment between buyer and seller. Since May 28, 2024, US equities settle on a T+1 basis, meaning one business day after the trade date. If you sell stock on Monday, the cash from that sale is officially settled and fully available on Tuesday. *In a cash account, this creates constraints because unsettled funds cannot be freely reused. However, in our system this is a non-issue: Alpaca's margin account (even at 1x multiplier) handles settlement gaps automatically. The broker extends temporary credit for the one-day settlement period, so proceeds from a sale are immediately available for new purchases. The constraint that matters for our system is the PDT rule, not settlement.*

**Sharpe Ratio**: A measure of risk-adjusted return, calculated as (Strategy Return - Risk-Free Rate) / Standard Deviation of Strategy Returns. A Sharpe of 1.0 means the strategy earns one unit of return for each unit of risk taken. A Sharpe of 2.0 is excellent. Most professional hedge funds target Sharpe ratios between 1.0 and 2.0. Sharpe ratios above 3.0 in backtesting are almost always indicative of overfitting.

**Slippage**: The difference between the expected price of a trade and the actual execution price. If you place a market order to buy a stock at $50.00 and the order fills at $50.03, that $0.03 is slippage. Slippage occurs because prices move between the time you decide to trade and the time the order reaches the exchange, and because the best available price may not have enough shares to fill your entire order.

**SMA (Simple Moving Average)**: The arithmetic mean of a security's price over a specified number of periods. A 200-day SMA is the average closing price over the last 200 trading days. SMAs are used to identify trends — price above the 200-day SMA generally indicates a long-term uptrend, while price below it indicates a downtrend.

**VWAP (Volume-Weighted Average Price)**: The average price of a security weighted by trading volume throughout the day. If a stock traded 1,000 shares at $50 and 2,000 shares at $51, the VWAP would be closer to $51 because more volume occurred there. Day traders use VWAP as a benchmark — buying below VWAP and selling above it suggests favorable execution relative to the day's average.

**Walk-Forward Optimization**: A backtesting methodology designed to prevent overfitting. Instead of optimizing strategy parameters on the entire dataset (which leads to overfitting), you optimize on a portion (the "in-sample" period, typically 70%) and then test the optimized parameters on the remaining data (the "out-of-sample" period, typically 30%). If the strategy performs well out-of-sample, it is more likely to work in live trading. Walk-forward efficiency (out-of-sample return divided by in-sample return) above 50% is generally considered acceptable.

**Wash Sale Rule**: An IRS rule that disallows claiming a tax loss on the sale of a security if you purchase a substantially identical security within 30 days before or after the sale. The disallowed loss is added to the cost basis of the replacement purchase rather than being lost entirely — but for frequent traders who repeatedly trade the same securities, wash sales can cascade and create enormous phantom tax liabilities that far exceed actual net profits. *Starting with the 2025 tax year, the wash sale rule was extended to cover cryptocurrency and digital assets (previously exempt). This makes the Section 475(f) mark-to-market election effectively mandatory for our system, as it completely eliminates wash sale complications for traders who qualify.*

**ADX (Average Directional Index)**: A trend strength indicator developed by Welles Wilder, operating on a scale from 0 to 100. ADX measures how strong a trend is, regardless of whether the trend is up or down. Readings below 20 indicate a weak or absent trend (ranging market). Above 25 indicates a strong trend. Above 50 indicates a very strong trend that may be approaching exhaustion. In our system, ADX serves as the master regime switch — it determines which strategies are allowed to fire on any given day. The companion indicators +DI and -DI provide trend direction (up or down). Standard period is 14.

**Opening Range Breakout (ORB)**: A day trading strategy that monitors price action during the first 15 minutes of the trading session, establishes the high and low of that period (the "opening range"), and enters a trade when price breaks above the range high (long) or below the range low (short). *ORB was evaluated as a secondary strategy but eliminated after backtesting. With proper regime filtering (ADX uptrend + minimum range size + VWAP), SPY produced only 3 valid trades in 12 months — the opening ranges on broad-market ETFs are too narrow (0.20–0.25% of price) for meaningful returns at our account size. Published ORB backtests showing strong results used futures with leverage or volatile individual stocks, which don't apply to our constraints.*

---

*This document incorporates findings from all four research phases and comprehensive real-data backtesting validation across 26 candidate instruments. The backtesting phase validated RSI-2 mean reversion on 17 instruments organized into three performance tiers, eliminated three alternative strategies (ORB, crypto 15-minute momentum, ETH/USD), and established an automated discovery mechanism for ongoing universe expansion. Combined backtested performance across the validated universe: 65–89% win rates, 1.35–6.04 profit factors, sub-3% max drawdowns, ~125 trades per year (10.4 per month), zero PDT consumption. The instrument universe is dynamic — the Supervisor Agent runs monthly re-validation and discovery scans against Alpaca's 12,000+ tradeable assets. Four companion documents contain the detailed research: phase1_market_microstructure_constraints.md, phase2_strategy_research_backtesting.md, phase3_signal_engineering_specification.md, and phase4_risk_economics_legal.md. This is not financial advice — it is an engineering specification for an experimental algorithmic trading system. All trading involves risk of loss, and the $5,000 seed capital should be considered money you are willing to lose entirely during the learning and validation period.*

v1.0.0
