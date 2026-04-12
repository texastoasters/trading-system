# Safety & Correctness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three safety/correctness gaps: scheduled reconcile via supervisor cron, 90-day cap on drawdown attribution lookback, and trailing stop indicator on dashboard position cards.

**Architecture:** One PR across two codebases. Python changes: `supervisor.py` (new `run_reconcile` fn + `--reconcile` flag) and `config.py` (lookback cap constant + clamp). Elixir changes: `queries.ex` (lookback clamp) and `dashboard_live.html.heex` (trailing row). All changes are small, isolated, and independently testable.

**Tech Stack:** Python 3 (supervisor, config), Elixir/Phoenix LiveView (dashboard), pytest, ExUnit.

---

## File Map

| File | Change |
|------|--------|
| `skills/supervisor/supervisor.py` | Add `run_reconcile(r)`, `--reconcile` argparse flag |
| `skills/supervisor/test_supervisor.py` | Add `TestRunReconcile` class (3 tests) |
| `scripts/config.py` | Add `ATTRIBUTION_MAX_LOOKBACK_DAYS = 90`, clamp in `get_drawdown_attribution` |
| `scripts/test_config.py` | Add 1 test to `TestGetDrawdownAttribution` |
| `dashboard/lib/dashboard/queries.ex` | Clamp cutoff in `drawdown_attribution/2` |
| `dashboard/test/dashboard/queries_test.exs` | Add 1 test |
| `dashboard/lib/dashboard_web/live/dashboard_live.html.heex` | Add trailing row to position card |
| `dashboard/test/dashboard_web/live/dashboard_live_test.exs` | Add 2 tests |
| `VERSION` | Bump to 0.17.0 |
| `docs/CHANGELOG.md` | Add v0.17.0 entry |
| `docs/FEATURE_WISHLIST.md` | Mark 3 items done |
| `.remember/remember.md` | Update |

---

## Context You Need

- `supervisor.py` lives at `skills/supervisor/supervisor.py`. It uses `subprocess.run` elsewhere (see `attempt_service_restart`). New `run_reconcile` follows that same pattern.
- `reconcile.py` lives at `scripts/reconcile.py`. When called as a subprocess from repo root with `PYTHONPATH=scripts`, it runs fine with `["python3", "scripts/reconcile.py", "--fix"]`.
- Cron: supervisor is invoked by cron with per-job flags: `--briefing` (9:20 AM), `--weekly` (4:35 PM Fri), etc. Add `--reconcile` to the same pattern. Cron entry: `15 9 * * 1-5 cd ~/trading-system && PYTHONPATH=scripts python3 skills/supervisor/supervisor.py --reconcile >> ~/trading-system/logs/reconcile.log 2>&1`
- `get_drawdown_attribution` in `scripts/config.py` line ~314 resolves `peak_date` from Redis then queries the DB. The clamp goes immediately after `peak_date` is resolved.
- `drawdown_attribution/2` in `dashboard/lib/dashboard/queries.ex` line 168. The clamp replaces the single `cutoff =` line with a two-line pattern.
- Position card template: `dashboard/lib/dashboard_web/live/dashboard_live.html.heex`. The "Stop:" row is at line ~177. Insert the trailing row directly after it.
- `pos["trailing"]` is a boolean in the Redis position map. `pos["trail_percent"]` is a float (e.g., `2.0`).
- Test patterns: see `TestAttemptServiceRestart` in `test_supervisor.py` for subprocess mock pattern. See `TestGetDrawdownAttribution` in `test_config.py` for DB mock pattern. See `describe "drawdown_attribution/2"` in `queries_test.exs` for Elixir pattern.

---

### Task 1: `run_reconcile` in supervisor.py

**Files:**
- Modify: `skills/supervisor/supervisor.py` (add function + argparse flag)
- Modify: `skills/supervisor/test_supervisor.py` (add test class)

- [ ] **Step 1: Write 3 failing tests**

Add this class at the end of `skills/supervisor/test_supervisor.py` (before the last line):

