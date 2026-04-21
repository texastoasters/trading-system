# Handoff

## State
Two PRs in flight on branch `fix/us-eastern-timezone-sell-failure`:
- v0.34.12: executor timezone fix (`ZoneInfo("America/New_York")` replacing bad `pytz.timezone("US/Eastern")`)
- v0.34.13: PM displacement queue fix — incoming signals now queued in `trading:displacement_pending:{target}` and re-evaluated after exit completes

## Next
- Merge PR #155 once CI passes — CI/CD will restart executor (currently down)
- Monitor that UNM/WMB re-entry works correctly after next displacement event

## Context
Never deploy to server directly — always through PR + CI/CD.
Today: PAGP whipsawed same-day (exited_today never set), executor killed by mistake, PDT at 1/3 going into close. Redis WRONGTYPE error in verify_alpaca.py is a separate unresolved issue.
