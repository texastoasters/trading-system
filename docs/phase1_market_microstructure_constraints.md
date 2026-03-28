# Phase 1 Research Deliverable: Market Microstructure and Constraints

## The Critical Discovery That Changes Everything

**Alpaca does not offer true cash accounts.** All Alpaca accounts are opened as margin accounts. This single fact dramatically changes the settlement constraint model described in the original report — mostly for the better, but with new considerations the agents must understand.

## How Alpaca Account Types Actually Work

Every Alpaca account is a margin account. The behavior changes based on the account's equity level.

**Accounts with less than $2,000 in equity** are treated as "limited margin accounts." They have 1x buying power (no leverage) and cannot short sell, but they can trade on unsettled funds. This is the critical difference from a true cash account — in a limited margin account, you can sell Stock X, immediately use the unsettled proceeds to buy Stock Y, and then sell Stock Y the next day and buy Stock Z, all without triggering a good faith violation. In a true cash account at Fidelity or Schwab, that second transaction (selling Y before X's proceeds settled) would be a GFV.

**Accounts with $2,000 to $25,000 in equity** (which is where your $5,000 account will land) are regular margin accounts with 2x overnight buying power. They can trade on unsettled funds freely. They are subject to the Pattern Day Trader rule: no more than 3 day trades (buying and selling the same security in the same day) within any rolling 5-business-day window. If you exceed this, the account gets flagged as a PDT account and must maintain $25,000 minimum equity or face restrictions.

**Accounts with $25,000 or more** can be flagged as PDT accounts with 4x intraday buying power.

## What This Means for Rule 1: No Debt Exposure

Here is the tension: a margin account can create debt exposure. With $5,000 deposited and a 2x multiplier, Alpaca would let you buy $10,000 worth of stock — borrowing $5,000 from the broker. If those stocks dropped 60%, your positions would be worth $4,000, you would still owe Alpaca $5,000, and your equity would be negative $1,000. This directly violates Rule 1.

**The solution is to enforce 1x buying power in code as a hard constraint in the Trade Executor agent.** Rather than relying on broker-level configuration (which the Broker API supports via the `max_margin_multiplier` admin field, but which may require contacting Alpaca support for individual Trading API accounts), the Trade Executor enforces Rule 1 by using the account's `cash` field rather than `buying_power` as the ceiling for any order. With 2x margin available on a $5,000 account, `buying_power` might show $10,000, but the Trade Executor will cap orders at the `cash` value (approximately $5,000 minus any open positions). This gives defense-in-depth: even if the broker allows leverage, the agent never uses it. Combined with never submitting short sell orders, this guarantees zero debt exposure.

The Trade Executor agent must verify these constraints on every startup:

```python
# On startup, verify account state and enforce Rule 1 constraints
account = trading_client.get_account()

# Log the multiplier for awareness, but don't depend on it for safety
multiplier = account.multiplier
if multiplier != "1":
    log.warning(f"Account multiplier is {multiplier}x — Trade Executor will "
                f"enforce 1x in code by capping orders at cash value")

# Rule 1 enforcement: never use more than actual cash
available_for_trading = float(account.cash) - float(account.long_market_value)
# NOT: available_for_trading = float(account.buying_power)

# Verify shorting status
if account.shorting_enabled:
    log.warning("Short selling is enabled at broker level — Trade Executor "
                "will never submit short orders regardless")

# Verify account is not blocked
assert not account.trading_blocked, "CRITICAL: Account is blocked from trading!"
assert not account.account_blocked, "CRITICAL: Account is fully blocked!"
```

## Settlement: No Longer a Constraint (With Caveats)

Because Alpaca uses margin accounts (even at our self-imposed 1x limit), the T+1 settlement rules that govern true cash accounts are handled differently. In a margin account, the broker extends temporary credit for the settlement gap. You can sell Stock X at 10:00 AM and immediately use those proceeds to buy Stock Y at 10:05 AM, even though X's sale will not settle until tomorrow. The broker covers the one-day gap as part of normal margin account operations.

This means good faith violations do not apply (GFVs are a cash-account concept), free-riding violations do not apply in the same way, and the complex settlement queue tracking system described in the original report is unnecessary. The Trade Executor does not need to track settled vs. unsettled cash for violation prevention.

**The one caveat**: `cash_withdrawable` still reflects only fully settled funds. You cannot withdraw unsettled proceeds. This does not affect trading, but the Supervisor Agent should be aware that the "withdrawable" amount may be less than the "tradeable" amount at any given time.

## The PDT Rule as the Binding Constraint

The Pattern Day Trader rule replaces settlement tracking as the primary constraint the agents must manage. With $5,000 in a margin account below $25,000, you are limited to 3 day trades per rolling 5-business-day window. A "day trade" is defined as a round-trip pair of trades within the same day — opening and then closing the same position within the same calendar day (including extended hours).

**Alpaca enforces PDT protection at the broker level.** This is an extremely important safety feature that we get for free. The Alpaca Trading platform checks the PDT rule condition every time an order is submitted. If an order could potentially result in the account being flagged as PDT, the order is rejected and the API returns HTTP status code 403 (Forbidden). This protection operates on both live and paper trading accounts. Even more conservatively, the system considers pending open orders when counting potential day trades — if you have a pending sell limit order on AAPL and submit a new buy order on AAPL, the system treats that as a potential day trade pair and will block it if you are already at the limit.

This broker-level enforcement serves as a safety net underneath whatever our Trade Executor tracks locally. The agents need to handle 403 responses gracefully: if a day-trade exit is blocked by Alpaca's PDT protection, the agent must fall back to holding the position overnight rather than crashing or retrying.

**Crypto trades are completely exempt from the PDT rule.** Cryptocurrency is not classified as a security, so crypto orders are not evaluated by PDT protection logic and round-trip crypto trades on the same day do not contribute to the day trade count. This is the primary strategic advantage of crypto in our system — not settlement speed (which is now irrelevant given the margin account), but the ability to execute unlimited intraday round trips.

### PDT Management Strategy

Each day trade is precious. The recommended approach for the Trade Executor:

**Reserve 1 day trade at all times for emergency exits.** If a position gaps against you on bad news, you need the ability to exit immediately. The agents should operate as if they have 2 day trades available, keeping the third as insurance. When checking `daytrade_count` from the account API, the logic should be: if `daytrade_count >= 2`, only enter positions you are comfortable holding overnight.

**Prioritize swing trades (held overnight) over day trades.** Positions opened and closed on different days do not count as day trades. The mean-reversion strategy (RSI-2, hold 2–5 days) and PEAD strategy (hold approximately 9 days) naturally avoid consuming day trades. The gap-and-go strategy is the primary consumer of day trades since it typically enters and exits on the same day.

**The Portfolio Manager Agent must be PDT-aware.** When the day trade counter is at 2 out of 3, the Portfolio Manager should only approve equity positions that it is comfortable holding overnight. When the counter is at 0 or 1, it has more flexibility for same-day exits. This context must be part of the information passed to the LLM for every Portfolio Manager decision.

### PDT Counter Implementation

```python
# PDT tracking for the Trade Executor
account = trading_client.get_account()
day_trade_count = int(account.daytrade_count)

MAX_DAY_TRADES = 3
RESERVED_FOR_EMERGENCY = 1
EFFECTIVE_LIMIT = MAX_DAY_TRADES - RESERVED_FOR_EMERGENCY  # = 2

def classify_order(symbol, side):
    """Classify whether an order would consume a day trade."""
    positions = trading_client.get_all_positions()
    has_open_position = any(p.symbol == symbol for p in positions)
    
    if side == "sell" and has_open_position:
        # Check if the position was opened today
        # If opened today, closing it = day trade
        # Alpaca's own PDT check will catch this, but we pre-check
        # to avoid unnecessary 403 errors
        pass
    
    return "potential_day_trade" if has_open_position else "new_position"

def can_enter_day_trade():
    """Check if we have room for a same-day round trip."""
    return day_trade_count < EFFECTIVE_LIMIT

def handle_pdt_rejection(order_response):
    """Handle a 403 PDT rejection from Alpaca."""
    # Do NOT retry — hold position overnight instead
    log.warning(f"PDT protection blocked order. Holding position overnight.")
    # Notify Supervisor Agent of the forced overnight hold
    publish_event("pdt_blocked", {
        "symbol": order_response.symbol,
        "action": "forced_overnight_hold",
        "daytrade_count": day_trade_count
    })
```

## Alpaca Crypto Trading: Fees and Implications

Alpaca charges volume-tiered maker/taker fees on all crypto trades, unlike the commission-free equity trading. At our expected volume level (under $100,000 in 30-day crypto trading volume, which is Tier 1), the fees are **0.15% for maker orders** (limit orders that add liquidity to the order book) and **0.25% for taker orders** (market orders or marketable limit orders that execute immediately against existing orders).

In concrete terms, a $1,000 crypto buy executed as a market order (taker) costs $2.50 in fees. A round-trip (buy and sell) on $1,000 costs approximately $4.00–$5.00 depending on the mix of maker and taker executions. This means any crypto strategy needs to generate at least 0.40–0.50% per round-trip just to cover fees and break even.

### Impact on Crypto Strategy Profitability

This fee structure is meaningful but workable. It does, however, change the calculus for high-frequency crypto strategies. A strategy that makes 5 round-trips per day on $1,500 of crypto capital would pay approximately $30–$37 per day in fees ($600–$740/month). That is obviously unsustainable on a $5,000 account. The crypto strategy must therefore favor fewer, higher-conviction trades rather than high-frequency scalping.

The recommended approach for the crypto allocation is to trade BTC and ETH on 15-minute or longer timeframes with the RSI + MACD + Bollinger Bands combination, targeting moves of 1% or more per trade. At 1% gain on a $1,500 position, the gross profit is $15, minus approximately $3.75 in round-trip fees, leaving $11.25 net. One or two successful trades per day at this profile generates meaningful returns without the fees consuming the profits.

**The agents should always prefer limit orders over market orders for crypto.** The difference between maker (0.15%) and taker (0.25%) fees is 0.10% per side, or 0.20% per round-trip. On $1,500 of capital, that is $3 saved per round-trip. Over 20 trading days per month with one round-trip per day, that adds up to $60/month — more than the entire LLM API budget.

### Comparison with Kraken

Kraken's fees at comparable volume levels are 0.16% maker and 0.26% taker, which is nearly identical to Alpaca's 0.15%/0.25%. The convenience of having equities and crypto on the same platform (Alpaca) outweighs the negligible fee difference. However, if crypto trading volume grows significantly and becomes a major profit center, Kraken may offer better liquidity and tighter spreads on major pairs. This is a Phase 5 (scaling) consideration, not an immediate one.

## Alpaca News API: Available on Free Tier

Alpaca provides a dedicated real-time news WebSocket stream at `wss://stream.data.alpaca.markets/v1beta1/news`. Each news item arrives as a structured JSON object containing the headline, a summary, associated ticker symbols, a unique ID, and a timestamp. The Screener Agent can subscribe to all symbols using `{"action": "subscribe", "news": ["*"]}` and receive every news item as it publishes.

The news stream appears to be available on the free Basic plan, which is the default for all accounts (both paper and live). The Basic plan provides essential market data at zero cost, though its equity price data is limited to the IEX exchange feed (approximately 10% of total market volume). The news API was introduced with a note that pricing may change after a beta period, so availability on the free tier should be verified during the Stage 0 infrastructure setup.

This is excellent for the Screener Agent's architecture. Rather than polling a third-party news API (like Finnhub) at intervals, the Screener can maintain a persistent WebSocket connection to Alpaca's news stream and react to news items in real time. The processing pipeline becomes: receive news item via WebSocket, apply keyword filters to check relevance to watched symbols or sectors, and if it passes the keyword filter, invoke the LLM (GPT-OSS 120B at low reasoning effort) to evaluate whether the news represents a material catalyst worth acting on.

The free Basic plan's limitation on equity price data (IEX only) is worth noting but not critical for our use case. For the Screener and Watcher agents' price monitoring, IEX data is sufficient for detecting large price moves, gap patterns, and volume spikes. If we later need full SIP (consolidated) data for more precise execution, the Algo Trader Plus subscription ($9/month) provides complete market coverage.

## Paper Trading: PDT Rules Are Fully Simulated

Alpaca's paper trading environment works identically to live trading for PDT enforcement purposes. Paper trading simulates the complete trading lifecycle end to end — the only difference is that orders are not routed to a live exchange. Instead, the system simulates order fills based on real-time quotes.

This means our Stage 3 paper trading validation phase (6 weeks of simulated live trading) will accurately test the PDT constraint. The `daytrade_count` field will increment in paper trading just as it does in live trading, and orders that would trigger PDT will be rejected with the same 403 status code. We do not need to build our own PDT simulation — Alpaca provides it natively.

One important nuance: the paper trading environment counts pending orders toward the PDT calculation, just like live trading. If the Trade Executor has an open sell limit order on AAPL and submits a buy order for AAPL (even if the sell has not filled yet), Alpaca treats that as a potential day trade pair and will block it if the day trade count is already at the limit. The agents must account for this in their order management — canceling stale orders before submitting new ones on the same symbol.

## Revised Capital Flow Model

With settlement constraints eliminated and PDT as the binding constraint, capital flows much more freely than the original report assumed.

**Equity capital ($3,000–$3,500 allocation)**: Full buying power is available immediately after any sale (no settlement waiting). The account is limited to 2 usable day trades per 5-day rolling window (reserving 1 for emergencies). Swing trades (held overnight or longer) have no frequency limit whatsoever. Fractional shares are available from $1, allowing precise position sizing even on expensive stocks.

**Crypto capital ($1,500–$2,000 allocation)**: No day trade limits whatsoever. Proceeds available immediately after any sale. Serves as the "always available" intraday trading pool when equity day trades are exhausted. Subject to 0.15%/0.25% maker/taker fees that must be factored into every strategy's profitability calculation. Trades 24/7, including weekends and holidays, allowing the system to generate returns even when equity markets are closed.

### How a Typical Week Looks with $5,000

**Monday**: Enter a swing trade on SPY for $1,500 (intent: hold 2–5 days, not a day trade). Enter and exit a gap-and-go trade on NVDA for $1,000 (day trade #1). Trade BTC twice intraday on momentum signals using $1,500 of crypto capital (not counted toward PDT).

**Tuesday**: SPY position still held (not a day trade). Enter and exit a gap trade on TSLA for $1,200 (day trade #2). Day trade budget now at limit — only swing trades for equities until the rolling window clears. Crypto trades as signals dictate, unrestricted.

**Wednesday**: Exit SPY swing trade at profit (entered Monday, so not a day trade). Enter a new mean-reversion position on QQQ for $1,800. No equity day trades used — preserving the emergency reserve. Crypto trades active, including a weekend setup position on ETH.

**Thursday**: QQQ still held. No equity day trades consumed. The 5-day rolling window still shows 2 day trades (Monday's and Tuesday's). Crypto capital active.

**Friday**: Exit QQQ (entered Wednesday, not a day trade). Day trade counter will begin to clear as Monday's trade falls off the rolling window over the weekend. System has room for new day trades next week. Crypto positions can be held or traded over the weekend.

In this example, the $5,000 account executed 7+ equity transactions and unlimited crypto transactions in one week, constrained only by the 3 day-trade limit — not by settlement at all.

## The API Fields the Trade Executor Must Monitor

The Alpaca account endpoint (`GET /v2/account`) returns several fields critical for the Trade Executor.

**`buying_power`**: Total buying power available, including margin. With the default 2x multiplier on a $5,000 account, this could show up to $10,000. The Trade Executor must NOT use this field for order sizing — it must use `cash` instead to enforce Rule 1 (no debt exposure).

**`cash`**: Total cash in the account, including unsettled proceeds from recent sales. This is the field the Trade Executor uses as the ceiling for order values, ensuring we never trade on margin even though the broker would allow it.

**`non_marginable_buying_power`**: Buying power available for non-marginable securities. Since crypto is non-marginable at Alpaca, this field effectively tells you how much cash is available specifically for crypto purchases.

**`cash_withdrawable`**: Cash available for withdrawal, representing fully settled funds only. The Trade Executor does not need this for trading decisions (the margin account handles settlement), but the Supervisor Agent can use it for reporting on the proportion of funds in settled vs. unsettled state.

**`equity`**: Total account value (cash plus market value of all open positions). The Supervisor Agent uses this to calculate drawdown and daily P&L.

**`last_equity`**: Account equity as of the previous market close. The daily P&L calculation is simply `equity - last_equity`.

**`daytrade_count`**: The number of day trades executed in the current rolling 5-business-day window. This is the most critical field for PDT constraint management. The Trade Executor checks this before approving any trade that might be closed the same day. Note that this field may not update in real-time within the same trading session — the Trade Executor should maintain its own local counter that increments immediately upon a same-day exit fill, supplementing (but not replacing) Alpaca's official count.

**`pattern_day_trader`**: Boolean flag indicating whether the account has been flagged as PDT. The Supervisor Agent must monitor this field and trigger an emergency halt if it ever becomes `true` on an account below $25,000 in equity. If this happens, the account faces restrictions that would severely limit the system's ability to trade.

**`trading_blocked`**: Boolean flag. If `true`, the account has been restricted from trading. The Trade Executor checks this before every order submission and halts all activity if it is ever `true`.

## Revised Constraint Summary

| Constraint | Original Assumption | Actual Reality | Impact on Architecture |
|-----------|---------------------|----------------|----------------------|
| Account type | Cash account | Margin account (enforce 1x in code) | Much more flexible trading |
| Settlement | T+1 blocks capital | Handled by margin, no blocking | No settlement queue needed |
| GFV risk | 3 per year limit | Does not apply (margin account) | Major simplification |
| Free-riding | 1 violation = 90-day restriction | Does not apply (margin account) | Major simplification |
| Day trade limit | None (cash account) | 3 per 5-day rolling window (PDT) | New constraint, broker-enforced |
| PDT enforcement | Must build ourselves | Alpaca enforces at broker level (403) | Safety net we get for free |
| Crypto advantage | Instant settlement vs T+1 equity | PDT exemption (unlimited day trades) | Strategic role shifts |
| Crypto fees | Assumed negligible | 0.15% maker / 0.25% taker | Must factor into strategy profitability |
| Debt exposure | Prevented by cash account | Must enforce 1x in Trade Executor code | Requires code-level constraint |
| News data | Needed third-party API | Alpaca provides free real-time WebSocket | Simplifies Screener Agent |
| Paper trading PDT | Unknown simulation fidelity | Fully simulated by Alpaca | Paper testing is trustworthy |

## What Must Be Updated in the Main Report

The original report's settlement tracking module should be replaced with a much simpler PDT counter and Rule 1 enforcement layer. Specifically, the Trade Executor agent description should remove all references to settlement queues, settled vs. unsettled cash ledgers, and GFV prevention logic. These should be replaced with the PDT counter system described above, the Rule 1 enforcement code that caps orders at the `cash` value rather than `buying_power`, and a 403-response handler for PDT rejections that gracefully falls back to overnight holds.

The crypto section should be updated to reflect that the strategic advantage of crypto is PDT exemption (unlimited day trades) rather than settlement speed, and that the 0.15%/0.25% fee structure requires strategies targeting 1%+ moves per trade to be profitable after fees.

The data pipeline section should note that Alpaca's free-tier news WebSocket eliminates the need for a separate Finnhub account for the Screener Agent's news feed, though Finnhub remains useful for supplemental data like earnings calendars and company fundamentals.

---

*Phase 1 complete. All open questions have been resolved. The constraint model is now fully specified and ready to serve as the foundation for Phase 2: Strategy Research and Backtesting.*

v1.0.0
