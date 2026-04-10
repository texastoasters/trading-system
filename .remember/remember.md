# Handoff

## State
Increased test coverage for `skills/portfolio_manager/portfolio_manager.py` from 41% → 86%. Added 33 new tests covering drawdown circuit breakers, position limits/displacement, BTC fee check, sizing edge cases, exit signals, process_signal, and process_pending_signals. 261 tests passing. On branch `feat/morning-briefing`.

## Next
1. Run `cpr` to commit + push + PR the PM test additions
2. Add tests for `skills/screener/screener.py` (0% coverage, no test file)
3. Add tests for `skills/watcher/watcher.py` (0% coverage, no test file)

## Context
`get_drawdown` computes from SIMULATED_EQUITY/PEAK_EQUITY — not from `Keys.DRAWDOWN` directly. Drawdown tests must set those two keys. Remaining uncovered lines (346-369, 373-385) are daemon_loop/main — require live Redis, skip.