```python
# ── run_reconcile ─────────────────────────────────────────────

class TestRunReconcile:
    def test_runs_reconcile_fix_as_subprocess(self):
        """Success path: subprocess called with correct args, no alert."""
        r = MagicMock()
        result = MagicMock(returncode=0)
        with patch("supervisor.subprocess.run", return_value=result) as mock_run, \
             patch("supervisor.critical_alert") as mock_alert:
            from supervisor import run_reconcile
            run_reconcile(r)
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args == ["python3", "scripts/reconcile.py", "--fix"]
        mock_alert.assert_not_called()

    def test_fires_critical_alert_on_nonzero_exit(self):
        """Non-zero exit code → critical_alert with 'reconcile' in message."""
        r = MagicMock()
        result = MagicMock(returncode=1, stderr=b"connection refused")
        with patch("supervisor.subprocess.run", return_value=result), \
             patch("supervisor.critical_alert") as mock_alert:
            from supervisor import run_reconcile
            run_reconcile(r)
        mock_alert.assert_called_once()
        assert "reconcile" in mock_alert.call_args[0][0].lower()

    def test_fires_critical_alert_on_exception(self):
        """Subprocess exception (e.g. timeout) → critical_alert, no raise."""
        r = MagicMock()
        with patch("supervisor.subprocess.run", side_effect=Exception("timed out")), \
             patch("supervisor.critical_alert") as mock_alert:
            from supervisor import run_reconcile
            run_reconcile(r)  # must not raise
        mock_alert.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /path/to/trading-system && PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py::TestRunReconcile -v
```

Expected: 3 failures — `ImportError: cannot import name 'run_reconcile'`

- [ ] **Step 3: Implement `run_reconcile` in supervisor.py**

Add this function after `run_morning_briefing` (around line 770, before the `# ── Main` block):

```python
def run_reconcile(r):
    """Run reconcile.py --fix as a subprocess. Called at 9:15 AM ET via cron."""
    print("[Supervisor] Running scheduled reconcile...")
    try:
        result = subprocess.run(
            ["python3", "scripts/reconcile.py", "--fix"],
            capture_output=True,
            timeout=60,
            env={**os.environ, "PYTHONPATH": "scripts"},
        )
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            critical_alert(
                f"Scheduled reconcile failed (exit {result.returncode})\n{stderr[:500]}"
            )
    except Exception as exc:
        critical_alert(f"Scheduled reconcile error: {exc}")
```

Ensure `import os` is at the top of supervisor.py (check — it likely is already).

- [ ] **Step 4: Add `--reconcile` flag to argparse in `main()`**

In the `main()` function argparse block (around line 782), add:

```python
parser.add_argument("--reconcile", action="store_true", help="Run reconcile --fix (9:15 AM ET)")
```

In the `if/elif` chain (after `args.briefing` block, before `args.health`), add:

```python
elif args.reconcile:
    run_reconcile(r)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py::TestRunReconcile -v
```

Expected: 3 passing.

- [ ] **Step 6: Run full supervisor test suite**

```bash
PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py -v
```

Expected: all passing, no regressions.

- [ ] **Step 7: Commit**

```bash
git add skills/supervisor/supervisor.py skills/supervisor/test_supervisor.py
git commit -m "feat: scheduled reconcile via supervisor --reconcile flag"
```

---

### Task 2: Attribution lookback cap in config.py

**Files:**
- Modify: `scripts/config.py` (add constant + clamp)
- Modify: `scripts/test_config.py` (add 1 test)

- [ ] **Step 1: Write the failing test**

In `scripts/test_config.py`, inside `class TestGetDrawdownAttribution`, add after the last test:

```python
def test_caps_peak_date_older_than_max_lookback(self):
    """peak_date > 90 days ago is clamped to exactly 90 days ago."""
    from config import get_drawdown_attribution, ATTRIBUTION_MAX_LOOKBACK_DAYS
    old_date = (date.today() - timedelta(days=200)).isoformat()
    r = self._r(peak_date=old_date)
    conn, cur = _make_conn([])
    get_drawdown_attribution(r, conn)
    called_date = cur.execute.call_args[0][1][0]
    max_allowed = date.today() - timedelta(days=ATTRIBUTION_MAX_LOOKBACK_DAYS)
    assert called_date >= max_allowed
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=scripts pytest scripts/test_config.py::TestGetDrawdownAttribution::test_caps_peak_date_older_than_max_lookback -v
```

