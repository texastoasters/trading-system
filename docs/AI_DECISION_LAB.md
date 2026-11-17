# T2 AI Decision Lab

A broker-isolated research tool: supplied evidence and portfolio → bounded AI research/CIO/critic → deterministic target preview → durable record and offline replay. **It is not an unattended trader, a performance backtest, or a claim of alpha.**

## Safety and scope

Every result has `executable: false`; no orders are constructed/submitted. There are no legacy trading-agent, Redis, database, broker, scheduler, or production-service imports. The existing trading hold is not removed by this feature. An accepted lab proposal is not approval to trade.

The runtime core needs only Python's standard library. Real inference additionally uses an existing Nous OAuth login through the installed Hermes runtime. No new subscription, provider fallback, automatic retry, billing-setting change or top-up is implemented. Keep keys out of input/run files.

## Commands

From the repository root:

```bash
# Structural validation, no model/network. --now also checks future shadow input.
python3 scripts/ai_decision_lab.py validate bundle.json

# Existing Nous login; use the actual managed dependency closure, not an old venv.
python3 scripts/ai_decision_lab_hermes.py run bundle.json \
  --model openai/gpt-6.1-sol --output /approved/durable/decision-runs

# Fully offline verification/replay, no Hermes dependency.
python3 scripts/ai_decision_lab.py replay /approved/durable/decision-runs/RUN_ID
```

Choose an approved durable output location; run artifacts are not disposable cache. The managed launcher obtains `hermes --print-runtime-command` and fails closed if the bootstrap contract changes. It neither installs Hermes nor repairs/configures it. `python3 scripts/ai_decision_lab.py run ...` also works when invoked in a correctly provisioned Hermes environment.

`--no-research` and `--no-critic` provide simpler comparison arms. The input's `max_calls` still bounds the declared call shape. A failed/blocked run exits nonzero and reports its saved path when persistence succeeded. `--now` is a test/diagnostic clock, not a way to claim stale observations are fresh; prospective runs should use the actual clock.

## Input contract (`schema_version=1`)

Money, quantities and weights are finite **decimal strings**, never binary floats. Times are timezone-aware ISO8601. Identifiers are preserved exactly, not silently normalized.

| Field | Meaning |
|---|---|
| `run_id` | Unique portable identifier; duplicates/path traversal rejected |
| `mode` | `shadow` or explicitly non-prospective `historical_diagnostic` |
| `as_of`, `expires_at` | Evidence decision time and expiry; expiry later than as_of |
| `mandate_version` | Versioned experimental mandate identifier |
| `universe` | Nonempty unique `{symbol, asset_class}` array; equity/etf/crypto |
| `portfolio` | `{snapshot_id, observed_at, cash, equity, pending_buy_notional, positions}` |
| `portfolio.positions` | `{symbol, quantity, market_value}` per holding; all holdings included in universe |
| `evidence` | `{evidence_id, kind, source, published_at, available_at, expires_at, symbols, content}` records |
| Evidence kinds | news, scheduled_event, technical, algorithmic, account, research |
| Optional evidence | Finite numeric `metrics`; scheduled `event_at` is distinct from publication/availability |
| Optional limits | `max_snapshot_age_seconds` defaults300; `max_calls` defaults3 |

Cash plus all holding values must reconcile to authoritative equity within $0.05 for rounding; buying power is never spend authority. Negative cash is accepted as an incident to diagnose, not permission to add risk. Capture conversion requires explicit asset-class mappings and complete sell-only open-order evidence; unsupported pending buys are rejected rather than assigned an invented zero reservation.

For evidence, `published_at <= available_at <= as_of`, and expiry follows availability. A scheduled future event may occur after `as_of`, but its outcome cannot be reported as already known. News content is untrusted data. Current evidence/snapshot and bundle/proposal expiry are rechecked after model calls. Historical diagnostics retain original availability and evaluate at original `as_of`; they are never counted as prospective forecasts.

Input acquisition is deliberately separate: see [the standalone prospective-evidence collector](PROSPECTIVE_EVIDENCE.md) for sourced metadata/news, scheduled-event and completed-session chart/quant inputs. Neither collector nor lab installs continuous detectors, fetches paid feeds, creates a new broker account, simulates fills/P&L, or operates exits/watchdogs.

## Decisions and deterministic money handling

Default roles: research synthesizes supplied evidence; CIO chooses a complete target portfolio including cash; critic can veto it. Model output must match the role contract in each saved prompt. Targets contain every universe symbol in input order (including zero weights), a thesis, known applicable citations and a falsifier. Hold must match actual current holdings; abstention is preserved.

Code—not the model—validates finite weights, timestamps, exact symbols, citations, aggregate capital and cash constraints. Total weights cannot exceed1 even by a small tolerance. Position values round down to cents; residuals stay cash. Existing negative cash/pending buys block added risk. Rotations never assume reductions have filled: separate funding/revalidation is required. This is a target preview, not executable quantities or broker protection.

## Allowance policy

The adapter requires the exact authenticated model in the catalog, the expected Nous HTTPS origin, a verified OAuth runtime and a fresh account read. Usage-window plans require server-reported Keep-going-on-credits=false; unknown/on is blocked without changing settings. Legacy credit-grant plans require enough existing subscription credits for a deliberately conservative per-request bound based on live catalog pricing; that preflight is **not an atomic reservation against other consumers**. Missing entitlement, pricing or access blocks inference; provider refusals are not retried on a paid fallback. There is no invented per-request billing override header.

Receipts retain available request ID/model/token counts/latency. Unknown actual cost remains unknown. Token-based estimates and account balance changes during concurrent usage are not claimed as exact per-run charges. Provider error bodies, HTTP headers, credentials and private chain-of-thought are not persisted.

## Persistence/replay

Runs atomically reserve `OUTPUT/RUN_ID` and refuse overwrites. Saved files include normalized input, role prompts/responses/receipts, `decision.json`, and SHA256 manifest with input/prompt/code/artifact fingerprints. Invalid input has a minimal sanitized failure record rather than unsafe raw content. Model records retain only the public role-contract fields (including allowlisted target fields); unexpected/private-reasoning fields are excluded before any write. Malformed model output is represented by a content fingerprint/size and safe receipt, not raw text.

Replay verifies safe flat artifact names, no symlinked run/artifacts, hashes and exact artifact set, input hash/schema and non-executable invariant. Where valid saved role responses exist, it revalidates them and recomputes the policy decision/preview. Early failures/blocks explicitly report `verification_kind: integrity_only`. Hashes are corruption/change checks, not a cryptographic signature against an attacker rewriting an entire run. Persistence failure must never lead to inference/order submission without a record.

## Verification

```bash
# Use the project's Python environment with requirements-dev.txt installed.
python -m pytest -q
python -m pytest --cov=decision_lab --cov-report=term-missing \
  scripts/test_ai_decision_lab.py scripts/decision_lab
```

Tests use explicitly labelled synthetic fixtures, not fabricated broker/model results. Coverage includes availability/expiry, scheduled events, symbols/citations, cash/allocation rounding, critic veto, invalid JSON/nonfinite output, quota/provider failures, call limits, duplicate runs, persistence and tampering. Real-inference smoke results are separate from tests and cannot establish profitable or future-blind performance.
