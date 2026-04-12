# Strategy Attribution, Position Age Alert, Paper Report — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add exit-type strategy attribution to the performance page, a position age Telegram nudge when a hold exceeds 5 days, and a weekly paper-vs-simulated equity comparison.

**Architecture:** Three independent slices. Feature 8 spans Python (executor DB writes) + Elixir (schema, query, LiveView). Features 9 and 10 are pure Python additions to supervisor + notify.py. All follow TDD: test fails first, minimal code to pass.

**Tech Stack:** Python / psycopg2 / TimescaleDB, Elixir / Phoenix LiveView / Ecto, pytest, ExUnit / DataCase

---

## File Map

| File | Change |
|---|---|
| `init-db/001_create_schema.sql` | Add `exit_reason TEXT` to `CREATE TABLE trades` |
| `init-db/002_add_exit_reason.sql` | New — `ALTER TABLE` for existing deployments |
| `scripts/config.py` | Add `Keys.age_alert(symbol)` static method |
| `skills/executor/executor.py` | Add `import psycopg2`, `import os`, `get_db()`, `_log_trade()`, wire into `execute_buy`, `execute_sell`, `_reconcile_stop_filled` |
| `skills/executor/test_executor.py` | Tests for `_log_trade`, `execute_buy` DB call, `execute_sell` DB call, `_reconcile_stop_filled` DB call |
| `dashboard/lib/dashboard/schemas/trade.ex` | Add `field :exit_reason, :string` |
| `dashboard/lib/dashboard/queries.ex` | Add `exit_type_attribution/1` |
| `dashboard/test/dashboard/queries_test.exs` | Tests for `exit_type_attribution/1` |
| `dashboard/lib/dashboard_web/live/performance_live.ex` | Add `attribution` assign, wire `set_range` + `refresh_db`, add test injection handler |
| `dashboard/lib/dashboard_web/live/performance_live.html.heex` | Add attribution table section |
| `dashboard/test/dashboard_web/live/performance_live_test.exs` | Tests for attribution assign and table rendering |
| `skills/supervisor/supervisor.py` | Add age alert block to `run_health_check`; add paper report block to `run_weekly_summary` |
| `skills/supervisor/test_supervisor.py` | Tests for age alert (3 cases) and paper report (3 cases) |
| `scripts/notify.py` | Add paper comparison section to `weekly_summary()` |
| `scripts/test_notify.py` | Tests for paper section rendering |
| `docs/FEATURE_WISHLIST.md` | Mark #8, #9, #10 done |
| `VERSION` | Bump to 0.24.0 |
| `CHANGELOG.md` | Add v0.24.0 entry |

---

## Task 1: SQL schema — add exit_reason column

**Files:**
- Modify: `init-db/001_create_schema.sql`
- Create: `init-db/002_add_exit_reason.sql`

- [ ] **Step 1: Add exit_reason to CREATE TABLE in init-db/001_create_schema.sql**

Find the `CREATE TABLE trades` block. Add one line after `notes TEXT`:

```sql
    notes           TEXT,                            -- agent reasoning summary
    exit_reason     TEXT                             -- stop_loss | take_profit | time_stop | stop_loss_auto | manual_liquidation
```

Also update the `COMMENT` at the top of the file if one exists.

- [ ] **Step 2: Create init-db/002_add_exit_reason.sql for existing deployments**

```sql
-- init-db/002_add_exit_reason.sql
-- Adds exit_reason column to trades table for systems already running 001.
-- Safe to run multiple times (IF NOT EXISTS).
ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_reason TEXT;
```

- [ ] **Step 3: Commit**

```bash
git add init-db/001_create_schema.sql init-db/002_add_exit_reason.sql
git commit -m "feat: add exit_reason column to trades schema"
```

---

## Task 2: Keys.age_alert — write failing test

**Files:**
- Modify: `scripts/test_config.py`

- [ ] **Step 1: Write the failing test**

Open `scripts/test_config.py`. Find the section testing the `Keys` class (look for existing tests of `Keys.heartbeat` or `Keys.exit_signaled`). Add:

```python
def test_keys_age_alert():
    from config import Keys
    key = Keys.age_alert("SPY")
    assert key == "trading:age_alert:SPY"

def test_keys_age_alert_crypto():
    from config import Keys
    key = Keys.age_alert("BTC/USD")
    assert key == "trading:age_alert:BTC/USD"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=scripts pytest scripts/test_config.py::test_keys_age_alert -v
```

Expected: `AttributeError: type object 'Keys' has no attribute 'age_alert'`

- [ ] **Step 3: Implement Keys.age_alert in scripts/config.py**

In `scripts/config.py`, find the `Keys` class. After the `manual_exit` static method (around line 248), add:

```python
    @staticmethod
    def age_alert(symbol: str) -> str:
        """Set when a position age nudge has been sent for this symbol today.
        24h TTL prevents repeat alerts within the same calendar day."""
        return f"trading:age_alert:{symbol}"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH=scripts pytest scripts/test_config.py::test_keys_age_alert scripts/test_config.py::test_keys_age_alert_crypto -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/config.py scripts/test_config.py
git commit -m "feat: add Keys.age_alert static method to config"
```

---

## Task 3: Executor DB infrastructure — write failing tests

**Files:**
- Modify: `skills/executor/test_executor.py`

- [ ] **Step 1: Write failing tests for _log_trade**

Open `skills/executor/test_executor.py`. Find the end of the file and add a new test class:

```python
# ── _log_trade ────────────────────────────────────────────────

class TestLogTrade:
    def test_log_trade_inserts_buy_row(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch("executor.psycopg2.connect", return_value=mock_conn):
            from executor import _log_trade
            _log_trade(
                symbol="SPY",
                side="buy",
                quantity=10,
                price=500.0,
                total_value=5000.0,
                order_id="ord-123",
                strategy="RSI2",
                asset_class="equity",
            )

        call_args = mock_cur.execute.call_args[0]
        assert "INSERT INTO trades" in call_args[0]
        params = call_args[1]
        assert params[0] == "SPY"
        assert params[1] == "buy"
        assert params[2] == 10
        assert params[3] == 500.0
        assert params[8] is None   # realized_pnl
        assert params[9] is None   # exit_reason
        mock_conn.commit.assert_called_once()
        mock_conn.close.assert_called_once()

    def test_log_trade_inserts_sell_row_with_exit_reason(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        with patch("executor.psycopg2.connect", return_value=mock_conn):
            from executor import _log_trade
            _log_trade(
                symbol="SPY",
                side="sell",
                quantity=10,
                price=510.0,
                total_value=5100.0,
                order_id="ord-456",
                strategy="RSI2",
                asset_class="equity",
                realized_pnl=98.0,
                exit_reason="take_profit",
            )

        call_args = mock_cur.execute.call_args[0]
        params = call_args[1]
        assert params[1] == "sell"
        assert params[8] == 98.0    # realized_pnl
        assert params[9] == "take_profit"  # exit_reason

    def test_log_trade_non_fatal_on_db_error(self):
        """DB failure must never crash the executor."""
        with patch("executor.psycopg2.connect", side_effect=Exception("db down")):
            from executor import _log_trade
            # Should not raise
            _log_trade(
                symbol="SPY", side="buy", quantity=10, price=500.0,
                total_value=5000.0, order_id="ord-999",
                strategy="RSI2", asset_class="equity",
            )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py::TestLogTrade -v
```

Expected: `ImportError` or `AttributeError: module 'executor' has no attribute '_log_trade'`

---

## Task 4: Implement executor DB infrastructure

**Files:**
- Modify: `skills/executor/executor.py`

- [ ] **Step 1: Add imports at the top of executor.py**

After the existing `import config` line, add:

```python
import os
import psycopg2
```

(Check if `import os` is already present — only add if missing.)

- [ ] **Step 2: Add get_db() after existing imports, before _handle_sigterm**

Copy supervisor's `get_db()` verbatim. Open `skills/supervisor/supervisor.py` lines 40–55 to see the exact implementation. The function reads these env vars: `TIMESCALEDB_HOST`, `TIMESCALEDB_PORT`, `TIMESCALEDB_DB`, `TIMESCALEDB_USER`, `TIMESCALEDB_PASSWORD`. It is marked `# pragma: no cover`.

Add immediately after the imports block:

```python
def get_db():  # pragma: no cover
    return psycopg2.connect(
        host=os.environ.get("TIMESCALEDB_HOST", "localhost"),
        port=int(os.environ.get("TIMESCALEDB_PORT", 5432)),
        dbname=os.environ.get("TIMESCALEDB_DB", "trading"),
        user=os.environ.get("TIMESCALEDB_USER", "trading"),
        password=os.environ.get("TIMESCALEDB_PASSWORD", ""),
    )
```

- [ ] **Step 3: Add _log_trade() after get_db()**

```python
def _log_trade(symbol, side, quantity, price, total_value, order_id,
               strategy, asset_class, realized_pnl=None, exit_reason=None):
    """Insert one trade row into TimescaleDB. Non-fatal — DB failure never blocks a trade."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO trades
               (symbol, side, quantity, price, total_value, order_id,
                strategy, asset_class, realized_pnl, exit_reason)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (symbol, side, quantity, price, total_value, order_id,
             strategy, asset_class, realized_pnl, exit_reason),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"  [Executor] ⚠️ Failed to log trade to DB: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py::TestLogTrade -v
```

Expected: all 3 PASS

---

## Task 5: Wire _log_trade into execute_buy — RED

**Files:**
- Modify: `skills/executor/test_executor.py`

- [ ] **Step 1: Write failing test**

Find the existing `TestExecuteBuy` class in `test_executor.py`. Add:

```python
    def test_execute_buy_logs_trade_to_db(self):
        r, store = make_redis({})
        trading_client = MagicMock()
        trading_client.get_clock.return_value = make_clock(is_open=True)
        trading_client.get_account.return_value = make_account(equity="5000.0")

        filled = make_order(status="filled")
        filled.filled_avg_price = "500.0"
        filled.filled_qty = "10"
        trading_client.submit_order.return_value = filled
        trading_client.get_order_by_id.return_value = filled

        order = make_buy_signal(symbol="SPY", qty=10, entry=500.0, stop=490.0)
        order["strategy"] = "RSI2"
        order["reasoning"] = ""

        with patch("executor._log_trade") as mock_log, \
             patch("executor.submit_stop_loss", return_value="stop-001"), \
             patch("executor.trade_alert"):
            from executor import execute_buy
            execute_buy(r, trading_client, order)

        mock_log.assert_called_once()
        call_kwargs = mock_log.call_args
        # _log_trade is called with positional args
        args = call_kwargs[0] if call_kwargs[0] else []
        kwargs = call_kwargs[1] if call_kwargs[1] else {}
        # Merge for inspection
        all_args = list(args) + list(kwargs.values())
        assert "SPY" in all_args
        assert "buy" in all_args
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py::TestExecuteBuy::test_execute_buy_logs_trade_to_db -v
```

Expected: FAIL — `AssertionError: assert mock_log.assert_called_once()` (called 0 times)

---

