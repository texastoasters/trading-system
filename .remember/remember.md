# Handoff

## State
v0.26.1 bugfix release complete. Two bugs fixed on `fix/log-volume-mount`:
1. Trades no data → added Ecto migration 20260414000002 for exit_reason column
2. Log tailing empty → fixed docker-compose.yml volume `${HOME}/trading-system/logs` → `./logs`
Schema management now lives in Ecto migrations (dashboard/priv/repo/migrations/). init-db/ SQL scripts deleted.

## Next
1. cpr `fix/log-volume-mount` → merge → tag v0.26.1
2. Switch back to `feat/langalpha-research-layer` to run LangAlpha spike (see docs/superpowers/plans/2026-04-14-langalpha-spike.md)

## Context
Production DB needed manual schema_migrations bootstrap before migration 2 could run (init-db scripts created DB without Ecto). Already done. Future migrations deploy cleanly via container start.
