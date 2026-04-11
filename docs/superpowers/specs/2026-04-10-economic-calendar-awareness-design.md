# Design: Economic Calendar Awareness

**Date:** 2026-04-10  
**Status:** Approved  
**Branch:** feat/economic-calendar-awareness

---

## Overview

Block new entry signals on scheduled high-volatility macro event days: FOMC decisions, CPI releases, and NFP (jobs report). RSI-2 mean reversion fails on these days because macro surprises override technical setups — a stock that looks oversold may continue falling after the event. Exits are always allowed through.

---

## Scope

**In scope:**
- FOMC decision days (~8/year)
- CPI release days (~12/year)
- NFP (non-farm payroll) days (~12/year)
- Entry block applies to all symbols including crypto (FOMC/CPI/NFP move BTC/USD too)
- Block applies to the event day only (screener runs at 4:15 PM ET; by then 8:30 AM releases are fully priced in; FOMC at 2 PM is close enough to treat same-day)

**Out of scope:**
- PPI, GDP, PCE, and other second-tier macro events (lower vol impact, more maintenance)
- Day-after blocking (data is known; next-morning signals are valid)
- Telegram alert when entries are blocked (not a safety event)
- Dashboard indicator for blocked days (YAGNI)

---

## Data Source

`scripts/economic_calendar.json` — a committed JSON file maintained manually when the Fed and BLS publish annual schedules (typically December for the following year). No API dependency, no network calls, no failure modes from external services.

Format:

```json
[
  {"date": "2026-01-09", "event": "NFP"},
  {"date": "2026-01-14", "event": "CPI"},
  {"date": "2026-01-28", "event": "FOMC"},
  {"date": "2026-02-06", "event": "NFP"},
  {"date": "2026-02-11", "event": "CPI"}
]
```

Fields:
- `date`: ISO 8601 date string (`YYYY-MM-DD`)
- `event`: one of `"FOMC"`, `"CPI"`, `"NFP"`

Dates are populated from official sources:
- FOMC: [federalreserve.gov/monetarypolicy/fomccalendars.htm](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)
- CPI: [bls.gov/schedule/news_release/cpi.htm](https://www.bls.gov/schedule/news_release/cpi.htm)
- NFP: [bls.gov/schedule/news_release/empsit.htm](https://www.bls.gov/schedule/news_release/empsit.htm)

---

## Implementation

### New function: `is_macro_event_day()` in `watcher.py`

```python
DEFAULT_CALENDAR_PATH = Path(__file__).parent.parent / "scripts" / "economic_calendar.json"

def is_macro_event_day(calendar_path=None):
    """Return True if today is a scheduled macro event day. Fails safe (returns False on any error)."""
    if calendar_path is None:
        calendar_path = DEFAULT_CALENDAR_PATH
    try:
        events = json.loads(Path(calendar_path).read_text())
        today = datetime.now().strftime("%Y-%m-%d")
        return any(e["date"] == today for e in events)
    except Exception:
        return False
```

Accepts an optional `calendar_path` for testability. Returns `False` on missing file, malformed JSON, or any other error — a missing or corrupt calendar never halts trading.

### Hook in entry evaluation loop

In `watcher.py`'s entry evaluation loop, after the earnings avoidance check:

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

`is_macro_event_day()` is called once per symbol but reads the same file each time — acceptable given the small file size and low call frequency (~17 symbols/cycle). No caching needed.

No changes to screener, PM, executor, or supervisor.

---

## Files Changed

| File | Change |
|---|---|
| `scripts/economic_calendar.json` | New file — 2026 FOMC + CPI + NFP dates |
| `skills/watcher/watcher.py` | Add `is_macro_event_day()`, add guard in entry loop |
| `skills/watcher/test_watcher.py` | Tests for `is_macro_event_day()` + entry loop guard |

---

## Testing

`is_macro_event_day()` accepts `calendar_path` so tests use `tmp_path` fixtures — no dependency on the real calendar file.

- Today in calendar → `True`
- Today not in calendar → `False`
- File missing → `False` (fail safe)
- Malformed JSON → `False` (fail safe)
- Entry loop: `is_macro_event_day()` returns `True` → symbol skipped, no signal published
- Entry loop: `is_macro_event_day()` returns `False` → evaluation proceeds normally

---

## Out of Scope

- Dynamic calendar fetching from an API
- Day-after blocking
- Telegram alert on blocked entries
- PPI, GDP, PCE events
- Cron entry (watcher runs as a daemon, not cron-triggered)