## Task 6: Implement _log_trade in execute_buy — GREEN

**Files:**
- Modify: `skills/executor/executor.py`

- [ ] **Step 1: Add _log_trade call to execute_buy**

In `execute_buy`, find the block that runs after a confirmed fill — after `r.set(Keys.POSITIONS, json.dumps(positions))` and before the `trade_alert(...)` call. Add:

```python
        _log_trade(
            symbol=symbol,
            side="buy",
            quantity=fill_qty,
            price=fill_price,
            total_value=round(fill_price * fill_qty, 4),
            order_id=str(alpaca_order.id),
            strategy=order["strategy"],
            asset_class="crypto" if is_crypto(symbol) else "equity",
        )
```

- [ ] **Step 2: Run test to verify it passes**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py::TestExecuteBuy::test_execute_buy_logs_trade_to_db -v
```

Expected: PASS

- [ ] **Step 3: Run full executor test suite to check no regressions**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py -v
```

Expected: all previously passing tests still PASS

---

## Task 7: Wire _log_trade into execute_sell — RED → GREEN

**Files:**
- Modify: `skills/executor/test_executor.py`, `skills/executor/executor.py`

- [ ] **Step 1: Write failing test**

Find the existing `TestExecuteSell` class. Add:

```python
    def test_execute_sell_logs_trade_with_exit_reason(self):
        pos = make_position(symbol="SPY", qty=10, entry=500.0, stop=490.0)
        pos["strategy"] = "RSI2"
        pos["entry_date"] = datetime.now().strftime("%Y-%m-%d")
        r, store = make_redis({"SPY": pos})

        trading_client = MagicMock()
        trading_client.get_clock.return_value = make_clock(is_open=True)

        filled = make_order(status="filled")
        filled.filled_avg_price = "510.0"
        filled.filled_qty = "10"
        trading_client.submit_order.return_value = filled
        trading_client.get_order_by_id.return_value = filled

        order = {
            "symbol": "SPY",
            "side": "sell",
            "signal_type": "take_profit",
            "reason": "RSI-2 at 65 > 60",
        }

        with patch("executor._log_trade") as mock_log, \
             patch("executor.exit_alert"):
            from executor import execute_sell
            execute_sell(r, trading_client, order)

        mock_log.assert_called_once()
        # Verify exit_reason is passed
        call_args = mock_log.call_args
        # _log_trade called with keyword args: exit_reason, realized_pnl
        assert call_args.kwargs.get("exit_reason") == "take_profit" or \
               "take_profit" in str(call_args)
        assert call_args.kwargs.get("side") == "sell" or "sell" in str(call_args)
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py::TestExecuteSell::test_execute_sell_logs_trade_with_exit_reason -v
```

Expected: FAIL — mock_log called 0 times

- [ ] **Step 3: Add _log_trade call to execute_sell**

In `execute_sell`, find the block after `exit_alert(...)` call and before the `print(f"[Executor] {emoji} SOLD...")` line. Add:

```python
        _log_trade(
            symbol=symbol,
            side="sell",
            quantity=quantity,
            price=fill_price,
            total_value=round(fill_price * quantity, 4),
            order_id=str(alpaca_order.id),
            strategy=pos.get("strategy", "RSI2"),
            asset_class="crypto" if is_crypto(symbol) else "equity",
            realized_pnl=round(pnl_dollar, 4),
            exit_reason=order.get("signal_type", "unknown"),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py -v
```

Expected: all PASS

---

## Task 8: Wire _log_trade into _reconcile_stop_filled — RED → GREEN

**Files:**
- Modify: `skills/executor/test_executor.py`, `skills/executor/executor.py`

- [ ] **Step 1: Write failing test**

Find (or create) the `TestReconcileStopFilled` class. Add:

```python
class TestReconcileStopFilled:
    def test_reconcile_logs_trade_with_stop_loss_auto(self):
        pos = make_position(symbol="SPY", qty=10, entry=500.0, stop=490.0)
        pos["strategy"] = "RSI2"
        pos["entry_date"] = datetime.now().strftime("%Y-%m-%d")
        positions = {"SPY": pos}
        r, store = make_redis({"SPY": pos})

        with patch("executor._log_trade") as mock_log, \
             patch("executor.exit_alert"):
            from executor import _reconcile_stop_filled
            _reconcile_stop_filled(r, pos, positions, "SPY", fill_price=488.0)

        mock_log.assert_called_once()
        assert "stop_loss_auto" in str(mock_log.call_args)
        assert "sell" in str(mock_log.call_args)
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py::TestReconcileStopFilled -v
```

Expected: FAIL

- [ ] **Step 3: Add _log_trade call to _reconcile_stop_filled**

In `_reconcile_stop_filled`, find the block after `exit_alert(...)` and before the `print(f"[Executor] ❌ STOP-LOSS AUTO-TRIGGERED...")` line. Add:

```python
    _log_trade(
        symbol=symbol,
        side="sell",
        quantity=quantity,
        price=fill_price,
        total_value=round(fill_price * quantity, 4),
        order_id=pos.get("order_id", ""),
        strategy=pos.get("strategy", "RSI2"),
        asset_class="crypto" if is_crypto(symbol) else "equity",
        realized_pnl=round(pnl_dollar, 4),
        exit_reason="stop_loss_auto",
    )
```

