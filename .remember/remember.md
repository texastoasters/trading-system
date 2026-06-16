# Session State (2026-06-16)

## In flight

- **fix/partial-stop-fill-pnl (v0.36.5)** → branch `fix/partial-stop-fill-pnl`
  - Carries forward the idempotent-redesign that did NOT make it into merged PR #225 (#225 = first cut only, v0.36.4, non-idempotent `_reconcile_partial_stop_fill`, dropped the 2 sold shares' P&L). Redesign commit 526ebf6 was on the deleted branch; ported its executor.py + test_executor.py onto fresh main.
  - **`_book_partial_stop_fill` (idempotent):** Redis qty > broker qty AND stop filled_qty>0 → book delta ONCE at stop's filled_avg_price (realized P&L → equity + `stop_loss_partial` trade), sync Redis to broker. Re-runs book nothing. Recovers the 2 TTE shares (≈ −$8.44) the first cut dropped.
  - Both paths use `_alpaca_position_qty()` broker truth. `execute_sell` books partial + syncs (clean Redis + clear `exit_signaled` if empty) then sells; `_check_cancelled_stops` same before resubmit.
  - executor.py 100% cov; full suite 1101 pass. Live position self-heals next exit cycle post-deploy.
  - `scripts/reconcile.py --fix` still does NOT fix qty drift (resubmits at stale qty) → flagged follow-up.

## Shipped 2026-06-16

- **#225 (v0.36.4)** merged — stopped the TTE 403 stop-loss alert loop + corrected share count, but missed the partial-fill P&L booking (fixed in v0.36.5 above).

## Recently shipped

- **P1 TSMOM block complete** (v0.36.0–0.36.3, PRs #219/#222/#223/#224): backtest harness, watcher integration, PM integration + per-strategy caps (`STRATEGY_MAX_CONCURRENT` replaced `DONCHIAN_SYMBOLS`/`TSMOM_SYMBOLS`), `/strategies` dashboard.
- TSMOM validated DSR=1.000 on 32-sym×10y (365 trades).

## Process reminders

- Bug fixes via PR + CI/CD only; never edit/deploy on the server directly. SSH is read-only for diagnosis.
- TDD: failing test first. Keep Python + Elixir at 100% coverage; no coveralls-ignore shortcuts.
- Roadmap board (Project #1): pick top Todo by priority, move to In Progress before starting.
