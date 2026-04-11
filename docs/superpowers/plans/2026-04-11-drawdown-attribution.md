# Drawdown Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a drawdown circuit breaker fires, show a per-instrument breakdown (realized + unrealized P&L since peak) in the Telegram alert and on the dashboard.

**Architecture:** Add `trading:peak_equity_date` Redis key (set alongside `PEAK_EQUITY`). New `get_drawdown_attribution(r, conn)` helper in `config.py` queries TimescaleDB for realized losses since peak date and merges with open-position unrealized P&L from Redis. `drawdown_alert()` gains an optional `attribution` param. Dashboard computes attribution via a new `Queries.drawdown_attribution/2` function and renders it in the main LiveView on every state update.

**Tech Stack:** Python 3, psycopg2, Redis, Elixir/Phoenix LiveView, Ecto, TimescaleDB, pytest, ExUnit

---

## File Map

**Create:** none

**Modify:**
- `scripts/config.py` — add `Keys.PEAK_EQUITY_DATE`; set it in `update_simulated_equity()`; init in `init_redis_state()`; add `get_drawdown_attribution(r, conn)`
- `scripts/test_config.py` — tests for `get_drawdown_attribution` and peak date setting
- `skills/executor/executor.py` — set `PEAK_EQUITY_DATE` alongside `PEAK_EQUITY` in `update_simulated_equity()`
- `skills/executor/test_executor.py` — test that peak date is set on new high
- `skills/supervisor/supervisor.py` — set `PEAK_EQUITY_DATE` in two places; wire attribution into `run_circuit_breakers()`
- `skills/supervisor/test_supervisor.py` — test peak date set, attribution wired
- `scripts/notify.py` — add optional `attribution` param to `drawdown_alert()`
- `scripts/test_notify.py` — tests for alert with/without attribution
- `dashboard/lib/dashboard/queries.ex` — add `drawdown_attribution/2`
- `dashboard/test/dashboard/queries_test.exs` — tests for `drawdown_attribution/2`
- `dashboard/lib/dashboard/redis_poller.ex` — add `"trading:peak_equity_date"` to `@redis_keys`
- `dashboard/test/dashboard/redis_poller_test.exs` — verify key is polled
- `dashboard/lib/dashboard_web/live/dashboard_live.ex` — add `drawdown_attribution` assign; compute in `handle_info`
- `dashboard/lib/dashboard_web/live/dashboard_live.html.heex` — render attribution table below drawdown badge
- `dashboard/test/dashboard_web/live/dashboard_live_test.exs` — test assign + render
- `VERSION`, `docs/CHANGELOG.md`, `docs/FEATURE_WISHLIST.md` — version bump

---

## Task 1: Add `PEAK_EQUITY_DATE` Redis key to config.py

**Files:**
- Modify: `scripts/config.py`
- Test: `scripts/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `scripts/test_config.py` (after existing imports and the `if "redis" not in sys.modules` block):

```python
def test_peak_equity_date_key_exists():
    assert Keys.PEAK_EQUITY_DATE == "trading:peak_equity_date"


def test_init_redis_state_sets_peak_equity_date():
    r = MagicMock()
    r.exists.return_value = False
    r.get.return_value = None
    init_redis_state(r)
    calls = [str(c) for c in r.set.call_args_list]
    assert any("peak_equity_date" in c for c in calls)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=scripts pytest scripts/test_config.py::test_peak_equity_date_key_exists scripts/test_config.py::test_init_redis_state_sets_peak_equity_date -v
```

Expected: `AttributeError: type object 'Keys' has no attribute 'PEAK_EQUITY_DATE'`

- [ ] **Step 3: Add key to `Keys` class and `init_redis_state` in config.py**

In `scripts/config.py`, add after `PEAK_EQUITY = "trading:peak_equity"` (around line 214):

```python
    PEAK_EQUITY_DATE = "trading:peak_equity_date"
```

In `init_redis_state()`, add after the `PEAK_EQUITY` init block:

```python
    if not r.exists(Keys.PEAK_EQUITY_DATE):
        r.set(Keys.PEAK_EQUITY_DATE, date.today().isoformat())
```

Also add `from datetime import date, timedelta` at the top of `config.py` if not already imported (check existing imports first).

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=scripts pytest scripts/test_config.py::test_peak_equity_date_key_exists scripts/test_config.py::test_init_redis_state_sets_peak_equity_date -v
```

Expected: PASS

- [ ] **Step 5: Run full config test suite**

```bash
PYTHONPATH=scripts pytest scripts/test_config.py -v
```

Expected: all green

- [ ] **Step 6: Commit**

```bash
git add scripts/config.py scripts/test_config.py
git commit -m "feat: add PEAK_EQUITY_DATE Redis key to config"
```

---

## Task 2: Set `PEAK_EQUITY_DATE` in executor when peak is updated

**Files:**
- Modify: `skills/executor/executor.py`
- Test: `skills/executor/test_executor.py`

The relevant code is in `update_simulated_equity()` around line 65–67:

```python
peak = float(r.get(Keys.PEAK_EQUITY) or config.INITIAL_CAPITAL)
if new_equity > peak:
    r.set(Keys.PEAK_EQUITY, str(round(new_equity, 2)))
```

- [ ] **Step 1: Write the failing test**

In `skills/executor/test_executor.py`, find the section that tests `update_simulated_equity` (or add new describe block). Add:

```python
def test_update_simulated_equity_sets_peak_date_on_new_high():
    """When new equity exceeds peak, PEAK_EQUITY_DATE is set to today."""
    from datetime import date
    r = MagicMock()
    r.get.side_effect = lambda key: {
        Keys.SIMULATED_EQUITY: "5000.0",
        Keys.PEAK_EQUITY: "4800.0",   # new_equity will exceed this
        Keys.DAILY_PNL: "0.0",
    }.get(key)

    update_simulated_equity(r, 500.0)  # new_equity = 5500 > peak 4800

    calls = {str(c) for c in r.set.call_args_list}
    assert any("peak_equity_date" in c and date.today().isoformat() in c for c in calls)


def test_update_simulated_equity_does_not_set_peak_date_when_not_new_high():
    """When new equity does not exceed peak, PEAK_EQUITY_DATE is not set."""
    r = MagicMock()
    r.get.side_effect = lambda key: {
        Keys.SIMULATED_EQUITY: "5000.0",
        Keys.PEAK_EQUITY: "6000.0",   # peak is higher
        Keys.DAILY_PNL: "0.0",
    }.get(key)

    update_simulated_equity(r, -200.0)  # new_equity = 4800 < peak 6000

    calls = [str(c) for c in r.set.call_args_list]
    assert not any("peak_equity_date" in c for c in calls)
```