- [ ] **Step 4: Run full test suite**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add skills/executor/executor.py skills/executor/test_executor.py
git commit -m "feat: executor writes buy/sell trades to TimescaleDB with exit_reason"
```

---

## Task 9: Elixir Trade schema + exit_type_attribution — RED

**Files:**
- Modify: `dashboard/test/dashboard/queries_test.exs`

- [ ] **Step 1: Write failing tests**

Open `dashboard/test/dashboard/queries_test.exs`. After the existing `describe "recent_trades/1"` block, add:

```elixir
  describe "exit_type_attribution/1" do
    test "returns empty list when no sell trades exist" do
      assert Queries.exit_type_attribution() == []
    end

    test "returns empty list for 30d range with no data" do
      assert Queries.exit_type_attribution(30) == []
    end

    test "returns empty list for :all range with no data" do
      assert Queries.exit_type_attribution(:all) == []
    end
  end
```

Also add a schema query builder test in the `describe "schema query builders"` block:

```elixir
    test "exit_type_attribution/1 returns a list" do
      result = Queries.exit_type_attribution(30)
      assert is_list(result)
    end
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd dashboard && mix test test/dashboard/queries_test.exs 2>&1 | tail -20
```

Expected: compile error — `Queries.exit_type_attribution/0 is undefined`

---

## Task 10: Implement Trade schema field + exit_type_attribution — GREEN

**Files:**
- Modify: `dashboard/lib/dashboard/schemas/trade.ex`
- Modify: `dashboard/lib/dashboard/queries.ex`

- [ ] **Step 1: Add exit_reason field to Trade schema**

In `dashboard/lib/dashboard/schemas/trade.ex`, add after `field :notes, :string`:

```elixir
    field :exit_reason, :string
```

- [ ] **Step 2: Add exit_type_attribution/1 to queries.ex**

In `dashboard/lib/dashboard/queries.ex`, add after `instrument_performance/1`:

```elixir
  @doc "Exit type attribution: trade count + avg/total P&L grouped by exit_reason."
  def exit_type_attribution(days_back \\ 30) do
    try do
      cutoff =
        case days_back do
          :all -> nil
          n -> DateTime.add(DateTime.utc_now(), -n * 86_400, :second)
        end

      base =
        from t in Trade,
          where: t.side == "sell" and not is_nil(t.realized_pnl) and not is_nil(t.exit_reason),
          group_by: t.exit_reason,
          select: %{
            exit_reason: t.exit_reason,
            trade_count: count(t.id),
            avg_pnl: avg(t.realized_pnl),
            total_pnl: sum(t.realized_pnl)
          }

      query =
        case cutoff do
          nil -> base
          dt -> where(base, [t], t.time >= ^dt)
        end

      Repo.all(query)
    rescue
      _ -> []
    end
  end
```

- [ ] **Step 3: Run tests to verify they pass**

```bash
cd dashboard && mix test test/dashboard/queries_test.exs 2>&1 | tail -20
```

Expected: all PASS

---

## Task 11: Performance page — attribution assign — RED

**Files:**
- Modify: `dashboard/test/dashboard_web/live/performance_live_test.exs`

- [ ] **Step 1: Write failing tests**

Open `dashboard/test/dashboard_web/live/performance_live_test.exs`. In the existing `describe "mount"` block, add:

```elixir
    test "attribution assign present and initially empty list", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      assigns = :sys.get_state(view.pid).socket.assigns
      assert is_list(assigns.attribution)
    end
```

In the existing `describe "set_range event"` block, add:

```elixir
    test "set_range refreshes attribution", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      render_click(view, "set_range", %{"range" => "90d"})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert is_list(assigns.attribution)
    end
```

Add a new describe block for the attribution table:

```elixir
  describe "attribution table" do
    test "renders attribution heading when attribution present", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/performance")
      rows = [
        %{exit_reason: "take_profit", trade_count: 5,
          avg_pnl: Decimal.new("1.50"), total_pnl: Decimal.new("75.00")},
        %{exit_reason: "time_stop", trade_count: 2,
          avg_pnl: Decimal.new("-0.30"), total_pnl: Decimal.new("-6.00")}
      ]
      send(view.pid, {:set_attribution, rows})
      html = render(view)
      assert html =~ "Exit Type"
      assert html =~ "RSI / Price breakout"
      assert html =~ "Time stop"
    end

    test "shows empty state when attribution is empty list", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/performance")
      # attribution is empty at mount — no attribution rows visible
      refute html =~ "Exit Type"
    end
  end
```

- [ ] **Step 2: Run to verify tests fail**

```bash
cd dashboard && mix test test/dashboard_web/live/performance_live_test.exs 2>&1 | tail -20
```

Expected: failures on missing `attribution` assign and `{:set_attribution, ...}` handler.

---

## Task 12: Implement performance page attribution — GREEN

**Files:**
- Modify: `dashboard/lib/dashboard_web/live/performance_live.ex`
- Modify: `dashboard/lib/dashboard_web/live/performance_live.html.heex`

- [ ] **Step 1: Add attribution assign to mount/2**

In `performance_live.ex` `mount/3`, in the `socket = socket |> assign(...)` chain, add after the `equity_points` assign:

```elixir
      |> assign(:attribution, if(connected?(socket), do: Queries.exit_type_attribution(30), else: []))
```

- [ ] **Step 2: Add attribution refresh to handle_event("set_range", ...)**

In the `handle_event("set_range", ...)` handler, add `attribution: Queries.exit_type_attribution(days_back)` to the `assign(socket, ...)` call:

```elixir
    {:noreply,
     assign(socket,
       rows: rows,
       range: range,
       sort_col: :total_pnl,
       sort_dir: :desc,
       summary: compute_summary(rows),
       equity_points: Queries.equity_curve(days_back),
       attribution: Queries.exit_type_attribution(days_back)
     )}
