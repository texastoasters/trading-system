# Remember

## v0.34.0 — Settings page: expand overrideable config + per-field override border

Expands the Settings LiveView from 10 fields across 3 cards to 47 inputs across 11 cards, covering nearly every hot-reloadable constant in `scripts/config.py`. Each input that currently differs from its baked-in default receives a yellow border matching the existing "Active overrides" indicator.

### Shipped

**`scripts/config.py` — `_SPEC` expansion (28 new scalar validators + 3 dict validators + 4 cross-checks)**
- Added scalar entries to `_SPEC`: `RSI2_SMA_PERIOD`, `RSI2_ATR_PERIOD`, `HEATMAP_DAYS`, `DIVERGENCE_WINDOW`, `MIN_VOLUME_RATIO`, `MAX_EQUITY_POSITIONS`, `MAX_CRYPTO_POSITIONS`, `EQUITY_ALLOCATION_PCT`, `CRYPTO_ALLOCATION_PCT`, `ATR_STOP_MULTIPLIER`, `DAILY_LOSS_LIMIT_PCT`, `MANUAL_EXIT_REENTRY_DROP_PCT`, `ATTRIBUTION_MAX_LOOKBACK_DAYS`, `IBS_ENTRY_THRESHOLD`, `IBS_MAX_HOLD_DAYS`, `IBS_ATR_MULT`, `STACKED_CONFIDENCE_BOOST`, `DONCHIAN_ENTRY_LEN`, `DONCHIAN_EXIT_LEN`, `DONCHIAN_MAX_HOLD_DAYS`, `DONCHIAN_ATR_MULT`, `ADX_PERIOD`, `ADX_RANGING_THRESHOLD`, `ADX_TREND_THRESHOLD`, `BTC_FEE_RATE`, `BTC_MIN_EXPECTED_GAIN`, `EARNINGS_DAYS_BEFORE`, `EARNINGS_DAYS_AFTER`. Each row carries `(cast_fn, min, max)`; load_overrides skips out-of-range values and logs via the existing warning path.
- Added dict validators for `TRAILING_TRIGGER_PCT`, `TRAILING_TRAIL_PCT` (per-tier floats keyed `"1"/"2"/"3"`) and `DAEMON_STALE_THRESHOLDS` (per-daemon ints). Helpers `_trail_cast`/`_trail_check` enforce `{tier → float in (0, 50]}`; `_daemon_cast`/`_daemon_check` enforce `{daemon name → int in [1, 1440]}`. Partial payloads merge over the defaults; malformed shapes are rejected whole.
- Added four cross-check blocks after the per-field loop:
  - **Drawdown ascending** (pre-existing, preserved): `CAUTION < DEFENSIVE < CRITICAL < HALT`.
  - **ADX thresholds**: `ADX_RANGING_THRESHOLD < ADX_TREND_THRESHOLD` — skipped together if violated so regime classification cannot flip-flop.
  - **Donchian lens**: `DONCHIAN_EXIT_LEN < DONCHIAN_ENTRY_LEN` — skipped if violated (tight exit on shorter lookback than entry).
  - **Allocation sum**: `EQUITY_ALLOCATION_PCT + CRYPTO_ALLOCATION_PCT == 1.0` — all-or-nothing; single-side override rejected so the invariant can't drift. Tested with a separate happy-path case that overrides both sides together.
