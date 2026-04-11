# Graceful Shutdown + Redis State Backup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add SIGTERM/SIGINT graceful shutdown to executor and PM daemons, and create a standalone Redis state backup script with 7-day rotation.

**Architecture:** Module-level `_shutdown` flag + signal handler installed in `daemon_loop()`; `while True:` → `while not _shutdown:`; signal handler and flag are module-level so they're importable and testable. Backup script is a standalone file that snapshots 8 Redis keys to JSON, auto-creates the backup dir, and prunes files beyond 7 days.

**Tech Stack:** Python `signal` stdlib, `glob`, `pathlib`; existing `config.py` Keys; existing mock patterns from test files.

---

## Files

| File | Change |
|---|---|
| `skills/executor/executor.py` | Add `_shutdown`, `_handle_sigterm`, install in `daemon_loop`, `while not _shutdown:` |
| `skills/executor/test_executor.py` | Tests for flag + handler |
| `skills/portfolio_manager/portfolio_manager.py` | Same pattern as executor |
| `skills/portfolio_manager/test_portfolio_manager.py` | Tests for flag + handler |
| `scripts/backup_redis.py` | New script — snapshot + rotation + dir creation + missing-key handling |
| `scripts/test_backup_redis.py` | Tests for all 4 backup behaviors |

---

## Task 1: Executor Graceful Shutdown

**Files:**
- Modify: `skills/executor/executor.py` (top of file, `daemon_loop`)
- Modify: `skills/executor/test_executor.py`

- [ ] **Step 1: Write failing tests**

Add to `skills/executor/test_executor.py` (before existing test classes):

```python
# ── Graceful Shutdown ────────────────────────────────────────

class TestGracefulShutdown:
    def setup_method(self):
        import executor
        executor._shutdown = False

    def teardown_method(self):
        import executor
        executor._shutdown = False

    def test_shutdown_flag_starts_false(self):
        import executor
        assert executor._shutdown is False

    def test_handle_sigterm_sets_shutdown(self):
        import executor
        executor._handle_sigterm(None, None)
        assert executor._shutdown is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py::TestGracefulShutdown -v
```

Expected: `AttributeError: module 'executor' has no attribute '_shutdown'`

- [ ] **Step 3: Add flag and handler to executor.py**

After the existing imports (after `from notify import ...` line), add:

```python
import signal

_shutdown = False


def _handle_sigterm(signum, frame):
    global _shutdown
    _shutdown = True
    print("[Executor] SIGTERM received — finishing current cycle then exiting")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py::TestGracefulShutdown -v
```

Expected: 2 PASSED

- [ ] **Step 5: Update daemon_loop to install handler and use flag**

In `skills/executor/executor.py`, replace the `daemon_loop` body:

```python
def daemon_loop():  # pragma: no cover
    """Listen for approved orders continuously."""
    global _shutdown
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    r = get_redis()
    init_redis_state(r)

    trading_client = TradingClient(
        config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, paper=config.PAPER_TRADING
    )

    verify_startup(trading_client, r)

    print("[Executor] Listening for approved orders...")

    pubsub = r.pubsub()
    pubsub.subscribe(Keys.APPROVED_ORDERS)

    while not _shutdown:
        r.set(Keys.heartbeat("executor"), datetime.now().isoformat())
        msg = pubsub.get_message(timeout=60)
        if msg is None or msg['type'] != 'message':
            _check_cancelled_stops(trading_client, r)
            continue

        try:
            order = json.loads(msg['data'])
            signal_type = order.get("signal_type", "")
            manual_tag = " 🖐 MANUAL" if signal_type == "manual_liquidation" else ""
            print(f"\n[Executor] Received {order['side']} order for {order['symbol']}{manual_tag}")
            process_order(r, trading_client, order)
        except Exception as e:
            print(f"[Executor] Error: {e}")
            critical_alert(f"Executor error: {e}")

    print("[Executor] Shutdown complete.")
```

- [ ] **Step 6: Run full executor test suite**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py -v
```

Expected: all tests pass, no regressions.

- [ ] **Step 7: Commit**

```bash
git add skills/executor/executor.py skills/executor/test_executor.py
git commit -m "feat(executor): graceful SIGTERM/SIGINT shutdown"
```

---

## Task 2: Portfolio Manager Graceful Shutdown

**Files:**
- Modify: `skills/portfolio_manager/portfolio_manager.py` (top of file, `daemon_loop`)
- Modify: `skills/portfolio_manager/test_portfolio_manager.py`

- [ ] **Step 1: Write failing tests**

Add to `skills/portfolio_manager/test_portfolio_manager.py` (before existing test classes):

```python
# ── Graceful Shutdown ────────────────────────────────────────