```

- [ ] **Step 3: Add attribution refresh to handle_info(:refresh_db, ...)**

In the `:refresh_db` handler's `assign` pipeline, add:

```elixir
     |> assign(:attribution, Queries.exit_type_attribution(days_back))
```

- [ ] **Step 4: Add test injection handler**

After the existing `handle_info({:set_equity_points, points}, socket)` handler, add:

```elixir
  def handle_info({:set_attribution, rows}, socket) do
    {:noreply, assign(socket, :attribution, rows)}
  end
```

- [ ] **Step 5: Add display name helper**

After the private `format_win_rate` helpers, add:

```elixir
  defp exit_type_label(nil), do: "Other"
  defp exit_type_label("take_profit"), do: "RSI / Price breakout"
  defp exit_type_label("time_stop"), do: "Time stop"
  defp exit_type_label("stop_loss"), do: "Stop loss"
  defp exit_type_label("stop_loss_auto"), do: "Stop loss (auto)"
  defp exit_type_label("manual_liquidation"), do: "Manual"
  defp exit_type_label(other), do: other
```

- [ ] **Step 6: Add attribution table to template**

In `performance_live.html.heex`, at the end of the file (after the per-instrument table `</div>`), add:

```heex
  <%# Attribution by exit type — only rendered when data is available %>
  <%= if @attribution != [] do %>
    <div class="bg-gray-800 rounded-lg border border-gray-700 p-4">
      <h2 class="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">Exit Type</h2>
      <div class="overflow-x-auto">
        <table class="w-full text-xs">
          <thead>
            <tr class="text-gray-500 border-b border-gray-700">
              <th class="text-left pb-2 pr-3">Type</th>
              <th class="text-right pb-2 pr-3">Trades</th>
              <th class="text-right pb-2 pr-3">Avg P&amp;L</th>
              <th class="text-right pb-2">Total P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            <%= for row <- @attribution do %>
              <tr class="border-b border-gray-700/50 hover:bg-gray-700/30">
                <td class="py-2 pr-3 text-gray-300">{exit_type_label(row.exit_reason)}</td>
                <td class="py-2 pr-3 text-right text-gray-400">{row.trade_count}</td>
                <td class={["py-2 pr-3 text-right",
                            if(row.avg_pnl && Decimal.compare(row.avg_pnl, Decimal.new(0)) == :gt,
                              do: "text-green-400", else: "text-red-400")]}>
                  {format_pnl(row.avg_pnl)}
                </td>
                <td class={["py-2 text-right font-medium",
                            if(row.total_pnl && Decimal.compare(row.total_pnl, Decimal.new(0)) == :gt,
                              do: "text-green-400", else: "text-red-400")]}>
                  {format_pnl(row.total_pnl)}
                </td>
              </tr>
            <% end %>
          </tbody>
        </table>
      </div>
    </div>
  <% end %>
```

- [ ] **Step 7: Run tests**

```bash
cd dashboard && mix test test/dashboard_web/live/performance_live_test.exs 2>&1 | tail -20
```

Expected: all PASS

- [ ] **Step 8: Run full Elixir test suite**

```bash
cd dashboard && mix test 2>&1 | tail -10
```

Expected: 0 failures

- [ ] **Step 9: Commit**

```bash
git add dashboard/lib/dashboard/schemas/trade.ex \
        dashboard/lib/dashboard/queries.ex \
        dashboard/lib/dashboard_web/live/performance_live.ex \
        dashboard/lib/dashboard_web/live/performance_live.html.heex \
        dashboard/test/dashboard/queries_test.exs \
        dashboard/test/dashboard_web/live/performance_live_test.exs
