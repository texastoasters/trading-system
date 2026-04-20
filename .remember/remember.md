# Handoff

## State
v0.34.7. PR open for fix/watcher-alert-rsi2-na-and-dedup — two watcher alert fixes:
1. Entry alerts now show qualifying indicators only (RSI-2/IBS/DCH); no more N/A
2. Entry alert dedup via Redis `trading:entry_alerted:{symbol}:{strategy}` (TTL midnight ET)

## Next
- Merge PR, tag v0.34.7 on main

## Context
Both fixes in watcher.py run_cycle() + Keys.entry_alerted() added to config.py.
PM pub/sub channel still receives all signals; only Telegram is deduped.
