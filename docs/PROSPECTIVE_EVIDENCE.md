# Read-only prospective evidence

Standalone source collection for the [non-executing AI decision lab](AI_DECISION_LAB.md).
This is not an unattended trader, broker reconciliation, or evidence of profitable decisions.
No order submission, broker reset, production restart, or containment override exists here.

## Setup

Run from the repository root. Requires Python 3.13, `uv`, SSH access to the existing
`openboog` host, and its already configured Alpaca credentials. No new paid feed or signup.

```sh
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements-evidence.txt
ssh openboog 'mkdir -p ~/.hermes/cache/scratch'
scp scripts/prospective_evidence/transport.py openboog:.hermes/cache/scratch/t2_evidence_get.py
```

The remote helper reads literal `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` assignments
from `~/.trading_env`, with or without `export`. It does not source/evaluate the file.
Credentials stay on that host. The fixed-host adapter sends only allowlisted GET requests;
Alpaca credentials are never forwarded to BEA. Redirects are rejected, not followed.
Installing this standalone cache helper is not installing or restarting the trader.

## Declare an experiment, then collect

Copy `docs/examples/prospective-research.example.json` to your experiment file.
Choose a **new unique `collection_id`** before every collection: identities are immutable
and an existing directory is not overwritten. Supply exact symbols and equity/ETF classes.

The example declares a **synthetic cash-only research starting book**. Its 100000.00 is
experimental capital, not observed broker cash, a spending cap, account creation/reset,
fills, returns, or a claim the held legacy account is reconciled. Repeated cash-only books
are independent research starting points, not a persistent simulated portfolio.
Alternatively provide `portfolio` instead of `research_cash`; its original observation
time is retained and must satisfy existing lab freshness rules. Never supply both.

For a later collection, optional `previous_events` must contain the **entire normalized
calendar snapshot** from a previously verified collection, not just its selected future
evidence. For example, after running `verify` on the prior collection:

```python
import json
from pathlib import Path

prior = json.loads(Path("artifacts/prospective/OLD-ID/capture.json").read_text())
path = Path("/path/to/experiment.json")
experiment = json.loads(path.read_text())
experiment["previous_events"] = prior["derived"]["normalized_calendar_events"]
# Also choose a NEW collection_id before collecting again.
path.write_text(json.dumps(experiment, indent=2))
```

The new capture records UID additions, changed normalized records and removals relative
to that declared snapshot. A missing UID means absent from the observed full snapshot,
not proof of authoritative cancellation. Prior events are bounded and validated; they
never supply release outcomes or retimestamp a portfolio. The complete experiment file,
including this snapshot, must fit the CLI's 64 KiB input bound.

```sh
.venv/bin/python scripts/collect_prospective_evidence.py collect \
  --experiment /path/to/experiment.json --output-dir artifacts/prospective
```

Success produces `bundle.json`, projected `capture.json`, and `manifest.json` under the
chosen collection identity. Model calls are **separate**: feed the resulting bundle through
the existing lab launcher documented in `AI_DECISION_LAB.md`, subject to its actual
provider entitlement and budget gates. Collection itself does not call a model.

Incomplete required sources or invalid derived data produce a nonzero result, not a
substitute dataset. A safely identifiable failed collection retains a sanitized partial
capture and failed manifest, with no runnable bundle. Storage failure is not durable
success; a leftover pending directory is not a published collection.

## What is and is not observed

- Alpaca sessions establish the real extended `session_close`. Daily bars qualify only
  when requested after that close. The bar timestamp labels midnight New York, **not**
  publication or first availability. Receipt clocks determine observed availability.
- Bars use explicit `feed=iex`, `adjustment=raw`, `asof=-`, `currency=USD`, `timeframe=1Day`.
  IEX is not consolidated coverage. Raw price changes are not split/dividend-adjusted
  total returns; neither candle patterns nor numerical features demonstrate alpha.
- Last 21 completed sessions support candle classification, SMA20, 20-session raw close
  change, price-relative and volume-relative features. Zero-volume denominators are
  unavailable, not invented zero scores. Partial current-session bars are excluded.
- Metadata-only news retains headline, IDs, created/updated timestamps, source, symbols
  and URL, never licensed article bodies. All pages are consumed through a successful
  null terminal token; a short page is not completion. Empty observed queries do **not**
  establish that no relevant news exists or that coverage is exhaustive.
- BEA's official ICS supplies exact scheduled release times, not outcome forecasts.
  Full event snapshots support UID additions, revisions and removals. Unknown publication
  time stays unknown; `DTSTAMP` is not a publication timestamp. Unsupported recurrence
  or cancelled schedules fail closed. Source SUMMARY whitespace is preserved exactly.
- Model context uses a declared 30-calendar-day future-event horizon while retaining the
  full snapshot for reconstruction. This is a fixed context-cost policy, not learned alpha.
  BLS access and exact future FOMC decision-time support remain explicit gaps; no guessed
  weekday/time is substituted.

Every external title/headline is untrusted evidence, never instructions, tools, endpoints,
credentials or trading policy. Retention of metadata is not permission to redistribute
licensed source data. Keep private captures private and honor the account's source terms.

## Verify offline

```sh
.venv/bin/python scripts/collect_prospective_evidence.py verify \
  artifacts/prospective/YOUR-COLLECTION-ID
```

Semantic verification recomputes normalized evidence and the bundle from frozen projected
source pages and the declared experiment, then compares the result. It needs neither
network nor model inference. Lower-level `archive.load_collection()` verifies only file
integrity; it is **not** semantic reconstruction. Decision replay remains a separate lab
operation and must also be exercised.

Hashes do not authenticate the upstream provider, recover omitted article content, prove
market/news completeness, or protect against a malicious rewrite of every artifact and
manifest. Saved raw-body hashes are receipt provenance: projected payloads cannot be used
to recompute an omitted original HTTP body's byte hash.

## Exercised development checkpoint

On 2026-11-16 CST, run `prospective-20261117T031900Z` collected actual SPY/QQQ/IWM/AAPL
inputs: 5 news metadata records, completed-session chart/quant evidence per symbol and 8
BEA releases within the declared horizon. Existing Nous OAuth/GPT-6.1-Sol completed
research/CIO/critic; the validated result held 100% of the explicit synthetic book in cash.
No orders were submitted. Both offline semantic reconstruction and deterministic decision
replay succeeded. Three calls recorded 28,919 input / 2,293 output tokens; exact billed cost
remains unknown. This proves integration, not trading edge, forward returns or continuous
operation. Final reviewed-head test/CI evidence belongs in the PR, not this dated checkpoint.