- [ ] **Step 2: Run to verify failure**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py::test_update_simulated_equity_sets_peak_date_on_new_high -v
```

Expected: FAIL (date not set)

- [ ] **Step 3: Update `update_simulated_equity()` in executor.py**

Modify the peak update block (around line 65–67) to:

```python
    peak = float(r.get(Keys.PEAK_EQUITY) or config.INITIAL_CAPITAL)
    if new_equity > peak:
        r.set(Keys.PEAK_EQUITY, str(round(new_equity, 2)))
        r.set(Keys.PEAK_EQUITY_DATE, date.today().isoformat())
```

Add `from datetime import date` to imports at top of `executor.py` if not already present.

- [ ] **Step 4: Run tests to verify pass**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py::test_update_simulated_equity_sets_peak_date_on_new_high skills/executor/test_executor.py::test_update_simulated_equity_does_not_set_peak_date_when_not_new_high -v
```

Expected: PASS

- [ ] **Step 5: Run full executor test suite**

```bash
PYTHONPATH=scripts pytest skills/executor/test_executor.py -v
```

Expected: all green

- [ ] **Step 6: Commit**

```bash
git add skills/executor/executor.py skills/executor/test_executor.py
git commit -m "feat: set PEAK_EQUITY_DATE when new equity high in executor"
```

---

## Task 3: Set `PEAK_EQUITY_DATE` in supervisor when peak is updated

**Files:**
- Modify: `skills/supervisor/supervisor.py`
- Test: `skills/supervisor/test_supervisor.py`

Two sites in supervisor set `PEAK_EQUITY`:
- Line ~58 inside `run_circuit_breakers()` 
- Line ~418 inside `reset_daily_state()` (or similar)

- [ ] **Step 1: Write the failing tests**

In `skills/supervisor/test_supervisor.py`, add:

```python
def test_run_circuit_breakers_sets_peak_date_on_new_high():
    """run_circuit_breakers sets PEAK_EQUITY_DATE when equity exceeds stored peak."""
    from datetime import date
    r = MagicMock()
    r.get.side_effect = lambda key: {
        Keys.SIMULATED_EQUITY: "5500.0",
        Keys.PEAK_EQUITY: "5000.0",   # equity is higher → new peak
        Keys.DRAWDOWN: "0.0",
        Keys.SYSTEM_STATUS: "active",
        Keys.RISK_MULTIPLIER: "1.0",
        Keys.DAILY_PNL: "0.0",
    }.get(key)
    r.exists.return_value = True

    run_circuit_breakers(r)

    calls = {str(c) for c in r.set.call_args_list}
    assert any("peak_equity_date" in c and date.today().isoformat() in c for c in calls)
```

- [ ] **Step 2: Run to verify failure**

```bash
PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py::test_run_circuit_breakers_sets_peak_date_on_new_high -v
```

Expected: FAIL

- [ ] **Step 3: Add peak date set to both sites in supervisor.py**

At line ~58 (inside the `if equity > peak:` block in `run_circuit_breakers`):

```python
    if equity > peak:
        r.set(Keys.PEAK_EQUITY, str(round(equity, 2)))
        r.set(Keys.PEAK_EQUITY_DATE, date.today().isoformat())
```

At line ~418 (wherever `r.set(Keys.PEAK_EQUITY, ...)` appears in `reset_daily_state` or EOD reset):

```python
        r.set(Keys.PEAK_EQUITY, str(round(equity, 2)))
        r.set(Keys.PEAK_EQUITY_DATE, date.today().isoformat())
```

Add `from datetime import date` to imports at top of `supervisor.py` if not already present.

- [ ] **Step 4: Run tests to verify pass**

```bash
PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py::test_run_circuit_breakers_sets_peak_date_on_new_high -v
```

Expected: PASS

- [ ] **Step 5: Run full supervisor test suite**

```bash
PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py -v
```

Expected: all green

- [ ] **Step 6: Commit**

```bash
git add skills/supervisor/supervisor.py skills/supervisor/test_supervisor.py
git commit -m "feat: set PEAK_EQUITY_DATE in supervisor on new equity high"
```

---

## Task 4: Implement `get_drawdown_attribution(r, conn)` in config.py

**Files:**
- Modify: `scripts/config.py`
- Test: `scripts/test_config.py`

Returns a list of dicts sorted by `total_pnl` ascending (worst first):
`[{"symbol": "SPY", "realized_pnl": -42.10, "unrealized_pnl": 0.0, "total_pnl": -42.10}, ...]`
Only includes symbols with non-zero total contribution.

- [ ] **Step 1: Write the failing tests**

Add to `scripts/test_config.py`:

```python
def _make_cursor(rows):
    """Helper: mock psycopg2 cursor returning given rows from fetchall()."""
    cur = MagicMock()
    cur.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


def test_get_drawdown_attribution_realized_only():
    """Realized losses from DB, no open positions."""
    from config import get_drawdown_attribution
    r = MagicMock()
    r.get.side_effect = lambda key: {
        "trading:peak_equity_date": "2026-03-01",
        "trading:positions": "{}",
    }.get(key)

    conn, cur = _make_cursor([("SPY", -42.10), ("NVDA", -28.30)])

    result = get_drawdown_attribution(r, conn)

    assert len(result) == 2
    assert result[0]["symbol"] == "SPY"
    assert result[0]["realized_pnl"] == pytest.approx(-42.10)
    assert result[0]["unrealized_pnl"] == pytest.approx(0.0)
    assert result[0]["total_pnl"] == pytest.approx(-42.10)
    # sorted worst first
    assert result[0]["total_pnl"] < result[1]["total_pnl"]


def test_get_drawdown_attribution_unrealized_only():
    """No closed trades since peak, but open position is underwater."""
    from config import get_drawdown_attribution
    import json
    r = MagicMock()
    positions = {"NVDA": {"entry_price": 800.0, "quantity": 10, "unrealized_pnl_pct": -3.5}}
    r.get.side_effect = lambda key: {
        "trading:peak_equity_date": "2026-03-01",
        "trading:positions": json.dumps(positions),
    }.get(key)

    conn, cur = _make_cursor([])  # no realized losses

    result = get_drawdown_attribution(r, conn)

    assert len(result) == 1
    assert result[0]["symbol"] == "NVDA"
    assert result[0]["realized_pnl"] == pytest.approx(0.0)
    # unrealized = entry_price * quantity * unrealized_pnl_pct / 100
    # = 800 * 10 * (-3.5 / 100) = -280.0
    assert result[0]["unrealized_pnl"] == pytest.approx(-280.0)
    assert result[0]["total_pnl"] == pytest.approx(-280.0)


def test_get_drawdown_attribution_mixed():
    """Realized loss for SPY + unrealized loss for NVDA, both show up."""
    from config import get_drawdown_attribution
    import json
    r = MagicMock()
    positions = {"NVDA": {"entry_price": 800.0, "quantity": 5, "unrealized_pnl_pct": -2.0}}
    r.get.side_effect = lambda key: {
        "trading:peak_equity_date": "2026-03-01",
        "trading:positions": json.dumps(positions),
    }.get(key)

    conn, cur = _make_cursor([("SPY", -42.10)])

    result = get_drawdown_attribution(r, conn)

    symbols = [row["symbol"] for row in result]
    assert "SPY" in symbols
    assert "NVDA" in symbols
    spy = next(r for r in result if r["symbol"] == "SPY")
    assert spy["realized_pnl"] == pytest.approx(-42.10)
    nvda = next(r for r in result if r["symbol"] == "NVDA")
    assert nvda["unrealized_pnl"] == pytest.approx(-80.0)  # 800*5*(-2/100)


def test_get_drawdown_attribution_empty():
    """No losses at all returns empty list."""
    from config import get_drawdown_attribution
    r = MagicMock()
    r.get.side_effect = lambda key: {
        "trading:peak_equity_date": "2026-03-01",
        "trading:positions": "{}",
    }.get(key)

    conn, cur = _make_cursor([])

    result = get_drawdown_attribution(r, conn)
    assert result == []


def test_get_drawdown_attribution_db_failure_returns_unrealized_only():
    """DB failure degrades gracefully — returns unrealized-only, no exception."""
    from config import get_drawdown_attribution
    import json
    r = MagicMock()
    positions = {"SPY": {"entry_price": 500.0, "quantity": 2, "unrealized_pnl_pct": -1.0}}
    r.get.side_effect = lambda key: {
        "trading:peak_equity_date": "2026-03-01",
        "trading:positions": json.dumps(positions),
    }.get(key)

    conn = MagicMock()
    conn.cursor.side_effect = Exception("DB connection error")

    result = get_drawdown_attribution(r, conn)

    # Should return unrealized contribution without raising
    assert len(result) == 1
    assert result[0]["symbol"] == "SPY"
    assert result[0]["realized_pnl"] == pytest.approx(0.0)
    assert result[0]["unrealized_pnl"] == pytest.approx(-10.0)  # 500*2*(-1/100)


def test_get_drawdown_attribution_missing_peak_date_uses_fallback():
    """Missing PEAK_EQUITY_DATE key falls back to 30-day window (no crash)."""
    from config import get_drawdown_attribution
    r = MagicMock()
    r.get.side_effect = lambda key: {
        "trading:peak_equity_date": None,   # key missing
        "trading:positions": "{}",
    }.get(key)

    conn, cur = _make_cursor([("QQQ", -15.0)])

    result = get_drawdown_attribution(r, conn)

    # Should still query with a fallback date — cur.execute was called
    assert cur.execute.called
    assert len(result) == 1


def test_get_drawdown_attribution_skips_position_missing_fields():
    """Position entry missing required fields is silently skipped."""
    from config import get_drawdown_attribution
    import json
    r = MagicMock()
    positions = {
        "SPY": {"entry_price": 500.0, "quantity": 2, "unrealized_pnl_pct": -1.0},
        "BAD": {},  # missing all fields
    }
    r.get.side_effect = lambda key: {
        "trading:peak_equity_date": "2026-03-01",
        "trading:positions": json.dumps(positions),
    }.get(key)

    conn, cur = _make_cursor([])

    result = get_drawdown_attribution(r, conn)

    symbols = [row["symbol"] for row in result]
    assert "SPY" in symbols
    assert "BAD" not in symbols
```

- [ ] **Step 2: Run to verify failure**

```bash
PYTHONPATH=scripts pytest scripts/test_config.py::test_get_drawdown_attribution_realized_only -v
```

Expected: `ImportError` or `AttributeError` — function doesn't exist yet.

- [ ] **Step 3: Implement `get_drawdown_attribution` in config.py**

Add after `get_drawdown()` function:

```python
def get_drawdown_attribution(r: redis.Redis, conn) -> list:
    """
    Returns per-instrument drawdown contribution since peak date.
    List of dicts: {symbol, realized_pnl, unrealized_pnl, total_pnl}
    Sorted by total_pnl ascending (worst first). Only non-zero totals included.
    Degrades gracefully: DB failure → unrealized only.
    """
    peak_date_str = r.get(Keys.PEAK_EQUITY_DATE)
    if peak_date_str:
        peak_date = date.fromisoformat(peak_date_str)
    else:
        peak_date = date.today() - timedelta(days=30)

    # Realized: query trades closed since peak date
    realized: dict[str, float] = {}
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT symbol, SUM(realized_pnl) FROM trades "
            "WHERE side = 'sell' AND realized_pnl IS NOT NULL AND time >= %s "
            "GROUP BY symbol",
            (peak_date,),
        )
        for symbol, pnl in cur.fetchall():
            realized[symbol] = float(pnl or 0)
    except Exception:
        pass  # degrade to unrealized-only

    # Unrealized: open positions from Redis
    unrealized: dict[str, float] = {}
    try:
        positions = json.loads(r.get(Keys.POSITIONS) or "{}")
        for symbol, pos in positions.items():
            entry = float(pos["entry_price"])
            qty = float(pos["quantity"])
            pct = float(pos["unrealized_pnl_pct"])
            unrealized[symbol] = entry * qty * pct / 100
    except Exception:
        pass

    # Merge
    all_symbols = set(realized) | set(unrealized)
    rows = []
    for symbol in all_symbols:
        r_pnl = realized.get(symbol, 0.0)
        u_pnl = unrealized.get(symbol, 0.0)
        total = r_pnl + u_pnl
        if total != 0.0:
            rows.append({
                "symbol": symbol,
                "realized_pnl": r_pnl,
                "unrealized_pnl": u_pnl,
                "total_pnl": total,
            })

    rows.sort(key=lambda x: x["total_pnl"])
    return rows
```

