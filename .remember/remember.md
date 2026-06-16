# Session State (2026-06-16)

## In flight

- **fix/stop-loss-qty-drift (v0.36.4)** → branch `fix/stop-loss-qty-drift`
  - Bug: a GTC stop partially filled (2 of 3) then cancelled → Redis qty stayed 3 while Alpaca held 1. Every watcher exit cycle: `execute_sell` cancel→market-sell qty3→`403 insufficient qty (available:1)`→restore-stop qty3→403→`critical_alert("Stop-loss failed for TTE …")`, looping. Observed live on TTE.
  - **Fix B (guard, load-bearing):** `_alpaca_position_qty()` reads broker's real qty; `execute_sell` reconciles Redis down to it before selling/replacing stop. Broker holds none → clean Redis + clear `exit_signaled`. API error / non-list → fall back to Redis qty (never delete on a blip).
  - **Fix A (source):** `_reconcile_partial_stop_fill()` books realized P&L for shares a stop sold before cancel + decrements qty; `_check_cancelled_stops` cancelled branch syncs remaining qty to broker before resubmit.
  - `skills/executor/executor.py` 100% cov; full suite 1097 pass.
  - Live position self-heals on next post-deploy exit cycle — no server change. `scripts/reconcile.py --fix` does NOT fix it (resubmits at same stale qty) → follow-up candidate.

## Recently shipped

- **P1 TSMOM block complete** (v0.36.0–0.36.3, PRs #219/#222/#223/#224): backtest harness, watcher integration, PM integration + per-strategy caps (`STRATEGY_MAX_CONCURRENT` replaced `DONCHIAN_SYMBOLS`/`TSMOM_SYMBOLS`), `/strategies` dashboard.
- TSMOM validated DSR=1.000 on 32-sym×10y (365 trades).

## Process reminders

- Bug fixes via PR + CI/CD only; never edit/deploy on the server directly. SSH is read-only for diagnosis.
- TDD: failing test first. Keep Python + Elixir at 100% coverage; no coveralls-ignore shortcuts.
- Roadmap board (Project #1): pick top Todo by priority, move to In Progress before starting.
