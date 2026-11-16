# Session State — AI Decision Lab (2026-11-15)

## Current feature branch

`feat/ai-decision-lab`, v0.38.0, based on `9d695cb` (merged PR228). Broker-isolated research/shadow capability; not a replacement trading deployment. See `docs/AI_DECISION_LAB.md` for the standalone input/CLI/receipt/replay contract.

- Bounded actual AI research → CIO portfolio targets/cash → critic; deterministic validity checks and non-executable monetary preview.
- Existing Nous OAuth adapter with fresh allowance/catalog checks; no new billing/provider configuration. Managed runtime launcher avoids obsolete Hermes venv dependencies.
- Preserved actual dated paper evidence exercised with GPT-6.1-Sol through all3 roles and deterministic offline replay. This is historical diagnostics, not profit evidence or a current broker snapshot.
- Parent review added strict no-overallocation and cent-rounding regressions; residuals retained as cash.
- Full Python tests, independent review, coverage and remote CI receipts are recorded in the PR. Do not carry a historical pass count forward without rerunning.

## Operational constraints

- The old openboog trading system has an explicit persistent service/cron/dashboard hold. Do not remove it, restart the bot, reset the account, alter broker orders or deploy this feature by SSH.
- Full AI-first trader is not delivered by this lab: continuous data detectors, isolated experiment-account truth/execution lifecycle, independent exit/monitor/notification operations and prospective performance evaluation remain later slices.
- PR229 (`fix/task4-integration`) is separate pre-existing work; preserve it. The original local checkout's dirty feature wishlist/untracked docs were not incorporated here.
- Prior audit found negative paper cash, account/ledger divergence and final-fill/crypto identity/protection defects. Disposable paper losses are experiment data, not a reason to spend effort rescuing fictional capital or erase evidence.

## Process reminders

Changes via PR review and CI/CD; never push feature code directly to openboog. No live-money authority, unapproved subscriptions/top-ups, or global Hermes configuration changes. Rule1 stays long-only/no borrowing/no debt. Use actual tool output, preserve failures and keep tests at public seams. **Keep Python + Elixir coverage at100%, with no coverage exclusions/ignore shortcuts.** Existing uncovered legacy lines must be reported explicitly, not concealed or used to weaken that standing standard.
