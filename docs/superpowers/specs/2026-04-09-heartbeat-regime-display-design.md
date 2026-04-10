# Design: Agent Heartbeat Panel + Regime Display Improvements

**Date:** 2026-04-09
**Wishlist items:** #6 (agent heartbeat panel), #7 (current regime prominently displayed)
**Scope:** Dashboard-only. No backend changes. All data already in Redis and polled every 2s.

---

## What Exists Today

**Heartbeat panel:** A horizontal `flex-wrap` row inside `dashboard_live.html.heex` (lines 98–115). Each agent is rendered as a tiny 8×8px colored dot + name + age text. Status logic (`heartbeat_status/2`, `heartbeat_dot/1`, `heartbeat_age/1`) and per-agent thresholds are already implemented in `dashboard_live.ex`. The visual issue: a stale agent's red dot is easy to miss at a glance.

**Regime card:** One of seven stat cards in the top grid. Shows emoji + name (e.g. `📈 UPTREND`) and `ADX 28.4` below. Helper functions `regime_emoji/1`, `regime_name/1`, `adx_value/1` exist. The `trading:regime` Redis key contains `plus_di` and `minus_di` but they are not displayed. No color coding on the card.

---

## Approved Design

### Feature 6 — Heartbeat Panel: Per-Agent Grid Cards

Replace the horizontal `flex-wrap` row with a `grid grid-cols-5 gap-3` layout. Each agent gets its own card with background and border color that reflects its status.

**Card states:**

| Status | Background | Border | Dot | Name color | Age color |
|--------|-----------|--------|-----|-----------|-----------|
| `:ok` | `bg-gray-900` | `border-gray-700` | `bg-green-500` | `text-gray-200` | `text-gray-600` |
| `:warning` | `bg-amber-950/20` | `border-amber-800` | `bg-yellow-500` | `text-amber-200` | `text-amber-900` |
| `:stale` | `bg-red-950/20` | `border-red-900` | `bg-red-500` | `text-red-300` | `text-red-900` |

Each card shows: dot (10×10px) centered, agent name (bold, 12px), age text (10px) below.

**No changes** to `heartbeat_status/2`, `heartbeat_age/1`, or `@heartbeat_thresholds` — logic is correct, only the template changes.

New helper needed: `heartbeat_card_classes/1` — private `defp`, returns a 4-tuple `{bg_class, border_class, name_class, age_class}` of Tailwind strings, keyed by status atom. Tested via rendered HTML (private functions not callable from tests directly).

### Feature 7 — Regime Card: Accent Border + +DI/-DI

Keep the card in the top stats grid at the same position. Two changes:

1. **Colored left border:** Tailwind pattern `border border-gray-700 border-l-4 border-l-{color}` — all sides get 1px gray-700, left side overrides to 4px colored. Colors: `border-l-green-500` (UPTREND), `border-l-red-500` (DOWNTREND), `border-l-gray-600` (RANGING/nil).
2. **+DI/-DI row:** Add a second subtext line below `ADX X.X`: `▲ +DI 22.1 · ▼ -DI 14.3`. +DI renders in `text-green-500`, -DI in `text-red-400`.

New helpers needed (all private `defp`, tested via rendered HTML):
- `regime_border_class/1` — returns the full left-border Tailwind class string (e.g. `"border-l-green-500"`) for a regime map.
- `plus_di_value/1` — extracts `plus_di` float from regime map; returns `nil` if absent (mirrors `adx_value/1`).
- `minus_di_value/1` — extracts `minus_di` float from regime map; returns `nil` if absent.

---

## File Changes

| File | Change |
|------|--------|
| `dashboard/lib/dashboard_web/live/dashboard_live.html.heex` | Agents panel: replace flex row with grid. Regime card: add border-l-4 class + +DI/-DI row. |
| `dashboard/lib/dashboard_web/live/dashboard_live.ex` | Add `heartbeat_card_classes/1`, `regime_border_class/1`, `plus_di_value/1`, `minus_di_value/1`. |
| `dashboard/test/dashboard_web/live/dashboard_live_test.exs` | Add tests for new helpers and rendered HTML. |

No changes to `redis_poller.ex`, `redis_subscriber.ex`, screener, or any Python agent.

---

## Tests

Follow TDD. Write failing tests first, then implement.

All helpers are private `defp` — tested exclusively via rendered HTML.

**Rendered HTML tests (via `send(view.pid, {:state_update, state})` + `render(view)`):**

- When executor heartbeat is stale, rendered HTML contains `border-red-900`
- When PM heartbeat is warning, rendered HTML contains `border-amber-800`
- When regime is UPTREND, rendered HTML contains `border-green-500` and `+DI`
- When regime is DOWNTREND, rendered HTML contains `border-red-500`
- When regime is RANGING, rendered HTML contains `border-gray-600`
- When regime is nil, rendered HTML does not crash and shows `—` for +DI/-DI

---

## Out of Scope

- No pulse/animation on stale agents
- No tooltip with threshold details
- No "last X trades" or position data on agent cards
- No layout changes beyond the agents panel and regime card
