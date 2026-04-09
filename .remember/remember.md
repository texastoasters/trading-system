# Handoff

## State
Fixed Bug 1 (executor sell race condition): `stop_cancelled` flag added to `execute_sell` in `skills/executor/executor.py`; exception handler now restores stop-loss if cancellation succeeded but sell failed. Tests in `skills/executor/test_executor.py` (5 passing). Feature wishlist created at `docs/FEATURE_WISHLIST.md`. Changes uncommitted, on `main` branch.

## Next
1. `cpr` the current changes (Bug 1 fix + tests + feature wishlist) — must create new branch first (on main)
2. Bug 2: reject qty≤0 orders at top of `execute_buy` and `execute_sell` (executor.py) — already partially done for sell, needs buy + PM side
3. Bug 3: PM should check existing position before approving entry to prevent feedback loop

## Context
- "cpr" = commit + push + PR (branch rules: never push to main; create new branch if on main; check for existing open PR before creating new one)
- Run tests: `PYTHONPATH=scripts python3 -m pytest skills/executor/test_executor.py -v`
- `alpaca` not installed in local venv — tests mock it via `sys.modules`
