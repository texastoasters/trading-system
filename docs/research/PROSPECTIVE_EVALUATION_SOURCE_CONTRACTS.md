# Prospective evaluation source contracts

Research date: 2026-11-16. Scope: official Alpaca documentation only; no authenticated request or entitlement assumption. This defines a forward **raw-price response proxy**, not a broker fill, regular-market open, total return, or P&L.

## Recommendation: smallest defensible milestone

Use only the already-permitted IEX `1Day` raw bars plus Alpaca's US trading calendar. Make the decision after source session T's returned extended `session_close`; freeze it strictly before the next trading date A's New York midnight. Anchor it to the first **bar-eligible IEX trade price** (`o`) in the first daily bar whose `t > frozen_at`; mark `c` at the end of the 1st and 5th trading sessions beginning with A. Do not call `o` the 09:30 opening price, an auction price, or the first unfiltered trade.

This is causally ordered under Alpaca's documented bar construction, but it is only a single-exchange, condition-filtered price response. Keep it separate from economic performance. Corporate-action-affected/unknown windows may be reported only as raw responses and must not enter an “economic return,” P&L, or model-promotion aggregate.

## Daily-bar contract and causal interpretation

`GET https://data.alpaca.markets/v2/stocks/bars` with explicit `symbols`, `timeframe=1Day`, RFC-3339 `start`/`end`, `feed=iex`, `adjustment=raw`, `asof=-`, `currency=USD`, `sort=asc`, `limit=10000`, then every `page_token` until `next_page_token=null`.

Official definitions:

- The [bars reference](https://docs.alpaca.markets/us/reference/stockbars.md) calls a bar an “OHLC aggregate of all the trades in a given interval”; `o`/`c` are only “Opening price”/“Closing price.” It does **not** say “09:30 price,” primary-exchange auction, NBBO, or executable price.
- Alpaca's [Market Data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq.md#how-are-bars-aggregated) says minute and daily bars are aggregated from trades; a trade timestamp is truncated “to the day (in New York)” for daily bars, and the bar timestamp is “the left side of the interval.” Thus a future daily `t` strictly after the frozen deadline denotes a future New York-day interval.
- The same FAQ says `Open is set if it hasn't been set yet` by a trade whose combined condition rule permits an open/close update. Therefore `o` is the first **eligible** price update in that day, not necessarily the day's first trade. Conditions can exclude prices: odd lots (`I`) do not update OHLC; official-open (`Q`) and official-close (`M`) do not update OHLC; extended-hours (`T`) does not update daily OHLC (but can update volume). A trade may have multiple conditions and Alpaca applies the strictest rule.
- The [historical-data guide](https://docs.alpaca.markets/us/docs/historical-stock-data-1.md#data-sources) says IEX is one exchange, about 2.5% of market volume, and is “ideal for initial app testing and situations where precise pricing may not be the primary focus.” It is not consolidated market coverage.
- The bars reference defines `raw` as “no adjustments”; `split`, `dividend`, `spin-off`, and `all` are separate retrospective transformations. `asof=-` skips rename mapping and returns data “based on the symbol alone.”

Consequently the proposed anchor is defensible only if named `first_eligible_iex_daily_price_proxy`. It is not `regular_open`, `official_open`, `arrival_quote`, `broker_fill`, or a claim that a trade could have been executed at `o`.

## Freeze, availability, and revisions

For source-session trading date T, first fetch the calendar row A immediately after T. Require `decision_frozen_at < A 00:00:00 America/New_York`; also require evidence collection and decision creation after T's timezone-aware returned `session_close`. Select the earliest bar with `t > decision_frozen_at`, and require its New York date to equal A. Equality at the deadline fails. Store the original proposal bytes/hash and collector receipt before the deadline.

REST bar `t` is interval start, not publication time. Store separately: `interval_start`, calendar `session_close_at`, request start, response receipt/`fetched_at`, and `first_observed_at`. Set `source_published_at=null`: the REST reference provides no historical per-bar publication timestamp or finalization SLA. A later fetch must never be backdated to session close.

Daily bars are revisable. Alpaca's [real-time stock-data guide](https://docs.alpaca.markets/us/docs/real-time-stock-pricing-data.md#updated-bars-updatedbars) says late trades can update a previous bar's close/volume, and its trade stream carries corrections and cancel/errors. Historical trades also have optional `u` values `canceled`, `incorrect`, or `corrected` in the [trade reference](https://docs.alpaca.markets/us/reference/stocktrades-1.md). The historical-bars contract makes no immutability promise. Append every re-fetch as a new observed version (payload hash + receipt); never overwrite the value first scored. Report first-observed and latest-observed results distinctly.

## Session horizons and early closes

Use `GET https://paper-api.alpaca.markets/v2/calendar?start=...&end=...&date_type=TRADING`. The [calendar reference](https://docs.alpaca.markets/us/reference/legacycalendar.md) says rows are market days and regular `open`/`close` account for early closures. Required fields are `date`, `open`/`close` (`HH:MM`), `session_open`/`session_close` (`HHMM`), and `settlement_date`.

Attach `America/New_York` with the timezone database. Never use a fixed UTC offset. Session horizon H=1/H=5 means A itself / the fifth returned trading-date row counting A as session 1, not “one/five later rows” and not calendar days. Require each outcome bar date to match its target row. Fetch only after that row's `session_close`; early `close` and early `session_close` come from the row, never hard-coded. Missing calendar rows or symbol bars produce unavailable outcomes, not forward-filled prices.

For anchor price `o_A`, define raw responses `c_H / o_A - 1` for H=1 and H=5, where `c_H` is the eligible daily-bar close on target session H (so `c_1 = c_A`). These are unitless raw-price responses, not total returns or trade P&L.

## Corporate actions and identity

Raw prices can jump mechanically across forward/reverse splits, spin-offs, dividends, mergers, redemptions, reorganizations, and symbol changes. `asof=-` intentionally prevents Alpaca from silently relabelling old symbols, but it also means a rename can make later same-symbol bars disappear. Preserve requested symbol, returned symbol, and bar date; never bridge tickers by guess.

For this milestone, no new source is required **only because the score is explicitly non-economic**. Mark every window `corporate_action_status=unchecked` unless independently classified. Do not rank, average, or promote models on unchecked raw responses as though they were returns. The cleanest initial report has two counts: causal raw-price responses and economically-scorable responses (initially zero unless action treatment is verified).

If economic scoring is authorized, the minimum added Alpaca source is `GET https://data.alpaca.markets/v1/corporate-actions` from the official [corporate-actions reference](https://docs.alpaca.markets/us/reference/corporateactions-1.md), with explicit `symbols`, relevant `types`, inclusive `start`/`end` (these filter/sort by `process_date`), `data_quality=all`, `sort=asc`, `limit=1000`, and terminal `page_token` traversal. Supported relevant types include `reverse_split`, `forward_split`, `unit_split`, `cash_dividend`, `stock_dividend`, `spin_off`, merger types, `name_change`, `redemption`, `reorganization`, and `capital_gains_distribution`. Responses group typed records and return `next_page_token`; records expose type-specific fields such as `id`, symbols/CUSIPs, `process_date`, `ex_date`/`effective_date`, rates, and old/new symbols.

That endpoint is authenticated and may return 401/403/429; actual account access and rate limits must be probed later with existing keys before adoption. The docs state no creation-time guarantee and warn of provider/processing delays. Therefore it can retrospectively classify/quarantine a window, but cannot prove an action was known at decision time. Re-fetch through a declared correction lag and version records. `data_quality=complete` excludes some unprocessed incomplete events, while `all` includes incomplete records; neither establishes complete real-time coverage.

A mechanically economic metric still needs a predeclared action policy. Simple supported split/dividend cases may be recomputed only after validating ratios/cash and share treatment; complex spin-offs, mergers, rights, redemptions, reorganizations, missing action data, and ticker continuity remain `economically_unscorable`. Alpaca `adjustment=all` bars may be a separate sensitivity series, but the docs do not define them as an immutable point-in-time total-return index; never replace retained raw observations silently.

## When daily bars are insufficient

If the claim must be an actual first trade after the deadline, expand to official `GET https://data.alpaca.markets/v2/stocks/trades` (or `/v2/stocks/{symbol}/trades`) with explicit RFC-3339 `start=deadline` and post-filter `t > deadline` (the API's start is inclusive), bounded `end`, `feed=iex`, `asof=-`, `currency=USD`, `sort=asc`, `limit<=10000`, and complete pagination. The [reference](https://docs.alpaca.markets/us/reference/stocktrades-1.md) returns `t,i,x,p,s,c,z` plus optional correction status `u`; limit is total across symbols and a page may be short while `next_page_token` remains non-null. Define which conditions/corrections qualify before choosing `p`.

If the claim must approximate an executable arrival mark, also add `GET https://data.alpaca.markets/v2/stocks/quotes` with the same inclusive bounds, strict post-filter, and pagination. The [quote reference](https://docs.alpaca.markets/us/reference/stockquotes-1.md) returns timestamp `t`, bid `bp/bs/bx`, ask `ap/as/ax`, conditions `c`, and tape `z`; zero bid/ask means no active side. A midpoint or buy/sell side must be predeclared. Neither trades nor quotes are currently transport-allowlisted, and actual IEX endpoint entitlement must be verified rather than inferred. Alpaca's [plan page](https://docs.alpaca.markets/us/docs/about-market-data-api.md) says Basic equities coverage is IEX, historical calls are 200/minute, and the latest 15 minutes are restricted; it also says all market-data endpoints except historical crypto require authentication. That describes a plan, not this account's effective permission—probe later, do not purchase or assume access.

Do not expand for the first milestone: the daily-bar proxy is causally usable under its narrow name. Expand only when the evaluation question changes from delayed raw-price response to trade/quote arrival or economically scorable performance.