class TestGracefulShutdown:
    def setup_method(self):
        import portfolio_manager
        portfolio_manager._shutdown = False

    def teardown_method(self):
        import portfolio_manager
        portfolio_manager._shutdown = False

    def test_shutdown_flag_starts_false(self):
        import portfolio_manager
        assert portfolio_manager._shutdown is False

    def test_handle_sigterm_sets_shutdown(self):
        import portfolio_manager
        portfolio_manager._handle_sigterm(None, None)
        assert portfolio_manager._shutdown is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=scripts pytest skills/portfolio_manager/test_portfolio_manager.py::TestGracefulShutdown -v
```

Expected: `AttributeError: module 'portfolio_manager' has no attribute '_shutdown'`

- [ ] **Step 3: Add flag and handler to portfolio_manager.py**

After `from notify import notify` line, add:

```python
import signal

_shutdown = False


def _handle_sigterm(signum, frame):
    global _shutdown
    _shutdown = True
    print("[PM] SIGTERM received — finishing current cycle then exiting")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=scripts pytest skills/portfolio_manager/test_portfolio_manager.py::TestGracefulShutdown -v
```

Expected: 2 PASSED

- [ ] **Step 5: Update daemon_loop**

In `skills/portfolio_manager/portfolio_manager.py`, replace `daemon_loop`:

```python
def daemon_loop():  # pragma: no cover
    """Listen for signals continuously."""
    global _shutdown
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    print("[PM] Starting daemon mode — listening for signals...")

    r = get_redis()
    init_redis_state(r)
    r.set(Keys.heartbeat("portfolio_manager"), datetime.now().isoformat())

    pubsub = r.pubsub()
    pubsub.subscribe(Keys.SIGNALS)

    while not _shutdown:
        # Update heartbeat on every iteration (fires every ~60s when idle)
        r.set(Keys.heartbeat("portfolio_manager"), datetime.now().isoformat())
        msg = pubsub.get_message(timeout=60)
        if msg is None or msg['type'] != 'message':
            continue

        try:
            signal = json.loads(msg['data'])
            print(f"\n[PM] Received {signal.get('signal_type', '?')} signal for {signal.get('symbol', '?')}")
            process_signal(r, signal)
        except Exception as e:
            print(f"[PM] Error processing signal: {e}")
            from notify import critical_alert
            critical_alert(f"Portfolio Manager error: {e}")

    print("[PM] Shutdown complete.")
```

- [ ] **Step 6: Run full PM test suite**

```bash
PYTHONPATH=scripts pytest skills/portfolio_manager/test_portfolio_manager.py -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add skills/portfolio_manager/portfolio_manager.py skills/portfolio_manager/test_portfolio_manager.py
git commit -m "feat(portfolio_manager): graceful SIGTERM/SIGINT shutdown"
```

---

## Task 3: Redis Backup Script — Core Write

**Files:**
- Create: `scripts/backup_redis.py`
- Create: `scripts/test_backup_redis.py`

- [ ] **Step 1: Write failing test for backup write**

Create `scripts/test_backup_redis.py`:

```python
"""
Tests for backup_redis.py

Run from repo root:
    PYTHONPATH=scripts pytest scripts/test_backup_redis.py -v
"""
import json
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime

sys.path.insert(0, "scripts")

# Mock redis before import
if "redis" not in sys.modules:
    sys.modules["redis"] = MagicMock()

import config
from config import Keys


def make_mock_redis(store: dict = None):
    base = {
        Keys.POSITIONS: json.dumps({"SPY": {"quantity": 10, "entry_price": 500.0}}),
        Keys.SIMULATED_EQUITY: "4823.12",
        Keys.PEAK_EQUITY: "5000.0",
        Keys.DRAWDOWN: "3.54",
        Keys.SYSTEM_STATUS: "active",
        Keys.UNIVERSE: json.dumps({"tier1": ["SPY"], "tier2": [], "tier3": []}),
        Keys.RISK_MULTIPLIER: "1.0",
        Keys.PDT_COUNT: "1",
    }
    if store:
        base.update(store)
    r = MagicMock()
    r.get.side_effect = lambda k: base.get(k)
    return r


