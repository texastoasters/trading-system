# Strategy Review — RSI-2 Mean Reversion (2026-04-16)

Research-only review. No code changed. Focus: why the live system is producing
same-day churn (CLMT ×3) and immediate-loss entries (KMI -2.2%), and what the
current exit/entry rules actually do vs. what the backtest assumed.

## Data availability caveats

- `trades` table has 27 rows total across the entire system lifetime (11 with
  `realized_pnl`). This is too sparse for statistical claims; findings below
  lean on code paths and the small sample.
- `signals` table: **0 rows**. Watcher publishes to Redis pub/sub but does not
  INSERT into `signals`. So signal_type distribution, acted-on rate, and
  rejection reasons are not recoverable from DB — only inferable from the
  trades that did fire.
- `agent_decisions` table: **0 rows**. No LLM decisions logged. Relevant to Q4.
- `trades.exit_reason` stores `signal_type` only (`take_profit`), not the
  granular `reason` string (`"RSI-2 at 62.1 > 60"` vs `"Close 32.42 > prev high
  32.35"`). See `executor.py:818`. So RSI-2 vs prev-high vs time-stop cannot
  be separated in the DB today.

---

## 1. Exit rule interactions — which exit fires, does the order match the backtest, can entries implicitly guarantee an immediate prev-day-high exit?

### Live order (`watcher.py:363–394`, `generate_exit_signals`)

1. `intraday_low <= stop_price` → `stop_loss` (sets 24h whipsaw, `watcher.py:370`)
2. `rsi2_val > RSI2_EXIT` (60) → `take_profit` (no whipsaw)
3. `latest_close > prev_high` → `take_profit` (no whipsaw)
4. `hold_days >= RSI2_MAX_HOLD_DAYS` → `time_stop` (no whipsaw)

`prev_high = high[-2]` = most recent *completed* daily bar's high
(`watcher.py:330`). On day D+1 before that day's bar closes, this is the high
of the signal bar (day D).

### Backtest order (`backtest_rsi2.py:182–213`, `backtest_rsi2_expanded.py:204–219`)

Identical: `stop_loss` → `rsi2 > 60` → `close > prev_high` → `time_stop`.
**Order matches.**

### But bar-timing assumptions differ — this is the real bug

