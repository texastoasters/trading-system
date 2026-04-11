# Handoff

## State
PR #75 (agent restart policy) and PR #76 (CHANGELOG + VERSION + git tags v0.1.0–v0.10.0) both open, awaiting merge. Current version: 0.10.0. All tags pushed to remote.

## Next
1. Merge PRs #75 and #76.
2. Pick up wishlist item #3: **alert on stop-loss cancelled without fill** — PR #72 covers filled stops; a stop silently `cancelled` without a fill leaves a naked position with no alert.
3. Then: **max daily loss limit** (#4) and **automated daily Redis backup** (#5).

## Context
- On every PR: bump `VERSION`, add `CHANGELOG.md` entry, tag after merge (`git tag -a vX.Y.Z && git push origin --tags`).
- Update `docs/FEATURE_WISHLIST.md` + `remember.md` as part of every PR — commit both to the branch.
- Versioning: 0.x.0 = new capability, 0.x.y = fix/minor. v1.0.0 when wishlist complete.
- `remember.md` must always be committed and pushed on every PR, not just saved locally.
