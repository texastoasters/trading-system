# Volume Filter on Entries — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent entry signals on thin-volume days by skipping instruments where today's volume < 50% of the 20-day ADV.

**Architecture:** Add `volume` to `fetch_daily_bars` return dict; add a one-line gate in `scan_instrument` after the NaN guard; expose `volume_ratio` in the result dict for observability. Single new constant in `config.py`. No other files change.

**Tech Stack:** Python, NumPy, pytest. All changes in `skills/screener/` and `scripts/config.py`.

---

## File Map

| File | Change |
|------|--------|
| `scripts/config.py` | Add `MIN_VOLUME_RATIO = 0.5` |
| `skills/screener/screener.py` | Add `volume` to `fetch_daily_bars`; add gate + `volume_ratio` to `scan_instrument` |
| `skills/screener/test_screener.py` | Add `.volume` to `make_bar`; add `volume` to `make_price_data`; add volume gate tests; update `test_result_contains_expected_fields` |
| `CHANGELOG.md` | Insert v0.17.0 entry (missing between v0.18.0 and v0.16.0) |
| `docs/CHANGELOG.md` | Delete (stale duplicate; root is authoritative) |
| `VERSION` | Bump to 0.19.0 |
| `docs/FEATURE_WISHLIST.md` | Mark item 6 (volume filter) done |
| `.remember/remember.md` | Update with v0.19.0 info |

---

## Task 1: Add volume to test helpers and `fetch_daily_bars`

**Files:**
- Modify: `skills/screener/test_screener.py:33-58`
- Modify: `skills/screener/screener.py:52-68`
- Modify: `scripts/config.py` (after `RSI2_ENTRY_AGGRESSIVE` block, ~line 113)

### Step 1.1: Write failing test — `fetch_daily_bars` returns `volume`

Add to `TestFetchDailyBars` in `skills/screener/test_screener.py`:

```python
def test_returns_volume_array_for_equity(self):
    bars = [make_bar(close=100.0 + i) for i in range(210)]
    stock_client = MagicMock()
    stock_client.get_stock_bars.return_value = {"SPY": bars}

    from screener import fetch_daily_bars
    result = fetch_daily_bars("SPY", stock_client, MagicMock())
    assert result is not None
    assert 'volume' in result
    assert len(result['volume']) == 210
    assert result['volume'][0] == 1000.0  # make_bar default volume
```

- [ ] Add the test above to `TestFetchDailyBars`

### Step 1.2: Update `make_bar` to include `.volume`

In `skills/screener/test_screener.py`, change `make_bar`:

```python
def make_bar(close=100.0, high=101.0, low=99.0, volume=1000.0):
    bar = MagicMock()
    bar.timestamp.strftime.return_value = "2024-01-01"
    bar.close = close
    bar.high = high
    bar.low = low
    bar.volume = volume
    return bar
```

- [ ] Update `make_bar` as above

### Step 1.3: Run test to verify it fails

```bash
PYTHONPATH=scripts pytest skills/screener/test_screener.py::TestFetchDailyBars::test_returns_volume_array_for_equity -v
```

Expected: FAIL — `AssertionError: assert 'volume' in result`

- [ ] Run; confirm failure

### Step 1.4: Add volume to `fetch_daily_bars` return dict (`screener.py`)

In `screener.py`, change the return statement of `fetch_daily_bars` (currently at ~line 63):

```python
        return {
            'dates': [b.timestamp.strftime("%Y-%m-%d") for b in bar_list],
            'high': np.array([float(b.high) for b in bar_list]),
            'low': np.array([float(b.low) for b in bar_list]),
            'close': np.array([float(b.close) for b in bar_list]),
            'volume': np.array([float(b.volume) for b in bar_list]),
        }
```

- [ ] Apply change to `screener.py`

### Step 1.5: Run test to verify it passes

```bash
PYTHONPATH=scripts pytest skills/screener/test_screener.py::TestFetchDailyBars::test_returns_volume_array_for_equity -v
```

Expected: PASS

- [ ] Run; confirm pass

### Step 1.6: Update `make_price_data` to include volume