- [ ] **Step 4: Run all attribution tests**

```bash
PYTHONPATH=scripts pytest scripts/test_config.py -k "attribution" -v
```

Expected: all 7 PASS

- [ ] **Step 5: Run full config test suite**

```bash
PYTHONPATH=scripts pytest scripts/test_config.py -v
```

Expected: all green

- [ ] **Step 6: Commit**

```bash
git add scripts/config.py scripts/test_config.py
git commit -m "feat: get_drawdown_attribution — realized + unrealized since peak"
```

---

## Task 5: Update `drawdown_alert()` to accept and format attribution

**Files:**
- Modify: `scripts/notify.py`
- Test: `scripts/test_notify.py`

Current signature: `drawdown_alert(drawdown_pct: float, action: str)`
New signature: `drawdown_alert(drawdown_pct: float, action: str, attribution: list | None = None)`

- [ ] **Step 1: Write the failing tests**

Add to `scripts/test_notify.py`:

```python
def test_drawdown_alert_without_attribution(mock_notify):
    """drawdown_alert with no attribution sends message unchanged."""
    notify.drawdown_alert(12.5, "50% position size. Only Tier 1 active.")
    mock_notify.assert_called_once()
    msg = mock_notify.call_args[0][0]
    assert "DRAWDOWN ALERT: 12.5%" in msg
    assert "50% position size" in msg
    # no attribution table
    assert "realized" not in msg.lower()
    assert "unrealized" not in msg.lower()


def test_drawdown_alert_with_attribution(mock_notify):
    """drawdown_alert with attribution appends per-symbol breakdown."""
    attribution = [
        {"symbol": "SPY",  "realized_pnl": -42.10, "unrealized_pnl":  0.00, "total_pnl": -42.10},
        {"symbol": "NVDA", "realized_pnl":   0.00, "unrealized_pnl": -28.30, "total_pnl": -28.30},
    ]
    notify.drawdown_alert(12.5, "50% position size.", attribution=attribution)
    mock_notify.assert_called_once()
    msg = mock_notify.call_args[0][0]
    assert "SPY" in msg
    assert "NVDA" in msg
    assert "-42.10" in msg
    assert "-28.30" in msg


def test_drawdown_alert_with_empty_attribution_omits_table(mock_notify):
    """Empty attribution list omits the breakdown section."""
    notify.drawdown_alert(5.0, "Caution.", attribution=[])
    mock_notify.assert_called_once()
    msg = mock_notify.call_args[0][0]
    assert "DRAWDOWN ALERT" in msg
    # no attribution section
    assert "realized" not in msg.lower()
```

Note: `mock_notify` is a pytest fixture that patches `notify.notify`. Check existing test_notify.py for how it's already defined (look for `@pytest.fixture` or `@patch`). If not defined, add:

```python
@pytest.fixture
def mock_notify():
    with patch.object(notify, "notify") as m:
        yield m
```

- [ ] **Step 2: Run to verify failure**

```bash
PYTHONPATH=scripts pytest scripts/test_notify.py::test_drawdown_alert_without_attribution -v
```

Expected: FAIL — `drawdown_alert` doesn't accept `attribution` kwarg yet.

- [ ] **Step 3: Update `drawdown_alert()` in notify.py**

Replace the existing function body:

```python
def drawdown_alert(drawdown_pct: float, action: str, attribution: list | None = None):
    """Alert when drawdown thresholds are breached."""
    msg = (
        f"⚠️ <b>DRAWDOWN ALERT: {drawdown_pct:.1f}%</b>\n"
        f"\n"
        f"Action taken: {action}\n"
    )

    if attribution:
        msg += "\n<b>Attribution since peak:</b>\n"
        for row in attribution:
            sym = row["symbol"]
            total = row["total_pnl"]
            realized = row["realized_pnl"]
            unrealized = row["unrealized_pnl"]
            if unrealized != 0.0 and realized != 0.0:
                detail = f"${realized:+.2f} realized, ${unrealized:+.2f} unrealized"
            elif unrealized != 0.0:
                detail = f"${unrealized:+.2f} unrealized"
            else:
                detail = f"${realized:+.2f} realized"
            msg += f"  {sym}: <b>${total:+.2f}</b> ({detail})\n"

    msg += f"\n<i>{_now_et().strftime('%Y-%m-%d %H:%M:%S ET')}</i>"
    notify(msg, silent=False)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
PYTHONPATH=scripts pytest scripts/test_notify.py -k "drawdown_alert" -v
```

Expected: all 3 PASS

- [ ] **Step 5: Run full notify test suite**

```bash
PYTHONPATH=scripts pytest scripts/test_notify.py -v
```

Expected: all green

- [ ] **Step 6: Commit**

```bash
git add scripts/notify.py scripts/test_notify.py
git commit -m "feat: drawdown_alert accepts optional attribution breakdown"
```

---

## Task 6: Wire attribution into `run_circuit_breakers()` in supervisor

**Files:**
- Modify: `skills/supervisor/supervisor.py`
- Test: `skills/supervisor/test_supervisor.py`

`run_circuit_breakers(r)` currently calls `drawdown_alert(dd, "...")` in three threshold branches. Update each to also pass `attribution`.

Pattern: open a DB connection, call `get_drawdown_attribution(r, conn)`, pass to alert. DB call is wrapped in try/except so a DB failure doesn't suppress the alert.

- [ ] **Step 1: Write the failing test**

Add to `skills/supervisor/test_supervisor.py`:

```python
def test_run_circuit_breakers_passes_attribution_to_alert():
    """When drawdown crosses HALT threshold, drawdown_alert is called with attribution."""
    r = MagicMock()
    r.get.side_effect = lambda key: {
        Keys.SIMULATED_EQUITY: "4000.0",
        Keys.PEAK_EQUITY: "5000.0",   # 20% drawdown → HALT
        Keys.DRAWDOWN: "20.0",
        Keys.SYSTEM_STATUS: "critical",
        Keys.RISK_MULTIPLIER: "0.5",
        Keys.DAILY_PNL: "0.0",
    }.get(key)
    r.exists.return_value = True

    attribution_rows = [{"symbol": "SPY", "realized_pnl": -200.0, "unrealized_pnl": 0.0, "total_pnl": -200.0}]

    with patch("supervisor.get_drawdown_attribution", return_value=attribution_rows) as mock_attr, \
         patch("supervisor.get_db") as mock_db, \
         patch("supervisor.drawdown_alert") as mock_alert:
        run_circuit_breakers(r)

    mock_alert.assert_called_once()
    call_kwargs = mock_alert.call_args
    assert call_kwargs.kwargs.get("attribution") == attribution_rows or \
           (len(call_kwargs.args) >= 3 and call_kwargs.args[2] == attribution_rows)


def test_run_circuit_breakers_alert_fires_even_if_db_fails():
    """If DB connection fails, drawdown_alert still fires (without attribution)."""
    r = MagicMock()
    r.get.side_effect = lambda key: {
        Keys.SIMULATED_EQUITY: "4000.0",
        Keys.PEAK_EQUITY: "5000.0",
        Keys.DRAWDOWN: "20.0",
        Keys.SYSTEM_STATUS: "critical",
        Keys.RISK_MULTIPLIER: "0.5",
        Keys.DAILY_PNL: "0.0",
    }.get(key)
    r.exists.return_value = True

    with patch("supervisor.get_db", side_effect=Exception("DB down")), \
         patch("supervisor.drawdown_alert") as mock_alert:
        run_circuit_breakers(r)  # must not raise

    mock_alert.assert_called_once()
```

- [ ] **Step 2: Run to verify failure**

```bash
PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py::test_run_circuit_breakers_passes_attribution_to_alert -v
```

Expected: FAIL — `drawdown_alert` called without `attribution` kwarg

- [ ] **Step 3: Update `run_circuit_breakers()` in supervisor.py**

Add a helper at the top of the function (before the threshold checks) and update each `drawdown_alert` call:

```python
def run_circuit_breakers(r):
    """Check all circuit breakers. Returns True if system should continue."""
    equity = get_simulated_equity(r)
    peak = float(r.get(Keys.PEAK_EQUITY) or config.INITIAL_CAPITAL)
    dd = get_drawdown(r)

    # Compute attribution for alert enrichment (best-effort)
    attribution = []
    try:
        conn = get_db()
        attribution = get_drawdown_attribution(r, conn)
        conn.close()
    except Exception:
        pass  # DB unavailable — alert fires without attribution

    prev_status = r.get(Keys.SYSTEM_STATUS) or "active"

    if dd >= config.DRAWDOWN_HALT:
        r.set(Keys.SYSTEM_STATUS, "halted")
        r.set(Keys.RISK_MULTIPLIER, "0.0")
        disable_tiers(r, [2, 3])
        if prev_status != "halted":
            drawdown_alert(dd, "25% position size. Only Tier 1 active. BTC disabled.", attribution=attribution)

    elif dd >= config.DRAWDOWN_CRITICAL:
        ...
        if prev_status not in ("defensive", "critical"):
            drawdown_alert(dd, "50% position size. Only Tier 1 active.", attribution=attribution)

    elif dd >= config.DRAWDOWN_DEFENSIVE:
        ...
        if prev_status not in ("caution", "defensive", "critical"):
            drawdown_alert(dd, "Caution: Tier 3 at reduced size.", attribution=attribution)
```

Important: read the existing function carefully before editing to preserve all the existing logic. Only add the attribution block at the top and add `attribution=attribution` to each `drawdown_alert(...)` call.

Also add to imports: `from config import get_drawdown_attribution` (or verify `config` is already imported as a module — if so use `config.get_drawdown_attribution`).

- [ ] **Step 4: Run tests to verify pass**

```bash
PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py::test_run_circuit_breakers_passes_attribution_to_alert skills/supervisor/test_supervisor.py::test_run_circuit_breakers_alert_fires_even_if_db_fails -v
```

Expected: PASS

- [ ] **Step 5: Run full supervisor test suite**

```bash
PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py -v
```

Expected: all green

- [ ] **Step 6: Commit**

```bash
git add skills/supervisor/supervisor.py skills/supervisor/test_supervisor.py
git commit -m "feat: wire drawdown attribution into circuit breaker alerts"
```

---

## Task 7: `Queries.drawdown_attribution/2` in dashboard

**Files:**
- Modify: `dashboard/lib/dashboard/queries.ex`
- Test: `dashboard/test/dashboard/queries_test.exs`

Signature: `drawdown_attribution(positions, peak_date \\ nil)` where:
- `positions` — map like `%{"SPY" => %{"entry_price" => 480.0, "quantity" => 10, "unrealized_pnl_pct" => -1.5}}`
- `peak_date` — `Date.t()` or `nil` (falls back to 30 days ago)

Returns list of maps sorted by `total_pnl` ascending (worst first):
`[%{symbol: "SPY", realized_pnl: -42.10, unrealized_pnl: 0.0, total_pnl: -42.10}]`

- [ ] **Step 1: Write the failing tests**

Add to `dashboard/test/dashboard/queries_test.exs`:

```elixir
describe "drawdown_attribution/2" do
  test "returns empty list when no trades and no positions" do
    result = Queries.drawdown_attribution(%{})
    assert result == []
  end

  test "returns empty list when no losing trades and no underwater positions" do
    # Insert a winning trade
    Dashboard.Repo.insert!(%Dashboard.Schemas.Trade{
      time: DateTime.utc_now(),
      symbol: "SPY",
      side: "sell",
      quantity: Decimal.new("10"),
      price: Decimal.new("500"),
      total_value: Decimal.new("5000"),
      realized_pnl: Decimal.new("50.00"),
      strategy: "rsi2",
      asset_class: "equity"
    })

    result = Queries.drawdown_attribution(%{})
    assert result == []
  end

  test "returns realized loss for closed trade" do
    cutoff = Date.add(Date.utc_today(), -5)
    Dashboard.Repo.insert!(%Dashboard.Schemas.Trade{
      time: DateTime.utc_now(),
      symbol: "NVDA",
      side: "sell",
      quantity: Decimal.new("5"),
      price: Decimal.new("770"),
      total_value: Decimal.new("3850"),
      realized_pnl: Decimal.new("-42.10"),
      strategy: "rsi2",
      asset_class: "equity"
    })

    result = Queries.drawdown_attribution(%{}, cutoff)
    assert length(result) == 1
    row = hd(result)
    assert row.symbol == "NVDA"
    assert Decimal.compare(row.realized_pnl, Decimal.new("-42.10")) == :eq
    assert Decimal.compare(row.unrealized_pnl, Decimal.new("0")) == :eq
  end

  test "returns unrealized loss from open position" do
    positions = %{
      "SPY" => %{"entry_price" => 500.0, "quantity" => 10, "unrealized_pnl_pct" => -2.0}
    }
    result = Queries.drawdown_attribution(positions, Date.add(Date.utc_today(), -1))
    assert length(result) == 1
    row = hd(result)
    assert row.symbol == "SPY"
    # unrealized = 500 * 10 * (-2.0 / 100) = -100.0
    assert Decimal.compare(row.unrealized_pnl, Decimal.new("-100.0")) == :eq
    assert Decimal.compare(row.realized_pnl, Decimal.new("0")) == :eq
  end

  test "merges realized and unrealized for same symbol" do
    cutoff = Date.add(Date.utc_today(), -5)
    Dashboard.Repo.insert!(%Dashboard.Schemas.Trade{
      time: DateTime.utc_now(),
      symbol: "QQQ",
      side: "sell",
      quantity: Decimal.new("3"),
      price: Decimal.new("430"),
      total_value: Decimal.new("1290"),
      realized_pnl: Decimal.new("-20.00"),
      strategy: "rsi2",
      asset_class: "equity"
    })

    positions = %{
      "QQQ" => %{"entry_price" => 440.0, "quantity" => 2, "unrealized_pnl_pct" => -1.0}
    }
    result = Queries.drawdown_attribution(positions, cutoff)
    row = Enum.find(result, & &1.symbol == "QQQ")
    assert row != nil
    # unrealized = 440 * 2 * (-1/100) = -8.8; realized = -20; total = -28.8
    total = Decimal.add(row.realized_pnl, row.unrealized_pnl)
    assert Decimal.compare(total, row.total_pnl) == :eq
  end

  test "sorts results worst-first by total_pnl" do
    cutoff = Date.add(Date.utc_today(), -5)
    for {sym, pnl} <- [{"SPY", "-50"}, {"NVDA", "-20"}, {"QQQ", "-80"}] do
      Dashboard.Repo.insert!(%Dashboard.Schemas.Trade{
        time: DateTime.utc_now(),
        symbol: sym,
        side: "sell",
        quantity: Decimal.new("1"),
        price: Decimal.new("100"),
        total_value: Decimal.new("100"),
        realized_pnl: Decimal.new(pnl),
        strategy: "rsi2",
        asset_class: "equity"
      })
    end

    result = Queries.drawdown_attribution(%{}, cutoff)
    totals = Enum.map(result, & Decimal.to_float(&1.total_pnl))
    assert totals == Enum.sort(totals)  # ascending
  end

  test "nil peak_date falls back to 30-day window" do
    # Just verify it doesn't crash with nil
    result = Queries.drawdown_attribution(%{}, nil)
    assert is_list(result)
  end
end
```

- [ ] **Step 2: Run to verify failure**

```bash
cd dashboard && mix test test/dashboard/queries_test.exs --only "drawdown_attribution" 2>&1 | tail -20
```

Expected: compile error — `Queries.drawdown_attribution/2` undefined.

- [ ] **Step 3: Implement `drawdown_attribution/2` in queries.ex**

Add after `total_realized_pnl/0`:

```elixir
@doc "Per-instrument drawdown attribution since peak_date. Merges realized (DB) + unrealized (Redis positions)."
def drawdown_attribution(positions, peak_date \\ nil) do
  cutoff =
    case peak_date do
      nil -> Date.add(Date.utc_today(), -30)
      d -> d
    end

  cutoff_dt = DateTime.new!(cutoff, ~T[00:00:00], "Etc/UTC")

  realized =
    try do
      from(t in Trade,
        where:
          t.side == "sell" and
            not is_nil(t.realized_pnl) and
            t.time >= ^cutoff_dt,
        group_by: t.symbol,
        select: {t.symbol, sum(t.realized_pnl)}
      )
      |> Repo.all()
      |> Map.new()
    rescue
      _ -> %{}
    end

  # Compute unrealized from positions map
  unrealized =
    positions
    |> Enum.reduce(%{}, fn {symbol, pos}, acc ->
      try do
        entry = pos["entry_price"] || 0.0
        qty = pos["quantity"] || 0
        pct = pos["unrealized_pnl_pct"] || 0.0
        u_pnl = Decimal.from_float(entry * qty * pct / 100)
        Map.put(acc, symbol, u_pnl)
      rescue
        _ -> acc
      end
    end)

  all_symbols = MapSet.union(MapSet.new(Map.keys(realized)), MapSet.new(Map.keys(unrealized)))

  all_symbols
  |> Enum.map(fn symbol ->
    r_pnl = Map.get(realized, symbol, Decimal.new("0"))
    u_pnl = Map.get(unrealized, symbol, Decimal.new("0"))
    total = Decimal.add(r_pnl, u_pnl)
    %{symbol: symbol, realized_pnl: r_pnl, unrealized_pnl: u_pnl, total_pnl: total}
  end)
  |> Enum.reject(fn row -> Decimal.compare(row.total_pnl, Decimal.new("0")) == :eq end)
  |> Enum.sort_by(fn row -> Decimal.to_float(row.total_pnl) end, :asc)
end
```

- [ ] **Step 4: Run attribution tests**

```bash
cd dashboard && mix test test/dashboard/queries_test.exs 2>&1 | tail -30
```

