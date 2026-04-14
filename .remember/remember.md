# Handoff

## State
feat/langalpha-research-layer branch open. Wishlist updated with "Research & Learning" section. Two plan files committed:
- docs/superpowers/plans/2026-04-14-langalpha-spike.md — local evaluation spike (no code, just run LangAlpha + judge output quality)
- docs/superpowers/plans/2026-04-14-langalpha-integration.md — full integration plan (export_trades.py, ingest_research.py, supervisor hooks, VPS deploy)

## Next
1. cpr this branch (docs-only, no review needed)
2. Run the spike — clone LangAlpha locally, export 30d of trades, write custom skill, judge output quality
3. Fill in spike-findings.md, then decide: proceed to Plan B or discard

## Context
Daytona not required — LangAlpha runs in Docker sandbox locally. ANTHROPIC_API_KEY from ~/.trading_env. Supervisor has two TODO LLM hooks (supervisor.py:551, supervisor.py:621) that Plan B wires up. supervisor.py has no LLM calls today — RSI thresholds and tiers are fully static.
