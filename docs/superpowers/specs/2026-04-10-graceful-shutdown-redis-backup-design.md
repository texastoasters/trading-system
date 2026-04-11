# Design: Graceful Shutdown + Redis State Backup

**Date:** 2026-04-10  
**Status:** Approved  
**Branch:** to be created from main

---

## Overview

Two independent safety features:

1. **Graceful shutdown** — executor and portfolio_manager daemons install a SIGTERM/SIGINT handler that sets a flag; the `while` loop condition checks the flag so the current cycle finishes before exiting. Prevents mid-cycle state corruption when `start_trading_system.sh --stop` is called.

2. **Redis state backup** — new standalone script `scripts/backup_redis.py` snapshots 8 critical Redis keys to `~/trading-system/backups/YYYY-MM-DD.json` daily, retaining 7 days. Provides a clean baseline for `reconcile.py` and crash recovery.

---

## Feature 1: Graceful Shutdown

### Scope

Affects: `skills/executor/executor.py`, `skills/portfolio_manager/portfolio_manager.py`.

Watcher is **excluded** — it is cron-triggered (single scan per invocation), not a long-running daemon. Supervisor's daemon loop runs health checks on a timer and has no mid-cycle state risk worth protecting.

### Pattern

Each daemon gets a module-level shutdown flag and a signal handler installed at the start of `daemon_loop()`:

```python
import signal

_shutdown = False

def _handle_sigterm(signum, frame):
    global _shutdown
    _shutdown = True
    print("[Agent] SIGTERM received — finishing current cycle then exiting")
```

The `while True:` loop becomes `while not _shutdown:`. On the next iteration boundary (after the current cycle completes), the loop exits cleanly.

### Blocking call behaviour

`pubsub.get_message(timeout=60)` blocks for up to 60 seconds. When SIGTERM fires, the signal handler runs, sets the flag, and the blocking call returns `None` (Python delivers signals between bytecodes — the timeout may be interrupted early or complete; either way, the flag is checked on the next loop iteration). The 10-second force-kill in `start_trading_system.sh` is the backstop for any stuck cycle.

SIGINT is also handled (same handler) so Ctrl-C during development produces a clean exit.

### No Redis writes on shutdown

Positions and stop orders are already persisted to Redis on every state change. No flush needed.

### Testing

`daemon_loop` is `# pragma: no cover`. The signal handler and flag are module-level and can be tested directly:

- Unit test: call `_handle_sigterm(signal.SIGTERM, None)` → `_shutdown` is `True`
- Unit test: reset `_shutdown = False`, verify it starts as False (baseline)
- Both functions are importable and testable without starting a daemon

---

## Feature 2: Redis State Backup

### New file

`scripts/backup_redis.py` — standalone script, same invocation pattern as `reconcile.py`.

```
Usage (from repo root, after source ~/.trading_env):
    PYTHONPATH=scripts python3 scripts/backup_redis.py
```

### Keys snapshotted

```python
BACKUP_KEYS = [
    Keys.POSITIONS,
    Keys.SIMULATED_EQUITY,
    Keys.PEAK_EQUITY,
    Keys.DRAWDOWN,
    Keys.SYSTEM_STATUS,
    Keys.UNIVERSE,
    Keys.RISK_MULTIPLIER,
    Keys.PDT_COUNT,
]
```

`DAILY_PNL` is excluded — it resets at market open and has no recovery value.
Cooldown keys, heartbeats, rejected signals, and watchlist are excluded — ephemeral.

### Output format

`~/trading-system/backups/YYYY-MM-DD.json`:

```json
{
  "timestamp": "2026-04-10T16:30:01",
  "trading:positions": {"SPY": {...}, "QQQ": {...}},
  "trading:simulated_equity": "4823.12",
  "trading:peak_equity": "5000.0",
  "trading:drawdown": "3.54",
  "trading:system_status": "active",
  "trading:universe": {...},
  "trading:risk_multiplier": "1.0",
  "trading:pdt:count": "1"
}
```

Values are stored as strings (raw Redis values) except for keys whose values are JSON objects (POSITIONS, UNIVERSE) — those are parsed to objects for readability.

### Rotation

After writing, glob `~/trading-system/backups/*.json`, sort ascending (ISO date names sort correctly), delete any files beyond the most recent 7.

### Cron

Not auto-installed. Documented in script header:

```
# Suggested cron (add with: crontab -e):
# 30 16 * * 1-5  cd ~/trading-system && source ~/.trading_env && PYTHONPATH=scripts python3 scripts/backup_redis.py >> ~/trading-system/logs/backup.log 2>&1
```

Runs at 4:30 PM ET Mon–Fri, after the 4:15 PM screener cycle and EOD review.

### Alerts

None. A missed backup is not a safety event. Output goes to stdout (captured by cron redirect to `backup.log`).

### Testing

No Alpaca API dependency — testable with a mock Redis and a tmp directory:

- Unit test: backup written with correct keys and timestamp
- Unit test: old files beyond 7 days pruned (create 9 fake files, run backup, assert only 7+1 remain)
- Unit test: missing backup dir created automatically if absent
- Unit test: Redis key with None value handled gracefully (key missing from Redis)

---

## Files Changed

| File | Change |
|---|---|
| `skills/executor/executor.py` | Add `_shutdown` flag, `_handle_sigterm`, install in `daemon_loop`, change `while True` → `while not _shutdown` |
| `skills/portfolio_manager/portfolio_manager.py` | Same pattern as executor |
| `scripts/backup_redis.py` | New script |
| `skills/executor/test_executor.py` | Tests for `_shutdown` flag and `_handle_sigterm` |
| `skills/portfolio_manager/test_portfolio_manager.py` | Same tests |
| `scripts/test_backup_redis.py` | New test file |

---

## Out of Scope

- Auto-installing the cron entry (YAGNI — user manages crontab)
- Telegram alert on backup success or failure
- Restore functionality (reconcile.py already handles Redis ↔ Alpaca reconciliation)
- Backup of TimescaleDB (separate concern, handled by DB-level tooling)
- Graceful shutdown for supervisor or screener (cron-invoked, no mid-cycle risk)
