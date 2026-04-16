# Config Hot-Reload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow RSI-2 thresholds, position limits, and drawdown circuit-breaker levels to be changed at runtime via a new `/settings` dashboard page without restarting any agent.

**Architecture:** A new Redis key `trading:config` stores a JSON object of overrides. Agents call `config.load_overrides(r)` at the top of each processing cycle; that function reads the key, validates each value, and applies valid entries to module-level globals. A new `/settings` Phoenix LiveView page provides a form to write or delete the key. Missing key = no-op; invalid values are skipped with a log warning.

**Tech Stack:** Python 3 (agents + config), Redis (storage), Phoenix LiveView (dashboard), Elixir/Jason (JSON encode/decode), Tailwind CSS (styling)

---

## File Structure

**Python (modify):**
- `scripts/config.py` — add `import sys`, `Keys.CONFIG`, `# HOT-RELOADABLE` comments on 10 params, `load_overrides(r)` function
- `scripts/test_config.py` — add `TestLoadOverrides` class
- `skills/screener/screener.py` — add `config.load_overrides(r)` in `run_scan()` after `config.init_redis_state(r)`
- `skills/screener/test_screener.py` — add `test_load_overrides_called_each_scan` to `TestRunScan`
- `skills/watcher/watcher.py` — add `config.load_overrides(r)` in `run_cycle()` after `config.init_redis_state(r)`
- `skills/watcher/test_watcher.py` — add `test_load_overrides_called_each_cycle` to `TestRunCycle`
- `skills/portfolio_manager/portfolio_manager.py` — add `config.load_overrides(r)` at top of `process_signal(r, signal)`
- `skills/portfolio_manager/test_portfolio_manager.py` — add `test_load_overrides_called_on_process_signal` to `TestProcessSignal`
- `skills/executor/executor.py` — add `config.load_overrides(r)` at top of `process_order(r, trading_client, order)`
- `skills/executor/test_executor.py` — add `test_load_overrides_called_on_process_order` to `TestProcessOrder`
- `skills/supervisor/supervisor.py` — add `config.load_overrides(r)` at top of `run_health_check(r)` and `run_eod_review(r)`
- `skills/supervisor/test_supervisor.py` — add tests to `TestRunHealthCheck` and `TestRunEodReview`

**Elixir (modify):**
- `dashboard/lib/dashboard_web/router.ex` — add `live "/settings", SettingsLive, :index`
- `dashboard/lib/dashboard_web/layouts/app.html.heex` — add Settings nav link

**Elixir (create):**
- `dashboard/lib/dashboard_web/live/settings_live.ex`
- `dashboard/lib/dashboard_web/live/settings_live.html.heex`
- `dashboard/test/dashboard_web/live/settings_live_test.exs`

---

## Task 1: `load_overrides` in config.py

**Files:**
- Modify: `scripts/config.py`
- Modify: `scripts/test_config.py`

### Step 1.1: Write failing tests for `load_overrides`

Add a `TestLoadOverrides` class at the end of `scripts/test_config.py`. Import `load_overrides` in the existing import block.

```python
# Add to the existing from config import (...) block:
from config import (
    Keys, _load_trading_env,
    get_redis, init_redis_state,
    get_active_instruments, get_tier,
    get_simulated_equity, get_drawdown,
    is_crypto, get_sector,
    load_overrides,                     # ← new
    DEFAULT_UNIVERSE, DEFAULT_TIERS, INITIAL_CAPITAL,
)
```

Then add this class at the bottom of `scripts/test_config.py`:

```python
# ── load_overrides ───────────────────────────────────────────

class TestLoadOverrides:
    def setup_method(self):
        """Reset all hot-reloadable globals to known defaults before each test."""
        import config as _c
        _c.RSI2_ENTRY_CONSERVATIVE = 10.0
        _c.RSI2_ENTRY_AGGRESSIVE = 5.0
        _c.RSI2_EXIT = 60.0
        _c.RSI2_MAX_HOLD_DAYS = 5
        _c.RISK_PER_TRADE_PCT = 0.01
        _c.MAX_CONCURRENT_POSITIONS = 5
        _c.DRAWDOWN_CAUTION = 5.0
        _c.DRAWDOWN_DEFENSIVE = 10.0
        _c.DRAWDOWN_CRITICAL = 15.0
        _c.DRAWDOWN_HALT = 20.0

    def test_no_op_when_key_absent(self):
        r = make_r(store={})
        load_overrides(r)
        assert config.RSI2_ENTRY_CONSERVATIVE == 10.0

    def test_no_op_on_invalid_json(self):
        r = make_r(store={Keys.CONFIG: "not_valid_json"})
        load_overrides(r)
        assert config.RSI2_EXIT == 60.0

    def test_applies_valid_subset(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "RSI2_ENTRY_CONSERVATIVE": 8.0,
            "RSI2_EXIT": 65.0,
        })})
        load_overrides(r)
        assert config.RSI2_ENTRY_CONSERVATIVE == 8.0
        assert config.RSI2_EXIT == 65.0
        assert config.RSI2_ENTRY_AGGRESSIVE == 5.0  # unchanged

    def test_skips_out_of_range_value_applies_others(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "RSI2_ENTRY_CONSERVATIVE": 99.0,  # > 30, out of range
            "RSI2_EXIT": 70.0,                 # valid
        })})
        load_overrides(r)
        assert config.RSI2_ENTRY_CONSERVATIVE == 10.0  # skipped
        assert config.RSI2_EXIT == 70.0                # applied

    def test_skips_wrong_type_applies_others(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "MAX_CONCURRENT_POSITIONS": "not_a_number",
            "RSI2_EXIT": 70.0,
        })})
        load_overrides(r)
        assert config.MAX_CONCURRENT_POSITIONS == 5  # skipped
        assert config.RSI2_EXIT == 70.0              # applied

    def test_skips_aggressive_when_gte_conservative(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "RSI2_ENTRY_AGGRESSIVE": 12.0,  # >= default conservative of 10.0
        })})
        load_overrides(r)
        assert config.RSI2_ENTRY_AGGRESSIVE == 5.0  # unchanged

    def test_applies_both_when_aggressive_lt_new_conservative(self):
        """When both are overridden and aggressive < new conservative, both apply."""
        r = make_r(store={Keys.CONFIG: json.dumps({
            "RSI2_ENTRY_CONSERVATIVE": 15.0,
            "RSI2_ENTRY_AGGRESSIVE": 8.0,  # < 15.0 ✓
        })})
        load_overrides(r)
        assert config.RSI2_ENTRY_CONSERVATIVE == 15.0
        assert config.RSI2_ENTRY_AGGRESSIVE == 8.0

    def test_skips_all_drawdown_keys_when_out_of_order(self):
        r = make_r(store={Keys.CONFIG: json.dumps({
            "DRAWDOWN_CAUTION": 15.0,    # >= DEFENSIVE of 10.0 → out of order
            "DRAWDOWN_DEFENSIVE": 10.0,
        })})
        load_overrides(r)
        assert config.DRAWDOWN_CAUTION == 5.0    # all drawdown keys skipped
        assert config.DRAWDOWN_DEFENSIVE == 10.0  # unchanged
```

- [ ] **Step 1.2: Run tests to verify they fail**

```bash
PYTHONPATH=scripts pytest scripts/test_config.py::TestLoadOverrides -v
```

Expected: `ImportError: cannot import name 'load_overrides' from 'config'`

### Step 1.3: Implement `load_overrides` in `config.py`

**1.3a** — Add `import sys` to the import block at the top of `scripts/config.py` (it already imports `os`, `json`, `redis`):

```python
import os
import sys       # ← add this line
import json
import redis
```

**1.3b** — Add `CONFIG` to the `Keys` class (after `RESTART_COUNT`):

```python
    RESTART_COUNT = "trading:restart_count"
    CONFIG = "trading:config"  # Hot-reload overrides (JSON). See load_overrides().
```

**1.3c** — Mark the 10 hot-reloadable parameters with inline comments. The comment is `# HOT-RELOADABLE via trading:config`. Add it to the right side of these existing lines:

```python
RSI2_ENTRY_CONSERVATIVE = 10.0   # HOT-RELOADABLE via trading:config
RSI2_ENTRY_AGGRESSIVE = 5.0      # HOT-RELOADABLE via trading:config
RSI2_EXIT = 60.0                 # HOT-RELOADABLE via trading:config
RSI2_MAX_HOLD_DAYS = 5           # HOT-RELOADABLE via trading:config
RISK_PER_TRADE_PCT = 0.01        # HOT-RELOADABLE via trading:config
MAX_CONCURRENT_POSITIONS = 5     # HOT-RELOADABLE via trading:config
DRAWDOWN_CAUTION = 5.0           # HOT-RELOADABLE via trading:config
DRAWDOWN_DEFENSIVE = 10.0        # HOT-RELOADABLE via trading:config
DRAWDOWN_CRITICAL = 15.0         # HOT-RELOADABLE via trading:config
DRAWDOWN_HALT = 20.0             # HOT-RELOADABLE via trading:config
```

**1.3d** — Add `load_overrides` function in the `# ── Helpers ─────` section of `scripts/config.py`, after `get_sector`:

