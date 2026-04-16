# Config Hot-Reload Design

**Date:** 2026-04-15
**Feature:** Wishlist item 8 — Runtime configuration override via dashboard
**Version target:** v0.29.0

---

## Goal

Allow runtime adjustment of RSI-2 thresholds, position limits, and drawdown circuit breaker levels without restarting any agent. Changes take effect at the start of the next agent cycle.

---

## Architecture

A new Redis key `trading:config` stores a JSON object of parameter overrides. Agents call `load_overrides(r)` at the top of each cycle; that function reads the key, validates each value, and applies valid entries to module-level globals in `config.py`. A new `/settings` Phoenix LiveView page provides a form UI to write (or delete) the key.

**Why poll-per-cycle instead of pub/sub:**  
Agents already have a Redis connection and a natural cycle boundary. Polling once per cycle adds negligible overhead and avoids any agent needing to run a separate subscriber thread for config changes.

**Why module-level globals:**  
All agents already read `config.RSI2_ENTRY_CONSERVATIVE`, `config.DRAWDOWN_HALT`, etc., as module globals. Overwriting those globals at cycle start is the smallest possible change that makes every downstream read pick up the new value without any agent refactoring.

---

## Hot-Reloadable Parameters

These 10 parameters can be overridden at runtime. All others are static and require a deploy.

| Parameter | Default | Type | Bounds |
|---|---|---|---|
| `RSI2_ENTRY_CONSERVATIVE` | 10.0 | float | (0.0, 30.0] |
| `RSI2_ENTRY_AGGRESSIVE` | 5.0 | float | (0.0, 20.0] |
| `RSI2_EXIT` | 60.0 | float | [50.0, 95.0] |
| `RSI2_MAX_HOLD_DAYS` | 5 | int | [1, 30] |
| `RISK_PER_TRADE_PCT` | 0.01 | float | (0.0, 0.05] |
| `MAX_CONCURRENT_POSITIONS` | 5 | int | [1, 20] |
| `DRAWDOWN_CAUTION` | 5.0 | float | (0.0, 100.0) |
| `DRAWDOWN_DEFENSIVE` | 10.0 | float | (0.0, 100.0) |
| `DRAWDOWN_CRITICAL` | 15.0 | float | (0.0, 100.0) |
| `DRAWDOWN_HALT` | 20.0 | float | (0.0, 100.0) |

The `trading:config` JSON key contains only the parameters to override. Absent keys use the module default. Example:

```json
{"RSI2_ENTRY_CONSERVATIVE": 8.0, "DRAWDOWN_HALT": 25.0}
```

---

## Python Changes (`scripts/config.py`)

### `Keys.CONFIG`

Add to the `Keys` class:

```python
CONFIG = "trading:config"  # Hot-reload overrides (JSON). See load_overrides().
```

### `load_overrides(r)`

New function at module level:

```python
def load_overrides(r: redis.Redis) -> None:
    """
    Read trading:config from Redis and apply valid overrides to module globals.

    Called at the top of each agent's main cycle. Missing key = no-op.
    Invalid type or out-of-range value: log warning, skip that key.
    This is the only supported mechanism for runtime parameter changes.
    """
```

**Validation rules per parameter:**

- `RSI2_ENTRY_CONSERVATIVE`: float, 0 < v ≤ 30
- `RSI2_ENTRY_AGGRESSIVE`: float, 0 < v ≤ 20; must be < `RSI2_ENTRY_CONSERVATIVE` after override
- `RSI2_EXIT`: float, 50 ≤ v ≤ 95
- `RSI2_MAX_HOLD_DAYS`: int (or float coerced to int), 1 ≤ v ≤ 30
- `RISK_PER_TRADE_PCT`: float, 0 < v ≤ 0.05
- `MAX_CONCURRENT_POSITIONS`: int, 1 ≤ v ≤ 20
- `DRAWDOWN_CAUTION/DEFENSIVE/CRITICAL/HALT`: float, 0 < v < 100; must maintain CAUTION < DEFENSIVE < CRITICAL < HALT

**Behavior:**

- JSON parse error → log warning, return (no changes applied)
- Individual key fails validation → log warning with key name and bad value, skip that key only
- All other keys in the same payload still applied
- No exception raised to caller; `load_overrides` is defensive by design

### `config.py` comments

Each hot-reloadable parameter gets a `# HOT-RELOADABLE via trading:config` inline comment.

---

## Agent Changes

Each agent calls `load_overrides(r)` once at the top of its main processing cycle, before reading any strategy parameters. Affected files:

- `skills/screener/screener.py` — top of `run_screener()`
- `skills/watcher/watcher.py` — top of `evaluate_watchlist()`
- `skills/portfolio_manager/portfolio_manager.py` — top of `process_signal()`
- `skills/executor/executor.py` — top of `process_order()`
- `skills/supervisor/supervisor.py` — top of `run_health_check()` and `run_eod()`

---

## Dashboard Changes

### New route

`/settings` → `DashboardWeb.SettingsLive`

Add to router: `live "/settings", SettingsLive`

### New LiveView (`dashboard/lib/dashboard_web/live/settings_live.ex`)

**Assigns:**

```elixir
%{
  form_params: %{...},  # current override values or defaults
  flash: nil | {:ok | :error, message}
}
```

**Events:**

| Event | Payload | Action |
|---|---|---|
| `save` | `%{"config" => params}` | Validate → write JSON to `trading:config` → flash |
| `reset` | `{}` | Delete `trading:config` key → flash "Defaults restored" |

**On mount:** Read `trading:config` from Redis. If present, merge with defaults for display; if absent, display defaults with visual indicator ("No active overrides — showing defaults").

### Template (`settings_live.html.heex`)

Three sections, each a card:

1. **RSI Strategy** — `RSI2_ENTRY_CONSERVATIVE`, `RSI2_ENTRY_AGGRESSIVE`, `RSI2_EXIT`, `RSI2_MAX_HOLD_DAYS`
2. **Position Limits** — `RISK_PER_TRADE_PCT`, `MAX_CONCURRENT_POSITIONS`
3. **Drawdown Thresholds** — `DRAWDOWN_CAUTION`, `DRAWDOWN_DEFENSIVE`, `DRAWDOWN_CRITICAL`, `DRAWDOWN_HALT`

Each field: numeric `<input type="number">` with step, min, max attributes matching validation bounds. Save and Reset buttons at bottom. Flash message on success/error.

### Nav

Add "Settings" link to existing nav sidebar.

---

## Testing

### Python (`scripts/tests/test_config_overrides.py`)

- `load_overrides` with missing key → no change to globals
- `load_overrides` with malformed JSON → no change
- `load_overrides` with valid subset → only specified globals change
- `load_overrides` with out-of-range value → skip that key, apply others
- `load_overrides` with `RSI2_ENTRY_AGGRESSIVE >= RSI2_ENTRY_CONSERVATIVE` → skip aggressive key
- `load_overrides` with drawdown values out of order → skip offending keys

### Elixir (`dashboard/test/dashboard_web/live/settings_live_test.exs`)

- Page renders with defaults when `trading:config` absent
- Page renders with override values when `trading:config` present
- `save` event with valid params → `trading:config` key set → flash `:ok`
- `save` event with invalid params (non-numeric) → flash `:error`, key not written
- `reset` event → `trading:config` key deleted → flash `:ok`

---

## Non-Goals

- No per-agent override targeting (all agents see the same overrides)
- No change history or audit log
- No validation of cross-parameter consistency in the dashboard (dashboard saves raw; agents validate on load)
- No WebSocket push when config changes (next cycle picks it up)
