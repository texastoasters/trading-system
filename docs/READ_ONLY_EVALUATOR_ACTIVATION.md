# Read-only evaluator activation

Operational companion to [the evaluator guide](PROSPECTIVE_EVALUATION.md). The templates in `ops/research/` are **not installed or enabled by a merge**. They never invoke the legacy updater, trading service, Redis, account/order endpoints or a broker writer.

## Authority and source pin

Activation requires explicit research-operation authority, reviewed source and successful exact-head CI. Openboog feature deployment remains PR/CI/CD-only; an isolated post-merge source pull/helper installation needs its own explicit operational permission. This document is not that permission. Never remove HOLD to make research provisioning work.

Use one controller/writer and a fresh campaign after this helper-path migration: the transport source fingerprint changed. Existing Mac diagnostics remain historical. Keep the live store private and unsynced; publish sealed read-only snapshots separately. Current host, source SHA, helper SHA, campaign identity and activation receipts belong in the private Pi Brain `Plans/T2 Read Only Evaluator Activation.md`, not copied into skills.

## Controller prerequisites

- Linux user systemd manager with `Linger=yes`, working network/DNS and existing permitted Nous authentication. Verify with `loginctl show-user "$USER" --property=Linger`; if absent, stop for the appropriate operator action rather than silently applying root configuration.
- Python3.13 evidence environment under `$HOME/.local/share/t2-research/venv`, isolated from Hermes' SDK dependency closure. Install only `requirements-evidence.txt` there; record actual Python/dependency versions.
- Pinned reviewed checkout under `$HOME/.local/share/t2-research/releases/<approved-sha>`. The `current` symlink points to that checkout; never repoint it under an active campaign. Revalidate the implementation fingerprint before resuming any stored campaign.
- Existing `hermes --print-runtime-command` must work under the service's explicit PATH. Fresh exact-model catalog/entitlement preflight must pass; a generic logged-in banner alone is insufficient. No global model/provider/billing changes or new paid access.

The controller already has these preparations when the private activation record says so; do not recreate/overwrite an existing release, venv or campaign merely to repeat a guide.

## Credential-isolated source helper

The fixed client path is now **`~/.local/share/t2-research/t2_evidence_get.py`** on the existing SSH alias **`openboog`**. There is no fallback to a disposable cache file and no model-selectable helper command/path.

After approved deployment/post-merge installation authority, obtain the helper bytes from the exact reviewed release into an isolated research directory, not the held trading checkout. Install a regular non-symlink file at that path, owner-only directory/file permissions; compare its SHA256 with the reviewed source before use. Do not overwrite a different helper silently or auto-update it during a registered study. Keep any previous helper/evidence as history.

The standalone module is stdlib-only. Existing literal credential assignments remain in the source host's existing private credential file; never copy them to the controller, scheduler environment, GitHub or model. The fixed allowlisted GET routes, no-redirect rule, bounded JSON projection and helper wire protocol are unchanged.

Verify source reachability from the **controller**, not only a laptop. Fix only the designated SSH alias to the approved reachable address recorded privately; preserve unrelated SSH blocks/keys and host-key verification. Do not change firewall or Tailscale policy as a shortcut. Run a genuine public source capture and offline reconstruction; do not infer entitlement from SSH `true` or an HTTP200.

## Fresh registration and configuration

From the reviewed controller checkout, copy/review the registration example, choose a new portable campaign ID and run the public `register` command with the actual clock. Record the returned absolute campaign directory. No imported model run, caller timestamp or backfill is permitted.

Create `$HOME/.config/t2-research/evaluator.env` from the **actual saved CLI registration receipt**, not a guessed path. For example, capture the public register command's JSON stdout as `$HOME/.local/share/t2-research/registration-receipt.json`, then generate the owner-only configuration:

```sh
"$HOME/.local/share/t2-research/venv/bin/python" -c 'import json,os; from pathlib import Path; os.umask(0o077); home=Path.home(); receipt=json.loads((home/".local/share/t2-research/registration-receipt.json").read_text()); assert receipt["status"]=="registered"; campaign=Path(receipt["campaign_path"]); assert campaign.is_absolute() and campaign.is_dir() and not campaign.is_symlink(); config=home/".config/t2-research"; config.mkdir(parents=True,exist_ok=True); target=config/"evaluator.env"; assert not target.exists(); target.write_text("T2_CAMPAIGN="+str(campaign)+"\n"); target.chmod(0o600)'
```

The file contains one non-secret `T2_CAMPAIGN` setting. Validate the returned directory through the public offline `verify` command before installing units. The controller/venv/release locations are fixed above; the only campaign-specific unit input is this path. Global Hermes configuration is untouched.

Copy the reviewed `ops/research/t2-evaluation.service` and `.timer` into `$HOME/.config/systemd/user/`. Unit installation is configuration, not activation. Verify the actual files using:

```sh
systemd-analyze --user verify "$HOME/.config/systemd/user/t2-evaluation.service" "$HOME/.config/systemd/user/t2-evaluation.timer"
systemd-analyze calendar '*-*-* *:00/15:00 UTC'
```

The service uses the project interpreter, private umask, exact CLI tick, no automatic service restart and a14-minute whole-process-group timeout. The engine's durable inference-start journal still governs ambiguous charges; a timeout is not permission to repeat inference. No agent supervisor/model debate is added around each tick.

## Explicit activation and actual dispatch proof

Only after all prerequisites and operational authority pass:

```sh
systemctl --user daemon-reload
systemctl --user enable --now t2-evaluation.timer
systemctl --user list-timers t2-evaluation.timer --all --no-pager
systemctl --user show t2-evaluation.timer --property=ActiveState,Unit,NextElapseUSecRealtime,LastTriggerUSec
journalctl --user -u t2-evaluation.service --no-pager -n 30
```

The timer combines a60-second initial trigger with quarter-hour calendar triggers, so a nearby calendar boundary can dispatch first. Later calendar dispatches align to900-second UTC buckets. `Persistent=false` prevents scheduler catch-up; the engine independently refuses historical forecast backfill. A repeated same-bucket dispatch must not repeat source/model charges. A configured/active timer is not proof its service completed.

Correlate the **actual timer trigger**, service exit, durable tick/checkpoint, source reconstruction, model receipts and offline report/replay. A manual CLI smoke is separate evidence, not scheduler proof. Timely actual forecasts may have genuinely pending future prices. Late windows, source loss or model refusal remain unavailable/waiting, never fabricated cash or performance.

Failures are visible in service status, the user journal and campaign records. No phone/Telegram alert delivery is claimed unless separately configured and verified. A terminal campaign must not be replaced/re-registered automatically; disable its timer and retain evidence after final review.

## Rollback and boundaries

To stop new scheduling, disable/stop **only** `t2-evaluation.timer`. Do not kill an active model process merely because output is quiet: verify useful-work progress and ambiguity first. Do not delete campaigns, recalculate timestamps, overwrite manifests, retry unknown charges, reset accounts or start the old bot. Recheck old service/cron/HOLD after provisioning. Broker-writing autonomy and economic promotion remain separate work.
