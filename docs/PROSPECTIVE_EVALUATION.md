# Continuous prospective evaluation

Research-only repeated checkpoints and later price observations. Every result remains `executable=false`. This is neither an order writer nor a persistent broker portfolio simulator. Existing trading containment remains authoritative. See [the implementing contract](PROSPECTIVE_EVALUATION_SPEC.md) and [source contracts](research/PROSPECTIVE_EVALUATION_SOURCE_CONTRACTS.md).

## What the study measures

A frozen campaign compares zero-yield cash, same-universe equal weighting, SMA20 slices and the existing three-role AI policy. One shared evidence bundle supplies all arms. Each cohort starts from an explicitly synthetic cash book; the example100000.00 is an arithmetic starting value, **not broker cash, an account reset, a spending cap or recommended capital**.

Decisions finish after a returned extended-session close and before the next trading day's New York midnight. The entry proxy is that next day's first condition-eligible IEX daily OHLC price. It is **not**09:30/official open, a quote or a broker fill. Horizons1/5 include the anchor session as1; the provider calendar supplies trading dates/early closes. Decisions cannot buy retrospectively at a known close or backfill a missed slot.

Outputs are raw, unadjusted **price responses**, not dividends/splits-adjusted total return, executable P&L or after-cost alpha. Corporate actions remain unchecked and economic scoring unavailable. Overlapping cohorts are correlated, not independent samples or a compounded equity curve. Failed/blocked/abstained AI is not silently converted to cash; missing symbol prices are not forward-filled or redistributed. First observations and later revision/missingness states remain distinguishable.

## Runtime and authority prerequisites

- Pinned reviewed source, Python3.13 project environment and `requirements-evidence.txt`. Linux/macOS POSIX locking; no Windows support claim.
- Existing `hermes` installation and permitted Nous authentication for the exact registered model. No provider fallback, signup, upgrade/top-up or global configuration change.
- Existing fixed-host GET helper reachable through the `openboog` SSH alias. Credentials stay there; only allowlisted market/news/calendar GETs are available. Public BEA retrieval carries no Alpaca headers. A development helper in a cache path is **not** a durable unattended deployment.
- One owner host/writer per campaign. `flock` coordinates cooperating local processes; it is not a distributed lock across synced copies or protection against a privileged writer replacing files.
- A persistent **private** runtime directory, not a24-hour scratch cache. Do not run another writable copy through Syncthing/Obsidian. Publish sealed permitted snapshots/receipts to the canonical private vault for cross-portal handoff; do not treat an actively syncing partial copy as a second runner.

Use an existing project environment. For a new isolated environment, with `uv` already installed:

```sh
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements-evidence.txt
```

The project interpreter owns evidence/campaign/scoring. Only the existing pure decision-lab CLI runs in Hermes' managed SDK subprocess. Hermes' dependency closure need not contain `icalendar`; never merge incompatible site-packages or install project packages into the global SDK as a workaround. The convenience `evaluate_prospective_hermes.py` entrypoint stays in the project interpreter and delegates to the same CLI.

## Register before collecting decisions

Review/copy `docs/examples/prospective-evaluation.example.json`; choose a new portable campaign ID for a new experiment. The request has no caller-provided registration/freeze time, outcomes, imported run or clock override. The runtime records actual registration and source fingerprints. Model/recipe/universe changes require another campaign, not edits to the registered protocol.

Choose a persistent private single-owner directory first. Example below uses local unsynced runtime storage; publish verified results separately to the canonical private vault:

```sh
export CAMPAIGNS="$HOME/.local/share/t2-research/campaigns"
.venv/bin/python scripts/evaluate_prospective.py register \
  --protocol docs/examples/prospective-evaluation.example.json \
  --output-dir "$CAMPAIGNS"
export CAMPAIGN="$CAMPAIGNS/daily-iex-research-v1"
```

Registration and records are create-once. A hash manifest checks integrity, not adversarial non-repudiation or completeness of an upstream feed. Never rewrite hashes/IDs to make a failed verification look green.

## One bounded tick

```sh
.venv/bin/python scripts/evaluate_prospective.py tick "$CAMPAIGN"
```

No timer is installed. A tick observes or advances one900-second bucket under the campaign lock. Repeating a completed bucket returns its saved receipt without source/inference repetition. A due decision reserves state before calling the model; interrupted ambiguous work is recovered or marked in-doubt, never blindly charged again. Source/model failures remain visible. Daily cohorts are bounded by the registered checkpoint count and campaign lifetime.

A tick outside an admissible window can correctly return waiting/observed. If the next anchor midnight has elapsed, it cannot create yesterday's forecast, even if the regular market has not opened yet. If future horizons have not matured, pending is the correct result—not a fixture-derived success. No CLI option overrides the clock.

Local caller request/receipt bounds bracket credential-isolated SSH calls. Remote machine clocks must not be compared as though they were the collector's clock. These bounds are not exact upstream HTTP-dispatch or publication timestamps; publication remains unknown where the source does not supply it. Clock regression or invalid provenance fails closed; no skew tolerance manufactures availability.

Exit0 means the tick was processed, not that AI accepted risk, a future outcome exists or the policy made money. Inspect status/reasons and report quality. Unavailable/failed/in-doubt results are not investment recommendations.

## Offline proof and reporting

```sh
.venv/bin/python scripts/evaluate_prospective.py verify "$CAMPAIGN"
.venv/bin/python scripts/evaluate_prospective.py report "$CAMPAIGN"
```

These commands do not call a source/model. They verify stored identities/hashes, reconstruct observations and collections, replay the decision, recompute controls and score supported future observations. Reports preserve coverage, failures/missed slots, first/latest revisions, measured token/latency receipts and absent billed cost as **unknown**. Token usage is not a billing receipt; no actual after-cost claim follows from a price-table estimate or a subscription balance delta.

Keep actual development/source/model diagnostics separate from pre-registered prospective checkpoints. Synthetic future fixtures belong only in tests. A real source-to-model smoke is plumbing evidence, not profitable autonomy.

## Activation and promotion remain separate

This document describes bounded commands, **not permission to install a daemon/cron, clear HOLD, restart the old bot, deploy by SSH, reset an account or trade**. A lasting read-only research timer still needs reviewed source/runtime provisioning, a durable helper/store, single-writer ownership and explicit operational activation. Broker-writing autonomy and financial promotion remain separately gated. Successful tests, model prose and a few raw-price responses do not close those gates.
