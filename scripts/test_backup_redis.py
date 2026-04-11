"""
Tests for backup_redis.py

Run from repo root:
    PYTHONPATH=scripts pytest scripts/test_backup_redis.py -v
"""
import json
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock

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


# ── Core Write ───────────────────────────────────────────────

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
        # JSON keys parsed to objects
        assert isinstance(data[Keys.POSITIONS], dict)
        assert isinstance(data[Keys.UNIVERSE], dict)


# ── Rotation ─────────────────────────────────────────────────

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


# ── Edge Cases ───────────────────────────────────────────────

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

        # Redis returns None for all keys
        r = MagicMock()
        r.get.return_value = None

        backup(r, backup_dir=tmp_path)

        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        # Only timestamp present; no data keys
        assert list(data.keys()) == ["timestamp"]

    def test_invalid_json_for_json_key_stored_as_raw(self, tmp_path):
        from backup_redis import backup

        r = MagicMock()
        r.get.side_effect = lambda k: b"not-valid-json" if k == Keys.POSITIONS else None

        backup(r, backup_dir=tmp_path)

        data = json.loads(list(tmp_path.glob("*.json"))[0].read_text())
        # Stored raw (bytes decoded to string) rather than crashing
        assert Keys.POSITIONS in data