git commit -m "feat: exit_reason column in Trade schema + exit_type_attribution query + performance page breakdown"
```

---

## Task 13: Supervisor position age alert — RED

**Files:**
- Modify: `skills/supervisor/test_supervisor.py`

- [ ] **Step 1: Write failing tests**

Open `skills/supervisor/test_supervisor.py`. Find `class TestRunHealthCheck`. Add three tests:

```python
    def test_age_alert_fires_for_position_held_5_days(self):
        positions = {
            "SPY": {
                "symbol": "SPY",
                "quantity": 10,
                "entry_price": 500.0,
                "entry_date": (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d"),
                "stop_price": 490.0,
                "strategy": "RSI2",
                "tier": 1,
                "unrealized_pnl_pct": -0.5,
            }
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        r.exists = lambda k: 0  # no dedup key present

        with patch("supervisor.notify") as mock_notify, \
             patch("supervisor.run_circuit_breakers"), \
             patch("supervisor.attempt_service_restart"), \
             patch("supervisor.critical_alert"):
            from supervisor import run_health_check
            run_health_check(r)

        # Telegram nudge sent
        notify_calls = [str(c) for c in mock_notify.call_args_list]
        assert any("SPY" in c and "5" in c for c in notify_calls), \
            f"Expected SPY age alert in notify calls: {notify_calls}"
        # Dedup key set
        set_calls = [str(c) for c in r.set.call_args_list]
        assert any("age_alert:SPY" in c for c in set_calls), \
            f"Expected age_alert key set: {set_calls}"

    def test_age_alert_suppressed_when_dedup_key_exists(self):
        positions = {
            "SPY": {
                "symbol": "SPY",
                "quantity": 10,
                "entry_price": 500.0,
                "entry_date": (datetime.now() - timedelta(days=6)).strftime("%Y-%m-%d"),
                "stop_price": 490.0,
                "strategy": "RSI2",
                "tier": 1,
                "unrealized_pnl_pct": -1.0,
            }
        }
        r = make_redis({
            Keys.POSITIONS: json.dumps(positions),
            Keys.age_alert("SPY"): "1",  # dedup key already set
        })

        age_notify_calls = []
        def capture_notify(msg, **kw):
            if "SPY" in msg and "days" in msg.lower():
                age_notify_calls.append(msg)
        with patch("supervisor.notify", side_effect=capture_notify), \
             patch("supervisor.run_circuit_breakers"), \
             patch("supervisor.attempt_service_restart"), \
             patch("supervisor.critical_alert"):
            from supervisor import run_health_check
            run_health_check(r)

        assert age_notify_calls == [], f"Unexpected age alert: {age_notify_calls}"

    def test_age_alert_not_fired_when_hold_days_below_threshold(self):
        positions = {
            "SPY": {
                "symbol": "SPY",
                "quantity": 10,
                "entry_price": 500.0,
                "entry_date": (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d"),
                "stop_price": 490.0,
                "strategy": "RSI2",
                "tier": 1,
                "unrealized_pnl_pct": 1.0,
            }
        }
        r = make_redis({Keys.POSITIONS: json.dumps(positions)})
        r.exists = lambda k: 0

        age_notify_calls = []
        def capture_notify(msg, **kw):
            if "age" in msg.lower() or ("SPY" in msg and "days" in msg.lower()):
                age_notify_calls.append(msg)
        with patch("supervisor.notify", side_effect=capture_notify), \
             patch("supervisor.run_circuit_breakers"), \
             patch("supervisor.attempt_service_restart"), \
             patch("supervisor.critical_alert"):
            from supervisor import run_health_check
            run_health_check(r)

        assert age_notify_calls == [], f"Should not alert for 2-day hold: {age_notify_calls}"
```

- [ ] **Step 2: Run to verify they fail**

```bash
PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py::TestRunHealthCheck::test_age_alert_fires_for_position_held_5_days skills/supervisor/test_supervisor.py::TestRunHealthCheck::test_age_alert_suppressed_when_dedup_key_exists skills/supervisor/test_supervisor.py::TestRunHealthCheck::test_age_alert_not_fired_when_hold_days_below_threshold -v
```

Expected: FAIL — age alert not yet implemented

---

## Task 14: Implement position age alert — GREEN

**Files:**
- Modify: `skills/supervisor/supervisor.py`

- [ ] **Step 1: Add age alert block to run_health_check**

In `run_health_check`, find the `# Position check` comment block where `positions` is loaded:

```python
    # Position check
    positions = json.loads(r.get(Keys.POSITIONS) or "{}")
    print(f"  📊 Open positions: {len(positions)}")
```

Immediately after the `print(...)` line, add:

```python
    # Position age nudge — once-per-day Telegram alert when a position has been
    # held at or past the time-stop threshold (5 days).  Acts as a human backstop
    # in case the executor is offline or the time-stop never cleared.
    for symbol, pos in positions.items():
        try:
            entry_dt = datetime.strptime(pos["entry_date"], "%Y-%m-%d")
            hold_days = (datetime.now() - entry_dt).days
        except Exception:
            hold_days = 0

        if hold_days >= config.RSI2_MAX_HOLD_DAYS:
            dedup_key = Keys.age_alert(symbol)
            if not r.exists(dedup_key):
                pnl_pct = float(pos.get("unrealized_pnl_pct", 0))
                notify(
                    f"⏰ <b>Position age nudge: {symbol}</b>\n"
                    f"\n"
                    f"Held {hold_days} days (time-stop threshold: {config.RSI2_MAX_HOLD_DAYS}d)\n"
                    f"Entry: ${float(pos.get('entry_price', 0)):,.2f} | "
                    f"Unrealized P&amp;L: {pnl_pct:+.1f}%\n"
                    f"Review or wait for exit signal."
                )
                r.set(dedup_key, "1", ex=86400)  # suppress for 24 hours
                print(f"  ⏰ {symbol}: position age alert sent ({hold_days}d held)")
```

- [ ] **Step 2: Run tests to verify they pass**

```bash
PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py::TestRunHealthCheck::test_age_alert_fires_for_position_held_5_days skills/supervisor/test_supervisor.py::TestRunHealthCheck::test_age_alert_suppressed_when_dedup_key_exists skills/supervisor/test_supervisor.py::TestRunHealthCheck::test_age_alert_not_fired_when_hold_days_below_threshold -v
```

Expected: all PASS

- [ ] **Step 3: Run full supervisor test suite**

```bash
PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py -v
```

Expected: all PASS, no regressions

- [ ] **Step 4: Commit**

```bash
git add skills/supervisor/supervisor.py skills/supervisor/test_supervisor.py scripts/config.py scripts/test_config.py
git commit -m "feat: position age alert in health check — Telegram nudge when hold >= 5 days"
```

---

## Task 15: Supervisor paper report — RED

**Files:**
- Modify: `skills/supervisor/test_supervisor.py`

- [ ] **Step 1: Write failing tests**

Find `class TestRunWeeklySummary` (or add one). Add:

```python
class TestRunWeeklySummaryPaperReport:
    """Tests for the paper-vs-simulated equity comparison added to run_weekly_summary."""

    def _run_with_alpaca(self, r, portfolio_value, should_fail=False):
        mock_account = MagicMock()
        mock_account.portfolio_value = str(portfolio_value)

        mock_client = MagicMock()
        if should_fail:
            mock_client.get_account.side_effect = Exception("API error")
        else:
            mock_client.get_account.return_value = mock_account

        with patch("supervisor.weekly_summary") as mock_summary, \
             patch("supervisor.get_db") as mock_db, \
             patch("supervisor.TradingClient", return_value=mock_client):
            cur = make_cursor()
            conn = MagicMock()
            conn.cursor.return_value = cur
            mock_db.return_value = conn
            from supervisor import run_weekly_summary
            run_weekly_summary(r)
        return mock_summary

    def test_paper_report_included_when_alpaca_available(self):
        r = make_redis()
        mock_summary = self._run_with_alpaca(r, portfolio_value=101_000.0)
        mock_summary.assert_called_once()
        metrics = mock_summary.call_args[0][0]
        assert "alpaca_portfolio_value" in metrics
        assert metrics["alpaca_portfolio_value"] == 101_000.0

    def test_divergence_computed_correctly(self):
        # simulated_equity=5000 (0% return), Alpaca=102_000 (2% return)
        r = make_redis()  # simulated_equity defaults to "5000.0"
        mock_summary = self._run_with_alpaca(r, portfolio_value=102_000.0)
        metrics = mock_summary.call_args[0][0]
        # Alpaca return: (102000 - 100000) / 100000 * 100 = 2.0%
        # Sim return: (5000 - 5000) / 5000 * 100 = 0.0%
        # Divergence: 2.0
        assert abs(metrics["paper_divergence_pct"] - 2.0) < 0.1
        assert abs(metrics["alpaca_return_pct"] - 2.0) < 0.1
        assert abs(metrics["simulated_return_pct"] - 0.0) < 0.1

    def test_paper_report_omitted_on_alpaca_failure(self):
        r = make_redis()
        mock_summary = self._run_with_alpaca(r, portfolio_value=0, should_fail=True)
        metrics = mock_summary.call_args[0][0]
        assert "alpaca_portfolio_value" not in metrics
```

- [ ] **Step 2: Run to verify they fail**

```bash
PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py::TestRunWeeklySummaryPaperReport -v
```

Expected: FAIL — `TradingClient` not imported in supervisor, paper metrics not in call args

---

## Task 16: Implement supervisor paper report — GREEN

**Files:**
- Modify: `skills/supervisor/supervisor.py`

- [ ] **Step 1: Add TradingClient to supervisor imports**

At the top of `supervisor.py`, after the `import os` line, add:

```python
from alpaca.trading.client import TradingClient
```

- [ ] **Step 2: Add paper report block to run_weekly_summary**

In `run_weekly_summary`, find the section after the `try/except` DB block (after `cur.close(); conn.close()` and before `# Compute weekly P&L %`). Add:

```python
    # Paper vs simulated equity comparison
    paper_data = {}
    try:
        tc = TradingClient(
            api_key=os.environ.get("ALPACA_API_KEY", ""),
            secret_key=os.environ.get("ALPACA_SECRET_KEY", ""),
            paper=True,
        )
        acct = tc.get_account()
        alpaca_value = float(acct.portfolio_value)
        alpaca_return_pct = (alpaca_value - 100_000) / 100_000 * 100
        sim_return_pct = (equity - 5_000) / 5_000 * 100
        divergence = abs(sim_return_pct - alpaca_return_pct)
        paper_data = {
            "alpaca_portfolio_value": round(alpaca_value, 2),
            "alpaca_return_pct": round(alpaca_return_pct, 2),
            "simulated_return_pct": round(sim_return_pct, 2),
            "paper_divergence_pct": round(divergence, 2),
        }
        print(f"[Supervisor] Paper report: Alpaca ${alpaca_value:,.2f} | "
              f"Sim ${equity:,.2f} | Δ {divergence:.1f}%")
    except Exception as e:
        print(f"  [Supervisor] Paper report unavailable: {e}")
```

Then, add `**paper_data` to the `weekly_summary({...})` call:

```python
    weekly_summary({
        "week": week_label,
        "equity": round(equity, 2),
        ...existing keys...,
        **paper_data,
    })
```

- [ ] **Step 3: Update supervisor test mock setup**

The supervisor test file mocks `psycopg2` and `redis` in `sys.modules`. It does NOT mock `alpaca`. Since `TradingClient` is now a top-level import, tests that don't use paper report will see `TradingClient` as a MagicMock (since alpaca is already in sys.modules via the executor's mock setup, or it's installed on the system).

Check if alpaca is already mocked in supervisor test setup. If not, add to the top of `test_supervisor.py`:

```python
for mod in ["alpaca", "alpaca.trading", "alpaca.trading.client"]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()
```

Add this block in the same location as the existing `for mod in ["psycopg2", "redis"]` block.

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py::TestRunWeeklySummaryPaperReport -v
```

Expected: all PASS

- [ ] **Step 5: Run full supervisor test suite**

```bash
PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py -v
```

Expected: all PASS

---

## Task 17: notify.py paper section — RED → GREEN

**Files:**
- Modify: `scripts/test_notify.py`
- Modify: `scripts/notify.py`

- [ ] **Step 1: Write failing tests**

Open `scripts/test_notify.py`. Find (or add) tests for `weekly_summary`. Add:

```python
class TestWeeklySummaryPaperReport:
    def _metrics(self, **overrides):
        base = {
            "week": "W15 2026",
            "equity": 5100.0,
            "weekly_pnl": 100.0,
            "weekly_pnl_pct": 2.0,
            "drawdown_pct": 0.0,
            "total_trades": 5,
            "winners": 4,
            "losers": 1,
            "best_trade": "SPY +1.5%",
            "worst_trade": "QQQ -0.3%",
            "universe_size": 17,
            "active_instruments": 17,
            "disabled_instruments": 0,
        }
        base.update(overrides)
        return base

    def test_paper_section_present_when_data_available(self):
        metrics = self._metrics(
            alpaca_portfolio_value=101_000.0,
            alpaca_return_pct=1.0,
            simulated_return_pct=2.0,
            paper_divergence_pct=1.0,
        )
        with patch("notify.notify") as mock_notify:
            from notify import weekly_summary
            weekly_summary(metrics)
        msg = mock_notify.call_args[0][0]
        assert "Paper vs Simulated" in msg
        assert "+2.00%" in msg or "2.00%" in msg
        assert "+1.00%" in msg or "1.00%" in msg

    def test_paper_section_absent_when_no_alpaca_data(self):
        metrics = self._metrics()  # no paper keys
        with patch("notify.notify") as mock_notify:
            from notify import weekly_summary
            weekly_summary(metrics)
        msg = mock_notify.call_args[0][0]
        assert "Paper vs Simulated" not in msg

    def test_paper_section_shows_warning_on_divergence_over_5pct(self):
        metrics = self._metrics(
            alpaca_portfolio_value=95_000.0,
            alpaca_return_pct=-5.0,
            simulated_return_pct=2.0,
            paper_divergence_pct=7.0,
        )
        with patch("notify.notify") as mock_notify:
            from notify import weekly_summary
            weekly_summary(metrics)
        msg = mock_notify.call_args[0][0]
        assert "DIVERGENCE" in msg or "⚠️" in msg
```

- [ ] **Step 2: Run to verify they fail**

```bash
PYTHONPATH=scripts pytest scripts/test_notify.py::TestWeeklySummaryPaperReport -v
```

Expected: FAIL — no paper section in message

- [ ] **Step 3: Add paper section to notify.py weekly_summary**

In `scripts/notify.py`, in the `weekly_summary(metrics: dict)` function, after `msg = (...)`, add:

```python
    if d.get("alpaca_portfolio_value"):
        divergence = d.get("paper_divergence_pct", 0.0)
        div_icon = "⚠️ DIVERGENCE" if divergence > 5.0 else "✅"
        msg += (
            f"\n"
            f"📊 <b>Paper vs Simulated</b>\n"
            f"Simulated: {d.get('simulated_return_pct', 0):+.2f}% | "
            f"Alpaca paper: {d.get('alpaca_return_pct', 0):+.2f}% | "
            f"Δ {divergence:.1f}% {div_icon}\n"
        )
```

Also update the docstring to document the new keys:
```
        alpaca_portfolio_value (optional), alpaca_return_pct (optional),
        simulated_return_pct (optional), paper_divergence_pct (optional)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=scripts pytest scripts/test_notify.py::TestWeeklySummaryPaperReport -v
```

Expected: all PASS

- [ ] **Step 5: Run full Python test suite**

```bash
PYTHONPATH=scripts pytest scripts/ skills/ -v 2>&1 | tail -20
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add skills/supervisor/supervisor.py skills/supervisor/test_supervisor.py \
        scripts/notify.py scripts/test_notify.py
git commit -m "feat: weekly paper vs simulated equity comparison report"
```

---

## Task 18: Final sweep — wishlist, VERSION, CHANGELOG

**Files:**
- Modify: `docs/FEATURE_WISHLIST.md`
- Modify: `VERSION`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Mark features done in FEATURE_WISHLIST.md**

In the `## 📋 Next Priority Wave (as of 2026-04-11)` section, mark items 7, 8, 9, 10 done:

```markdown
7. [x] **Equity curve chart** — ✅ Done (PR #92)
8. [x] **Strategy attribution by exit type** — ✅ Done (this PR): executor logs trades to TimescaleDB with `exit_reason`; attribution table on /performance page.
9. [x] **Position age alert** — ✅ Done (this PR): supervisor health check sends Telegram nudge when position held ≥ 5 days, once per day per symbol.
10. [x] **Paper trading report vs Alpaca balance** — ✅ Done (this PR): weekly summary includes paper vs simulated return comparison; warns on divergence > 5%.
```

- [ ] **Step 2: Bump VERSION**

```bash
echo "0.24.0" > VERSION
```

- [ ] **Step 3: Update CHANGELOG.md**

Add a new entry at the top (after the header):

```markdown
## [0.24.0] - 2026-04-12

### Added
- **Strategy attribution by exit type** (#8): executor now writes all trades to TimescaleDB with `exit_reason` column (`take_profit`, `time_stop`, `stop_loss`, `stop_loss_auto`, `manual_liquidation`). New exit-type attribution table on `/performance` page shows trade count, avg P&L, and total P&L per exit category.
- **Position age alert** (#9): supervisor health check sends a once-per-day Telegram nudge when any open position has been held for ≥ 5 days (the time-stop threshold). Uses a Redis dedup key with 24h TTL to avoid repeat spam.
- **Paper trading report** (#10): Friday weekly summary now includes a paper-vs-simulated equity comparison. Fetches Alpaca paper portfolio value, computes percentage returns for both, and flags divergence > 5% as a potential sizing/accounting bug.
- **Executor TimescaleDB writes** (prerequisite): executor previously never wrote to the `trades` hypertable. All fills (buy, sell, stop_loss_auto) are now logged with symbol, price, quantity, P&L, and exit_reason.
```

- [ ] **Step 4: Run full test suite one final time**

```bash
PYTHONPATH=scripts pytest scripts/ skills/ -v 2>&1 | tail -5
cd dashboard && mix test 2>&1 | tail -5
```

Expected: 0 failures across all Python and Elixir tests.

- [ ] **Step 5: Final commit**

```bash
git add docs/FEATURE_WISHLIST.md VERSION CHANGELOG.md .remember/remember.md
git commit -m "feat: strategy attribution + position age alert + paper report (v0.24.0)"
```
