# Phase 4 Research Deliverable: Risk Management, Economics, and Legal Framework

## Purpose

This document completes the research plan by establishing the risk management protocols that protect the $5,000 capital, the economic model that determines whether the system can sustain itself, and the legal/tax framework that governs how profits and losses are reported. This is the "operating manual" for the business side of the trading system.

---

## Part A: Risk Management Protocols

### Position Sizing: The Fixed-Fractional Method

The system uses fixed-fractional position sizing at 1% risk per trade. On a $5,000 account, this means $50 maximum risk per trade. The formula:

```
Position Size (shares) = Max Risk ($50) / (Entry Price - Stop Price)
```

Example: Buying SPY at $550 with a stop at $546 (2x ATR distance of $4):
- Position Size = $50 / $4 = 12.5 shares (round down to 12)
- Total position value = 12 × $550 = $6,600... but this exceeds our $5,000 cash.
- Adjusted: Maximum position = $5,000 / $550 = 9 shares (Rule 1 cap)
- Actual risk = 9 × $4 = $36 (0.72% of account — within the 1% limit)

This illustrates an important interaction between Rule 1 (never exceed cash) and the position sizing formula. On a $5,000 account, expensive stocks like SPY naturally have smaller positions due to the cash limit, even when the 1% risk formula would allow more shares. This is an additional safety feature — the system is inherently more conservative on expensive instruments.

### Why Not Kelly Criterion?

The Kelly Criterion calculates the theoretically optimal bet size to maximize long-term growth: Kelly % = W - [(1-W)/R], where W is win rate and R is win/loss ratio. For our RSI-2 strategy (75% win rate, average win 0.82% / average loss ~0.57%, R = 1.44):

Kelly % = 0.75 - (0.25 / 1.44) = 0.75 - 0.174 = 0.576 = 57.6%

A full Kelly bet would risk 57.6% of the account per trade — wildly aggressive and producing devastating drawdowns. Even half-Kelly (28.8%) or quarter-Kelly (14.4%) would be far too aggressive for a $5,000 account where the goal is learning, not maximum growth.

The fixed 1% risk method is deliberately conservative. It sacrifices theoretical growth rate for dramatically reduced drawdown risk. With 1% risk per trade, you can survive 50 consecutive losing trades before losing half the account. This survivability is the priority during the learning phase.

### Tiered Drawdown Response Protocol

These thresholds are enforced by the Supervisor Agent in deterministic code — never left to LLM judgment.

**5% drawdown ($250 loss from peak equity)**: Continue trading normally. The Supervisor Agent flags underperforming strategies for its end-of-day review but takes no automated action. This level of drawdown is normal and expected even for profitable strategies.

**10% drawdown ($500)**: Automatically reduce all position sizes by 50% (from 1% risk to 0.5% risk per trade). The Supervisor Agent conducts a strategy performance review, comparing each strategy's rolling 30-day metrics against its validation thresholds from Phase 2. Any strategy that has individually drawn down more than 15% is disabled.

**15% drawdown ($750)**: Cut to 25% of normal position sizes (0.25% risk per trade). Disable ORB entirely (the most aggressive strategy). Only RSI-2 mean reversion and crypto momentum remain active, and only on the highest-conviction signals.

**20% drawdown ($1,000 loss from peak)**: Halt all trading. The system enters "review mode" where no new positions are opened. The Supervisor Agent conducts a comprehensive analysis of what went wrong, the Portfolio Manager runs a full strategy performance assessment, and trading only resumes after Dan manually approves the revised strategy parameters. This is the nuclear option that protects against catastrophic loss.

### Daily Loss Limit

The system halts for the day after losing 3% of the account ($150 on $5,000). This prevents a single bad day from inflicting outsized damage. The daily loss limit is calculated at the start of each trading day based on that morning's equity:

```python
daily_loss_limit = float(account.equity) * 0.03
start_of_day_equity = float(account.equity)

# Check after every trade
current_equity = float(account.equity)
daily_pnl = current_equity - start_of_day_equity

if daily_pnl <= -daily_loss_limit:
    halt_all_trading_until_next_session()
    supervisor.log("Daily loss limit hit: ${:.2f}".format(daily_pnl))
```

### Maximum Concurrent Positions

At $5,000 capital, the system should hold no more than 3 positions simultaneously across all strategies. This prevents overconcentration and ensures that a single bad event (a flash crash, a news shock) cannot devastate the portfolio even if it affects multiple holdings.

