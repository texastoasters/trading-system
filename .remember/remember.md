# Session State — Read-only Evaluator Activation

## Current branch and scope

`ops/read-only-evaluator`, proposed v0.40.1, based on merged PR233 (`dafb77c8d51ab953c729f8dfea95cea1816f0d50` / v0.40.0).

Dan authorized read-only evaluator provisioning/activation while the old trader stays held. No new PR merge or post-merge SSH helper installation authority is inherited. Read `docs/READ_ONLY_EVALUATOR_ACTIVATION.md`, `docs/PROSPECTIVE_EVALUATION.md` and `CONTEXT.md`. Canonical operational state: Pi Brain `Plans/T2 Read Only Evaluator Activation.md`; broader mandate: `Plans/T2 AI First Trading Redesign.md`.

- Fixed credential-isolated GET helper moves from scratch to `~/.local/share/t2-research/t2_evidence_get.py`. No arbitrary path, cache fallback, credential, endpoint or wire-schema change. Changing the transport fingerprint requires a fresh campaign, never rewriting historical stores.
- User systemd templates are uninstalled/inactive artifacts until explicit activation prerequisites pass. One controller/writer; private unsynced durable campaign; no outer agent inference per tick.
- Python3.13 project environment owns campaign/evidence/scoring; only pure lab inference uses the existing managed SDK closure. No global provider/dependency/billing changes.
- Actual timer trigger, service completion and durable prospective source/model checkpoint are distinct verification gates. Future outcomes remain pending; fixture/development runs are not prospective profits.

## Retained constraints

Old openboog service/cron/dashboard containment remains authoritative. No HOLD removal, old-bot restart, broker writes, account reset, purchases or top-ups. Reviewed PR/CI workflow; any isolated post-merge source pull/helper installation requires explicit operational permission, not an SSH feature-deployment shortcut.

Preserve original dirty checkouts, wishlist and separate PR229. Every result remains `executable=false`; synthetic cash is not broker cash or a capital recommendation. Long-only/no borrowing/no debt. Keep Python + Elixir coverage at100% without exclusions/ignores; disclose inherited gaps. Public test seams only. Reliable research plumbing is not an unattended trader or evidence of after-cost alpha.
