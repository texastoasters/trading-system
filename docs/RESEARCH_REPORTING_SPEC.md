# Research reporting and alerting contract

Authorized November17: Dan accepted building daily reporting/failure alerts. This is separate from the frozen research experiment and legacy trading bot. No new merge/deployment authority is inherited. New code stays outside evaluator fingerprint roots; do not modify/repoint the active release, campaign protocol, records or scheduler. A separately pinned reporter reads through the frozen evaluator's public offline interfaces.

## Operational policy

One deterministic reporter on the existing controller; no LLM/market/broker calls. Check every5minutes; one daily message at/after19:30 America/Chicago, current local date only. First activation after that time may send today's summary immediately. Defer a busy snapshot to the next check; never backdate or fabricate a successful scan. Read existing Photon home target, never hard-code/copy its private recipient or credentials.

Reports: actual checkpoint/observation/failure coverage; latest AI requested allocation; recorded model-response/token totals with incomplete usage explicit; last available first/latest1/5-session matched raw-price responses and cohort dates; pending/unavailable distinct. Recorded responses never prove total calls or zero charges when started inference lacks a response. Cash is a valid accepted decision, not failure substitution. No compounded portfolio curve, ranking, adjusted return, economic alpha or estimated bill. Always label read-only/no orders, unchecked corporate actions and billed cost unknown. Keep text<=2000 characters without truncating safety caveats; bounded deterministic layout, not source/model rationale text.

Failure events: each new missed-slot/collection-failure/in-doubt tick and unacceptable/late/unavailable AI checkpoint once. Ongoing conditions: disabled evaluator timer, failed service, service running>900seconds, no completed tick>2700seconds since latest completion/registration, offline verification unavailable. Future prices pending, ordinary waiting, accepted cash and a bounded active/busy evaluator are not failures. Historical failure events don't become recurring new incidents. Condition recovery only follows an observed verified clear state; busy/unobserved data cannot clear prior verification/staleness conditions. Never restart/repair evaluator or retry inference.

## Snapshot and policy seams

New package `scripts/research_reporting`, entry `scripts/research_reporting_cli.py`; no production evaluator/evidence/lab changes. Public errors sanitized; strict types, aware UTC times, no bool-as-int, nonfinite values, raw exception bodies or untrusted rationale in text. Existing delegated public-seam TDD authority applies. New Python100% without exclusions/ignores/private mocks/limit mutation; inherited gaps disclosed.

`read_snapshot.collect(source_root, campaign, expected_campaign_id, expected_registration_hash, *, now=None)` runs under selected frozen source and public `campaign_lock`, `load_campaign`, `verify_campaign`, `report_campaign`. It returns:

```
{schema_version:1,status:'verified'|'busy'|'unavailable',observed_at:aware_ISO,
 campaign_id:expected_id,registration_hash:expected_hash,
 registered_at:aware_ISO|null,report:public_report|null,
 verification:public_verification|null}
```

For verified, actual id/hash must match expectations and both public verification/report say verified/executable=false. Busy/unavailable have null report/verification; no caller override of experiment time. Capture `observed_at` after the completed read, failure or busy result, never before import/lock/verification latency. `now` is only the reporter-observation clock/test seam, never passed into evaluator logic. Reader CLI uses `-I`, project interpreter and explicit pinned source path; source stdout/stderr are bounded/sanitized. No SDK/inference import or tick. Public error categories only. The actual scheduler supplies real time.

`policy.assess(snapshot, health, *, now)` returns:

```
{summary:str|null,event_findings:[{key:str,text:str}],
 conditions:{condition_name:safe_text},indeterminate_conditions:[name],busy:bool}
```

Health DTO: exact keys `timer_active` bool, `service_active_state` str, `service_result` str, `service_exit_status` exact int, `service_elapsed_seconds` finite nonnegative number|null. Service elapsed uses same-host monotonic systemd/Python clocks, not parsing human timezone dates. Unavailable observation is an active verification condition, not healthy silence. Busy means defer summary/events and preserve verification/stale conditions via indeterminate names. Operational conditions still assessed. Validate snapshot/health first; malformed input raises public `ReportingError`. Event keys derive immutable IDs/slots, not arbitrary model text. Policy does not own schedules, transport or durable state.

