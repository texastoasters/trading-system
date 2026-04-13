# Log Tailing — Design Spec

**Date:** 2026-04-12
**Scope:** Dashboard `/logs` page (items #1 + #5 from 2026-04-12 priority wave)
**Status:** Approved

---

## What We're Building

Two features shipped as one PR:

1. **Dashboard `/logs` page** — Live-scrolling combined log tail for all trading system log sources. Three tabs (Agents, Docker, VPS). All sources off by default; per-source toggles. Lines prepended with color-coded service name. GenServer + PubSub backend.

2. **Log infrastructure** — `start_trading_system.sh --logs` for terminal-based tailing; `docker logs --follow` redirectors started alongside agents so Docker container logs land in `~/trading-system/logs/`; logrotate config (`scripts/logrotate.conf`) for daily rotation with 30-day retention.

---

## Log Sources

### Agents tab (5 sources)

| Source ID | Display name | Log file path |
|-----------|-------------|---------------|
| `executor` | executor | `~/trading-system/logs/executor_{YYYY-MM-DD}.log` |
| `portfolio_manager` | portfolio_manager | `~/trading-system/logs/portfolio_manager_{YYYY-MM-DD}.log` |
| `watcher` | watcher | `~/trading-system/logs/watcher_{YYYY-MM-DD}.log` |
| `screener` | screener | `~/trading-system/logs/screener.log` |
| `supervisor` | supervisor | `~/trading-system/logs/supervisor.log` |

Daemon agents (executor, PM, watcher) use date-suffixed filenames per `start_trading_system.sh`. Cron agents (screener, supervisor) append to single named files per their crontab redirects.

Inside the dashboard container, the log dir is mounted at `/app/logs`.

### Docker tab (3 sources)

| Source ID | Display name | Log file path |
|-----------|-------------|---------------|
| `docker_redis` | redis | `~/trading-system/logs/docker_redis.log` |
| `docker_timescaledb` | timescaledb | `~/trading-system/logs/docker_timescaledb.log` |
| `docker_dashboard` | dashboard | `~/trading-system/logs/docker_dashboard.log` |

Written by `docker logs --follow {container_name}` background processes started by `start_trading_system.sh`. No Docker socket needed.

### VPS tab (1 source)

| Source ID | Display name | File path inside container |
|-----------|-------------|--------------------------|
| `vps_syslog` | syslog | `/var/log/host/syslog` |

Host `/var/log` is mounted read-only at `/var/log/host` in the dashboard container.

---

## Architecture

### GenServer: `Dashboard.LogTailer`

Supervised GenServer in the app supervision tree. Owns all file reading.

**State:**
```elixir
%{
  sources: %{
    source_id => %{
      path: String.t(),
      label: String.t(),
      color: atom(),   # :blue | :green | :yellow | :red | :purple | :cyan | :orange | :gray | :white
      offset: non_neg_integer()  # byte offset of last-read position
    }
  }
}
```

**Startup:** All 9 sources registered with `offset: 0`. Immediately seeks to EOF on first poll so only new lines are shown (no historical dump on connect).

**Polling:** `:timer.send_interval(1000, :poll)` — every 1 second.

**On `:poll`:**
1. For each source, open the file at current `offset`, read to EOF, split on `\n`, drop empty.
2. Update `offset` to new EOF position.
3. If any lines found, broadcast `{:log_lines, [%{source: id, label: label, color: color, line: line}]}` on PubSub topic `"logs"`.
4. If file does not exist (e.g. agent not started today), skip silently — offset stays 0.

**File rotation handling:** If `offset > File.stat!(path).size`, the file has been rotated. Reset `offset: 0`.

**Date-suffix resolution:** At poll time, `LogTailer` computes today's date to resolve daemon agent paths (`executor_{YYYY-MM-DD}.log`). Date is computed fresh each poll so the transition at midnight picks up the new file automatically.

### PubSub

Topic: `"logs"`

Message: `{:log_lines, lines}` where `lines` is a list of:
```elixir
%{
  source: "executor",
  label: "executor",
  color: :blue,
  line: "[Executor] ✅ SPY buy filled @ $521.30"
}
```

### LiveView: `DashboardWeb.LogsLive`

**Route:** `live "/logs", LogsLive, :index`

**State:**
```elixir
%{
  tab: :agents | :docker | :vps,
  active_sources: MapSet.t(),   # set of source IDs — empty by default
  log_lines: :queue.queue(),    # ring buffer, max 500 entries
  line_count: non_neg_integer()
}
```

**Mount:** Subscribe to `"logs"` PubSub. No sources active.

**`handle_info({:log_lines, lines}, socket)`:**
- Filter `lines` to only those whose `source` is in `active_sources`.
- Append filtered lines to `log_lines` queue. If `line_count >= 500`, drop from front.
- Push update to client.

**`handle_event("toggle_source", %{"source" => id})`:**
- Toggle source ID in/out of `active_sources` MapSet.
- No GenServer interaction needed (GenServer always polls everything).

**`handle_event("set_tab", %{"tab" => tab})`:** Switch active tab.

**`handle_event("clear")`:** Empty `log_lines` and reset `line_count`.

---

## UI

### Layout

```
┌─────────────────────────────────────────────────────┐
│  Logs                                     [Clear]   │
│  ┌──────────┬──────────┬──────────┐                 │
│  │  Agents  │  Docker  │   VPS    │                 │
│  └──────────┴──────────┴──────────┘                 │
│  Toggles for active tab:                            │
│  [executor ●] [watcher ○] [portfolio_manager ○] … │
│                                                     │
│  ┌─────────────────────────────────────────────┐   │
│  │ [executor] [Executor] ✅ SPY buy filled…    │   │
│  │ [watcher ] [Watcher] RSI-2 12.3 < 15 — si…│   │
│  │ [executor] [Executor] Stop-loss placed…     │   │
│  │                                             │   │
│  │                              ↑ auto-scroll  │   │
│  └─────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

- Tab bar at top.
- Toggles show all sources for the active tab. Toggle state is per-source, persists across tab switches (so you can have executor + redis both active and switch tabs to toggle more).
- Combined output panel spans the full width below the toggles. Shared across all tabs — all active sources from all tabs interleave here.
- Service name prefix is fixed-width (15 chars, padded), colored by source.
- Monospace font. Horizontal scroll if lines overflow.
- Auto-scrolls to bottom on new lines. No user-controlled auto-scroll toggle (YAGNI).
- "Clear" button empties the buffer.

### Colors

| Source | Color class |
|--------|-------------|
| executor | blue |
| portfolio_manager | green |
| watcher | yellow |
| screener | purple |
| supervisor | cyan |
| docker_redis | red |
| docker_timescaledb | orange |
| docker_dashboard | gray |
| vps_syslog | white |

---

## `start_trading_system.sh` Changes

### `--logs` flag

New case in the `case` block:

```bash
--logs|-logs|logs)
    tail_logs
    ;;
