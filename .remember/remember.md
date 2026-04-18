# Handoff

## State
v0.34.3. Deploy pipeline + README rewrite shipped together.

## What's in flight
- PR #140 (docs/readme-rewrite): README rewrite — docs only, ready to merge
- PR #141 (ci/deploy-on-merge): GitHub Actions deploy workflow — ready to merge once DEPLOY_SSH_KEY + DEPLOY_HOST secrets are added to the repo

## Deploy pipeline setup
- `DEPLOY_SSH_KEY`: Ed25519 private key (~/.ssh/trading-deploy) — Actions runner → prod SSH
- `DEPLOY_HOST`: Vultr public IP of openboog
- Prod server deploy key (~/.ssh/trading-system-repo) added to GitHub repo deploy keys — handles git pull on server
- SSH config on prod routes github.com through trading-system-repo key
- Verified: `ssh -T git@github.com` works on prod; `git pull` works

## Context
watcher.py: `os` import used by `_get_db()` (TSDB_PASSWORD env var) — keep it. Trailing `r.set(Keys.POSITIONS)` at end of `generate_exit_signals` is intentional — test verifies it as last call.