```python
def load_overrides(r: redis.Redis) -> None:
    """
    Read trading:config from Redis and apply valid overrides to module globals.

    Called at the top of each agent's main cycle. Missing key = no-op.
    Invalid type or out-of-range value: log warning, skip that key.
    This is the only supported mechanism for runtime parameter changes.
    """
    raw = r.get(Keys.CONFIG)
    if not raw:
        return

    try:
        overrides = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        print("[config] WARNING: trading:config contains invalid JSON, skipping overrides")
        return

    _SPEC = {
        "RSI2_ENTRY_CONSERVATIVE": (float, lambda v: 0 < v <= 30),
        "RSI2_ENTRY_AGGRESSIVE":    (float, lambda v: 0 < v <= 20),
        "RSI2_EXIT":                (float, lambda v: 50 <= v <= 95),
        "RSI2_MAX_HOLD_DAYS":       (int,   lambda v: 1 <= v <= 30),
        "RISK_PER_TRADE_PCT":       (float, lambda v: 0 < v <= 0.05),
        "MAX_CONCURRENT_POSITIONS": (int,   lambda v: 1 <= v <= 20),
        "DRAWDOWN_CAUTION":         (float, lambda v: 0 < v < 100),
        "DRAWDOWN_DEFENSIVE":       (float, lambda v: 0 < v < 100),
        "DRAWDOWN_CRITICAL":        (float, lambda v: 0 < v < 100),
        "DRAWDOWN_HALT":            (float, lambda v: 0 < v < 100),
    }

    validated = {}
    for key, (cast, check) in _SPEC.items():
        if key not in overrides:
            continue
        try:
            val = cast(overrides[key])
            if not check(val):
                raise ValueError(f"{val} out of range")
        except (TypeError, ValueError) as e:
            print(f"[config] WARNING: override {key}={overrides[key]!r} invalid ({e}), skipping")
            continue
        validated[key] = val

    # Cross-check: AGGRESSIVE must be < CONSERVATIVE (use effective value after override)
    if "RSI2_ENTRY_AGGRESSIVE" in validated:
        effective_conservative = validated.get(
            "RSI2_ENTRY_CONSERVATIVE", RSI2_ENTRY_CONSERVATIVE
        )
        if validated["RSI2_ENTRY_AGGRESSIVE"] >= effective_conservative:
            print(
                f"[config] WARNING: RSI2_ENTRY_AGGRESSIVE="
                f"{validated['RSI2_ENTRY_AGGRESSIVE']} >= "
                f"RSI2_ENTRY_CONSERVATIVE={effective_conservative}, skipping aggressive override"
            )
            del validated["RSI2_ENTRY_AGGRESSIVE"]

    # Cross-check: drawdown thresholds must be strictly ascending
    _dd_keys = ["DRAWDOWN_CAUTION", "DRAWDOWN_DEFENSIVE", "DRAWDOWN_CRITICAL", "DRAWDOWN_HALT"]
    _mod = sys.modules[__name__]
    _dd_vals = [validated.get(k, getattr(_mod, k)) for k in _dd_keys]
    for i in range(len(_dd_vals) - 1):
        if _dd_vals[i] >= _dd_vals[i + 1]:
            print(
                "[config] WARNING: drawdown thresholds out of order after overrides, "
                "skipping all drawdown overrides"
            )
            for k in _dd_keys:
                validated.pop(k, None)
            break

    # Apply validated overrides to module globals
    for key, val in validated.items():
        setattr(_mod, key, val)
```

- [ ] **Step 1.4: Run tests to verify they pass**

```bash
PYTHONPATH=scripts pytest scripts/test_config.py -v
```

Expected: all existing tests + all `TestLoadOverrides` tests PASS

- [ ] **Step 1.5: Commit**

```bash
git add scripts/config.py scripts/test_config.py
git commit -m "feat: add load_overrides to config.py with Keys.CONFIG and hot-reload markers"
```

---

## Task 2: Wire Agents

**Files:**
- Modify: `skills/screener/screener.py`, `skills/screener/test_screener.py`
- Modify: `skills/watcher/watcher.py`, `skills/watcher/test_watcher.py`
- Modify: `skills/portfolio_manager/portfolio_manager.py`, `skills/portfolio_manager/test_portfolio_manager.py`
- Modify: `skills/executor/executor.py`, `skills/executor/test_executor.py`
- Modify: `skills/supervisor/supervisor.py`, `skills/supervisor/test_supervisor.py`

### Step 2.1: Write failing tests for all agents

**`skills/screener/test_screener.py`** — add to `TestRunScan`:

```python
def test_load_overrides_called_each_scan(self):
    r = self._make_redis()
    with patch('screener.get_redis', return_value=r), \
         patch('screener.config.init_redis_state'), \
         patch('screener.config.load_overrides') as mock_load, \
         patch('screener.fetch_daily_bars', return_value=None):
        from screener import run_scan
        run_scan()
    mock_load.assert_called_once_with(r)
```