The positions should be diversified across asset classes and strategies when possible: ideally no more than 2 equity positions and 1 crypto position at any given time, though the exact split depends on signal availability.

### Correlation Monitoring

The Portfolio Manager Agent should check whether a proposed new position is highly correlated with existing positions. If all current positions are in the same sector (e.g., two tech stocks plus a long BTC position), the effective diversification is minimal. A simple heuristic: if a new position is in the same sector as an existing position, reduce its size by 50%.

---

## Part B: Economic Model

### Complete Monthly Cost Breakdown

| Cost Category | Budget Level | Recommended Level | Notes |
|--------------|-------------|-------------------|-------|
| GPT-OSS 120B (Together.ai) | $1.50–$2.50 | $3.00–$5.00 | Low effort for routine tasks, high effort for complex decisions |
| Claude Sonnet 4 escalation | $0 | $1.00–$2.00 | 2–3 escalation calls per week for high-stakes decisions |
| Alpaca equities | $0 | $0 | Commission-free |
| Alpaca crypto fees | $1–$3 | $3–$8 | 0.15–0.25% per trade, depends on frequency |
| Market data | $0 | $0 | Free basic plan includes IEX data + news stream |
| Compute (local) | $0 | $0 | Running on existing hardware |
| Compute (VPS) | $0 | $5–$10 | Optional: for 24/7 crypto monitoring |
| **Monthly Total** | **$2.50–$5.50** | **$12–$25** |  |

### Break-Even Analysis

At the **budget level** ($2.50–$5.50/month), the system needs to generate 0.05–0.11% monthly return on $5,000 to break even. This is $2.50–$5.50 per month — less than one successful trade using the RSI-2 strategy (which averages 0.57% per trade on SPY, or $28.50 per trade on a $5,000 position).

At the **recommended level** ($12–$25/month), the system needs 0.24–0.50% monthly return. This is still modest — approximately one successful RSI-2 trade per month covers the entire operating cost.

### Revenue Projections (Scenario Analysis)

These projections use the Phase 2 backtested performance metrics, degraded by the standard 50% backtest-to-live factor.

**Conservative scenario (1% monthly return after all costs)**:
- Monthly profit: $50 (growing with compounding)
- Annual return: ~12.7% (compounding monthly)
- Account after 12 months: ~$5,634
- Account after 24 months: ~$6,349

**Moderate scenario (2% monthly return after all costs)**:
- Monthly profit: $100 (growing with compounding)
- Annual return: ~26.8%
- Account after 12 months: ~$6,341
- Account after 24 months: ~$8,042

**Optimistic scenario (3% monthly return after all costs)**:
- Monthly profit: $150 (growing with compounding)
- Annual return: ~42.6%
- Account after 12 months: ~$7,129
- Account after 24 months: ~$10,163

**Reality check**: The RSI-2 strategy on QQQ achieved 12.7% CAGR in backtesting while being invested only 14% of the time. After the standard 50% live degradation, that's roughly 6.4% annualized from one strategy. Adding ORB and crypto returns could reasonably push the total to 10–15% annualized in a good year. The 2% monthly (26.8% annual) scenario is ambitious but within range of a well-executing system. The 3% monthly scenario is the upper bound and should not be expected.

### When to Increase Capital

The system should not receive additional capital injection until it has demonstrated profitability for at least 3 consecutive months on paper trading AND 2 consecutive months on live trading. Additional deposits should be sized proportionally — adding $1,000 at a time rather than doubling the account, to avoid abruptly changing the risk profile.

---

## Part C: Tax Strategy

### The Wash Sale Problem for Algorithmic Traders

The wash sale rule is the single biggest tax trap for frequent traders and deserves extensive attention in the agents' design. Under IRC Section 1091, if you sell a security at a loss and buy the same or a "substantially identical" security within 30 calendar days before or after the sale, the loss is disallowed for tax purposes. The disallowed loss is added to the cost basis of the replacement purchase.

For our system, which may trade SPY and QQQ repeatedly throughout the year, this creates a cascading nightmare. Every time the system sells SPY at a loss and re-enters SPY within 30 days (which the RSI-2 strategy does by design — it trades the same symbols repeatedly), the loss is deferred. These deferred losses compound and can create situations where the trader owes taxes on phantom income that far exceeds actual profits.

