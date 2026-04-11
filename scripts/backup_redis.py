#!/usr/bin/env python3
"""
backup_redis.py — Daily Redis State Backup

Snapshots 8 critical Redis keys to ~/trading-system/backups/YYYY-MM-DD.json.
Retains the most recent 7 daily files; older files are pruned automatically.

Usage (from repo root, after source ~/.trading_env):
    PYTHONPATH=scripts python3 scripts/backup_redis.py

Suggested cron (add with: crontab -e):
# 30 16 * * 1-5  cd ~/trading-system && source ~/.trading_env && PYTHONPATH=scripts python3 scripts/backup_redis.py >> ~/trading-system/logs/backup.log 2>&1
"""

import glob
import json
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
    if backup_dir is None:  # pragma: no cover
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
                snapshot[key] = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
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
    excess = max(0, len(files) - RETAIN_DAYS)
    for path in files[:excess]:
        os.remove(path)
        print(f"[Backup] Pruned: {path}")


if __name__ == "__main__":  # pragma: no cover
    r = get_redis()
    backup(r)
