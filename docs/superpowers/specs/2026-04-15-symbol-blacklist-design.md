# Symbol Blacklist Design

**Date:** 2026-04-15  
**Status:** Approved

## Overview

Operator-driven mechanism to permanently exclude a symbol from trading. Blacklisting sells any open position and prevents re-entry until the operator explicitly removes the symbol. Lives on the Universe page of the Phoenix LiveView dashboard.

## Data Model

Extend the existing `trading:universe` Redis JSON with a top-level `"blacklisted"` key:

```json
{
  "tier1": ["SPY", "QQQ", ...],
  "tier2": ["GOOGL", ...],
  "tier3": ["IWM", ...],
  "blacklisted": {
    "OKE": { "since": "2026-04-14", "former_tier": "tier3" }
  }
}
```

- Symbol is removed from its tier array and added to `blacklisted` dict atomically (read-modify-write under a Redis lock or single JSON SET).
- `former_tier` stored so unblacklisting restores the symbol to its original tier without manual lookup.
- `since` stored as ISO date string for display in the dashboard.

## Backend — Python

### New helpers in `scripts/config.py` or a new `scripts/universe.py`

**`blacklist_symbol(r, symbol)`**
1. Read and parse `trading:universe` JSON.
2. Find which tier array contains `symbol`; record as `former_tier`.
3. Remove `symbol` from that tier array.
4. Add entry to `blacklisted` dict: `{symbol: {"since": today_iso, "former_tier": former_tier}}`.
5. Write updated JSON back to `trading:universe`.
6. Publish sell signal to `trading:signals`: `{"symbol": symbol, "action": "sell", "reason": "blacklisted"}`.

**`unblacklist_symbol(r, symbol)`**
1. Read and parse `trading:universe` JSON.
2. Look up `former_tier` from `blacklisted[symbol]`.
3. Append `symbol` to the appropriate tier array.
4. Remove `symbol` from `blacklisted` dict.
5. Write updated JSON back to `trading:universe`.

### Watcher belt-and-suspenders

In `skills/watcher/watcher.py`, before generating any signal for a symbol, check that it is not in `blacklisted`. If it is, skip — this guards against a race where the watcher processes a stale watchlist entry after blacklisting.

`get_active_instruments(r)` already ignores non-tier keys, so blacklisted symbols are automatically excluded from screener and PM without further changes.

## Dashboard — Elixir/Phoenix

### LiveView assigns

```elixir
%{
  tiers: %{tier1: [...], tier2: [...], tier3: [...]},
  blacklisted: %{"OKE" => %{"since" => "...", "former_tier" => "tier3"}, ...},
  positions: %{"OKE" => %{...}, ...},   # existing
  collapsed: %{"tier1" => false, "tier2" => false, "tier3" => true, "blacklist" => false}
}
```

### Events

| Event | Payload | Action |
|-------|---------|--------|
| `"toggle_section"` | `%{"id" => "tier3"}` | Flip collapsed state for that section |
| `"blacklist_symbol"` | `%{"symbol" => "OKE"}` | Call `blacklist_symbol/2`, reload universe assigns |
| `"unblacklist_symbol"` | `%{"symbol" => "OKE"}` | Call `unblacklist_symbol/2`, reload universe assigns |
| `"liquidate_confirm"` | `%{"symbol" => "OKE"}` | Existing liquidate logic, now behind modal |

### Confirmation modals

Both destructive actions show a LiveView modal before firing:

- **Blacklist:** *"Blacklist {SYMBOL}? Any open position will be queued for sale at next market open. Re-entry blocked until removed."* — Confirm / Cancel
- **Liquidate:** *"Liquidate {SYMBOL}? Submits a market sell order immediately."* — Confirm / Cancel

Modal state tracked in assigns: `%{confirm_modal: nil | %{action: atom, symbol: string}}`.

### Universe page layout

Each section (Tier 1, Tier 2, Tier 3, Blacklisted) is collapsible via a chevron toggle. Tier 3 starts collapsed by default.

**Tier section rows** — columns: Symbol, RSI(2) badge, Price, vs SMA(200)%, Position badge (if open), Blacklist button.

**Blacklisted section rows** — columns: Symbol (struck-through), Date blacklisted, Former tier badge, Pending-sell badge (shown if symbol still appears in `trading:positions`), Remove button.

RSI(2) badge colors: green if < 10, orange if > 70, gray otherwise.

## Sell Queue Mechanism

Blacklisting during market hours or outside market hours both route through the watcher's sell signal path (`trading:signals` → executor). The executor treats a `"blacklisted"` reason sell the same as any other sell signal — cancel stop, submit market sell, log trade.

If market is closed when blacklisted, the signal sits in the watcher's next evaluation cycle. At next market open the watcher picks up the signal and the executor processes it normally.

The `Pending sell` badge in the dashboard disappears automatically once the executor clears the position from `trading:positions` in Redis — no explicit badge state to manage.

## Error Handling

- If `blacklist_symbol` fails to find `symbol` in any tier array, return an error tuple; dashboard shows a flash alert "Symbol not found in universe".
- If Redis write fails, propagate error to LiveView flash.
- Unblacklist is idempotent: if symbol is already in target tier, no-op.

## Testing

**Python unit tests (`scripts/test_universe.py` or inline in existing test file):**
- `blacklist_symbol` removes from tier, adds to blacklisted dict, publishes sell signal
- `blacklist_symbol` on unknown symbol raises/returns error
- `unblacklist_symbol` restores to `former_tier`, removes from blacklisted
- `unblacklist_symbol` on non-blacklisted symbol is no-op / error

**Elixir unit tests (`test/dashboard_web/live/universe_live_test.exs`):**
- Blacklist button click → modal appears
- Modal confirm → `blacklist_symbol` called, symbol disappears from tier list
- Modal cancel → no change
- Liquidate button click → modal appears
- Symbol with open position in blacklist section → Pending sell badge visible
- Symbol with no position in blacklist section → no badge
- Toggle collapse → section body hidden/shown
- Unblacklist → symbol returns to correct tier

## Out of Scope

- Bulk blacklist / CSV import
- Blacklist reason field (free text) — manual is sufficient for now
- Automatic blacklisting triggered by drawdown or loss streaks
