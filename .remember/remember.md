# Handoff

## State
v0.34.4. CI pipeline cleaned up — Node.js 24 opt-in + coveralls/comment jobs skipped on push to main.

## Open PRs
- PR #140 (docs/readme-rewrite): README rewrite — docs only, ready to merge
- PR #142 (fix/actions-node24): Node.js 24 + coveralls/comment PR-only gate — ready to merge

## Deploy pipeline
- `DEPLOY_SSH_KEY` + `DEPLOY_HOST` secrets must be added before deploy workflow fires
- Prod server deploy key (~/.ssh/trading-system-repo) configured for git pull from GitHub
- `ssh -T git@github.com` verified working on prod

## Context
watcher.py: `os` import used by `_get_db()` (TSDB_PASSWORD env var) — keep it. Trailing `r.set(Keys.POSITIONS)` at end of `generate_exit_signals` is intentional — test verifies it as last call.