**`skills/watcher/test_watcher.py`** — add to `TestRunCycle`:

```python
def test_load_overrides_called_each_cycle(self):
    r = make_redis()
    with patch('watcher.get_redis', return_value=r), \
         patch('watcher.config.init_redis_state'), \
         patch('watcher.config.load_overrides') as mock_load, \
         patch('watcher.generate_exit_signals', return_value=[]), \
         patch('watcher.generate_entry_signals', return_value=[]), \
         patch('watcher.publish_signals'), \
         patch('watcher.notify'):
        from watcher import run_cycle
        run_cycle()
    mock_load.assert_called_once_with(r)
```

**`skills/portfolio_manager/test_portfolio_manager.py`** — add to `TestProcessSignal`:

```python
def test_load_overrides_called_on_process_signal(self):
    r = make_redis()
    with patch('portfolio_manager.config.load_overrides') as mock_load:
        from portfolio_manager import process_signal
        process_signal(r, make_signal(signal_type="entry"))
    mock_load.assert_called_once_with(r)
```

**`skills/executor/test_executor.py`** — add to `TestProcessOrder`:

```python
def test_load_overrides_called_on_process_order(self):
    r = make_redis(positions={})
    tc = MagicMock()
    with patch('executor.config.load_overrides') as mock_load, \
         patch('executor.validate_order', return_value=(False, "blocked for test")):
        from executor import process_order
        process_order(r, tc, {"side": "buy", "symbol": "SPY", "quantity": 1,
                              "entry_price": 100.0, "order_value": 100.0})
    mock_load.assert_called_once_with(r)
```

**`skills/supervisor/test_supervisor.py`** — add to `TestRunHealthCheck` and `TestRunEodReview`:

```python
# In TestRunHealthCheck:
def test_load_overrides_called_on_health_check(self):
    r = make_redis()
    with patch('supervisor.config.load_overrides') as mock_load, \
         patch('supervisor.notify'):
        from supervisor import run_health_check
        run_health_check(r)
    mock_load.assert_called_once_with(r)

# In TestRunEodReview:
def test_load_overrides_called_on_eod_review(self):
    r = make_redis()
    conn = MagicMock()
    with patch('supervisor.config.load_overrides') as mock_load, \
         patch('supervisor.get_db', return_value=conn), \
         patch('supervisor.notify'), \
         patch('supervisor.daily_summary'):
        from supervisor import run_eod_review
        run_eod_review(r)
    mock_load.assert_called_once_with(r)
```

- [ ] **Step 2.2: Run tests to verify they fail**

```bash
PYTHONPATH=scripts pytest skills/screener/test_screener.py::TestRunScan::test_load_overrides_called_each_scan \
  skills/watcher/test_watcher.py::TestRunCycle::test_load_overrides_called_each_cycle \
  skills/portfolio_manager/test_portfolio_manager.py::TestProcessSignal::test_load_overrides_called_on_process_signal \
  skills/executor/test_executor.py::TestProcessOrder::test_load_overrides_called_on_process_order \
  skills/supervisor/test_supervisor.py::TestRunHealthCheck::test_load_overrides_called_on_health_check \
  skills/supervisor/test_supervisor.py::TestRunEodReview::test_load_overrides_called_on_eod_review -v
```

Expected: all 6 FAIL — `AssertionError: Expected 'load_overrides' to have been called once`

### Step 2.3: Wire all agents

**`skills/screener/screener.py`** — in `run_scan()`, add one line after `config.init_redis_state(r)`:

```python
def run_scan():
    """Run a complete scan of the active universe."""
    r = get_redis()
    config.init_redis_state(r)
    config.load_overrides(r)   # ← add this line
```

**`skills/watcher/watcher.py`** — in `run_cycle()`, add one line after `config.init_redis_state(r)`:

```python
def run_cycle():
    """Run one complete evaluation cycle."""
    r = get_redis()
    config.init_redis_state(r)
    config.load_overrides(r)   # ← add this line
```

**`skills/portfolio_manager/portfolio_manager.py`** — in `process_signal(r, signal)`, add one line at the top of the function body:

```python
def process_signal(r, signal):
    """Process a single signal — entry or exit."""
    config.load_overrides(r)   # ← add this line
    sig_type = signal.get("signal_type", "")
```

**`skills/executor/executor.py`** — in `process_order(r, trading_client, order)`, add one line at the top:

```python
def process_order(r, trading_client, order):
    """Process a single approved order."""
    config.load_overrides(r)   # ← add this line
    account = trading_client.get_account()
```

**`skills/supervisor/supervisor.py`** — add one line at top of `run_health_check(r)` and `run_eod_review(r)`:

```python
def run_health_check(r):
    """Check all agents are alive and system state is consistent."""
    config.load_overrides(r)   # ← add this line
    print("[Supervisor] Running health check...")

def run_eod_review(r):
    """End-of-day review — compute metrics and send daily summary."""
    config.load_overrides(r)   # ← add this line
    print("[Supervisor] Running end-of-day review...")
```

- [ ] **Step 2.4: Run all agent tests to verify they pass**

```bash
PYTHONPATH=scripts pytest skills/screener/test_screener.py \
  skills/watcher/test_watcher.py \
  skills/portfolio_manager/test_portfolio_manager.py \
  skills/executor/test_executor.py \
  skills/supervisor/test_supervisor.py \
  scripts/test_config.py -v
```

Expected: all tests PASS

- [ ] **Step 2.5: Commit**

```bash
git add skills/screener/screener.py skills/screener/test_screener.py \
        skills/watcher/watcher.py skills/watcher/test_watcher.py \
        skills/portfolio_manager/portfolio_manager.py skills/portfolio_manager/test_portfolio_manager.py \
        skills/executor/executor.py skills/executor/test_executor.py \
        skills/supervisor/supervisor.py skills/supervisor/test_supervisor.py
git commit -m "feat: wire load_overrides into all agent cycle entry points"
```

---

## Task 3: Elixir Router and Nav

**Files:**
- Modify: `dashboard/lib/dashboard_web/router.ex`
- Modify: `dashboard/lib/dashboard_web/layouts/app.html.heex`

- [ ] **Step 3.1: Add `/settings` route**

In `dashboard/lib/dashboard_web/router.ex`, add after the `/logs` route:

```elixir
    live "/logs", LogsLive, :index
    live "/settings", SettingsLive, :index    # ← add this line
```

- [ ] **Step 3.2: Add Settings nav link**

In `dashboard/lib/dashboard_web/layouts/app.html.heex`, add after the Logs link:

```html
    <a href="/logs" class="px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200 rounded hover:bg-gray-800 transition-colors shrink-0">
      Logs
    </a>
    <a href="/settings" class="px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200 rounded hover:bg-gray-800 transition-colors shrink-0">
      Settings
    </a>
```

- [ ] **Step 3.3: Commit**

```bash
git add dashboard/lib/dashboard_web/router.ex \
        dashboard/lib/dashboard_web/layouts/app.html.heex
git commit -m "feat: add /settings route and nav link"
```

---

## Task 4: SettingsLive (TDD)

**Files:**
- Create: `dashboard/test/dashboard_web/live/settings_live_test.exs`
- Create: `dashboard/lib/dashboard_web/live/settings_live.ex`
- Create: `dashboard/lib/dashboard_web/live/settings_live.html.heex`

### Step 4.1: Write failing tests

Create `dashboard/test/dashboard_web/live/settings_live_test.exs`:

```elixir
defmodule DashboardWeb.SettingsLiveTest do
  use DashboardWeb.ConnCase

  setup do
    # Ensure no leftover config key from previous test
    Redix.command(:redix, ["DEL", "trading:config"])
    :ok
  end

  describe "mount" do
    test "renders page title", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ "Settings"
    end

    test "shows default values when trading:config absent", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ "10"    # RSI2_ENTRY_CONSERVATIVE default
      assert html =~ "60"    # RSI2_EXIT default
      assert html =~ "20"    # DRAWDOWN_HALT default
    end

    test "shows override values when trading:config present", %{conn: conn} do
      Redix.command(:redix, ["SET", "trading:config",
        Jason.encode!(%{"RSI2_ENTRY_CONSERVATIVE" => 7.0, "RSI2_EXIT" => 55.0})])
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ "7"
      assert html =~ "55"
    end

    test "shows no-overrides indicator when trading:config absent", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ "No active overrides"
    end

    test "shows active-overrides indicator when trading:config present", %{conn: conn} do
      Redix.command(:redix, ["SET", "trading:config", Jason.encode!(%{"RSI2_EXIT" => 55.0})])
      {:ok, _view, html} = live(conn, "/settings")
      assert html =~ "Active overrides"
    end
  end

  describe "save event" do
    test "writes config to Redis and shows success flash", %{conn: conn} do
      {:ok, view, _} = live(conn, "/settings")

      params = %{
        "RSI2_ENTRY_CONSERVATIVE" => "8.0",
        "RSI2_ENTRY_AGGRESSIVE" => "3.0",
        "RSI2_EXIT" => "65.0",
        "RSI2_MAX_HOLD_DAYS" => "4",
        "RISK_PER_TRADE_PCT" => "0.01",
        "MAX_CONCURRENT_POSITIONS" => "5",
        "DRAWDOWN_CAUTION" => "5.0",
        "DRAWDOWN_DEFENSIVE" => "10.0",
        "DRAWDOWN_CRITICAL" => "15.0",
        "DRAWDOWN_HALT" => "20.0"
      }

      html = view |> form("#settings-form", config: params) |> render_submit()
      assert html =~ "saved"

      {:ok, raw} = Redix.command(:redix, ["GET", "trading:config"])
      assert raw != nil
      decoded = Jason.decode!(raw)
      assert decoded["RSI2_ENTRY_CONSERVATIVE"] == 8.0
    end

    test "shows error flash on non-numeric input", %{conn: conn} do
      {:ok, view, _} = live(conn, "/settings")

      params = %{
        "RSI2_ENTRY_CONSERVATIVE" => "not_a_number",
        "RSI2_ENTRY_AGGRESSIVE" => "3.0",
        "RSI2_EXIT" => "65.0",
        "RSI2_MAX_HOLD_DAYS" => "4",
        "RISK_PER_TRADE_PCT" => "0.01",
        "MAX_CONCURRENT_POSITIONS" => "5",
        "DRAWDOWN_CAUTION" => "5.0",
        "DRAWDOWN_DEFENSIVE" => "10.0",
        "DRAWDOWN_CRITICAL" => "15.0",
        "DRAWDOWN_HALT" => "20.0"
      }

      html = view |> form("#settings-form", config: params) |> render_submit()
      assert html =~ "expected a number"

      {:ok, raw} = Redix.command(:redix, ["GET", "trading:config"])
      assert raw == nil
    end
  end

  describe "reset event" do
    test "deletes trading:config key and shows success flash", %{conn: conn} do
      Redix.command(:redix, ["SET", "trading:config", Jason.encode!(%{"RSI2_EXIT" => 55.0})])

      {:ok, view, _} = live(conn, "/settings")
      html = view |> element("button[phx-click='reset']") |> render_click()
      assert html =~ "Defaults restored"

      {:ok, raw} = Redix.command(:redix, ["GET", "trading:config"])
      assert raw == nil
    end
  end
end
```