Expected: all green

- [ ] **Step 5: Run full dashboard test suite**

```bash
cd dashboard && mix test 2>&1 | tail -20
```

Expected: all green

- [ ] **Step 6: Commit**

```bash
git add dashboard/lib/dashboard/queries.ex dashboard/test/dashboard/queries_test.exs
git commit -m "feat: Queries.drawdown_attribution/2 for dashboard"
```

---

## Task 8: Wire attribution into dashboard LiveView

**Files:**
- Modify: `dashboard/lib/dashboard/redis_poller.ex`
- Modify: `dashboard/lib/dashboard_web/live/dashboard_live.ex`
- Modify: `dashboard/lib/dashboard_web/live/dashboard_live.html.heex`
- Test: `dashboard/test/dashboard/redis_poller_test.exs`
- Test: `dashboard/test/dashboard_web/live/dashboard_live_test.exs`

- [ ] **Step 1: Write failing tests**

In `dashboard/test/dashboard/redis_poller_test.exs`, add:

```elixir
test "polls trading:peak_equity_date key" do
  assert "trading:peak_equity_date" in Dashboard.RedisPoller.redis_keys()
end
```

Note: if `redis_keys/0` is not a public function, add `def redis_keys, do: @redis_keys` to `redis_poller.ex` for testability.

In `dashboard/test/dashboard_web/live/dashboard_live_test.exs`, add to the `:state_update` describe block:

```elixir
test "assigns drawdown_attribution from state update", %{conn: conn} do
  {:ok, view, _} = live(conn, "/")

  state = %{
    "trading:simulated_equity" => 4800.0,
    "trading:peak_equity" => 5000.0,
    "trading:drawdown" => 4.0,
    "trading:peak_equity_date" => "2026-03-01",
    "trading:system_status" => "active",
    "trading:daily_pnl" => -50.0,
    "trading:pdt:count" => 0,
    "trading:risk_multiplier" => 1.0,
    "trading:regime" => nil,
    "trading:positions" => %{},
    "trading:watchlist" => [],
    "trading:universe" => nil,
    "trading:heartbeat:screener" => nil,
    "trading:heartbeat:watcher" => nil,
    "trading:heartbeat:portfolio_manager" => nil,
    "trading:heartbeat:executor" => nil,
    "trading:heartbeat:supervisor" => nil,
    "trading:cooldowns" => []
  }

  send(view.pid, {:state_update, state})
  Process.sleep(50)

  assigns = :sys.get_state(view.pid).socket.assigns
  assert is_list(assigns.drawdown_attribution)
end
```

- [ ] **Step 2: Run to verify failure**

```bash
cd dashboard && mix test test/dashboard/redis_poller_test.exs test/dashboard_web/live/dashboard_live_test.exs 2>&1 | tail -20
```

Expected: failures on the new tests.

- [ ] **Step 3: Add key to redis_poller.ex**

In the `@redis_keys` list, add `"trading:peak_equity_date"` after `"trading:peak_equity"`:

```elixir
  @redis_keys [
    "trading:simulated_equity",
    "trading:peak_equity",
    "trading:peak_equity_date",   # ← add this line
    "trading:daily_pnl",
    ...
```

If needed for testing, add public accessor:

```elixir
def redis_keys, do: @redis_keys
```

- [ ] **Step 4: Update `dashboard_live.ex`**

In `mount/3` initial assigns, add:

```elixir
      |> assign(:drawdown_attribution, [])
      |> assign(:peak_equity_date, nil)
```

In `handle_info({:state_update, state}, socket)`, add to the assign chain:

```elixir
      |> assign(:peak_equity_date, state["trading:peak_equity_date"])
```

Then after the assign chain, compute attribution:

```elixir
    positions = state["trading:positions"] || %{}
    peak_date =
      case state["trading:peak_equity_date"] do
        nil -> nil
        s ->
          case Date.from_iso8601(s) do
            {:ok, d} -> d
            _ -> nil
          end
      end

    socket = assign(socket, :drawdown_attribution, Queries.drawdown_attribution(positions, peak_date))

    {:noreply, socket}
```

Make sure `alias Dashboard.Queries` is at the top of `dashboard_live.ex` (check existing aliases).

- [ ] **Step 5: Run tests to verify pass**

```bash
cd dashboard && mix test test/dashboard/redis_poller_test.exs test/dashboard_web/live/dashboard_live_test.exs 2>&1 | tail -20
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add dashboard/lib/dashboard/redis_poller.ex dashboard/lib/dashboard_web/live/dashboard_live.ex dashboard/test/dashboard/redis_poller_test.exs dashboard/test/dashboard_web/live/dashboard_live_test.exs
git commit -m "feat: wire drawdown_attribution assign into dashboard LiveView"
```

---

## Task 9: Render attribution table in dashboard template

**Files:**
- Modify: `dashboard/lib/dashboard_web/live/dashboard_live.html.heex`
- Test: `dashboard/test/dashboard_web/live/dashboard_live_test.exs`

- [ ] **Step 1: Write the failing render test**

Add to `dashboard/test/dashboard_web/live/dashboard_live_test.exs`:

```elixir
test "renders attribution table when drawdown_attribution is non-empty", %{conn: conn} do
  {:ok, view, _} = live(conn, "/")

  state = %{
    "trading:simulated_equity" => 4800.0,
    "trading:peak_equity" => 5000.0,
    "trading:drawdown" => 4.0,
    "trading:peak_equity_date" => Date.to_iso8601(Date.add(Date.utc_today(), -5)),
    "trading:system_status" => "active",
    "trading:daily_pnl" => -50.0,
    "trading:pdt:count" => 0,
    "trading:risk_multiplier" => 1.0,
    "trading:regime" => nil,
    "trading:positions" => %{
      "SPY" => %{"entry_price" => 500.0, "quantity" => 10, "unrealized_pnl_pct" => -2.0}
    },
    "trading:watchlist" => [],
    "trading:universe" => nil,
    "trading:heartbeat:screener" => nil,
    "trading:heartbeat:watcher" => nil,
    "trading:heartbeat:portfolio_manager" => nil,
    "trading:heartbeat:executor" => nil,
    "trading:heartbeat:supervisor" => nil,
    "trading:cooldowns" => []
  }

  send(view.pid, {:state_update, state})
  Process.sleep(50)

  html = render(view)
  assert html =~ "Attribution"
  assert html =~ "SPY"
end

test "hides attribution section when drawdown_attribution is empty", %{conn: conn} do
  {:ok, view, _} = live(conn, "/")
  # default state has no positions and no trades → attribution empty
  html = render(view)
  refute html =~ "Attribution"
end
```