In `skills/screener/test_screener.py`, change `make_price_data`:

```python
def make_price_data(n=250, close_val=100.0, volume_val=1_000_000.0):
    """Minimal price data arrays."""
    close = np.ones(n) * close_val
    return {
        'dates': [f"2024-{i:04d}" for i in range(n)],
        'close': close,
        'high': close * 1.01,
        'low': close * 0.99,
        'volume': np.ones(n) * volume_val,
    }
```

- [ ] Update `make_price_data` as above

### Step 1.7: Add `MIN_VOLUME_RATIO` to `config.py`

After `RSI2_ENTRY_AGGRESSIVE = 5.0` in `scripts/config.py` (around line 113), add:

```python
# Volume filter: skip entry if today's volume < this fraction of the 20-day average daily
# volume (ADV). Adapts per instrument without per-instrument calibration.
MIN_VOLUME_RATIO = 0.5
```

- [ ] Add constant to `config.py`

### Step 1.8: Run full test suite to confirm no regressions

```bash
PYTHONPATH=scripts pytest skills/screener/test_screener.py -v
```

Expected: all existing tests PASS (volume field is additive — no existing assertion breaks)

- [ ] Run; confirm all pass

### Step 1.9: Commit

```bash
git add scripts/config.py skills/screener/screener.py skills/screener/test_screener.py
git commit -m "feat: add volume to fetch_daily_bars + MIN_VOLUME_RATIO config"
```

- [ ] Commit

---

## Task 2: Volume gate in `scan_instrument` + `volume_ratio` field

**Files:**
- Modify: `skills/screener/screener.py:97-148`
- Modify: `skills/screener/test_screener.py` (`TestScanInstrument`)

### Step 2.1: Write failing tests for the volume gate

Add three tests to `TestScanInstrument` in `test_screener.py`:

```python
def test_thin_volume_returns_none(self):
    # today = 400_000, avg_20d = 1_000_000 → ratio 0.4 < 0.5 → blocked
    data = make_price_data(close_val=110.0, volume_val=1_000_000.0)
    data['volume'][-1] = 400_000.0  # today thin
    with patch('screener.rsi', return_value=np.array([3.0])), \
         patch('screener.sma', return_value=np.array([100.0])), \
         patch('screener.atr', return_value=np.array([2.0])):
        from screener import scan_instrument
        result = scan_instrument("SPY", data, ranging_regime())
    assert result is None

def test_normal_volume_passes(self):
    # today = 1_000_000, avg_20d = 1_000_000 → ratio 1.0 ≥ 0.5 → passes
    data = make_price_data(close_val=110.0, volume_val=1_000_000.0)
    with patch('screener.rsi', return_value=np.array([3.0])), \
         patch('screener.sma', return_value=np.array([100.0])), \
         patch('screener.atr', return_value=np.array([2.0])):
        from screener import scan_instrument
        result = scan_instrument("SPY", data, ranging_regime())
    assert result is not None

def test_zero_avg_volume_does_not_filter(self):
    # all volume zeros → avg_volume_20d == 0 → guard skips filter
    data = make_price_data(close_val=110.0, volume_val=0.0)
    with patch('screener.rsi', return_value=np.array([3.0])), \
         patch('screener.sma', return_value=np.array([100.0])), \
         patch('screener.atr', return_value=np.array([2.0])):
        from screener import scan_instrument
        result = scan_instrument("SPY", data, ranging_regime())
    assert result is not None
```

- [ ] Add the three tests above to `TestScanInstrument`

### Step 2.2: Write failing test — result includes `volume_ratio`

Add to `TestScanInstrument`:

```python
def test_result_includes_volume_ratio(self):
    data = make_price_data(close_val=110.0, volume_val=1_000_000.0)
    with patch('screener.rsi', return_value=np.array([3.0])), \
         patch('screener.sma', return_value=np.array([100.0])), \
         patch('screener.atr', return_value=np.array([2.0])):
        from screener import scan_instrument
        result = scan_instrument("SPY", data, ranging_regime())
    assert result is not None
    assert 'volume_ratio' in result
    assert result['volume_ratio'] == 1.0  # today == avg

def test_volume_ratio_none_when_avg_volume_zero(self):
    data = make_price_data(close_val=110.0, volume_val=0.0)
    with patch('screener.rsi', return_value=np.array([3.0])), \
         patch('screener.sma', return_value=np.array([100.0])), \
         patch('screener.atr', return_value=np.array([2.0])):
        from screener import scan_instrument
        result = scan_instrument("SPY", data, ranging_regime())
    assert result is not None
    assert result['volume_ratio'] is None
```

- [ ] Add the two tests above

### Step 2.3: Update `test_result_contains_expected_fields` to include `volume_ratio`

Change the fields tuple in the existing test (line ~157):

```python
for field in ('symbol', 'rsi2', 'sma200', 'atr14', 'close', 'prev_high',
              'above_sma', 'priority', 'entry_threshold', 'volume_ratio'):
    assert field in result
```

- [ ] Apply change

### Step 2.4: Run new tests to verify they fail

```bash
PYTHONPATH=scripts pytest skills/screener/test_screener.py::TestScanInstrument::test_thin_volume_returns_none skills/screener/test_screener.py::TestScanInstrument::test_normal_volume_passes skills/screener/test_screener.py::TestScanInstrument::test_zero_avg_volume_does_not_filter skills/screener/test_screener.py::TestScanInstrument::test_result_includes_volume_ratio skills/screener/test_screener.py::TestScanInstrument::test_volume_ratio_none_when_avg_volume_zero skills/screener/test_screener.py::TestScanInstrument::test_result_contains_expected_fields -v
```

Expected: FAILs — `volume_ratio` not in result, thin-volume not blocked

- [ ] Run; confirm failures

### Step 2.5: Implement volume gate in `scan_instrument`

In `screener.py`, add after the NaN guard (after line 114, before the `# Determine entry threshold` comment):

```python
    # Volume gate: skip thin-volume days (today < MIN_VOLUME_RATIO * 20-day avg)
    latest_volume = data['volume'][-1]
    avg_volume_20d = float(np.mean(data['volume'][-20:]))
    if avg_volume_20d > 0 and latest_volume < config.MIN_VOLUME_RATIO * avg_volume_20d:
        return None  # thin-volume day — skip entry
```

And change the return dict at the end of `scan_instrument` to include `volume_ratio`:

```python
    return {
        "symbol": symbol,
        "tier": None,  # filled by caller
        "rsi2": round(latest_rsi2, 2),
        "sma200": round(latest_sma200, 2),
        "atr14": round(latest_atr14, 4),
        "close": round(latest_close, 2),
        "prev_high": round(prev_high, 2),
        "above_sma": above_sma,
        "priority": priority,
        "entry_threshold": threshold,
        "volume_ratio": round(latest_volume / avg_volume_20d, 2) if avg_volume_20d > 0 else None,
    }
```

- [ ] Apply both changes to `screener.py`

### Step 2.6: Run all volume gate tests

```bash
PYTHONPATH=scripts pytest skills/screener/test_screener.py::TestScanInstrument -v
```

Expected: all PASS

- [ ] Run; confirm all pass

### Step 2.7: Run full screener test suite

```bash
PYTHONPATH=scripts pytest skills/screener/test_screener.py -v
```

Expected: all PASS

- [ ] Run; confirm all pass

### Step 2.8: Commit

```bash
git add skills/screener/screener.py skills/screener/test_screener.py
git commit -m "feat: volume filter gate in scan_instrument — skip thin-volume days"
```

- [ ] Commit

---

## Task 3: Changelog merge + version bump

**Files:**
- Modify: `CHANGELOG.md` (insert v0.17.0 entry between v0.18.0 and v0.16.0)
- Delete: `docs/CHANGELOG.md`
- Modify: `VERSION`
- Modify: `CHANGELOG.md` (add v0.19.0 entry)
- Modify: `docs/FEATURE_WISHLIST.md`
- Modify: `.remember/remember.md`

### Step 3.1: Insert v0.17.0 into root `CHANGELOG.md`