A real-world case study from algorithmic trading research illustrates the severity: a trader with $30,000 in net profit had $195,000 in disallowed losses due to wash sales across 18,500 triggered wash sales out of 22,000 loss-generating trades. The tax bill was calculated on the $30,000 + $195,000 = $225,000, not the $30,000 actual profit.

**Critical 2025 change for crypto**: Starting with the 2025 tax year, the Infrastructure Investment and Jobs Act (enacted 2021) extended wash sale rules to digital assets including cryptocurrencies. Prior to 2025, crypto was exempt from wash sale rules because the IRS classified it as property rather than securities. This means our crypto trading strategy is now subject to the same wash sale complexities as our equity strategies. This is a significant change from what many older resources indicate.

### The Section 475(f) Mark-to-Market Election: The Solution

The Section 475(f) mark-to-market (MTM) election is strongly recommended for this system. When elected:

**Wash sale rules are eliminated.** IRC Section 1091 does not apply to traders using mark-to-market accounting. This single benefit alone justifies the election for any algorithmic trading system that trades the same securities repeatedly.

**Trading losses become ordinary losses.** Instead of capital losses (limited to $3,000 per year deduction against ordinary income), trading losses are ordinary business losses that can offset any type of income without limitation. If the system loses $3,000 in a year, that full $3,000 offsets your ordinary income (wages, etc.) — not capped at $3,000 like capital losses.

**Simplified reporting.** All gains and losses are reported on Form 4797 Part II as ordinary income, rather than on Schedule D with the complex wash sale tracking that would otherwise be required.

**Potential QBI deduction.** Ordinary business income from trading may qualify for the 20% Qualified Business Income deduction under Section 199A (subject to income thresholds and the fact that trading is a "specified service trade or business").

### 475(f) Election Requirements and Timing

**Who qualifies**: You must meet "Trader Tax Status" (TTS) criteria. The IRS looks at whether trading is substantial, regular, frequent, and continuous, aimed at profiting from short-term market swings. An automated system executing trades daily on multiple instruments will likely qualify, but there is no bright-line test. Hundreds of trades per year, executed almost daily, with significant capital at risk, is the general threshold established by court cases.

**Election deadline for 2026**: The election statement must be filed by April 15, 2026, attached to your 2025 tax return or extension. This is a hard deadline — filing an extension for the tax return does NOT extend the deadline for the election itself. Late elections are generally not allowed.

**Practical implication**: If the system begins live trading in 2026, you should file the 475(f) election by April 15, 2026, to have it apply to 2026 trading activity. If you miss this deadline, you must wait until April 15, 2027 to elect for 2027.

**Important 2025 change**: Under Revenue Procedure 2025-23, once the 475(f) election is made, revocation is now subject to a five-year lock-in period. Revoking within five years requires IRS consent, the non-automatic change procedures of Revenue Procedure 2015-13, and a user fee of approximately $13,225. This means the election should be considered carefully — it is effectively permanent for the first five years.

**The tradeoff**: All gains are taxed at ordinary income rates (up to 37% + 3.8% NIIT) rather than the preferential long-term capital gains rate (0–20%). However, since our system generates exclusively short-term gains (positions held for days, not years), this tradeoff is irrelevant — short-term gains are already taxed at ordinary rates regardless of the election.

### Tax Action Items

1. **Before live trading begins**: Consult with a tax professional experienced in trader taxation to confirm TTS qualification and file the 475(f) election on time.
2. **Trade logging**: The system must log every trade with entry date, exit date, symbol, quantity, entry price, exit price, and realized P&L. This data feeds directly into tax reporting. TimescaleDB is the storage system for this.
3. **Year-end mark-to-market**: On December 31, all open positions are treated as if sold at fair market value. The system should log these "deemed sales" with the year-end market price for tax reporting purposes.
4. **Quarterly estimated taxes**: If trading generates significant income, estimated tax payments are due quarterly (April 15, June 15, September 15, January 15). The Supervisor Agent should track cumulative annual P&L and alert Dan when estimated tax payments may be owed.

### Record-Keeping Requirements

The IRS requires traders to maintain detailed records of all transactions. The system's TimescaleDB database satisfies this requirement, but the data must be exportable in a format suitable for tax preparation. At minimum, each trade record must include:

- Date and time of entry and exit
- Symbol traded
- Number of shares/units
- Entry price and exit price
- Commission/fees paid
- Realized gain or loss
- Whether the trade was marked-to-market at year-end

The Trade Executor should generate a daily trade log and a monthly summary. The Supervisor Agent should generate an annual tax summary on January 1 of each year.