- [ ] **Step 4.2: Run tests to verify they fail**

```bash
cd dashboard && mix test test/dashboard_web/live/settings_live_test.exs
```

Expected: `UndefinedFunctionError` or compilation error — `DashboardWeb.SettingsLive` does not exist

### Step 4.3: Implement `settings_live.ex`

Create `dashboard/lib/dashboard_web/live/settings_live.ex`:

```elixir
defmodule DashboardWeb.SettingsLive do
  use DashboardWeb, :live_view

  @defaults %{
    "RSI2_ENTRY_CONSERVATIVE" => "10.0",
    "RSI2_ENTRY_AGGRESSIVE"   => "5.0",
    "RSI2_EXIT"               => "60.0",
    "RSI2_MAX_HOLD_DAYS"      => "5",
    "RISK_PER_TRADE_PCT"      => "0.01",
    "MAX_CONCURRENT_POSITIONS"=> "5",
    "DRAWDOWN_CAUTION"        => "5.0",
    "DRAWDOWN_DEFENSIVE"      => "10.0",
    "DRAWDOWN_CRITICAL"       => "15.0",
    "DRAWDOWN_HALT"           => "20.0"
  }

  @float_keys ~w(RSI2_ENTRY_CONSERVATIVE RSI2_ENTRY_AGGRESSIVE RSI2_EXIT
                 RISK_PER_TRADE_PCT DRAWDOWN_CAUTION DRAWDOWN_DEFENSIVE
                 DRAWDOWN_CRITICAL DRAWDOWN_HALT)
  @int_keys ~w(RSI2_MAX_HOLD_DAYS MAX_CONCURRENT_POSITIONS)

  def mount(_params, _session, socket) do
    {form_params, has_overrides} = load_config()
    {:ok, assign(socket, form_params: form_params, has_overrides: has_overrides)}
  end

  def handle_event("save", %{"config" => params}, socket) do
    case parse_config(params) do
      {:ok, config_map} ->
        case Redix.command(:redix, ["SET", "trading:config", Jason.encode!(config_map)]) do
          {:ok, _} ->
            {form_params, _} = load_config()
            {:noreply,
             socket
             |> assign(form_params: form_params, has_overrides: true)
             |> put_flash(:info, "Settings saved.")}
          {:error, _} ->
            {:noreply, put_flash(socket, :error, "Failed to save settings.")}
        end
      {:error, msg} ->
        {:noreply, put_flash(socket, :error, msg)}
    end
  end

  def handle_event("reset", _params, socket) do
    case Redix.command(:redix, ["DEL", "trading:config"]) do
      {:ok, _} ->
        {:noreply,
         socket
         |> assign(form_params: @defaults, has_overrides: false)
         |> put_flash(:info, "Defaults restored.")}
      {:error, _} ->
        {:noreply, put_flash(socket, :error, "Failed to reset settings.")}
    end
  end

  defp load_config do
    case Redix.command(:redix, ["GET", "trading:config"]) do
      {:ok, nil} ->
        {@defaults, false}
      {:ok, raw} ->
        overrides = Jason.decode!(raw)
        merged =
          Map.merge(@defaults, Map.new(overrides, fn {k, v} -> {k, to_string(v)} end))
        {merged, true}
      {:error, _} ->
        {@defaults, false}
    end
  end

  defp parse_config(params) do
    result =
      Enum.reduce_while(@float_keys ++ @int_keys, %{}, fn key, acc ->
        val = Map.get(params, key, "")

        case parse_value(key, val) do
          {:ok, parsed} -> {:cont, Map.put(acc, key, parsed)}
          {:error, _} = err -> {:halt, err}
        end
      end)

    case result do
      {:error, _} = err -> err
      map -> {:ok, map}
    end
  end

  defp parse_value(key, val) when key in @float_keys do
    case Float.parse(String.trim(val)) do
      {f, _} -> {:ok, f}
      :error  -> {:error, "#{key}: expected a number, got #{inspect(val)}"}
    end
  end

  defp parse_value(key, val) when key in @int_keys do
    case Integer.parse(String.trim(val)) do
      {i, _} -> {:ok, i}
      :error  -> {:error, "#{key}: expected an integer, got #{inspect(val)}"}
    end
  end
end
```