- **Explicitly excluded from the UI** (user decision): `DONCHIAN_SYMBOLS` (static curated list, wishlist #56 plans automated promotion), `INITIAL_CAPITAL`, `MAX_AUTO_RESTARTS`, `PDT_MAX_DAY_TRADES`.

**`dashboard/lib/dashboard_web/live/settings_live.ex` — rewrite**
- New module attributes: `@scalar_defaults` (38 keys), `@dict_defaults` (3 nested maps), `@float_keys`, `@int_keys`, `@tiers ~w(1 2 3)`, `@daemons ~w(executor portfolio_manager watcher)`.
- Public helpers exposed for the template only: `tiers/0`, `daemons/0`. (Unused module-private accessors were dropped to keep coverage at 100%.)
- `mount/3` returns `{form_params, dict_params, overridden, has_overrides}` where `overridden` is a `MapSet` of keys present in the stored Redis JSON that match a known default.
- `handle_event("save", ...)` routes through a single `with` pipeline: `parse_scalars → parse_tier_dict(TRAILING_TRIGGER_PCT) → parse_tier_dict(TRAILING_TRAIL_PCT) → parse_daemon_dict(DAEMON_STALE_THRESHOLDS) → validate_drawdown → validate_adx → validate_donchian → validate_allocations → validate_trailing`. Any failure short-circuits with a flash error and Redis is not touched.
- `input_class/2` is the single point that computes each input's class string: base classes + `border-yellow-400` if `MapSet.member?(overridden, key)`, otherwise base + `border-gray-600`. Called from every input in the template so override status is visually per-field, not global.
- `load_config/0` handles: missing key (all defaults, empty overridden), malformed JSON (fallback), non-map dict value (fallback for that dict only), partial dict overrides (merged field-by-field with base defaults). All four paths are covered.

**`dashboard/lib/dashboard_web/live/settings_live.html.heex` — new 11-card layout**
- Cards, in order: RSI Strategy (9 inputs, extended), IBS Strategy (3), Donchian Breakout (4), ADX Regime (3), Position Limits (6, extended), Risk Management (5), Crypto (BTC) (2), Earnings Blackout (2), Trailing Stops per tier (6, 2 dicts × 3 tiers), Daemon Stale Thresholds (3), Drawdown Thresholds (4, existing).
- Every input calls `class={DashboardWeb.SettingsLive.input_class(@overridden, "KEY")}`. For dict inputs, the class key is the dict's top-level name (e.g. `TRAILING_TRIGGER_PCT`), so overriding any tier paints all three inputs for that dict yellow — consistent with how the override entered Redis (as one JSON object).
- Nested input names use bracket syntax that Phoenix parses directly into nested maps: `config[TRAILING_TRIGGER_PCT][1]`, `config[DAEMON_STALE_THRESHOLDS][executor]`. No flattening helper needed.
- `<` inside descriptions uses the `{"<"}` HEEx expression form so Phoenix escapes to `&lt;` on render; test assertions pin this exact escaped form.

### Design decisions locked
- **Per-field border, not per-card**. The top-right "Active overrides" badge stays as a global indicator; each overridden input then gets its own yellow border. Non-overridden inputs in a card with overrides show only `border-gray-600`.
- **Allocation override is all-or-nothing**. Changing just `EQUITY_ALLOCATION_PCT` without also changing `CRYPTO_ALLOCATION_PCT` breaks the sum-to-1.0 invariant, so `load_overrides` rejects single-side overrides and the LiveView validator flashes "must sum to 1.0". Users must submit both sides together.
- **Dict shape errors reject the dict, not the whole payload**. Non-map or malformed dict entries in Redis fall back to defaults for that dict only; other overrides survive.
- **`DONCHIAN_SYMBOLS` stays code-only for now**. Alt-strategy research showed only 7 names profitable for Donchian-BO; exposing the full universe as checkboxes would invite misconfiguration. Wishlist item #56 plans automated promotion/demotion from the monthly refit, which is the right long-term home.
- **No `reset per field`**. The "Reset to Defaults" button wipes the entire `trading:config` key; there's no per-field reset. Matches the current mental model: overrides are one JSON object, not a grab bag.

### Tests added (+29 Elixir → 45 in settings_live_test; +63 Python in test_config → 180; full suite 424 passing, 100% coverage on 9 Python modules; 100% coverage on settings_live.ex)
- Python: `TestLoadOverridesExpandedScalars` with per-key happy-path + out-of-range tests (parametrized over all 27 scalar keys); ADX/Donchian/allocation cross-check tests; `TestLoadOverridesDicts` with 11 cases across the three dict keys.
- Elixir: `expanded scalar fields` (render assertions for all 28 new scalar inputs, grouped by card), `expanded dict fields` (nested name assertions), `expanded field descriptions` (one assertion per description string, matching escaped form), `per-field override border` (MapSet-driven class assertions with both `assert` and `refute` regex), `save expanded form` (7 cases: happy-path writes, dict shape, trailing trail≥trigger, allocation sum, ADX order, Donchian order).
- Coverage fill tests: tier-dict trailing-garbage + non-numeric; daemon-dict trailing-garbage + non-numeric; non-map dict value in Redis falls back to defaults; added `test_exited_today` in test_config to close the last Python gap.

### Followups
- `DONCHIAN_SYMBOLS` dynamic promotion (auto-promote/demote from monthly refit instead of the static curated 7) remains on the wishlist.
- No existing FEATURE_WISHLIST.md entry for this settings expansion; it was an ad-hoc user request, not a tracked item.