Expected: FAIL — `AttributeError: module 'config' has no attribute 'ATTRIBUTION_MAX_LOOKBACK_DAYS'`

- [ ] **Step 3: Add constant and clamp to config.py**

Near the other strategy constants (around line 90, near `DAILY_LOSS_LIMIT_PCT`), add:

```python
# Maximum lookback for drawdown attribution queries. Prevents unbounded DB scans
# during prolonged drawdowns where peak_equity_date may be months old.
ATTRIBUTION_MAX_LOOKBACK_DAYS = 90
```

In `get_drawdown_attribution` (around line 314), replace the peak_date resolution block:

```python
    peak_date_str = r.get(Keys.PEAK_EQUITY_DATE)
    if peak_date_str:
        peak_date = date.fromisoformat(peak_date_str)
    else:
        peak_date = date.today() - timedelta(days=30)
```

with:

```python
    peak_date_str = r.get(Keys.PEAK_EQUITY_DATE)
    if peak_date_str:
        peak_date = date.fromisoformat(peak_date_str)
    else:
        peak_date = date.today() - timedelta(days=30)
    max_lookback = date.today() - timedelta(days=ATTRIBUTION_MAX_LOOKBACK_DAYS)
    if peak_date < max_lookback:
        peak_date = max_lookback
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH=scripts pytest scripts/test_config.py::TestGetDrawdownAttribution::test_caps_peak_date_older_than_max_lookback -v
```

Expected: PASS.

- [ ] **Step 5: Run full config test suite**

```bash
PYTHONPATH=scripts pytest scripts/test_config.py -v
```

Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add scripts/config.py scripts/test_config.py
git commit -m "fix: cap drawdown attribution lookback at 90 days in config.py"
```

---

### Task 3: Attribution lookback cap in queries.ex

**Files:**
- Modify: `dashboard/lib/dashboard/queries.ex` (clamp cutoff)
- Modify: `dashboard/test/dashboard/queries_test.exs` (add 1 test)

- [ ] **Step 1: Write the failing test**

In `dashboard/test/dashboard/queries_test.exs`, inside `describe "drawdown_attribution/2"`, add:

```elixir
test "clamps peak_date older than 90 days to 90-day cutoff" do
  # 200-day-old peak_date must not raise — treated as 90-day lookback
  old_date = Date.add(Date.utc_today(), -200)
  result = Queries.drawdown_attribution(%{}, old_date)
  assert result == []
end
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd dashboard && mix test test/dashboard/queries_test.exs --only "clamps peak_date" 2>&1 | tail -10
```

Expected: test fails because the old_date is passed through unclamped (no error yet, but behaviour will differ once clamp is in). Actually this test will PASS immediately since DB always rescues in tests — you may see it pass. If so, the test is not red-green testable in isolation; proceed to implementation regardless and confirm all tests pass at the end.

- [ ] **Step 3: Implement the clamp in queries.ex**

In `dashboard/lib/dashboard/queries.ex`, replace line 168:

```elixir
  def drawdown_attribution(positions, peak_date \\ nil) do
    cutoff = peak_date || Date.add(Date.utc_today(), -30)
    cutoff_dt = DateTime.new!(cutoff, ~T[00:00:00], "Etc/UTC")
```

with:

```elixir
  def drawdown_attribution(positions, peak_date \\ nil) do
    raw_cutoff = peak_date || Date.add(Date.utc_today(), -30)
    max_cutoff = Date.add(Date.utc_today(), -90)
    cutoff = Enum.max([raw_cutoff, max_cutoff], Date)
    cutoff_dt = DateTime.new!(cutoff, ~T[00:00:00], "Etc/UTC")