---

## Part D: Regulatory Compliance

### Algorithmic Trading Is Fully Legal

There is no law, regulation, or SEC/FINRA rule prohibiting individual retail investors from using AI, LLMs, or autonomous agents to make trading decisions. Alpaca explicitly supports and encourages algorithmic trading through their API. No registration, license, or filing is required for personal algorithmic trading at any account size.

### What the System Must Not Do

**Market manipulation** is illegal regardless of whether it is done by a human or an algorithm. The system must avoid:

- **Spoofing**: Placing orders with the intent to cancel them before execution to manipulate prices. Prevention: the Trade Executor never submits orders it does not intend to fill. All orders are either market orders (fill immediately) or limit orders that remain active until filled, canceled by the system, or expired at end of day.

- **Wash trading**: Buying and selling the same security to create the appearance of activity without changing beneficial ownership. Prevention: the system never simultaneously holds a long and short position in the same security (which is impossible anyway given Rule 1's no-shorting constraint).

- **Layering**: Placing multiple orders at different price levels to create a false impression of supply/demand. Prevention: the Trade Executor submits only one order per signal per symbol. It does not place multiple limit orders at different prices to "feel out" the market.

### Broker Terms of Service

Alpaca's terms of service explicitly permit algorithmic and automated trading. The system should maintain a reasonable order-to-trade ratio (orders submitted vs. orders filled) to avoid being flagged as potentially manipulative. A ratio below 5:1 (no more than 5 orders for every 1 fill) is generally considered acceptable.

---

## Part E: Operational Risk Management

### System Failure Modes and Mitigations

**Internet/API outage while positions are open**: The system should set server-side stop-loss orders via Alpaca's API (GTC stop orders) immediately after entering any position. This ensures the stop-loss executes even if the system loses connectivity. Alpaca executes server-side orders regardless of whether the client is connected.

**LLM API outage**: If Together.ai or Anthropic's API is unreachable, the system should fall back to code-only operation. The Trade Executor and its risk management rules are entirely code-based and do not depend on LLM availability. The Screener and Portfolio Manager lose their LLM capability but can fall back to purely technical signals (RSI-2 + SMA without news evaluation). The system should never be unable to close a position due to LLM unavailability.

**OpenClaw crash**: Each agent should have a "last known state" checkpoint in Redis. On restart, agents resume from their last checkpoint rather than starting from scratch. The Supervisor Agent detects missing heartbeats from other agents and attempts to restart them.

**Data feed failure**: If Alpaca's WebSocket data feed disconnects, the Watcher Agent should halt all new signal generation but NOT close existing positions (which have server-side stop-losses protecting them). It should attempt to reconnect with exponential backoff. If the disconnect persists for more than 30 minutes during market hours, the Supervisor should alert Dan via Telegram.

**Unexpected account state**: On every startup and every 15 minutes during trading hours, the Trade Executor should verify:
- `cash >= 0` (Rule 1: no debt)
- `pattern_day_trader == false` (PDT flag not triggered)
- `trading_blocked == false` (account not restricted)
- No positions exist that the system didn't open (detecting unauthorized access)

If any of these checks fail, the system halts immediately and alerts Dan.

---

## Summary: The Three Pillars of System Safety

**Pillar 1: Capital preservation.** The 1% risk per trade, 3% daily loss limit, and tiered drawdown protocol ensure that the $5,000 capital erodes slowly (if at all) while the system learns. Even in a worst-case scenario of 50 consecutive losing trades, the account would still have roughly $2,500 — enough to continue learning.

**Pillar 2: Economic viability.** At $3–$5/month in operating costs, the system needs to generate only 0.06–0.10% monthly return to cover itself. A single successful RSI-2 trade generates 5–10x this amount. The economic bar is intentionally low so that the system can focus on learning quality over profit maximization.

**Pillar 3: Legal compliance.** The 475(f) mark-to-market election eliminates wash sale complications, the system's trade logging satisfies IRS record-keeping requirements, and the anti-manipulation safeguards prevent regulatory violations. The estimated tax tracking ensures Dan isn't surprised by a large tax bill.

---

*Phase 4 complete. All four research phases are now finished. The system has a comprehensive foundation: market microstructure constraints (Phase 1), validated trading strategies (Phase 2), signal engineering specifications (Phase 3), and risk management, economic, and legal frameworks (Phase 4). The next step is to update the main report with findings from all four phases and begin the architecture plan implementation.*

v1.0.0