| | Backtest | Live |
|--|----------|------|
| Entry trigger bar | day D (close) | day D (screener at 16:15 ET) |
| Entry fill price | `close[D]` | day D+1 opening print |
| First exit check | day D+1 | day D+1 within minutes of fill |
| Exit rule `close > prev_high` uses | `high[D]` (the signal bar's high) | `high[D]` (same) |

In the backtest, the entry price is `close[D]` which is definitionally ≤
`high[D]` — so the prev-high exit cannot fire on entry day. In live, fill
happens at day-D+1 *open*, which can be (and empirically is) above `high[D]`
on a gap-up. First watcher cycle after fill compares `latest_close` to
`high[D]` and exits immediately at ~entry price minus spread/fees.

### Empirical evidence (buy/sell pairing over full DB)

```
 symbol |    day     |  buy_price | sell_price | hold_min | exit_reason
--------+------------+------------+------------+----------+-----------------------
 CLMT   | 2026-04-16 |  32.39     |  32.38     |   108.5  | blacklist_liquidation
 CLMT   | 2026-04-16 |  32.45     |  32.43     |    22.3  | take_profit
 DAR    | 2026-04-16 |  59.18     |  59.83     |   189.5  | take_profit   (+2.60)
 OSK    | 2026-04-16 | 142.93     | 143.04     |   303.3  | blacklist_liquidation
 CLMT   | 2026-04-16 |  32.37     |  32.42     |    15.2  | take_profit
 PAGP   | 2026-04-15 |  23.31     |  23.48     |  1326.1  | take_profit   (+1.70)
 MPLX   | 2026-04-15 |  54.94     |  55.31     |  1326.2  | take_profit   (+1.48)
```

Five of eight round-trips closed same-day within 15–303 minutes at ≈ entry
price. That hold duration rules out the daily-bar RSI-2 exit (RSI-2 updates
once per day) and rules out time_stop. It is consistent with the `close >
prev_high` rule firing on the first intraday watcher cycle after fill.

KMI (sold 2026-04-16 13:52 for -$9.36 realized) fits the same story cited in
the task description: entry at $32.66, prev_day_high $31.85, exit at $31.94
when price dipped back to that level.

### Implicit guarantee of immediate exit

**Yes — conditional on the entry-day open gapping above `high[D]`.** The
current entry rules (`watcher.py:189–289`) do not inspect the entry price
relative to `prev_high` at all, so there is no interlock preventing this.
`strong_signal` (screener.py:134–135: `latest_rsi2 < 5 AND above_sma`) tends
to fire after a sharp one-day dump, which often mean-reverts as a gap-up
open the next morning — directly feeding this failure mode.

---

## 2. Entry filter gap — price > prev_day_high not checked

### Code state

- `screener.py:112` computes and exposes `prev_high = high[-2]`.
- `screener.py:163` emits `prev_high` in the watchlist payload.
- `screener.py:132–142` priority classifier checks only `above_sma`, `rsi2`,
  and `divergence`. **No `close vs prev_high` check.**
- `watcher.py:281` passes `prev_high` into the signal's `indicators` dict,
  but `watcher.py:189–289` uses it for display only; the entry filter does
  not gate on it.
- Executor does not check either (it's deterministic and purpose-limited to
  safety: account cash, stop placement, etc.).

### How often would a `price > prev_day_high` filter have fired historically?

Cannot be answered precisely from DB alone — we'd need the daily bar for each
signal date to compute `prev_high`. Proxy from the live sample above:

- Five of eight round-trips (CLMT ×3, DAR, OSK) show hold durations and
  exit-price-at-entry-price patterns diagnostic of this failure mode.
- Long-hold success cases (PAGP, MPLX, DAR +$2.60) all had either overnight
  holds or large same-day moves — consistent with entries that did *not*
  satisfy `entry > prev_high`.

Small sample, but **the churn is concentrated in exactly the trades the filter
would have blocked.** A real frequency estimate needs a backtest that also
models the live bar-offset: enter at `open[i+1]` (next day), not `close[i]`.

---

## 3. Time stop — is 5 days right?

### Current

`RSI2_MAX_HOLD_DAYS = 5` (`config.py:129`), hot-reloaded to **7** in Redis
(`trading:config` shows `RSI2_MAX_HOLD_DAYS: 7`).

### Live data

**0 time_stop exits in trades history.** No ability to evaluate from DB.

### How to evaluate

Cannot be determined without running a backtest. Needed:
- Per-instrument sweep of `max_hold_days ∈ {3, 5, 7, 10, 14}` in
  `backtest_rsi2_universe.py`.
- For each instrument, histogram of "bars from entry to RSI-2 cross of 60"
  — this tells you the natural mean-reversion timescale.
- If most winners recover within 3 bars, 5 is fine; if a meaningful tail
  needs 7–10, the current 5 (or 7 override) is truncating profitable
  holds into time-stop losers.

Different instruments likely want different windows. BTC/USD (crypto, 24/7)
is a candidate for a shorter window; sector ETFs (slower, more reverting)
may want longer.

---

## 4. RSI-2 thresholds per-tier and EOD learning loop

### Current thresholds (all global, not per-tier or per-instrument)

- `RSI2_ENTRY_CONSERVATIVE = 10.0` (`config.py:111`, applied when regime ≠ UPTREND)
- `RSI2_ENTRY_AGGRESSIVE = 5.0` (`config.py:114`, applied when regime = UPTREND)
- `RSI2_EXIT = 60.0` (`config.py:120`)
- `strong_signal` tightens conservatively to `rsi2 < 5` regardless of tier
  (`screener.py:134`).

Applied uniformly across all symbols in `screener.py:124–127`. No per-tier
or per-symbol override path exists.

### EOD learning loop — does it adjust thresholds?

**No.** CLAUDE.md says:

> EOD learning loop: LLM analyzes trade history and adjusts RSI-2 thresholds/tier assignments.

Reality: `run_eod_review` (`supervisor.py:320–430`) sends a Telegram daily
summary and INSERTs into `daily_summary`. It does not:

- call any LLM (no `anthropic.` or `client.messages.create` in the file)
- write to `trading:config` (grep: no matches in `supervisor.py`)
- modify `DEFAULT_TIERS` / `trading:tiers`

Verification:
- `agent_decisions` table: 0 rows across all time.
- `trading:config` current contents match values a user entered via the
  `/settings` page (`MAX_CONCURRENT_POSITIONS: 6`, `RSI2_MAX_HOLD_DAYS: 7`
  — not values an auto-tuner would plausibly pick). Introduced by commit
  81cc6e9 "config hot-reload" as a manual override path.

**The learning loop documented in CLAUDE.md is not implemented.** Either
implement it or remove the claim.

---

## 5. Recommendations (ranked)

### Ship without backtest

1. **[HIGH / easy]** Screener-side filter: in `screener.py:132–142`, reject
   any priority promotion where `latest_close >= prev_high`. Rationale: the
   backtest enters at `close[D]` and has no ability to trigger prev-high
   exit at entry — this filter brings live screener output into alignment
   with the backtest's implicit entry condition. Directly kills the CLMT/KMI
   churn pattern at its source.

2. **[HIGH / easy]** Watcher-side pre-trade check: in
   `generate_entry_signals` (`watcher.py:189–289`), fetch current intraday
   price via the existing `fetch_intraday_bars` helper and skip if
   `current_price >= prev_high * (1 + buffer)` where `buffer ≈ 0.001`.
   Complements #1 by protecting against gap-up opens that the screener's
   EOD snapshot could not see. One extra API call per candidate; acceptable.

3. **[MEDIUM / easy]** Whipsaw-on-breakeven: extend `watcher.py:370` (which
   currently sets the 24h whipsaw only on stop-loss) to also set a short
   (~4h) whipsaw when `signal_type == "take_profit" AND hold_days == 0 AND
   abs(pnl_pct) < 0.002`. Belt-and-suspenders against residual churn when
   #1 and #2 miss.

4. **[LOW / easy]** Observability fix for `trades.exit_reason`: change
   `executor.py:818` from `order.get("signal_type", "unknown")` to
   `order.get("reason", order.get("signal_type", "unknown"))` — matches what
   the Telegram path already does at `executor.py:862`. Without this, the
   empirical analysis in Q1 is inferential rather than directly queryable.

5. **[LOW / easy]** Populate `signals` table from the watcher: the pub/sub
   message already carries everything the schema needs. Adding an INSERT
   after publish unlocks all downstream signal-distribution analysis.

6. **[LOW / easy]** CLAUDE.md alignment: either implement the EOD LLM
   learning loop or delete the sentence claiming it exists. Also mark the
   portfolio manager "escalates to Sonnet 4" claim as unverifiable until
   `agent_decisions` is populated.

### Require backtest first

7. **[HIGH / hard]** Per-instrument (or per-tier) RSI-2 entry thresholds.
   Sweep `{3, 5, 7, 10, 12}` × regime over 2-year window in
   `backtest_rsi2_universe.py`. Emit to `trading:thresholds:{symbol}` and
   have screener consult this before falling back to
   `RSI2_ENTRY_CONSERVATIVE/AGGRESSIVE`. Risk: overfitting; mitigate with
   walk-forward validation. Easily the largest alpha upside if it holds up.

8. **[MEDIUM / medium]** Per-instrument time-stop sweep. Same backtest harness,
   sweep `max_hold_days`. Persist per-instrument. Today's global value is
   untested against live data (zero time_stop exits).

9. **[HIGH / medium]** Align backtest entry mechanics with live:
   change backtest to enter at `open[i+1]` instead of `close[i]`, re-run
   all existing backtest reports. Live PnL will converge toward the new
   backtest number (expect it to be *worse* than current published numbers).
   Without this, tier/threshold decisions based on the current backtest are
   optimistic.

### Flagged for deeper investigation (not addressed here)

10. **CLMT and OSK appeared on the watchlist with `strong_signal` after
    being blacklisted and liquidated at 18:55.** Worth tracing whether the
    screener respects `universe.blacklisted` (the watcher does at
    `watcher.py:187–200`, but the *screener* may not, which would explain
    their re-appearance on the watchlist — the watcher would then skip
    them, but the dashboard would still display them). Recommend checking
    `screener.py` for the `blacklisted` exclusion and adding it if absent.
