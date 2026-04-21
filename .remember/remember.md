# Handoff

## State
Branch `fix/us-eastern-timezone-sell-failure` — PR pending (vcr done, cpr next).
Fix: `_seconds_until_midnight_et` in `skills/executor/executor.py:679` switched from `pytz.timezone("US/Eastern")` to `zoneinfo.ZoneInfo("America/New_York")`. VERSION bumped to 0.34.12, CHANGELOG updated. 100% coverage, 928 tests passing.

## Next
- Create PR via cpr (in progress)
- Merge once CI passes — CI/CD auto-deploys to server (executor is down until deploy)
- Investigate Redis WRONGTYPE error seen in `verify_alpaca.py` (separate issue, not blocking)

## Context
Never deploy to server directly — always through PR + CI/CD. Dan was explicit about this.
Today's incident: PAGP re-entered same-day because `exited_today` key was never set (bug). PDT count is at 1/3 (correct). Executor was accidentally killed; CI/CD deploy will restart it.
