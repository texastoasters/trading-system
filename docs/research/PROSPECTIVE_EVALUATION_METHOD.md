# Prospective Evaluation Method

> Investigation proposal, not the frozen recipe. `../PROSPECTIVE_EVALUATION_SPEC.md` owns adopted interfaces/defaults: existing four-symbol example,20-session trend, horizons1/5, and missing bars/failed AI never synthesized as cash. Suggested alternatives below remain research, not implemented requirements.

## Decision and boundary

Build one **read-only campaign module** around the existing evidence and decision-lab seams. It evaluates prospective, price-only allocation cohorts; it is not a trader, backtest, broker simulator, scheduler, or promotion decision. The estimand is each AI cohort’s paired incremental future price return versus matched controls, with measured usage and real costs reported separately; this informs the objective of maximizing expected net returns but cannot establish after-cost value while billed cost is unknown. Existing HOLD/containment remains authoritative: no broker writes, account reset, subscription change, global configuration, or installed daemon/cron.

The first campaign may be registered and frozen now. Its real outcomes must remain `pending` until genuinely future market data arrive. Fixture maturity tests are separate and must be labelled synthetic.

## Audited seams

- `collect_prospective_evidence.py collect --experiment … --output-dir …` calls injected GET transports and clock, then publishes one collection identity. `verify` performs offline semantic reconstruction.
- `prospective_evidence.collect_sources(symbols, alpaca, public, *, clock, max_pages)` is the source seam. Captures bound pagination, request/receipt times, body-hash receipts, raw IEX daily bars, news metadata, sessions, and the BEA calendar.
- `build_collection(capture, experiment)` creates a validated `mode=shadow` bundle. `write_collection`/`write_failed_collection` reserve an identity and rename a pending directory; reuse fails.
- `decision_lab.run_lab(bundle, provider, output_dir, *, now, research, critic)` is the inference seam. The default arm uses researcher → CIO → critic, at most three calls. Every prompt, validated response or failure receipt, decision, and manifest is written before publication. `replay_run` verifies hashes and deterministically reconstructs supported decisions offline.
- Provider adapters expose `complete(role, payload)`. Clocks, transports, providers, and filesystem roots are already injectable test seams. CLI `validate`, `run`, and `replay` cover the same public interfaces; `ai_decision_lab_hermes.py` only supplies the managed runtime.

These are deep enough to reuse, but they do **not** provide campaign registration, exclusive locking, repeated idempotent ticks, matched controls, outcome maturity, aggregate reports, or billed-cost reconciliation. Their “immutable” property is create-once identity plus hashes, not adversarial non-repudiation: a writer could coordinate file and manifest rewrites. Also, `bundle.run_id` is coupled to lab run identity; do not mutate a frozen bundle merely to create extra AI arms.

## Minimal campaign interface

One executable, manually invoked runner under normal PR/release gates:

```text
register(protocol.json, root) -> campaign_id
 tick(campaign_id, root, now=system_clock) -> tick receipt
report(campaign_id, root) -> report.json + report.md
verify(campaign_id, root) -> deterministic offline reconstruction
```

`tick` owns collection, arm evaluation, and later maturity behind this small interface. No caller may supply `registered_at`, `frozen_at`, or “prospective” status. Operator activation is an explicit handoff command; no installation or unattended scheduling is in this milestone.

## Immutable protocol

`register` exclusively creates `campaign/<id>/protocol.json` and its SHA-256 manifest before any campaign freeze. It rejects an existing ID, a registration at/after the first declared freeze deadline, any historical input/run import, and any protocol containing observed outcomes. Store code revision, schema/recipe/prompt versions, exact model ID, universe, feed/adjustment, cadence, freeze deadline, arms, allocation formulas, horizons, missing-data rules, cost policy, report metrics, planned end condition, and operator activation receipt.

Engineering default (evaluation control, not investment advice): five liquid US-listed ETFs `SPY, QQQ, IWM, TLT, GLD`; daily cohorts; normalized synthetic starting value `100.00` solely to satisfy current lab arithmetic, never a dollar cap or broker balance; long-only/no debt; horizons 1, 5, and 20 future completed daily bars. Promotion thresholds remain explicitly `undecided`; they may be registered later only for a new campaign, never selected from this campaign’s observed results.

Registration is append-once under the operator’s filesystem permissions and hash-addressed. Report that limitation; do not call it cryptographically immutable against a privileged writer.

## Arms and frozen cohort

Each freeze produces exactly one archived source bundle and fingerprint. All arms receive that same universe, source payload, availability cutoff, feed, raw-price policy, starting value, entry rule, and outcome windows:

1. **Cash:** 100% cash.
2. **Equal weight:** 20% per symbol.
3. **Simple trend:** using only completed pre-freeze bars, assign 20% to a symbol when its latest close is above its 60-session simple moving average; otherwise retain that 20% in cash. Insufficient history means cash, not exclusion or reweighting.
4. **AI CIO:** the existing three-role run, temperature zero, exact pinned model, no fallback, `max_calls=3`; use the complete CIO target vector only when the run is accepted.

Persist every arm result. AI provider failure, malformed output, critic block, CIO abstention, expiry, or missing model is a `failed`, `blocked`, `abstained`, or `missing` AI arm—not cash and not a dropped cohort. Never score only accepted winners. A future CIO-only ablation requires a separately pre-registered campaign or an identity/interface revision; it is not silently added after results.

## Tick lifecycle and concurrency

1. Acquire one nonblocking exclusive campaign lock. Derive `tick_id` from campaign ID plus scheduled freeze deadline—not invocation time.
2. If finalized, verify hashes and return the prior receipt without source/model calls. If another process holds the lock, return `busy`. Never overwrite.
3. Reject deadlines before registration, late first-time freezes beyond the protocol grace, clock regression, and any `historical_diagnostic` artifact. A missed deadline is durably `missed`, not backfilled.
4. Collect once, publish the complete or failed evidence archive, and freeze its fingerprint. Partial source capture remains a failure artifact.
5. Derive controls from that frozen archive; run the AI arm against the exact frozen bundle. Persist proposals/failures and provider receipts before checking outcomes.
6. Query only observations received after freeze for entry/maturity. Keep unavailable horizons `pending`. Finalize each horizon once; repeated ticks only verify/advance genuinely pending state.
7. Publish via pending-directory rename. On crash, a later tick verifies and finalizes a complete pending artifact or quarantines it; it never re-calls inference under the same `tick_id` after an unreceipted ambiguity.

## Entry, maturity, and return semantics

This is a **price-only allocation/cohort study**, not executable P&L. Entry is each symbol’s first future daily bar `o` whose provider-defined bar timestamp/session start is strictly later than the freeze deadline. Do not assume that timestamp means the regular-market open: implementation is gated on a sibling contract test against the sessions endpoint and provider documentation/fixtures. A bar at or before freeze can be evidence but never the entry.

For horizon `h`, use the raw close of the `h`th completed daily bar beginning with the entry bar. A missing symbol entry leaves that target weight in cash and records `non_fill`; never redistribute. Until all required future bars arrive or a pre-registered terminal-data rule fires, status is `pending`. Raw bars omit dividends/splits adjustments, spread, slippage, fees, taxes, borrow, partial fills, and total return; surface these caveats beside every result.

Cohort return is the fixed target-weight sum of symbol price returns; cash return is zero. Report paired arm-minus-control differences on identical cohorts, coverage, non-fills, failures, abstentions, latency, turnover proxy, and drawdown within each horizon path. Daily overlapping 5/20-bar cohorts are correlated: do not count them as independent samples, compound them into one equity curve, annualize a short interval, or claim significance/profitability from naive sample counts.

## Cost semantics

Preserve per-call request ID, returned model, measured input/output/total tokens, and latency from the existing manifest. `billed_cost` is populated only by an actual provider billing/account receipt linked to the run; absent evidence is `unknown`, never zero. A contemporaneous price-table calculation is `estimated_cost` with currency, quote source/time, rates, token basis, and assumptions. Subscription allocation is a separately labelled assumption. Reports show measured usage, estimated cost, and billed cost in separate columns and compute no “after-cost” claim when billed cost is unknown.

## Deterministic report and reconstruction

The report reads only protocol, tick manifests, frozen archives, arm records, cost receipts, and matured future-bar archives. Stable sorting, decimal arithmetic, canonical JSON, explicit schema versions, artifact hashes, and no network/model access make repeated reports byte-identical apart from an explicitly excluded generation receipt. Reconstruction must reproduce each control allocation, AI decision replay status, entry selection, maturity state, cohort return, and aggregate count; hash/invariant disagreement fails closed.

## Acceptance for the bounded milestone

- Fixture tests prove pre-freeze registration; rejection of late/backdated/historical registration; lock contention; retry idempotency; crash recovery; and no duplicate source/model calls.
- One fingerprint is asserted across all arms; controls use no post-freeze values; accepted, failed, blocked, abstained, and missing AI records all survive reporting.
- A real checkpoint can end honestly with future horizons `pending`; synthetic future bars mature only fixture campaigns.
- Contract tests prove the daily-bar entry anchor, strict post-freeze selection, non-fill behavior, and no known-close fill.
- Cost tests distinguish measured usage, quote estimate, unknown billed cost, and actual billed receipt; unknown never becomes zero.
- Offline `verify` and two clean `report` runs reconstruct identical outputs after network/provider access is disabled.
- The runner has no broker-write dependency and no scheduler/install path. Deployment remains a reviewed PR plus explicit operator activation; HOLD is unchanged.
