# Dashboard Layout + Signal Scores Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Surface signal scores on the watchlist panel and rebalance the two-column layout so the right column fills its empty space.

**Architecture:** Template-only layout restructure plus a sort in the LiveView `handle_info` callback. No new Redis keys, no new agents, no schema changes.

**Tech Stack:** Phoenix LiveView, Tailwind CSS, ExUnit

---

## 1. Layout Restructure

### Problem

The main two-column grid (`lg:grid-cols-2`) places all variable-length panels in the left column and two short fixed panels in the right column. On a busy trading day with several positions and a long watchlist, the left column can be 3–4× taller than the right, leaving a large blank area.

**Current left column:** Open Positions → Watchlist → Cooldowns (conditional) → Drawdown Attribution (conditional)
**Current right column:** Live Signal Feed → Daily Performance

### Fix

Move the two conditional panels from the left column to the right column. They appear only when non-empty, so they naturally fill whatever space the right side has available.

**New left column:** Open Positions → Watchlist
**New right column:** Live Signal Feed → Daily Performance → Cooldowns → Drawdown Attribution

No breakpoint changes. No new CSS classes. The two panels move from one `<div class="space-y-4">` to the other.

---

## 2. Signal Score on Watchlist

### Data

`signal_score` (float, 0–90) is already attached to every entry signal by the watcher (v0.35.0) and published in the `trading:watchlist` Redis key. The dashboard already reads this key into `@watchlist`. No backend changes needed.

### Sort

Before assigning `@watchlist`, sort items descending by `signal_score`. Items with no score sort last. This happens in `dashboard_live.ex` in the `handle_info({:state_update, state}, socket)` callback, in the same place positions are filtered.

```elixir
watchlist =
  (state["trading:watchlist"] || [])
  |> Enum.sort_by(fn item -> item["signal_score"] || -1 end, :desc)
```

The existing `visible_watchlist` filter (exclude symbols already in positions) happens in the template and is unchanged.

### Display

Each watchlist row gains a score badge between the tier badge and the indicator values.

**Score color thresholds:**
- `≥ 70` — green (`text-green-400`)
- `50–69` — yellow (`text-yellow-400`)
- `< 50` — gray (`text-gray-500`)
- absent/nil — `—` in gray

**Row layout (before):**
```
[symbol w-16] [tier badge] · RSI X.X · IBS X.X · DCH X.X
```

**Row layout (after):**
```
[symbol w-16] [tier badge] [score] · RSI X.X · IBS X.X · DCH X.X
```

The score is a plain `<span>` with `font-mono font-bold w-6 text-right` so it aligns across rows.

A tooltip explains the score: "Signal quality score (0–90). Higher = stronger setup. Factors: tier, RSI-2 depth, regime, SMA-200 buffer."

---

## 3. Files Changed

| File | Change |
|------|--------|
| `dashboard/lib/dashboard_web/live/dashboard_live.html.heex` | Move Cooldowns + Attribution blocks to right column `<div>`; add score badge to watchlist row |
| `dashboard/lib/dashboard_web/live/dashboard_live.ex` | Sort `@watchlist` descending by `signal_score` before assign |
| `dashboard/test/dashboard_web/live/dashboard_live_test.exs` | Tests for score badge display, score sorting, score-absent fallback, panel placement |

---

## 4. Testing

**Score display tests:**
- Item with `signal_score: 82.0` renders `"82"` in green
- Item with `signal_score: 55.0` renders `"55"` in yellow
- Item with `signal_score: 30.0` renders `"30"` in gray
- Item with no `signal_score` key renders `"—"`

**Sort test:**
- State with three items (scores 30, 82, 55) assigns `@watchlist` in order [82, 55, 30]

**Layout test:**
- State with non-empty cooldowns renders the Cooldowns panel in the DOM (panel presence already tested; no new structural assertion needed beyond the existing cooldown panel tests)

All new tests follow TDD: write failing test, verify failure, implement, verify pass.

---

## 5. Edge Cases

- **Score is integer vs float:** `item["signal_score"]` from JSON decode may be `82` (integer) or `82.0` (float). Use `round/1` before display to always render as integer string: `"#{round(score)}"`.
- **Score is 0:** Treated as `< 50`, renders gray `"0"`. Not hidden.
- **Watchlist empty:** No change — existing "No watchlist items" fallback unchanged.
- **Cooldowns and Attribution both absent:** Right column shows only Signal Feed + Daily Performance — same as current behavior, no empty placeholder rendered.
