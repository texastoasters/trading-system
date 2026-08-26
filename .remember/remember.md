# Session State (2026-08-26)

## In flight

- **feat/paper-equity-as-capital (v0.37.0)** → branch `feat/paper-equity-as-capital`
  - Drop $5K simulated cap. Seed `trading:simulated_equity` from Alpaca paper `account.equity` (~$100K) on first start.
  - `init_redis_state` no longer writes equity/peak keys (would re-cap a wiped Redis at INITIAL_CAPITAL before verify_startup).
  - CLAUDE.md / AGENTS.md left untouched this PR (agent-instruction write blocked). README + PM SKILL.md + config comments updated.

## Openboog ops (not this PR)

- Files restored from git HEAD a6370f0. History wiped (FLUSHDB + truncate trades/signals/daily_summary). New Alpaca paper keys in both env files. systemd EnvironmentFile + start still pending until this PR is ready.

## Process reminders

- Bug fixes via PR + CI/CD only; never edit/deploy on the server directly. SSH is read-only for diagnosis. Ops restore / crash-loop stop / wipe / systemd env are ops, not feature deploys.
- TDD: failing test first. Keep Python + Elixir at 100% coverage; no coveralls-ignore shortcuts.
- Roadmap board (Project #1): pick top Todo by priority, move to In Progress before starting.