### Step 4.4: Implement `settings_live.html.heex`

Create `dashboard/lib/dashboard_web/live/settings_live.html.heex`:

```heex
<div class="min-h-screen bg-gray-900 text-gray-100 px-3 sm:px-6 py-4 space-y-4">

  <%!-- Header --%>
  <div class="flex items-center justify-between">
    <div class="flex items-center gap-3">
      <a href="/" class="text-gray-500 hover:text-gray-300 transition-colors text-sm">← Dashboard</a>
      <h1 class="text-lg font-bold text-white tracking-tight">Settings</h1>
    </div>
    <div class="text-xs">
      <%= if @has_overrides do %>
        <span class="text-yellow-400">Active overrides</span>
      <% else %>
        <span class="text-gray-500">No active overrides — showing defaults</span>
      <% end %>
    </div>
  </div>

  <%!-- Flash --%>
  <%= if live_flash(@flash, :info) do %>
    <div class="bg-green-900/40 border border-green-700 rounded-lg px-4 py-2 text-sm text-green-300">
      {live_flash(@flash, :info)}
    </div>
  <% end %>
  <%= if live_flash(@flash, :error) do %>
    <div class="bg-red-900/40 border border-red-700 rounded-lg px-4 py-2 text-sm text-red-300">
      {live_flash(@flash, :error)}
    </div>
  <% end %>

  <form id="settings-form" phx-submit="save" class="space-y-4">

    <%!-- RSI Strategy --%>
    <div class="bg-gray-800 rounded-lg border border-gray-700 p-4 space-y-3">
      <h2 class="text-sm font-semibold text-gray-200">RSI Strategy</h2>
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div>
          <label class="block text-xs text-gray-400 mb-1">Entry Conservative</label>
          <input type="number" name="config[RSI2_ENTRY_CONSERVATIVE]"
            value={@form_params["RSI2_ENTRY_CONSERVATIVE"]}
            step="0.1" min="0.1" max="30"
            class="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500">
        </div>
        <div>
          <label class="block text-xs text-gray-400 mb-1">Entry Aggressive</label>
          <input type="number" name="config[RSI2_ENTRY_AGGRESSIVE]"
            value={@form_params["RSI2_ENTRY_AGGRESSIVE"]}
            step="0.1" min="0.1" max="20"
            class="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500">
        </div>
        <div>
          <label class="block text-xs text-gray-400 mb-1">Exit Threshold</label>
          <input type="number" name="config[RSI2_EXIT]"
            value={@form_params["RSI2_EXIT"]}
            step="1" min="50" max="95"
            class="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500">
        </div>
        <div>
          <label class="block text-xs text-gray-400 mb-1">Max Hold Days</label>
          <input type="number" name="config[RSI2_MAX_HOLD_DAYS]"
            value={@form_params["RSI2_MAX_HOLD_DAYS"]}
            step="1" min="1" max="30"
            class="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500">
        </div>
      </div>
    </div>

    <%!-- Position Limits --%>
    <div class="bg-gray-800 rounded-lg border border-gray-700 p-4 space-y-3">
      <h2 class="text-sm font-semibold text-gray-200">Position Limits</h2>
      <div class="grid grid-cols-2 gap-3 sm:w-1/2">
        <div>
          <label class="block text-xs text-gray-400 mb-1">Risk per Trade (%)</label>
          <input type="number" name="config[RISK_PER_TRADE_PCT]"
            value={@form_params["RISK_PER_TRADE_PCT"]}
            step="0.001" min="0.001" max="0.05"
            class="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500">
        </div>
        <div>
          <label class="block text-xs text-gray-400 mb-1">Max Concurrent Positions</label>
          <input type="number" name="config[MAX_CONCURRENT_POSITIONS]"
            value={@form_params["MAX_CONCURRENT_POSITIONS"]}
            step="1" min="1" max="20"
            class="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500">
        </div>
      </div>
    </div>

    <%!-- Drawdown Thresholds --%>
    <div class="bg-gray-800 rounded-lg border border-gray-700 p-4 space-y-3">
      <h2 class="text-sm font-semibold text-gray-200">Drawdown Thresholds (%)</h2>
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div>
          <label class="block text-xs text-gray-400 mb-1">Caution</label>
          <input type="number" name="config[DRAWDOWN_CAUTION]"
            value={@form_params["DRAWDOWN_CAUTION"]}
            step="0.5" min="0.5" max="99"
            class="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500">
        </div>
        <div>
          <label class="block text-xs text-gray-400 mb-1">Defensive</label>
          <input type="number" name="config[DRAWDOWN_DEFENSIVE]"
            value={@form_params["DRAWDOWN_DEFENSIVE"]}
            step="0.5" min="0.5" max="99"
            class="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500">
        </div>
        <div>
          <label class="block text-xs text-gray-400 mb-1">Critical</label>
          <input type="number" name="config[DRAWDOWN_CRITICAL]"
            value={@form_params["DRAWDOWN_CRITICAL"]}
            step="0.5" min="0.5" max="99"
            class="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500">
        </div>
        <div>
          <label class="block text-xs text-gray-400 mb-1">Halt</label>
          <input type="number" name="config[DRAWDOWN_HALT]"
            value={@form_params["DRAWDOWN_HALT"]}
            step="0.5" min="0.5" max="99"
            class="w-full bg-gray-700 border border-gray-600 rounded px-2 py-1.5 text-sm text-white focus:outline-none focus:border-blue-500">
        </div>
      </div>
    </div>

    <%!-- Actions --%>
    <div class="flex gap-3">
      <button type="submit"
        class="px-4 py-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded transition-colors">
        Save
      </button>
      <button type="button" phx-click="reset"
        class="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-gray-300 text-sm font-medium rounded transition-colors">
        Reset to Defaults
      </button>
    </div>

  </form>

</div>
```

