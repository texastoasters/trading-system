# Economic Calendar Awareness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Block new entry signals on scheduled macro event days (FOMC, CPI, NFP) by adding `is_macro_event_day()` to the watcher's entry evaluation loop.

**Architecture:** New `scripts/economic_calendar.json` holds dated events. New `is_macro_event_day(calendar_path=None)` function in `watcher.py` reads the file and checks today's date — fails safe on any error. Hooked in after the earnings avoidance check in `generate_entry_signals`.

**Tech Stack:** Python stdlib only (`json`, `pathlib`). No new dependencies.

---

## Files

| File | Change |
|---|---|
| `scripts/economic_calendar.json` | New — 2026 FOMC + CPI + NFP dates |
| `skills/watcher/watcher.py` | Add `is_macro_event_day()`, add guard in `generate_entry_signals` |
| `skills/watcher/test_watcher.py` | New test class `TestIsMacroEventDay` + entry loop guard tests |

---

## Task 1: `is_macro_event_day()` — core function

**Files:**
- Modify: `skills/watcher/watcher.py`
- Modify: `skills/watcher/test_watcher.py`

- [ ] **Step 1: Write failing tests**

Find `class TestIsNearEarnings` in `skills/watcher/test_watcher.py` and add this new class directly after it (before `# ── generate_entry_signals`):

```python
# ── is_macro_event_day ────────────────────────────────────────

class TestIsMacroEventDay:
    def test_returns_true_when_today_in_calendar(self, tmp_path):
        from watcher import is_macro_event_day
        today = datetime.now().strftime("%Y-%m-%d")
        cal = [{"date": today, "event": "FOMC"}]
        cal_path = tmp_path / "calendar.json"
        cal_path.write_text(json.dumps(cal))
        assert is_macro_event_day(calendar_path=cal_path) is True

    def test_returns_false_when_today_not_in_calendar(self, tmp_path):
        from watcher import is_macro_event_day
        cal = [{"date": "2000-01-01", "event": "FOMC"}]
        cal_path = tmp_path / "calendar.json"
        cal_path.write_text(json.dumps(cal))
        assert is_macro_event_day(calendar_path=cal_path) is False

    def test_returns_false_when_file_missing(self, tmp_path):
        from watcher import is_macro_event_day
        assert is_macro_event_day(calendar_path=tmp_path / "nonexistent.json") is False

    def test_returns_false_when_json_malformed(self, tmp_path):
        from watcher import is_macro_event_day
        cal_path = tmp_path / "calendar.json"
        cal_path.write_text("not valid json {{")
        assert is_macro_event_day(calendar_path=cal_path) is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=scripts python3 -m pytest skills/watcher/test_watcher.py::TestIsMacroEventDay -v
```

Expected: `AttributeError: module 'watcher' has no attribute 'is_macro_event_day'`

- [ ] **Step 3: Add `is_macro_event_day` to watcher.py**

Add `from pathlib import Path` to the imports at the top of `skills/watcher/watcher.py` (alongside the existing `from datetime import datetime, timedelta`):

```python
from pathlib import Path
```

Then add the function directly after `is_near_earnings` (around line 145):