The root `CHANGELOG.md` currently jumps from v0.18.0 to `[0.16.0]`. Insert between them:

```markdown
## v0.17.0 — 2026-04-11

### Added
- **Scheduled reconcile**: `supervisor.py --reconcile` runs `scripts/reconcile.py --fix` at 9:15 AM ET Mon–Fri via cron. Catches overnight Redis↔Alpaca state drift automatically. Fires `critical_alert` on non-zero exit.
- **Dashboard trailing stop indicator**: position cards show a "Trail: X%" row (amber) when a position has been upgraded to an Alpaca trailing stop.

### Fixed
- **Drawdown attribution lookback cap**: `get_drawdown_attribution()` (Python) and `Queries.drawdown_attribution/2` (Elixir) cap `peak_equity_date` lookback at 90 days. Prevents unbounded DB scans during prolonged drawdowns.

---
```

Insert it immediately after the closing `---` of v0.18.0 and before `## [0.16.0]`.

- [ ] Insert v0.17.0 into `CHANGELOG.md`

### Step 3.2: Delete `docs/CHANGELOG.md`

```bash
git rm docs/CHANGELOG.md
```

- [ ] Delete `docs/CHANGELOG.md`

### Step 3.3: Bump VERSION to 0.19.0

```bash
echo "0.19.0" > VERSION
```

- [ ] Update `VERSION`

### Step 3.4: Add v0.19.0 entry to top of `CHANGELOG.md`

Insert after the header block (`---` separator after line 9), before `## v0.18.0`:

```markdown
## v0.19.0 — 2026-04-11

### Added
- **Volume filter on entries**: `scan_instrument` skips instruments where today's volume < 50% of the 20-day average daily volume (ADV). Prevents entries on holiday half-sessions and anomalously thin-volume days. `volume_ratio` added to watchlist payload for observability. No special-casing for BTC/USD — 20-day ADV in BTC units, same ratio logic.

---
```

- [ ] Add v0.19.0 entry to `CHANGELOG.md`

### Step 3.5: Mark item 6 done in `docs/FEATURE_WISHLIST.md`

Find the line for item 6 (volume filter) and mark it complete. The exact line will look like:
`6. Volume filter on entries` or similar. Change it to include `[x]` or `✓` per the existing done-item format in that file.

First read the file to see the exact format:
```bash
grep -n "volume\|Volume\|✓\|done\|\[x\]" docs/FEATURE_WISHLIST.md | head -20
```
Then apply the matching format.

- [ ] Read `docs/FEATURE_WISHLIST.md` to see done-item format
- [ ] Mark item 6 done

### Step 3.6: Update `.remember/remember.md`

Update the version history section to include v0.19.0 and mark item 6 done. Key facts to capture:
- v0.19.0: volume filter on entries — `scan_instrument` returns None if today_vol < 50% of 20d ADV; `volume_ratio` in result dict; `MIN_VOLUME_RATIO = 0.5` in config
- Next priority: items 7–9 (equity curve chart, strategy attribution by exit type, position age alert)

- [ ] Update `.remember/remember.md`

### Step 3.7: Run full test suite one last time

```bash
PYTHONPATH=scripts pytest skills/screener/test_screener.py -v
```

Expected: all PASS

- [ ] Run; confirm all pass

### Step 3.8: Commit and push

```bash
git add CHANGELOG.md VERSION docs/FEATURE_WISHLIST.md .remember/remember.md
git commit -m "chore: v0.19.0 — changelog merge, version bump, wishlist update"
git push -u origin feat/volume-filter
```

- [ ] Commit and push

---

## Post-Implementation

After all tasks complete, run `cpr` to create the PR targeting main.

PR title: `feat: volume filter on entries (v0.19.0)`

PR body should include:
- What: skip instruments where today's volume < 50% of 20-day ADV
- Why: thin-volume days → wide spreads, sloppy fills (TSLA, XLV, IWM)
- `volume_ratio` in watchlist payload for observability
- Also: inserted missing v0.17.0 into root CHANGELOG, deleted stale `docs/CHANGELOG.md`
