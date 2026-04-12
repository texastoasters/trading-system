# Volume Filter on Entries — Design Spec

**Date:** 2026-04-11
**Feature:** Prevent entry signals on anomalously thin-volume days by comparing today's volume to the instrument's 20-day average daily volume (ADV).

---

## Problem

RSI-2 entry signals can fire on days when an instrument's volume is unusually low (holiday half-sessions, random quiet Mondays). Thin-volume days produce wide bid/ask spreads and sloppy fills — particularly noticeable on TSLA, XLV, and IWM.

---

## Approach

Relative volume gate: skip instrument if today's volume < 50% of its 20-day average. Adapts naturally to each instrument's baseline (SPY at 100M+ shares/day vs XLV at 5M), works for BTC/USD without special-casing, and requires a single constant.

Rejected alternative: absolute dollar-volume threshold — requires per-tier or per-instrument calibration as scales differ 20x across the universe.

---

## Components

### 1. `fetch_daily_bars` — add volume (`screener.py`)

The bar objects returned by Alpaca's `get_stock_bars` and `get_crypto_bars` already include `.volume`. Add to the return dict:

```python
'volume': np.array([float(b.volume) for b in bar_list]),
```

No other changes to the fetch function.

### 2. `scan_instrument` — volume gate (`screener.py`)

After the NaN guard (which already returns `None` for bad data), add before the priority classification block:

```python
latest_volume = data['volume'][-1]
avg_volume_20d = float(np.mean(data['volume'][-20:]))
if avg_volume_20d > 0 and latest_volume < config.MIN_VOLUME_RATIO * avg_volume_20d:
    return None  # thin-volume day — skip entry
```

`avg_volume_20d > 0` guard handles edge cases where all recent volume is zero (e.g., newly listed instruments with sparse history).

Add to the result dict for observability:

```python
"volume_ratio": round(latest_volume / avg_volume_20d, 2) if avg_volume_20d > 0 else None,
```

`volume_ratio` appears in the watchlist payload and Redis watchlist key — useful for diagnosing why an instrument disappeared from the watchlist on a given day.

### 3. `config.py` — one new constant

```python
MIN_VOLUME_RATIO = 0.5  # skip entry if today's volume < 50% of 20-day avg
```

---

## What Does NOT Change

- Watcher, Portfolio Manager, Executor, Supervisor: no changes
- Dashboard: no changes (volume_ratio is present in watchlist data but not displayed)
- Exit signals: volume filter only gates entries; exits always proceed
- BTC/USD: no special-casing needed — 20-day ADV in BTC units, same ratio logic

---

## Data Flow

```
fetch_daily_bars() → {dates, high, low, close, volume}
                                                  ↓
scan_instrument() → volume gate (return None if thin) → result dict with volume_ratio
                                                  ↓
run_scan() → watchlist published to Redis (instruments that passed the gate only)
```

---

## Tests Required

### `skills/screener/test_screener.py`

- `fetch_daily_bars` returns `volume` array when bars have volume data
- `scan_instrument` returns `None` when today's volume < 50% of 20-day avg (thin-volume day blocked)
- `scan_instrument` returns result when today's volume ≥ 50% of 20-day avg (normal day passes)
- `scan_instrument` result includes `volume_ratio` field
- `scan_instrument` does not filter when `avg_volume_20d == 0` (zero-volume guard)
- All other existing `scan_instrument` behavior unchanged (NaN guard, priority classification, etc.)

---

## Changelog Merge (included in same PR)

`docs/CHANGELOG.md` is a stale duplicate of root `CHANGELOG.md`. Root is authoritative and more detailed but is missing the v0.17.0 entry. Fix:
- Insert v0.17.0 into root `CHANGELOG.md` (between v0.18.0 and [0.16.0])
- Delete `docs/CHANGELOG.md`