```python
_DEFAULT_CALENDAR_PATH = Path(__file__).parent.parent / "scripts" / "economic_calendar.json"


def is_macro_event_day(calendar_path=None):
    """Return True if today is a scheduled macro event day (FOMC, CPI, NFP).
    Fails safe — returns False on missing file, malformed JSON, or any error.
    """
    if calendar_path is None:
        calendar_path = _DEFAULT_CALENDAR_PATH
    try:
        events = json.loads(Path(calendar_path).read_text())
        today = datetime.now().strftime("%Y-%m-%d")
        return any(e["date"] == today for e in events)
    except Exception:
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=scripts python3 -m pytest skills/watcher/test_watcher.py::TestIsMacroEventDay -v
```

Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add skills/watcher/watcher.py skills/watcher/test_watcher.py
git commit -m "feat(watcher): add is_macro_event_day() with fail-safe JSON calendar"
```

---

## Task 2: Entry loop guard

**Files:**
- Modify: `skills/watcher/watcher.py`
- Modify: `skills/watcher/test_watcher.py`

- [ ] **Step 1: Write failing tests**

Add to `class TestGenerateEntrySignals` in `skills/watcher/test_watcher.py` (after the existing `test_skips_symbol_near_earnings` test):

```python
    def test_skips_symbol_on_macro_event_day(self):
        r = make_redis({Keys.WATCHLIST: json.dumps([make_watchlist_item()])})
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False), \
             patch('watcher.is_near_earnings', return_value=False), \
             patch('watcher.is_macro_event_day', return_value=True):
            from watcher import generate_entry_signals
            signals = generate_entry_signals(r, MagicMock(), MagicMock())
        assert signals == []

    def test_entry_proceeds_when_not_macro_event_day(self):
        item = make_watchlist_item("SPY", close=490.0, atr14=5.0, sma200=480.0)
        r = make_redis({
            Keys.WATCHLIST: json.dumps([item]),
            Keys.REGIME: json.dumps({"regime": "RANGING", "adx": 15.0}),
        })
        with patch('watcher.is_market_hours', return_value=True), \
             patch('watcher.check_whipsaw', return_value=False), \
             patch('watcher.is_near_earnings', return_value=False), \
             patch('watcher.is_macro_event_day', return_value=False), \
             patch('watcher.fetch_recent_bars', return_value=MagicMock()):
            from watcher import generate_entry_signals
            generate_entry_signals(r, MagicMock(), MagicMock())
            # Reaching here without skipping confirms the guard passed through
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=scripts python3 -m pytest skills/watcher/test_watcher.py::TestGenerateEntrySignals::test_skips_symbol_on_macro_event_day skills/watcher/test_watcher.py::TestGenerateEntrySignals::test_entry_proceeds_when_not_macro_event_day -v
```

Expected: both FAIL — `test_skips_symbol_on_macro_event_day` fails because the guard doesn't exist yet (signal is generated instead of skipped).

- [ ] **Step 3: Add guard to `generate_entry_signals` in watcher.py**

In `skills/watcher/watcher.py`, find the earnings avoidance block in `generate_entry_signals`:

```python
        # Earnings avoidance
        if is_near_earnings(symbol):
            print(f"  [Watcher] {symbol}: skipped (near earnings window)")
            continue
```

Add the macro event guard immediately after it:

```python
        # Earnings avoidance
        if is_near_earnings(symbol):
            print(f"  [Watcher] {symbol}: skipped (near earnings window)")
            continue

        # Economic calendar avoidance
        if is_macro_event_day():
            print(f"  [Watcher] {symbol}: skipped (macro event day)")
            continue
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=scripts python3 -m pytest skills/watcher/test_watcher.py::TestGenerateEntrySignals::test_skips_symbol_on_macro_event_day skills/watcher/test_watcher.py::TestGenerateEntrySignals::test_entry_proceeds_when_not_macro_event_day -v
```

Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add skills/watcher/watcher.py skills/watcher/test_watcher.py
git commit -m "feat(watcher): skip entries on macro event days (FOMC/CPI/NFP)"
```

---

## Task 3: Create economic_calendar.json with 2026 dates

**Files:**
- Create: `scripts/economic_calendar.json`

- [ ] **Step 1: Create the calendar file**

Create `scripts/economic_calendar.json` with 2026 FOMC, CPI, and NFP dates:

```json
[
  {"date": "2026-01-07", "event": "NFP"},
  {"date": "2026-01-14", "event": "CPI"},
  {"date": "2026-01-28", "event": "FOMC"},
  {"date": "2026-02-04", "event": "NFP"},
  {"date": "2026-02-11", "event": "CPI"},
  {"date": "2026-02-18", "event": "FOMC"},
  {"date": "2026-03-06", "event": "NFP"},
  {"date": "2026-03-11", "event": "CPI"},
  {"date": "2026-03-18", "event": "FOMC"},
  {"date": "2026-04-01", "event": "NFP"},
  {"date": "2026-04-10", "event": "CPI"},
  {"date": "2026-04-29", "event": "FOMC"},
  {"date": "2026-05-06", "event": "NFP"},
  {"date": "2026-05-13", "event": "CPI"},
  {"date": "2026-06-03", "event": "NFP"},
  {"date": "2026-06-10", "event": "CPI"},
  {"date": "2026-06-17", "event": "FOMC"},
  {"date": "2026-07-08", "event": "NFP"},
  {"date": "2026-07-15", "event": "CPI"},
  {"date": "2026-07-29", "event": "FOMC"},
  {"date": "2026-08-05", "event": "NFP"},
  {"date": "2026-08-12", "event": "CPI"},
  {"date": "2026-09-02", "event": "NFP"},
  {"date": "2026-09-09", "event": "CPI"},
  {"date": "2026-09-16", "event": "FOMC"},
  {"date": "2026-10-07", "event": "NFP"},
  {"date": "2026-10-14", "event": "CPI"},
  {"date": "2026-10-28", "event": "FOMC"},
  {"date": "2026-11-04", "event": "NFP"},
  {"date": "2026-11-12", "event": "CPI"},
  {"date": "2026-12-02", "event": "NFP"},
  {"date": "2026-12-09", "event": "CPI"},
  {"date": "2026-12-16", "event": "FOMC"}
]
```

**Verify dates against official sources before committing:**
- FOMC: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
- CPI: https://www.bls.gov/schedule/news_release/cpi.htm
- NFP: https://www.bls.gov/schedule/news_release/empsit.htm

- [ ] **Step 2: Commit**

```bash
git add scripts/economic_calendar.json
git commit -m "chore: add 2026 economic calendar (FOMC, CPI, NFP dates)"
```

---

## Task 4: Full suite + coverage + wishlist + version bump + PR

**Files:**
- Modify: `docs/FEATURE_WISHLIST.md`
- Modify: `VERSION`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run full watcher test suite**

```bash
PYTHONPATH=scripts python3 -m pytest skills/watcher/test_watcher.py -v
```

Expected: all tests pass, no regressions.

- [ ] **Step 2: Check coverage**

```bash
PYTHONPATH=scripts python3 -m pytest skills/watcher/test_watcher.py --cov --cov-report=term-missing 2>&1 | grep "watcher.py"
```

Expected: `skills/watcher/watcher.py  NNN  0  100%`

- [ ] **Step 3: Update FEATURE_WISHLIST.md**

In `docs/FEATURE_WISHLIST.md`, change:

```
- [ ] **Economic calendar awareness** — Block or reduce position sizes on FOMC days, CPI releases, and other high-vol macro events.
```

to:

```
- [x] **Economic calendar awareness** — Block entries on FOMC, CPI, and NFP days. Dates in `scripts/economic_calendar.json`, updated annually. PR #84.
```

Also update item 8 in the "Next Priority Wave" section:

```
8. ~~Economic calendar awareness~~ ✅ Done (PR #84): blocks entries on FOMC/CPI/NFP days via `scripts/economic_calendar.json`.
```

And update the last line:

```
*Last updated: 2026-04-10. Economic calendar awareness done (PR #84). Next: per-instrument P&L breakdown, trailing stop-loss.*
```

- [ ] **Step 4: Bump VERSION**

Change `VERSION` from `0.12.0` to `0.13.0`.

- [ ] **Step 5: Update CHANGELOG.md**

Add at the top (after the header block, before `## [0.12.0]`):

```markdown
## [0.13.0] - 2026-04-10

### Added
- **Economic calendar awareness** (PR #84): watcher skips new entry signals on FOMC decision days, CPI release days, and NFP (jobs report) days. Dates stored in `scripts/economic_calendar.json` (updated annually from official Fed/BLS schedules). Fails safe — missing or corrupt calendar file never halts trading. Crypto is not exempt (FOMC/CPI/NFP move BTC/USD). Exits always allowed through.
```

- [ ] **Step 6: Commit**

```bash
git add docs/FEATURE_WISHLIST.md VERSION CHANGELOG.md
git commit -m "chore: bump to v0.13.0 — economic calendar awareness"
```

- [ ] **Step 7: Invoke finishing-a-development-branch skill**