Reader/policy tests use public registration/lock/verify/report interfaces and external I/O fixtures. No internal collaborator substitution. Reader's frozen-source import must resolve the selected checkout rather than accidentally using reporter-adjacent evaluator modules. Record source fingerprints and verify live campaign hashes unchanged before/after read-only observation.

## Durable notification semantics

Separate private unsynced reporter state/outbox, never campaign records. Public seam `outbox.cycle(state_dir, campaign_id, assessment, sender, *, now)` consumes the policy assessment. `sender(text)` returns `{provider_id: nonempty_str, verified: true}` only after actual provider acceptance plus exact sender-record readback; every uncertain result/exception after durable send-start remains in-doubt. The default daily policy is19:30 America/Chicago; tests inject only this reporter clock. Cycle returns a sanitized status and notice metadata, never recipient/credential/raw exception text. State belongs to one campaign and rejects identity mismatch; expired unsent daily intents are not delivered as current reports.

Single local writer with validated advisory lock and atomic state publication. Durable pending intent precedes send; durable send-start precedes any external submission. One daily key per local date; stable event/condition keys deduplicate unchanged findings. Group new findings into bounded alert messages; at most two external submission attempts per cycle, with remaining safe pending work preserved for later checks. Raw historical events must be deduplicated before applying fresh-work limits, so an already-reported history cannot disable monitoring. Queued alerts/recoveries identify their actual observation time. Persist claimed keys before submission, so a later new event doesn't cause earlier ambiguous notices to be resubmitted.

The installed v0.23.1 Photon contract is `hermes send --to photon --json --file PATH` (stdin also supported). Require exact `success is true` and a nonempty opaque `message_id`; exit0/skipped alone isn't acceptance. Never persist/output raw `note` because it contains the private home destination. SDK acceptance can be checked against Hermes' **local sender index**, not a provider history GET: source `gateway/rich_sent_store.py` exposes `lookup`, with index `$HERMES_HOME/state/rich_sent_index.json`, composite keys ending `:<message_id>` and text field `t`. A bounded read-only index adapter may require exactly one matching accepted Photon ID and exact original requested text, without copying/logging recipient keys. Missing/ambiguous/mismatching records remain unconfirmed. This compatibility shape is version/source-verified, not a promise of a public remote history API. Keep text<=2000 characters and one chunk. Source references: official CLI/Photon docs; installed `hermes_cli/send_cmd.py`, `tools/send_message_tool.py`, `plugins/platforms/photon/adapter.py`, `gateway/rich_sent_store.py`.

A crash/timeout after send-start remains in-doubt, never blindly resent. Pending pre-send intents may safely resume. Provider acceptance, local sender-record verification, actual recipient delivery and human read are separate states; do not mark accepted-and-recorded from CLI exit0 alone or invent exactly-once external delivery. Preserve provider ID and verify exact local sender text. Local record verifies requested text, not necessarily transformed/rendered iMessage bytes. Recipient delivery/read acknowledgement stays unknown. Delivery uncertainty/failure stays visible in reporter status/journal and daily report where possible; no unapproved alternate recipient/channel or global gateway restart.

Reporter startup/health/daily activity is independent from evaluator units. A failed messenger cannot promise its own failure text. Exercise external send/readback with a clearly labelled real test through existing home target after applicable installation authority; do not inject faults into live evaluator/campaign. Prove failure notification/dedup/recovery through isolated genuine external-I/O seams and actual scheduler dispatch separately from manual execution.

## Release and acceptance

Reviewed PR/CI workflow, exact-head two-axis independent review, full Python/Elixir gates. Provision reporter into its own pinned release path and units; never repoint evaluator `current` or register a replacement study. Explicit merge/deployment authorization before changed-code installation. Verify scheduled execution, provider acceptance plus exact local sender-record readback, durable outbox/dedupe, unchanged campaign/fingerprint and old trader HOLD. Save concise canonical proof/private receipts and consumer readback; future outcomes remain real/pending.