- [ ] **Step 2: Run to verify failure**

```bash
cd dashboard && mix test test/dashboard_web/live/dashboard_live_test.exs -k "attribution" 2>&1 | tail -20
```

Expected: FAIL — "Attribution" not found in HTML.

- [ ] **Step 3: Add attribution section to template**

In `dashboard_live.html.heex`, find the drawdown card (around the `{format_pct(@drawdown)}` line). After the closing `</div>` of the drawdown card, add a new section:

```heex
<%= if @drawdown_attribution != [] do %>
  <div class="bg-gray-800 rounded-lg border border-gray-700 p-4 mt-4">
    <div class="text-xs text-gray-500 uppercase tracking-wider mb-2">Attribution since peak</div>
    <table class="w-full text-sm">
      <thead>
        <tr class="text-xs text-gray-500">
          <th class="text-left py-0.5">Symbol</th>
          <th class="text-right py-0.5">Realized</th>
          <th class="text-right py-0.5">Unrealized</th>
          <th class="text-right py-0.5">Total</th>
        </tr>
      </thead>
      <tbody>
        <%= for row <- @drawdown_attribution do %>
          <tr class="border-t border-gray-700">
            <td class="py-0.5 font-mono text-gray-200">{row.symbol}</td>
            <td class={["text-right py-0.5 font-mono", if(Decimal.compare(row.realized_pnl, Decimal.new("0")) == :lt, do: "text-red-400", else: "text-gray-500")]}>
              <%= if Decimal.compare(row.realized_pnl, Decimal.new("0")) != :eq do %>
                {format_equity(Decimal.to_float(row.realized_pnl))}
              <% else %>
                —
              <% end %>
            </td>
            <td class={["text-right py-0.5 font-mono", if(Decimal.compare(row.unrealized_pnl, Decimal.new("0")) == :lt, do: "text-red-400", else: "text-gray-500")]}>
              <%= if Decimal.compare(row.unrealized_pnl, Decimal.new("0")) != :eq do %>
                {format_equity(Decimal.to_float(row.unrealized_pnl))}
              <% else %>
                —
              <% end %>
            </td>
            <td class={["text-right py-0.5 font-mono font-bold", if(Decimal.compare(row.total_pnl, Decimal.new("0")) == :lt, do: "text-red-400", else: "text-green-400")]}>
              {format_equity(Decimal.to_float(row.total_pnl))}
            </td>
          </tr>
        <% end %>
      </tbody>
    </table>
  </div>
<% end %>
```

- [ ] **Step 4: Run template tests**

```bash
cd dashboard && mix test test/dashboard_web/live/dashboard_live_test.exs 2>&1 | tail -20
```

Expected: all green

- [ ] **Step 5: Run full dashboard test suite**

```bash
cd dashboard && mix test 2>&1 | tail -20
```

Expected: all green

- [ ] **Step 6: Commit**

```bash
git add dashboard/lib/dashboard_web/live/dashboard_live.html.heex dashboard/test/dashboard_web/live/dashboard_live_test.exs
git commit -m "feat: render drawdown attribution table on dashboard"
```

---

## Task 10: Full test suite + coverage check

- [ ] **Step 1: Run all Python tests**

```bash
PYTHONPATH=scripts pytest scripts/ skills/ -v 2>&1 | tail -30
```

Expected: all green, 100% coverage

- [ ] **Step 2: Run Python coverage**

```bash
PYTHONPATH=scripts pytest scripts/ skills/ --cov=scripts --cov=skills --cov-report=term-missing 2>&1 | tail -30
```

Expected: 100% — no uncovered lines

- [ ] **Step 3: Run all Elixir tests**

```bash
cd dashboard && mix test 2>&1 | tail -20
```

Expected: all green

- [ ] **Step 4: Run Elixir coverage**

```bash
cd dashboard && mix test --cover 2>&1 | tail -20
```

Expected: 100%

---

## Task 11: Version bump + changelog + wishlist

**Files:**
- Modify: `VERSION`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/FEATURE_WISHLIST.md`
- Modify: `.remember/remember.md`

- [ ] **Step 1: Bump version**

Change `VERSION` to `0.16.0`.

- [ ] **Step 2: Update CHANGELOG.md**

Add at top (after the header):

```markdown
## [0.16.0] — 2026-04-11

### Added
- Drawdown attribution: per-instrument breakdown (realized + unrealized since peak) in Telegram circuit breaker alerts and dashboard main page
- `trading:peak_equity_date` Redis key: tracks when the current peak was set, used to bound attribution query
- `get_drawdown_attribution(r, conn)` in `scripts/config.py`: returns sorted list of per-symbol P&L contributions
- `Queries.drawdown_attribution/2` in dashboard: merges TimescaleDB realized losses with Redis unrealized positions
- Attribution table in dashboard LiveView below drawdown badge (hidden when no losses)
```

- [ ] **Step 3: Update FEATURE_WISHLIST.md**

Mark item as done in the Next Priority Wave section:

```markdown
10. ~~Drawdown attribution~~ ✅ Done (PR #87): per-instrument breakdown in CB alerts + dashboard.
```

Update the `*Last updated*` line at the bottom:

```markdown
*Last updated: 2026-04-11. Drawdown attribution done (PR #87). Next: TBD.*
```

- [ ] **Step 4: Update `.remember/remember.md`**

```markdown
## State
feat/drawdown-attribution branch — all tasks done. Bump to v0.16.0. Ready for cpr.

## Next
1. cpr → merge PR, tag v0.16.0
2. Next wishlist: TBD
```

- [ ] **Step 5: Commit**

```bash
git add VERSION docs/CHANGELOG.md docs/FEATURE_WISHLIST.md .remember/remember.md
git commit -m "chore: bump to v0.16.0, update changelog and wishlist for drawdown attribution"
```
