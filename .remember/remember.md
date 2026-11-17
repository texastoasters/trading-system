# Session State — Prospective Evidence (2026-11-16)

## Current feature branch

`feat/prospective-evidence`, v0.39.0, based on merged `c97f670` (PR231/v0.38.1).
Standalone read-only source capture into the already merged non-executing AI lab (PR230).
See `docs/PROSPECTIVE_EVIDENCE.md` and `docs/AI_DECISION_LAB.md` for interfaces and caveats.

- Credential-isolated fixed-host GET acquisition: Alpaca metadata-only news, explicit IEX/raw daily bars, real extended-session calendar; public official BEA ICS, no guessed BLS/FOMC times.
- Completed-session candle/quant evidence, explicit synthetic or caller-supplied book origin, immutable complete/failed archives and offline semantic reconstruction. Full calendar retained; fixed30-day event context horizon limits irrelevant model input.
- Actual fresh CLI collection, three-role GPT-6.1-Sol inference, semantic reconstruction and deterministic decision replay were exercised under run `prospective-20261117T031900Z`. The synthetic book remained100% cash with zero orders. Full Python suite passed; independent reviews and exact-head CI remain gates until recorded in the PR. Never reuse historical counts as current proof.
- No fixtures are actual source/model results. Receipt hashes of original HTTP bodies cannot be recomputed from projected metadata. Offline semantic reconstruction and decision replay are distinct proofs, neither establishes profit.

## Operational constraints

- Old openboog trading has a persistent service/cron/dashboard hold. Do not remove it, restart the bot, reset the account, alter broker orders or deploy feature code by SSH. The standalone credential-isolation helper lives only in remote cache, not the production trader.
- Full AI-first trader remains unfinished: continuous prospective collection/evaluation, experiment-account truth/execution lifecycle, exits/watchdogs and performance promotion are later slices.
- PR229 (`fix/task4-integration`) is separate; preserve it and the original local checkout's dirty wishlist/untracked docs.
- Prior negative paper cash/account divergence is dated incident evidence. Synthetic research cash is not a fresh broker observation, rescue, account reset or realized P&L.

## Process reminders

Changes via reviewed PR and CI/CD. No live-money authority, new subscriptions/top-ups or global Hermes configuration changes. Rule1 remains long-only/no borrowing/no debt. Preserve public-seam TDD and actual failures. **Keep Python + Elixir coverage at100%, with no coverage exclusions/ignore shortcuts.** Disclose pre-existing legacy gaps, never weaken the standard or conceal them.
