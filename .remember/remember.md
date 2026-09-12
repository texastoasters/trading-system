# Session State (2026-09-12)

## In flight

- **fix/p0-safety-invariants (v0.37.1)** → branch `fix/p0-safety-invariants`
  - Fix TSMOM exit routing: preserve stop-losses, use `TSMOM_MAX_HOLD_DAYS`, and block RSI-2/prior-high mean-reversion exits.
  - Preserve cumulative high-water drawdown across daily reset; initialize missing peak state only.
  - Move defensive/critical temporary Tier 2/3 gates to `trading:disabled_tiers`; enforce at Watcher, TSMOM, and PM final approval boundaries without mutating permanent exclusions.
  - Regression suite: 1120 passed. Independent GPT-5.6-Sol review passed. No deployment or Redis change occurred.

## Openboog ops (not this PR)

- Open P0: `scripts/verify_alpaca.py` can submit a market order and overwrite/delete the production watchlist; do not run it on openboog until safely redesigned.
- Open controls: decide live 5% risk / 20-position overrides, allocation enforcement, PDT semantics, and missing schedule policy before profitability optimization.

## Process reminders

- Bug fixes via PR + CI/CD only; never edit/deploy on the server directly. SSH is read-only for diagnosis. Ops restore / crash-loop stop / wipe / systemd env are ops, not feature deploys.
- TDD: failing test first. Keep Python + Elixir at 100% coverage; no coveralls-ignore shortcuts.
- Roadmap board (Project #1): pick top Todo by priority, move to In Progress before starting.