```

- [ ] **Step 4: Run full dashboard test suite**

```bash
cd dashboard && mix test
```

Expected: all passing, no regressions. Coverage at 100%.

- [ ] **Step 5: Commit**

```bash
git add dashboard/lib/dashboard/queries.ex dashboard/test/dashboard/queries_test.exs
git commit -m "fix: cap drawdown attribution lookback at 90 days in queries.ex"
```

---

### Task 4: Trailing stop indicator on position cards

**Files:**
- Modify: `dashboard/lib/dashboard_web/live/dashboard_live.html.heex` (add trailing row)
- Modify: `dashboard/test/dashboard_web/live/dashboard_live_test.exs` (add 2 tests)

- [ ] **Step 1: Write 2 failing tests**

In `dashboard/test/dashboard_web/live/dashboard_live_test.exs`, find the `describe "format helpers with non-float inputs"` block (near the end) and add a new describe block before it:

```elixir
describe "trailing stop indicator on position cards" do
  defp trailing_position_state(trailing, trail_percent) do
    %{
      "trading:positions" => %{
        "SPY" => %{
          "symbol" => "SPY", "tier" => 1, "quantity" => 10.0,
          "entry_price" => 500.0, "stop_price" => 490.0, "current_price" => 510.0,
          "entry_date" => nil, "unrealized_pnl_pct" => 2.0,
          "trailing" => trailing, "trail_percent" => trail_percent
        }
      },
      "trading:heartbeat:screener" => nil, "trading:heartbeat:watcher" => nil,
      "trading:heartbeat:portfolio_manager" => nil, "trading:heartbeat:executor" => nil,
      "trading:heartbeat:supervisor" => nil
    }
  end

  test "shows Trail row when position is trailing", %{conn: conn} do
    {:ok, view, _} = live(conn, "/")
    send(view.pid, {:state_update, trailing_position_state(true, 2.0)})
    html = render(view)
    assert html =~ "Trail:"
    assert html =~ "2.0"
  end

  test "hides Trail row when position is not trailing", %{conn: conn} do
    {:ok, view, _} = live(conn, "/")
    send(view.pid, {:state_update, trailing_position_state(false, nil)})
    html = render(view)
    refute html =~ "Trail:"
  end
end
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dashboard && mix test test/dashboard_web/live/dashboard_live_test.exs 2>&1 | grep "trailing stop"
```

Expected: 2 failures — "Trail:" not found in HTML.

- [ ] **Step 3: Add trailing row to position card template**

In `dashboard/lib/dashboard_web/live/dashboard_live.html.heex`, find the "Stop:" row (around line 177):

```heex
                  <div>
                    <span class="text-gray-500">Stop:</span>
                    <span class="text-gray-300 ml-1 font-mono">{format_price(pos["stop_price"])}</span>
                  </div>
```

Insert immediately after it:

```heex
                  <%= if pos["trailing"] do %>
                  <div>
                    <span class="text-gray-500">Trail:</span>
                    <span class="text-amber-400 ml-1 font-mono">{format_float(pos["trail_percent"])}%</span>
                  </div>
                  <% end %>
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd dashboard && mix test test/dashboard_web/live/dashboard_live_test.exs 2>&1 | tail -5
```

Expected: all passing.

- [ ] **Step 5: Run full dashboard test suite and coveralls**

```bash
cd dashboard && mix test && mix coveralls
```

Expected: all passing, 100% coverage.

- [ ] **Step 6: Commit**

```bash
git add dashboard/lib/dashboard_web/live/dashboard_live.html.heex \
        dashboard/test/dashboard_web/live/dashboard_live_test.exs
git commit -m "feat: show trailing stop Trail% row on position cards"
```

---

### Task 5: Version bump, changelog, wishlist, remember

**Files:**
- Modify: `VERSION`
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/FEATURE_WISHLIST.md`
- Modify: `.remember/remember.md`

- [ ] **Step 1: Bump VERSION**

Change `VERSION` content to:

```
0.17.0
```

- [ ] **Step 2: Add CHANGELOG entry**

Prepend to `docs/CHANGELOG.md` after the header:

```markdown
## v0.17.0 — 2026-04-11

### Added
- **Scheduled reconcile** (wishlist): `supervisor.py --reconcile` runs `scripts/reconcile.py --fix`
  at 9:15 AM ET Mon–Fri via cron. Catches overnight Redis↔Alpaca state drift automatically.
  Fires `critical_alert` on non-zero exit. Cron entry: `15 9 * * 1-5`.
- **Dashboard: trailing stop indicator** (wishlist): position cards now show a "Trail: X%"
  row (amber) when a position has been upgraded to an Alpaca trailing stop (PR #86).

### Fixed
- **Drawdown attribution lookback cap**: `get_drawdown_attribution()` (Python) and
  `Queries.drawdown_attribution/2` (Elixir) now cap `peak_equity_date` lookback at 90 days.
  Prevents unbounded DB scans and confusing attribution tables during prolonged drawdowns.
```

