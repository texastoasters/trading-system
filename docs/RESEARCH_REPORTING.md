# Research reports and failure alerts

This reporter explains the frozen experiment; it never makes an investment decision, calls a market/broker endpoint or runs an LLM. See [the contract](RESEARCH_REPORTING_SPEC.md). Installation/activation is separate from merging; old trading containment remains authoritative.

## What arrives

One concise daily iMessage at the first successful check at/after **19:30 America/Chicago**, plus deduplicated problem/recovery notices. Current local date only; no backdated daily messages. The five-minute timer is offset from evaluator quarter-hours to reduce lock contention. A busy evaluator can defer a report; this is not a failure by itself.

The daily message identifies actual evidence coverage, newest AI allocation, recorded model responses/tokens and unknown billed cost, and dated first/latest1/5-session raw-price comparisons. Recorded responses are not proof of total calls or zero charges when a started inference remains in-doubt. Pending, unavailable and accepted cash remain distinct. These are not adjusted total returns, compounded portfolios, broker fills or after-cost alpha.

Alerts cover new missed/failed/ambiguous work, unavailable verification/sources, disabled scheduling, failed/overlong execution and stale completed work. They do not restart anything, retry inference, clear HOLD or change the study. The reporter and evaluator share a controller: a complete controller/network outage cannot be guaranteed to text its own failure. No external uptime service or unapproved fallback channel is implied.

## Preserve the experiment

Pin reporting code separately under `$HOME/.local/share/t2-research/reporting/releases/<approved-sha>`, with that reporter's own `current` symlink. Do **not** repoint `$HOME/.local/share/t2-research/current`, edit evaluator sources/protocol/records, or register a replacement study. The source/helper/active campaign belong to the canonical private Pi Brain `Plans/T2 Read Only Evaluator Activation.md`; reporter state/permissions belong to `Plans/T2 Research Reporting and Alerts.md`.

Source must pass independent reviews and exact-head CI; explicit applicable merge/install/activation authority is required. Obtain the reporter from reviewed merged Git source, never deploy an arbitrary worktree by SSH. Reuse the existing Python3.13 project environment; reporting adds no global SDK dependency/provider change.

## Generate private configuration from actual registration

After reviewed source provisioning, the existing evaluator environment file contains one literal, non-secret campaign setting. The generator below reads it as data, never sources/evals it, and asks the **frozen** offline CLI for verified identity. It stops rather than overwriting an existing reporter config.

```sh
"$HOME/.local/share/t2-research/venv/bin/python" -c '
import json,os,subprocess
from pathlib import Path
os.umask(0o077)
home=Path.home(); root=home/".local/share/t2-research"
setting=(home/".config/t2-research/evaluator.env").read_text()
assert setting.startswith("T2_CAMPAIGN=") and setting.count("\n")==1 and setting.endswith("\n")
campaign=Path(setting[len("T2_CAMPAIGN="):-1]); assert campaign.is_absolute() and campaign.is_dir() and not campaign.is_symlink()
frozen=(root/"current").resolve(); scripts=frozen/"scripts"; python=root/"venv/bin/python"
raw=subprocess.check_output([str(python),str(scripts/"evaluate_prospective.py"),"verify",str(campaign)],text=True,timeout=90)
verified=json.loads(raw)["verification"]; assert verified["verified"] is True and verified["executable"] is False
config={"schema_version":1,"campaign_id":verified["campaign_id"],"registration_hash":verified["registration_hash"],"campaign_path":str(campaign),"frozen_scripts_path":str(scripts),"project_python":str(python),"hermes_home":str(home/".hermes"),"state_dir":str(root/"reporting/state")}
target=home/".config/t2-research/reporting.json"; assert not target.is_symlink()
with target.open("x") as output: json.dump(config,output,indent=2)
target.chmod(0o600)
'
```

`frozen_scripts_path` is the physical **scripts directory**, not repository root or a mutable reporter checkout. The helper executes under the frozen project interpreter with `-I`, isolates source selection and uses public offline interfaces under the campaign lock. It never imports the managed inference SDK or calls tick. State must remain private/unsynced and outside the campaign directory.

## Preview before enabling

With the separate reporter checkout pinned and configuration generated:

```sh
"$HOME/.local/share/t2-research/venv/bin/python" \
  "$HOME/.local/share/t2-research/reporting/current/scripts/research_reporting_cli.py" \
  --config "$HOME/.config/t2-research/reporting.json" --preview
```

Preview must not send or create notification state. Verify the summary and explicit conditions; an error is not a successful empty report. Verify campaign/source hashes unchanged separately from normal evaluator appends. Do not pause the experiment merely to make a diagnostic look clean.

## Notification truth and crash recovery

Delivery uses existing `hermes send --to photon --json --file -`, not copied credentials/recipient IDs, new channels or a direct sidecar API. The CLI's raw `note` contains the destination and must not be logged. The adapter requires exact success plus opaque message ID and exact original text in Hermes' local sender index. This is **provider acceptance and local record verification**, not a provider-history GET, rendered-byte guarantee, phone delivery or human read acknowledgement.

Keep a notice within2000characters/one chunk; attachment-control markers are not notification content. A missing local record can coexist with a genuinely accepted send because indexing is best-effort. A post-submit timeout/crash stays **in-doubt**; do not delete state or blindly resend. Pending pre-send intents may resume; expired prior-day daily intents must not appear as current reports.

Notification state is independently durable, campaign-bound and single-writer. Daily/event/condition keys suppress repeats. Busy/unobserved data cannot certify recovery. Unconfirmed deliveries are visible in status/journal and require reconciliation; a broken messenger cannot promise its own failure text.

## Install and explicitly activate the reporter units

Only after operational authority, copy reviewed `ops/research/t2-research-reporting.service`/`.timer` into `$HOME/.config/systemd/user/`. Do not replace evaluator units. Verify the actual files and calendar first:

```sh
systemd-analyze --user verify "$HOME/.config/systemd/user/t2-research-reporting.service" "$HOME/.config/systemd/user/t2-research-reporting.timer"
systemd-analyze calendar '*-*-* *:02/5:00 UTC'
systemctl --user daemon-reload
systemctl --user enable --now t2-research-reporting.timer
systemctl --user show t2-research-reporting.timer --property=ActiveState,UnitFileState,LastTriggerUSec,NextElapseUSecRealtime
journalctl --user -u t2-research-reporting.service --no-pager -n 20
```

An enabled timer is not completion proof. Correlate actual trigger/invocation, persisted intent/send-start, accepted local record and final outbox state. Verify one real scheduled report through the existing home destination; label any standalone channel diagnostic separately. Fault/dedup/recovery proofs use an isolated fixture runtime, never sabotage the active evaluator. Recheck study fingerprint/data and old trader HOLD afterward. Publish private canonical receipts and consumer readback; do not publish raw home IDs or SDK output.

To stop new reporting, disable/stop **only** `t2-research-reporting.timer`. Do not delete its ledger, kill a productive send solely for quiet output, reset the study, restart the old bot or change billing. A later reporting-code update must preserve pending/in-doubt delivery state and cannot silently migrate another campaign's ledger.
