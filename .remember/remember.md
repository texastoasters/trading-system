# Handoff

## State
feat/log-tailing → PR #100 open. v0.26.0. 331 tests, 100% coverage. All wishlist items #1 + #5 done.

## Next
1. Merge PR #100 after review
2. Tag v0.26.0 after merge
3. On VPS: `sudo cp scripts/logrotate.conf /etc/logrotate.d/trading-system` to activate log rotation

## Context
logrotate.conf has two stanzas (static-name files get dateext, date-suffixed daemon logs don't — avoids double-dated filenames). docker log redirectors use `--since <now>` to skip history replay on restart.
