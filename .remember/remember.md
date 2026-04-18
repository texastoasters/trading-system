# Handoff

## State
v0.34.5. Deploy pipeline fully working end-to-end.

## Deploy pipeline (complete)
- Tests pass → tag commit with VERSION → SSH deploy to prod via webfactory/ssh-agent
- DEPLOY_SSH_KEY: private key from ~/.ssh/trading-deploy (comment: github-actions-deploy)
- DEPLOY_HOST: Vultr public IP of openboog
- Prod server deploy key (~/.ssh/trading-system-repo, comment: openboog-deploy) in GitHub Deploy Keys for git pull

## Context
watcher.py: `os` import used by `_get_db()` (TSDB_PASSWORD env var) — keep it. Trailing `r.set(Keys.POSITIONS)` at end of `generate_exit_signals` is intentional — test verifies it as last call.