class TestBackupWrite:
    def test_backup_written_with_correct_keys_and_timestamp(self, tmp_path):
        from backup_redis import backup

        r = make_mock_redis()
        backup(r, backup_dir=tmp_path)

        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1

        data = json.loads(files[0].read_text())
        assert "timestamp" in data
        assert data[Keys.SIMULATED_EQUITY] == "4823.12"
        assert data[Keys.PEAK_EQUITY] == "5000.0"
        assert data[Keys.DRAWDOWN] == "3.54"
        assert data[Keys.SYSTEM_STATUS] == "active"
        assert data[Keys.RISK_MULTIPLIER] == "1.0"
        assert data[Keys.PDT_COUNT] == "1"
        # JSON keys parsed
        assert isinstance(data[Keys.POSITIONS], dict)
        assert isinstance(data[Keys.UNIVERSE], dict)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=scripts pytest scripts/test_backup_redis.py::TestBackupWrite -v
```

Expected: `ModuleNotFoundError: No module named 'backup_redis'`

- [ ] **Step 3: Create scripts/backup_redis.py**

```python
#!/usr/bin/env python3
"""
backup_redis.py — Daily Redis State Backup

Snapshots 8 critical Redis keys to ~/trading-system/backups/YYYY-MM-DD.json.
Retains the most recent 7 daily files; older files are pruned.

Usage (from repo root, after source ~/.trading_env):
    PYTHONPATH=scripts python3 scripts/backup_redis.py

Suggested cron (add with: crontab -e):
# 30 16 * * 1-5  cd ~/trading-system && source ~/.trading_env && PYTHONPATH=scripts python3 scripts/backup_redis.py >> ~/trading-system/logs/backup.log 2>&1
"""

import json
import glob
import os
from datetime import datetime
from pathlib import Path

from config import Keys, get_redis

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

JSON_KEYS = {Keys.POSITIONS, Keys.UNIVERSE}

RETAIN_DAYS = 7

DEFAULT_BACKUP_DIR = Path.home() / "trading-system" / "backups"


