# Deployment containment gate

## Contract (v0.38.1)

CI must not invoke the legacy remote updater while containment is active or
unverifiable. That updater pulls source, replaces cron/logrotate files, starts
containers and only then stops/starts the trading service. A systemd refusal
alone therefore does not contain all deployment side effects.

- Deploy workflow accepts only successful `push` runs on this repository's
  `main`, not fork or pull-request workflows.
- Deployment checks out the tested SHA without persisting checkout credentials.
- SSH streams `scripts/deploy_guard.py` to remote `python3 -`; no remote install.
- Remote guard lists `/var/lib/t2-containment`. An entry named `HOLD`, including
  a directory or dangling link, blocks. Missing/not-directory/unreadable state
  also blocks. Exit78 is a visible deployment failure, never success.
- Only an explicitly readable directory without HOLD allows the existing SSH
  user's `~/.local/bin/update-trading-system` to run. Its status is propagated;
  inability to launch it fails closed. Python/SSH failure also prevents launch.
- No marker creation/removal, service/container/cron write or broker action is
  performed by the preflight itself. Missing containment directories require an
  operator decision, not automatic creation or treating absence as approval.

## Test seams and limits

Autonomous engineering mandate supplies test-seam judgment. Tests exercise the
public guard entrypoint with actual disposable helpers, plus workflow wiring.
They cover held/unknown state, missing helper and exit-status propagation.

This is a point-in-time preflight, not a shared lock against concurrent HOLD
changes. It protects this CI invocation only; direct legacy-updater calls and
other writers are not made safe by it. The legacy updater still pulls branch
HEAD rather than a bound artifact: exact-version deployment remains separate
work. Existing SSH host-key policy is unchanged, not claimed hardened here.

## Separate unresolved release incident

Deploy run43799609152 failed tag push with403 for `github-actions[bot]` despite
logged `Contents: write`. Read-only Nov16 checks also show repository workflow
write default, Actions enabled, no tag-targeting ruleset. Those observations do
not establish the token failure's cause or demonstrate its repair.

This change does not broaden permissions, add credentials, create tags manually,
rerun that workflow, remove HOLD, or authorize trading. Merge/review and any
later release diagnostic/promotion are separate operator actions. Never retry
the old unguarded workflow as a harmless test: its dependent deployment can run.