- [ ] **Step 4.5: Run tests to verify they pass**

```bash
cd dashboard && mix test test/dashboard_web/live/settings_live_test.exs
```

Expected: all tests PASS

- [ ] **Step 4.6: Run full Elixir test suite to verify no regressions**

```bash
cd dashboard && mix test
```

Expected: all tests PASS, 100% coverage maintained

- [ ] **Step 4.7: Commit**

```bash
git add dashboard/lib/dashboard_web/live/settings_live.ex \
        dashboard/lib/dashboard_web/live/settings_live.html.heex \
        dashboard/test/dashboard_web/live/settings_live_test.exs
git commit -m "feat: add SettingsLive page for runtime config hot-reload"
```

---

## Task 5: Wishlist, VERSION, and CHANGELOG

**Files:**
- Modify: `docs/FEATURE_WISHLIST.md`
- Modify: `VERSION`
- Modify: `CHANGELOG.md`
- Modify: `.remember/remember.md`

- [ ] **Step 5.1: Mark wishlist item 8 done**

In `docs/FEATURE_WISHLIST.md`, find the config hot-reload item and change `[ ]` to `[x]`. Add a note referencing the PR number once created.

- [ ] **Step 5.2: Bump VERSION to 0.29.0**

```
0.29.0
```

- [ ] **Step 5.3: Add CHANGELOG entry**

Add to top of `CHANGELOG.md`:

```markdown
## [0.29.0] - 2026-04-15

### Added
- Config hot-reload via `trading:config` Redis key — 10 strategy parameters (RSI-2 thresholds, position limits, drawdown circuit-breaker levels) can now be changed at runtime without restarting agents
- `load_overrides(r)` function in `scripts/config.py` — called at top of each agent cycle; invalid/out-of-range values are skipped with a log warning; missing key is a no-op
- `/settings` dashboard page — form to save or reset runtime config overrides, grouped by RSI Strategy / Position Limits / Drawdown Thresholds
```

- [ ] **Step 5.4: Update `.remember/remember.md`**

Run the `remember:remember` skill to capture current state.

- [ ] **Step 5.5: Commit all**

```bash
git add docs/FEATURE_WISHLIST.md VERSION CHANGELOG.md .remember/remember.md
git commit -m "chore: bump v0.29.0, mark wishlist item 8 done (config hot-reload)"
```