- [ ] **Step 3: Update FEATURE_WISHLIST.md**

Mark the following items `[x]` with PR reference:

1. Find `- [ ] **Scheduled reconcile**` and change to:
   ```
   - [x] **Scheduled reconcile** — `supervisor.py --reconcile` calls `scripts/reconcile.py --fix`; cron at 9:15 AM ET Mon–Fri. PR #88.
   ```

2. Find `- [ ] **Drawdown attribution lookback cap**` and change to:
   ```
   - [x] **Drawdown attribution lookback cap** — Capped at 90 days in both `config.py` (`ATTRIBUTION_MAX_LOOKBACK_DAYS`) and `Queries.drawdown_attribution/2`. PR #88.
   ```

3. Find `- [ ] **Dashboard: trailing stop indicator on position cards**` and change to:
   ```
   - [x] **Dashboard: trailing stop indicator on position cards** — Position cards show "Trail: X%" row (amber) when `trailing=True`. PR #88.
   ```

Also update the next priority wave section — strike through items 1, 3, 4:

```
1. ~~**Scheduled reconcile**~~ ✅ Done (PR #88)
3. ~~**Drawdown attribution lookback cap**~~ ✅ Done (PR #88)
4. ~~**Dashboard: trailing stop indicator on position cards**~~ ✅ Done (PR #88)
```

- [ ] **Step 4: Update .remember/remember.md**

```markdown
# Trading System — Session Memory

## Version History
- v0.17.0 (PR #88, 2026-04-11): Safety/correctness — scheduled reconcile, attribution lookback cap, trailing stop indicator
- v0.16.0 (PR #87, 2026-04-11): Drawdown attribution — per-instrument P&L since peak in Telegram alerts + dashboard
- v0.15.0 (PR #86, 2026-04-11): Trailing stop-loss — Alpaca native trailing stop after N% gain, per-tier
- v0.14.0 (PR #85): Per-instrument P&L breakdown — /performance page
- v0.13.0 (PR #84): Economic calendar awareness — block entries on FOMC/CPI/NFP days
- v0.12.0 (PR #83): Graceful shutdown + automated Redis state backup
- v0.11.0 (PR #81): Cancelled stop auto-resubmit; daily loss CB → critical_alert; sell-through on halt

## Next Priority Wave (remaining after v0.17.0)
See docs/FEATURE_WISHLIST.md. Open items 5–10 in the wave:
5. Dashboard: one-click pause
6. Volume filter on entries
7. Equity curve chart
8. Strategy attribution by exit type
9. Position age alert
10. Paper trading report vs Alpaca balance

## Key Architecture Notes
- `trading:peak_equity_date` Redis key: set by executor on new equity highs, supervisor on daily reset
- Drawdown attribution: capped at 90 days. `ATTRIBUTION_MAX_LOOKBACK_DAYS = 90` in config.py.
- `get_drawdown_attribution(r, conn)` in config.py: merges realized (TimescaleDB) + unrealized (Redis)
- Dashboard attribution panel: conditional, hidden when empty, sorted worst-first
- ExCoveralls ignore: use `# coveralls-ignore-start` / `# coveralls-ignore-stop` (NOT `-end`, NOT `-next-line`)
- Supervisor cron jobs: --briefing (9:20 AM), --reset-daily (9:25 AM), --eod (4:15 PM), --weekly (4:35 PM Fri), --reconcile (9:15 AM)
```

- [ ] **Step 5: Commit and push**

```bash
git add VERSION docs/CHANGELOG.md docs/FEATURE_WISHLIST.md .remember/remember.md
git commit -m "chore: bump to v0.17.0, update changelog and wishlist for PR #88"
git push
```

---

## Run Order

Tasks must run in order 1→2→3→4→5. Each commit is independent — they can be reviewed in isolation.

## Final Verification

```bash
# Python tests
PYTHONPATH=scripts pytest skills/supervisor/test_supervisor.py scripts/test_config.py -v

# Elixir tests + coverage
cd dashboard && mix test && mix coveralls
```

Both must be green before opening the PR.
