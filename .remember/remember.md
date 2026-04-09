# Handoff

## State
executor.py at 100% coverage (60 tests, `skills/executor/test_executor.py`). All 4 covered files at 100%: config.py, indicators.py, notify.py, executor.py. 141 tests passing. Changes uncommitted, on `main` branch. Need cpr.

## Next
1. `cpr` — commit all uncommitted changes (executor.py pragma edits + test_executor.py full rewrite) to a new branch + PR
2. Bug 2: reject qty≤0 in `execute_buy` (already done in sell); also PM side check
3. Bug 3: PM should check existing position before approving entry (feedback loop prevention)

## Context
- `Keys.PDT_COUNT = "trading:pdt:count"` (colon-separated, not underscore) — tripped up tests
- Patch `executor.critical_alert` not `notify.critical_alert` (executor imports it at load time)
- `daemon_loop` and `main` have `# pragma: no cover`; `if __name__ == "__main__"` also marked