def backup(r, backup_dir=None):
    """Snapshot BACKUP_KEYS to a dated JSON file and prune old files."""
    if backup_dir is None:
        backup_dir = DEFAULT_BACKUP_DIR

    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    snapshot = {"timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
    for key in BACKUP_KEYS:
        raw = r.get(key)
        if raw is None:
            continue
        if key in JSON_KEYS:
            try:
                snapshot[key] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                snapshot[key] = raw
        else:
            snapshot[key] = raw

    date_str = datetime.now().strftime("%Y-%m-%d")
    out_path = backup_dir / f"{date_str}.json"
    out_path.write_text(json.dumps(snapshot, indent=2))
    print(f"[Backup] Written: {out_path}")

    _prune(backup_dir)


def _prune(backup_dir):
    """Delete all but the RETAIN_DAYS most recent backup files."""
    files = sorted(glob.glob(str(backup_dir / "*.json")))
    excess = len(files) - RETAIN_DAYS
    for path in files[:excess]:
        os.remove(path)
        print(f"[Backup] Pruned: {path}")


if __name__ == "__main__":  # pragma: no cover
    r = get_redis()
    backup(r)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH=scripts pytest scripts/test_backup_redis.py::TestBackupWrite -v
```

Expected: 1 PASSED

- [ ] **Step 5: Commit**

```bash
git add scripts/backup_redis.py scripts/test_backup_redis.py
git commit -m "feat(backup): Redis state backup script with timestamped JSON"
```

---

## Task 4: Backup Rotation

**Files:**
- Modify: `scripts/test_backup_redis.py`

- [ ] **Step 1: Write failing test for rotation**

Add to `scripts/test_backup_redis.py`:

```python
class TestBackupRotation:
    def test_old_files_pruned_beyond_7_days(self, tmp_path):
        from backup_redis import backup

        # Create 9 existing backups
        for i in range(1, 10):
            (tmp_path / f"2026-04-0{i}.json").write_text("{}")

        r = make_mock_redis()
        backup(r, backup_dir=tmp_path)

        remaining = sorted(tmp_path.glob("*.json"))
        # 9 old + 1 new = 10 total, prune to 7
        assert len(remaining) == 7

    def test_fewer_than_7_files_not_pruned(self, tmp_path):
        from backup_redis import backup

        # Create 3 existing backups
        for i in range(1, 4):
            (tmp_path / f"2026-04-0{i}.json").write_text("{}")

        r = make_mock_redis()
        backup(r, backup_dir=tmp_path)

        remaining = list(tmp_path.glob("*.json"))
        # 3 old + 1 new = 4, all kept
        assert len(remaining) == 4
```

- [ ] **Step 2: Run tests to verify they pass (rotation already implemented)**

```bash
PYTHONPATH=scripts pytest scripts/test_backup_redis.py::TestBackupRotation -v
```

Expected: 2 PASSED (rotation implemented in Task 3's `_prune`)

- [ ] **Step 3: Commit**

```bash
git add scripts/test_backup_redis.py
git commit -m "test(backup): rotation — prune beyond 7, keep fewer-than-7"
```

---

## Task 5: Dir Auto-Creation + Missing Key Handling

**Files:**
- Modify: `scripts/test_backup_redis.py`

- [ ] **Step 1: Write failing tests**

Add to `scripts/test_backup_redis.py`:

```python
class TestBackupEdgeCases:
    def test_missing_backup_dir_created_automatically(self, tmp_path):
        from backup_redis import backup

        new_dir = tmp_path / "does_not_exist"
        assert not new_dir.exists()

        r = make_mock_redis()
        backup(r, backup_dir=new_dir)

        assert new_dir.exists()
        assert len(list(new_dir.glob("*.json"))) == 1

    def test_missing_redis_key_omitted_from_backup(self, tmp_path):
        from backup_redis import backup

        # Redis returns None for all keys (nothing set)
        r = MagicMock()
        r.get.return_value = None

        backup(r, backup_dir=tmp_path)

        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        # Only timestamp present; no data keys
        assert list(data.keys()) == ["timestamp"]
```

- [ ] **Step 2: Run tests to verify they pass (both behaviors already implemented)**

```bash
PYTHONPATH=scripts pytest scripts/test_backup_redis.py::TestBackupEdgeCases -v
```

Expected: 2 PASSED

- [ ] **Step 3: Commit**

```bash
git add scripts/test_backup_redis.py
git commit -m "test(backup): dir auto-creation and missing Redis key handling"
```

---

## Task 6: Full Suite + Coverage + Finish Branch

- [ ] **Step 1: Run full executor suite**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py -v
```

Expected: all tests pass.

- [ ] **Step 2: Run full PM suite**

```bash
PYTHONPATH=scripts pytest skills/portfolio_manager/test_portfolio_manager.py -v
```

Expected: all tests pass.

- [ ] **Step 3: Run full backup suite**

```bash
PYTHONPATH=scripts pytest scripts/test_backup_redis.py -v
```

Expected: all tests pass.

- [ ] **Step 4: Check coverage**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py skills/portfolio_manager/test_portfolio_manager.py scripts/test_backup_redis.py --cov=skills/executor/executor --cov=skills/portfolio_manager/portfolio_manager --cov=scripts/backup_redis --cov-report=term-missing
```

Expected: 100% on all three modules (daemon_loop is `# pragma: no cover`; new `_handle_sigterm` + `_shutdown` are module-level and covered).

- [ ] **Step 5: Update FEATURE_WISHLIST.md**

Mark items 5 and 6 done in `docs/FEATURE_WISHLIST.md`:

```
- [x] **Automated daily Redis state backup** — `scripts/backup_redis.py`: snapshots 8 keys to `~/trading-system/backups/YYYY-MM-DD.json`, 7-day rotation, suggested cron at 4:30 PM ET Mon–Fri. PR #83.
- [x] **Graceful shutdown** — executor and PM install SIGTERM/SIGINT handler; current cycle completes before exit. PR #83.
```

Also update the "Next Priority Wave" section to reflect both done.

- [ ] **Step 6: Bump VERSION and CHANGELOG**

`VERSION`: `0.12.0`

Add to top of `CHANGELOG.md`:

```markdown
## [0.12.0] - 2026-04-10

### Added
- **Graceful shutdown** (PR #83): executor and portfolio_manager daemons install SIGTERM/SIGINT handlers. `while True:` → `while not _shutdown:` — current cycle completes before exit. Prevents mid-cycle state corruption when `start_trading_system.sh --stop` is called.
- **Automated Redis state backup** (PR #83): new `scripts/backup_redis.py` snapshots 8 critical keys (`positions`, `simulated_equity`, `peak_equity`, `drawdown`, `system_status`, `universe`, `risk_multiplier`, `pdt_count`) to `~/trading-system/backups/YYYY-MM-DD.json`. Retains 7 days, prunes older files. Suggested cron at 4:30 PM ET Mon–Fri. No Alpaca dependency — testable standalone.
```

- [ ] **Step 7: Commit version bump**

```bash
git add docs/FEATURE_WISHLIST.md VERSION CHANGELOG.md
git commit -m "chore: bump to v0.12.0 — graceful shutdown + Redis backup"
```

- [ ] **Step 8: Invoke finishing-a-development-branch skill**