```

`tail_logs()`:
1. Compute `DATE_SUFFIX=$(date '+%Y-%m-%d')`.
2. If `tmux` is available: create session `trading-logs` (or attach if exists), split into panes, one per agent's today-file + screener.log + supervisor.log. Layout: tiled.
3. If no tmux: `tail -f ~/trading-system/logs/*_${DATE_SUFFIX}.log ~/trading-system/logs/screener.log ~/trading-system/logs/supervisor.log`.

### Docker log redirectors

Added to `start_system()` after agents start:

```bash
start_docker_log_redirectors() {
    local containers=("trading_redis" "trading_timescaledb" "trading_dashboard")
    for container in "${containers[@]}"; do
        local name="${container#trading_}"  # strip prefix
        local log_file="${LOG_DIR}/docker_${name}.log"
        # Kill stale redirector if any
        local pid_file="${PID_DIR}/docker_${name}.pid"
        if [ -f "$pid_file" ]; then
            kill "$(cat "$pid_file")" 2>/dev/null || true
            rm -f "$pid_file"
        fi
        nohup docker logs --follow "$container" >> "$log_file" 2>&1 &
        echo $! > "$pid_file"
        log_info "docker log redirector: ${container} → ${log_file}"
    done
}
```

`stop_system()` kills these PIDs alongside agent PIDs.

---

## `docker-compose.yml` Changes

Add to `dashboard` service:

```yaml
volumes:
  - ${HOME}/trading-system/logs:/app/logs:ro
  - /var/log:/var/log/host:ro
```

---

## `scripts/logrotate.conf`

```
/home/linuxuser/trading-system/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    dateext
    dateformat -%Y-%m-%d
}
```

`copytruncate`: truncates the live file in-place after copying, so running processes continue writing without needing SIGHUP. `delaycompress`: keeps yesterday's rotated file uncompressed for one day (useful for the `--follow` redirectors which may briefly read old data after rotation).

Install on VPS:
```bash
sudo cp scripts/logrotate.conf /etc/logrotate.d/trading-system
sudo logrotate --debug /etc/logrotate.d/trading-system  # verify
```

Also update `start_trading_system.sh` log cleanup:
```bash
find "$LOG_DIR" -name "*.log*" -mtime +30 -delete 2>/dev/null || true
```
(Was `+7`. Extended to 30 to match logrotate retention.)

---

## New Files

| File | Purpose |
|------|---------|
| `dashboard/lib/dashboard/log_tailer.ex` | GenServer — file polling + PubSub broadcast |
| `dashboard/lib/dashboard_web/live/logs_live.ex` | LiveView controller |
| `dashboard/lib/dashboard_web/live/logs_live.html.heex` | Template |
| `scripts/logrotate.conf` | Logrotate config for VPS |

## Modified Files

| File | Change |
|------|--------|
| `dashboard/lib/dashboard/application.ex` | Add `LogTailer` to supervision tree |
| `dashboard/lib/dashboard_web/router.ex` | Add `live "/logs", LogsLive, :index` |
| `dashboard/lib/dashboard_web/components/layouts/app.html.heex` | Add "Logs" nav link |
| `docker-compose.yml` | Add volume mounts to dashboard service |
| `start_trading_system.sh` | `--logs` flag + docker log redirectors + stop cleanup + `mtime +30` |

---

## Testing

### `LogTailer` tests (`test/dashboard/log_tailer_test.exs`)

- Polls file and broadcasts new lines since last offset
- Skips missing files silently
- Resets offset to 0 when file size shrinks (rotation)
- Date-suffix resolution picks correct file for today
- Seeks to EOF on first poll (no historical dump)

### `LogsLive` tests (`test/dashboard_web/live/logs_live_test.exs`)

- Page renders with all tabs, all toggles off by default
- Toggle activates source; second toggle deactivates
- Lines from inactive sources are filtered out
- Lines from active sources appear in output
- Clear button empties buffer
- Tab switch does not change active_sources
- Buffer caps at 500 lines (oldest dropped)

### `start_trading_system.sh` (manual verification)

- `--logs` with tmux: session created, panes running
- `--logs` without tmux: falls back to multi-file tail
- `--stop`: docker log redirector PIDs killed
- `--status`: no change

---

## Out of Scope

- Historical log search / grep within the UI
- Log level filtering
- Timestamp parsing / sorting across sources (lines display in arrival order)
- Auto-scroll toggle (always auto-scrolls)
