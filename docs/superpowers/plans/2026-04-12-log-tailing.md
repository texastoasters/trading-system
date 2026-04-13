# Log Tailing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a live `/logs` dashboard page (GenServer + PubSub, 3 tabs, per-source toggles) and supporting log infrastructure (`--logs` flag, docker log redirectors, logrotate config).

**Architecture:** `Dashboard.LogTailer` GenServer polls 9 log files every 1s, broadcasting new lines on PubSub topic `"logs"`. `LogsLive` subscribes, filters to user-selected sources, and renders a shared ring-buffered output panel. All 9 sources (5 agent files, 3 docker redirector files, 1 syslog) are plain files — no Docker socket required.

**Tech Stack:** Elixir/Phoenix LiveView, Phoenix.PubSub, ExUnit/ConnCase, Bash (start_trading_system.sh), logrotate (Ubuntu)

---

## File Map

| Action | Path | Responsibility |
|--------|------|---------------|
| Create | `dashboard/lib/dashboard/log_tailer.ex` | GenServer — polls log files, PubSub broadcast |
| Create | `dashboard/lib/dashboard_web/live/logs_live.ex` | LiveView controller — tab/toggle/buffer state |
| Create | `dashboard/lib/dashboard_web/live/logs_live.html.heex` | Template — tabs, toggles, combined output |
| Create | `scripts/logrotate.conf` | Logrotate config — daily, gzip, 30-day retention |
| Create | `test/dashboard/log_tailer_test.exs` | LogTailer unit tests |
| Create | `test/dashboard_web/live/logs_live_test.exs` | LogsLive integration tests |
| Modify | `dashboard/lib/dashboard/application.ex` | Add LogTailer to supervision tree |
| Modify | `dashboard/lib/dashboard_web/router.ex` | Add `live "/logs"` route |
| Modify | `dashboard/lib/dashboard_web/layouts/app.html.heex` | Add "Logs" nav link |
| Modify | `dashboard/assets/js/app.js` | Add `ScrollBottom` hook |
| Modify | `docker-compose.yml` | Add log dir + syslog volume mounts to dashboard service |
| Modify | `start_trading_system.sh` | `--logs` flag, docker log redirectors, `mtime +30` |

---

## Task 1: Dashboard.LogTailer GenServer

**Files:**
- Create: `dashboard/lib/dashboard/log_tailer.ex`
- Create: `test/dashboard/log_tailer_test.exs`

All commands run from `dashboard/` directory.

- [ ] **Step 1: Write failing tests**

Create `test/dashboard/log_tailer_test.exs`:

```elixir
defmodule Dashboard.LogTailerTest do
  use ExUnit.Case, async: true

  alias Dashboard.LogTailer

  setup do
    log_dir = Path.join(System.tmp_dir!(), "log_tailer_#{:rand.uniform(1_000_000)}")
    File.mkdir_p!(log_dir)
    on_exit(fn -> File.rm_rf!(log_dir) end)
    %{log_dir: log_dir}
  end

  defp start_tailer(log_dir) do
    start_supervised!(
      {LogTailer, log_dir: log_dir, syslog_path: "/nonexistent", poll_interval: 100_000}
    )
  end

  test "does not broadcast pre-existing content on first poll", %{log_dir: log_dir} do
    file = Path.join(log_dir, "screener.log")
    File.write!(file, "old line\n")

    pid = start_tailer(log_dir)
    Phoenix.PubSub.subscribe(Dashboard.PubSub, "logs")

    send(pid, :poll)
    refute_receive {:log_lines, _}, 300
  end

  test "broadcasts new lines written after init", %{log_dir: log_dir} do
    file = Path.join(log_dir, "screener.log")
    File.write!(file, "existing\n")

    pid = start_tailer(log_dir)
    Phoenix.PubSub.subscribe(Dashboard.PubSub, "logs")

    File.write!(file, "new line\n", [:append])
    send(pid, :poll)

    assert_receive {:log_lines, lines}, 1000
    assert length(lines) == 1
    assert hd(lines).source == "screener"
    assert hd(lines).label == "screener"
    assert hd(lines).color == "purple"
    assert hd(lines).line == "new line"
  end

  test "skips missing files without error or broadcast", %{log_dir: log_dir} do
    pid = start_tailer(log_dir)
    Phoenix.PubSub.subscribe(Dashboard.PubSub, "logs")

    send(pid, :poll)
    refute_receive {:log_lines, _}, 300
  end

  test "resets offset to 0 and skips on rotation detection", %{log_dir: log_dir} do
    file = Path.join(log_dir, "screener.log")
    File.write!(file, "long existing content that sets a high offset\n")

    pid = start_tailer(log_dir)
    Phoenix.PubSub.subscribe(Dashboard.PubSub, "logs")

    # Simulate rotation: truncate to shorter content
    File.write!(file, "new\n")

    send(pid, :poll)
    refute_receive {:log_lines, _}, 300

    # Next poll reads from 0 and finds "new"
    send(pid, :poll)
    assert_receive {:log_lines, lines}, 1000
    assert hd(lines).line == "new"
  end

  test "resolves today's date-suffixed file for daemon agents", %{log_dir: log_dir} do
    date = Date.utc_today()
    file = Path.join(log_dir, "executor_#{date}.log")
    File.write!(file, "")

    pid = start_tailer(log_dir)
    Phoenix.PubSub.subscribe(Dashboard.PubSub, "logs")

    File.write!(file, "exec line\n", [:append])
    send(pid, :poll)

    assert_receive {:log_lines, lines}, 1000
    assert Enum.any?(lines, &(&1.source == "executor" and &1.line == "exec line"))
  end

  test "broadcasts lines from multiple active files in one message", %{log_dir: log_dir} do
    screener = Path.join(log_dir, "screener.log")
    supervisor = Path.join(log_dir, "supervisor.log")
    File.write!(screener, "")
    File.write!(supervisor, "")

    pid = start_tailer(log_dir)
    Phoenix.PubSub.subscribe(Dashboard.PubSub, "logs")

    File.write!(screener, "screener line\n", [:append])
    File.write!(supervisor, "supervisor line\n", [:append])
    send(pid, :poll)

    assert_receive {:log_lines, lines}, 1000
    sources = Enum.map(lines, & &1.source)
    assert "screener" in sources
    assert "supervisor" in sources
  end
end
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dashboard && mix test test/dashboard/log_tailer_test.exs
```

Expected: all tests fail with `UndefinedFunctionError` (module does not exist).

- [ ] **Step 3: Implement LogTailer**

Create `dashboard/lib/dashboard/log_tailer.ex`:

```elixir
defmodule Dashboard.LogTailer do
  use GenServer

  alias Phoenix.PubSub

  @pubsub Dashboard.PubSub
  @topic "logs"
  @default_poll_interval 1_000

  # ---------------------------------------------------------------------------
  # Public API
  # ---------------------------------------------------------------------------

  def start_link(opts \\ []) do
    {name, opts} = Keyword.pop(opts, :name, __MODULE__)
    server_opts = if name, do: [name: name], else: []
    GenServer.start_link(__MODULE__, opts, server_opts)
  end

  # ---------------------------------------------------------------------------
  # Callbacks
  # ---------------------------------------------------------------------------

  @impl true
  def init(opts) do
    log_dir = Keyword.get(opts, :log_dir, "/app/logs")
    syslog_path = Keyword.get(opts, :syslog_path, "/var/log/host/syslog")
    poll_interval = Keyword.get(opts, :poll_interval, @default_poll_interval)

    sources =
      log_dir
      |> build_sources(syslog_path)
      |> Map.new(fn {id, source} ->
        offset =
          case File.stat(resolve_path(source)) do
            {:ok, %{size: size}} -> size
            {:error, _} -> 0
          end

        {id, Map.put(source, :offset, offset)}
      end)

    :timer.send_interval(poll_interval, :poll)
    {:ok, %{sources: sources}}
  end

  @impl true
  def handle_info(:poll, state) do
    {new_sources, all_lines} =
      Enum.reduce(state.sources, {state.sources, []}, fn {id, source}, {acc_sources, acc_lines} ->
        path = resolve_path(source)

        case read_new_lines(path, source.offset) do
          {:ok, lines, new_offset} ->
            tagged =
              Enum.map(lines, fn line ->
                %{source: id, label: source.label, color: source.color, line: line}
              end)

            {Map.put(acc_sources, id, %{source | offset: new_offset}), acc_lines ++ tagged}

          {:skip, new_offset} ->
            {Map.put(acc_sources, id, %{source | offset: new_offset}), acc_lines}
        end
      end)

    if all_lines != [] do
      PubSub.broadcast(@pubsub, @topic, {:log_lines, all_lines})
    end

    {:noreply, %{state | sources: new_sources}}
  end

  # ---------------------------------------------------------------------------
  # Private helpers
  # ---------------------------------------------------------------------------

  defp build_sources(log_dir, syslog_path) do
    %{
      "executor" =>
        %{label: "executor", color: "blue", type: :dated, dir: log_dir, name: "executor"},
      "portfolio_manager" =>
        %{label: "portfolio_manager", color: "green", type: :dated, dir: log_dir, name: "portfolio_manager"},
      "watcher" =>
        %{label: "watcher", color: "yellow", type: :dated, dir: log_dir, name: "watcher"},
      "screener" =>
        %{label: "screener", color: "purple", type: :static, path: Path.join(log_dir, "screener.log")},
      "supervisor" =>
        %{label: "supervisor", color: "cyan", type: :static, path: Path.join(log_dir, "supervisor.log")},
      "docker_redis" =>
        %{label: "redis", color: "red", type: :static, path: Path.join(log_dir, "docker_redis.log")},
      "docker_timescaledb" =>
        %{label: "timescaledb", color: "orange", type: :static, path: Path.join(log_dir, "docker_timescaledb.log")},
      "docker_dashboard" =>
        %{label: "dashboard", color: "gray", type: :static, path: Path.join(log_dir, "docker_dashboard.log")},
      "vps_syslog" =>
        %{label: "syslog", color: "white", type: :static, path: syslog_path}
    }
  end

  defp resolve_path(%{type: :dated, dir: dir, name: name}) do
    Path.join(dir, "#{name}_#{Date.utc_today()}.log")
  end

  defp resolve_path(%{type: :static, path: path}), do: path

  defp read_new_lines(path, offset) do
    case File.stat(path) do
      {:error, _} ->
        {:skip, offset}

      {:ok, %{size: size}} when size < offset ->
        # File rotated — reset; content will be read next poll
        {:skip, 0}

      {:ok, %{size: size}} when size == offset ->
        {:skip, offset}

      {:ok, _} ->
        case File.open(path, [:read, :binary]) do
          {:ok, file} ->
            :file.position(file, offset)
            content = IO.read(file, :all)
            File.close(file)
            new_offset = offset + byte_size(content)
            lines = content |> String.split("\n") |> Enum.reject(&(&1 == ""))
            {:ok, lines, new_offset}

          {:error, _} ->
            {:skip, offset}
        end
    end
  end
end
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd dashboard && mix test test/dashboard/log_tailer_test.exs
```

Expected: all 6 tests pass, no warnings.

- [ ] **Step 5: Commit**

```bash
git add dashboard/lib/dashboard/log_tailer.ex dashboard/test/dashboard/log_tailer_test.exs
git commit -m "feat: Dashboard.LogTailer GenServer — file polling + PubSub broadcast"
```

---

## Task 2: LogsLive LiveView + Template

**Files:**
- Create: `dashboard/lib/dashboard_web/live/logs_live.ex`
- Create: `dashboard/lib/dashboard_web/live/logs_live.html.heex`
- Create: `test/dashboard_web/live/logs_live_test.exs`

- [ ] **Step 1: Write failing tests**

Create `test/dashboard_web/live/logs_live_test.exs`:

```elixir
defmodule DashboardWeb.LogsLiveTest do
  use DashboardWeb.ConnCase

  import Phoenix.LiveViewTest

  describe "mount" do
    test "renders page with all tabs, all toggles inactive by default", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/logs")

      assert has_element?(view, "button[phx-value-tab=agents]", "Agents")
      assert has_element?(view, "button[phx-value-tab=docker]", "Docker")
      assert has_element?(view, "button[phx-value-tab=vps]", "VPS")
      # All agent toggles show ○ (inactive)
      assert has_element?(view, "button[phx-value-source=executor]")
      refute render(view) =~ "executor ●"
      # Output area shows empty-state message
      assert has_element?(view, "#log-output")
      assert render(view) =~ "No logs selected"
    end

    test "agents tab is active by default, shows agent sources only", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/logs")

      assert has_element?(view, "button[phx-value-source=executor]")
      assert has_element?(view, "button[phx-value-source=watcher]")
      assert has_element?(view, "button[phx-value-source=screener]")
      refute has_element?(view, "button[phx-value-source=docker_redis]")
      refute has_element?(view, "button[phx-value-source=vps_syslog]")
    end
  end

  describe "toggle_source" do
    test "activates source on first click, deactivates on second", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/logs")

      view |> element("button[phx-value-source=executor]") |> render_click()
      assert render(view) =~ "executor ●"

      view |> element("button[phx-value-source=executor]") |> render_click()
      refute render(view) =~ "executor ●"
    end

    test "lines from inactive sources are filtered out", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/logs")

      # Only activate screener
      view |> element("button[phx-value-tab=agents]") |> render_click()
      view |> element("button[phx-value-source=screener]") |> render_click()

      Phoenix.PubSub.broadcast(Dashboard.PubSub, "logs", {
        :log_lines,
        [
          %{source: "executor", label: "executor", color: "blue", line: "exec line"},
          %{source: "screener", label: "screener", color: "purple", line: "screen line"}
        ]
      })

      html = render(view)
      refute html =~ "exec line"
      assert html =~ "screen line"
    end

    test "lines from active sources appear in output", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/logs")

      view |> element("button[phx-value-source=executor]") |> render_click()

      Phoenix.PubSub.broadcast(Dashboard.PubSub, "logs", {
        :log_lines,
        [%{source: "executor", label: "executor", color: "blue", line: "SPY buy @ $521"}]
      })

      assert render(view) =~ "SPY buy @ $521"
    end
  end

  describe "set_tab" do
    test "switching tab shows sources for that tab", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/logs")

      view |> element("button[phx-value-tab=docker]") |> render_click()

      assert has_element?(view, "button[phx-value-source=docker_redis]")
      refute has_element?(view, "button[phx-value-source=executor]")
    end

    test "switching tab preserves active_sources across tabs", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/logs")

      view |> element("button[phx-value-source=executor]") |> render_click()
      view |> element("button[phx-value-tab=docker]") |> render_click()
      view |> element("button[phx-value-tab=agents]") |> render_click()

      assert render(view) =~ "executor ●"
    end
  end

  describe "clear" do
    test "clear empties log buffer and restores empty-state message", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/logs")

      view |> element("button[phx-value-source=executor]") |> render_click()

      Phoenix.PubSub.broadcast(Dashboard.PubSub, "logs", {
        :log_lines,
        [%{source: "executor", label: "executor", color: "blue", line: "some line"}]
      })

      assert render(view) =~ "some line"

      view |> element("button", "Clear") |> render_click()
      assert render(view) =~ "No logs selected"
    end
  end

  describe "ring buffer" do
    test "caps buffer at 500 lines, dropping oldest", %{conn: conn} do
      {:ok, view, _html} = live(conn, "/logs")
      view |> element("button[phx-value-source=executor]") |> render_click()

      lines =
        Enum.map(1..600, fn i ->
          %{source: "executor", label: "executor", color: "blue", line: "line #{i}"}
        end)

      Phoenix.PubSub.broadcast(Dashboard.PubSub, "logs", {:log_lines, lines})

      html = render(view)
      # First 100 dropped (600 - 500 = 100)
      refute html =~ "line 1\n"
      refute html =~ ">line 100<"
      assert html =~ "line 101"
      assert html =~ "line 600"
    end
  end
end
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd dashboard && mix test test/dashboard_web/live/logs_live_test.exs
```

Expected: fails with `UndefinedFunctionError` or route `no route found`.

- [ ] **Step 3: Implement LogsLive controller**

Create `dashboard/lib/dashboard_web/live/logs_live.ex`:

```elixir
defmodule DashboardWeb.LogsLive do
  use DashboardWeb, :live_view

  @max_lines 500

  @source_tabs %{
    agents: ~w[executor portfolio_manager watcher screener supervisor],
    docker: ~w[docker_redis docker_timescaledb docker_dashboard],
    vps: ~w[vps_syslog]
  }

  @source_meta %{
    "executor"          => %{label: "executor",          color: "blue"},
    "portfolio_manager" => %{label: "portfolio_manager",  color: "green"},
    "watcher"           => %{label: "watcher",            color: "yellow"},
    "screener"          => %{label: "screener",           color: "purple"},
    "supervisor"        => %{label: "supervisor",         color: "cyan"},
    "docker_redis"      => %{label: "redis",              color: "red"},
    "docker_timescaledb"=> %{label: "timescaledb",        color: "orange"},
    "docker_dashboard"  => %{label: "dashboard",          color: "gray"},
    "vps_syslog"        => %{label: "syslog",             color: "white"}
  }

  @impl true
  def mount(_params, _session, socket) do
    if connected?(socket) do
      Phoenix.PubSub.subscribe(Dashboard.PubSub, "logs")
    end

    {:ok,
     socket
     |> assign(:page_title, "Logs")
     |> assign(:tab, :agents)
     |> assign(:active_sources, MapSet.new())
     |> assign(:log_lines, [])
     |> assign(:line_count, 0)
     |> assign(:source_tabs, @source_tabs)
     |> assign(:source_meta, @source_meta)}
  end

  @impl true
  def handle_event("set_tab", %{"tab" => tab}, socket) when tab in ~w[agents docker vps] do
    {:noreply, assign(socket, :tab, String.to_existing_atom(tab))}
  end

  @impl true
  def handle_event("toggle_source", %{"source" => id}, socket) do
    active = socket.assigns.active_sources

    new_active =
      if MapSet.member?(active, id),
        do: MapSet.delete(active, id),
        else: MapSet.put(active, id)

    {:noreply, assign(socket, :active_sources, new_active)}
  end

  @impl true
  def handle_event("clear", _params, socket) do
    {:noreply, socket |> assign(:log_lines, []) |> assign(:line_count, 0)}
  end

  @impl true
  def handle_info({:log_lines, lines}, socket) do
    active = socket.assigns.active_sources
    filtered = Enum.filter(lines, &MapSet.member?(active, &1.source))

    if filtered == [] do
      {:noreply, socket}
    else
      current = socket.assigns.log_lines
      count = socket.assigns.line_count
      combined = current ++ filtered
      total = count + length(filtered)

      {trimmed, new_count} =
        if total > @max_lines do
          drop = total - @max_lines
          {Enum.drop(combined, drop), @max_lines}
        else
          {combined, total}
        end

      {:noreply, socket |> assign(:log_lines, trimmed) |> assign(:line_count, new_count)}
    end
  end

  defp color_class("blue"), do: "text-blue-400"
  defp color_class("green"), do: "text-green-400"
  defp color_class("yellow"), do: "text-yellow-400"
  defp color_class("purple"), do: "text-purple-400"
  defp color_class("cyan"), do: "text-cyan-400"
  defp color_class("red"), do: "text-red-400"
  defp color_class("orange"), do: "text-orange-400"
  defp color_class("gray"), do: "text-gray-400"
  defp color_class("white"), do: "text-white"
  defp color_class(_), do: "text-gray-400"
end
```

- [ ] **Step 4: Implement the template**

Create `dashboard/lib/dashboard_web/live/logs_live.html.heex`:

```heex
<div class="flex flex-col p-4 gap-4" style="height: calc(100vh - 40px);">

  <%!-- Header --%>
  <div class="flex items-center justify-between">
    <h1 class="text-lg font-semibold text-gray-200">Logs</h1>
    <button
      phx-click="clear"
      class="px-3 py-1 text-xs bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-gray-200 rounded border border-gray-700 transition-colors"
    >
      Clear
    </button>
  </div>

  <%!-- Tab bar --%>
  <div class="flex gap-1 border-b border-gray-800">
    <%= for {tab_id, label} <- [{:agents, "Agents"}, {:docker, "Docker"}, {:vps, "VPS"}] do %>
      <button
        phx-click="set_tab"
        phx-value-tab={tab_id}
        class={[
          "px-4 py-2 text-xs font-medium transition-colors",
          if(@tab == tab_id,
            do: "border-b-2 border-blue-500 text-blue-400 -mb-px",
            else: "text-gray-500 hover:text-gray-300"
          )
        ]}
      >
        <%= label %>
      </button>
    <% end %>
  </div>

  <%!-- Source toggles for active tab --%>
  <div class="flex flex-wrap gap-2">
    <%= for source_id <- @source_tabs[@tab] do %>
      <% meta = @source_meta[source_id] %>
      <% active = MapSet.member?(@active_sources, source_id) %>
      <button
        phx-click="toggle_source"
        phx-value-source={source_id}
        class={[
          "px-3 py-1 text-xs font-mono rounded border transition-colors",
          if(active,
            do: "bg-gray-800 border-gray-600 text-gray-200",
            else: "bg-transparent border-gray-800 text-gray-600 hover:border-gray-600 hover:text-gray-400"
          )
        ]}
      >
        <%= meta.label %> <%= if active, do: "●", else: "○" %>
      </button>
    <% end %>
  </div>

  <%!-- Combined log output --%>
  <div
    id="log-output"
    class="flex-1 overflow-y-auto overflow-x-auto bg-gray-950 rounded font-mono text-xs p-3 min-h-0"
    phx-hook="ScrollBottom"
  >
    <%= if @log_lines == [] do %>
      <p class="text-gray-700">No logs selected. Toggle sources above to start tailing.</p>
    <% else %>
      <%= for line <- @log_lines do %>
        <div class="flex gap-2 leading-5 hover:bg-gray-900 px-1 rounded">
          <span class={["shrink-0 w-36 truncate", color_class(line.color)]}>
            [<%= line.label %>]
          </span>
          <span class="text-gray-300 break-all"><%= line.line %></span>
        </div>
      <% end %>
    <% end %>
  </div>

</div>
```

- [ ] **Step 5: Add route, then run tests**

Add the route temporarily so tests can run. Edit `dashboard/lib/dashboard_web/router.ex`:

```elixir
live "/logs", LogsLive, :index
```

Add inside the existing `scope "/", DashboardWeb do` block, after the `/performance` line.

```bash
cd dashboard && mix test test/dashboard_web/live/logs_live_test.exs
```

Expected: all tests pass.

- [ ] **Step 6: Run full test suite, check coverage**

```bash
cd dashboard && mix test
```

Expected: all existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add dashboard/lib/dashboard_web/live/logs_live.ex \
        dashboard/lib/dashboard_web/live/logs_live.html.heex \
        dashboard/test/dashboard_web/live/logs_live_test.exs \
        dashboard/lib/dashboard_web/router.ex
git commit -m "feat: LogsLive — /logs page with tabs, toggles, ring-buffered output"
```

---

## Task 3: Wire Up — Supervision, Nav, JS Hook

**Files:**
- Modify: `dashboard/lib/dashboard/application.ex`
- Modify: `dashboard/lib/dashboard_web/layouts/app.html.heex`
- Modify: `dashboard/assets/js/app.js`

- [ ] **Step 1: Add LogTailer to supervision tree**

In `dashboard/lib/dashboard/application.ex`, add `Dashboard.LogTailer` after `Dashboard.RedisSubscriber` and before the `Supervisor.child_spec(Dashboard.MarketClock, ...)` line:

```elixir
      # Background GenServers
      Dashboard.RedisPoller,
      Dashboard.RedisSubscriber,
      Dashboard.LogTailer,
      # MarketClock is :temporary — Alpaca API failures must not crash the app
      Supervisor.child_spec(Dashboard.MarketClock, restart: :temporary),
```

- [ ] **Step 2: Add "Logs" nav link**

In `dashboard/lib/dashboard_web/layouts/app.html.heex`, add after the `/performance` link:

```heex
    <a href="/logs" class="px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200 rounded hover:bg-gray-800 transition-colors shrink-0">
      Logs
    </a>
```

- [ ] **Step 3: Add ScrollBottom JS hook**

In `dashboard/assets/js/app.js`, replace:

```js
let liveSocket = new LiveSocket("/live", Socket, {
  longPollFallbackMs: 2500,
  params: {_csrf_token: csrfToken},
  hooks: {}
})
```

with:

```js
let Hooks = {}

Hooks.ScrollBottom = {
  updated() {
    this.el.scrollTop = this.el.scrollHeight
  }
}

let liveSocket = new LiveSocket("/live", Socket, {
  longPollFallbackMs: 2500,
  params: {_csrf_token: csrfToken},
  hooks: Hooks
})
```

- [ ] **Step 4: Run full test suite**

```bash
cd dashboard && mix test
```

Expected: all tests pass. LogTailer starts up silently (all files missing → no broadcasts → no noise).

- [ ] **Step 5: Commit**

```bash
git add dashboard/lib/dashboard/application.ex \
        dashboard/lib/dashboard_web/layouts/app.html.heex \
        dashboard/assets/js/app.js
git commit -m "feat: wire up LogTailer supervision, Logs nav, ScrollBottom hook"
```

---

## Task 4: docker-compose Volume Mounts

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add volume mounts to dashboard service**

In `docker-compose.yml`, add a `volumes:` key to the `dashboard:` service. The dashboard service currently has no `volumes:` block. Add it after the `depends_on:` block:

```yaml
    depends_on:
      redis:
        condition: service_healthy
      timescaledb:
        condition: service_healthy
    volumes:
      - ${HOME}/trading-system/logs:/app/logs:ro
      - /var/log:/var/log/host:ro
    healthcheck:
```

- [ ] **Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: mount log dir and /var/log into dashboard container"
```

---

## Task 5: start_trading_system.sh — `--logs`, Docker Redirectors, mtime

**Files:**
- Modify: `start_trading_system.sh`

- [ ] **Step 1: Add DOCKER_CONTAINERS config and helper functions**

After the `AGENTS=("executor" "portfolio_manager" "watcher")` line, add:

```bash
DOCKER_CONTAINERS=("trading_redis:redis" "trading_timescaledb:timescaledb" "trading_dashboard:dashboard")
```

After the `log_step()` helper function block and before `check_env()`, add:

```bash
# ── Docker Log Redirectors ───────────────────────────────────

start_docker_log_redirectors() {
    log_step "Starting docker log redirectors..."
    for entry in "${DOCKER_CONTAINERS[@]}"; do
        local container="${entry%%:*}"
        local name="${entry##*:}"
        local log_file="${LOG_DIR}/docker_${name}.log"
        local pid_file="${PID_DIR}/docker_${name}.pid"

        # Kill stale redirector if running
        if [ -f "$pid_file" ]; then
            kill "$(cat "$pid_file")" 2>/dev/null || true
            rm -f "$pid_file"
        fi

        if docker container inspect "$container" > /dev/null 2>&1; then
            nohup docker logs --follow "$container" >> "$log_file" 2>&1 &
            echo $! > "$pid_file"
            log_info "docker log redirector: ${container} → docker_${name}.log"
        else
            log_warn "Container ${container} not running — skipping log redirector"
        fi
    done
}

stop_docker_log_redirectors() {
    for entry in "${DOCKER_CONTAINERS[@]}"; do
        local name="${entry##*:}"
        local pid_file="${PID_DIR}/docker_${name}.pid"
        if [ -f "$pid_file" ]; then
            kill "$(cat "$pid_file")" 2>/dev/null || true
            rm -f "$pid_file"
            log_info "stopped docker log redirector: docker_${name}.log"
        fi
    done
}

# ── Log Tailing ──────────────────────────────────────────────

tail_logs() {
    local DATE_SUFFIX
    DATE_SUFFIX=$(date '+%Y-%m-%d')

    declare -A log_files=(
        ["executor"]="${LOG_DIR}/executor_${DATE_SUFFIX}.log"
        ["portfolio_manager"]="${LOG_DIR}/portfolio_manager_${DATE_SUFFIX}.log"
        ["watcher"]="${LOG_DIR}/watcher_${DATE_SUFFIX}.log"
        ["screener"]="${LOG_DIR}/screener.log"
        ["supervisor"]="${LOG_DIR}/supervisor.log"
    )

    if command -v tmux &>/dev/null; then
        # Kill existing session if stale
        tmux kill-session -t trading-logs 2>/dev/null || true

        tmux new-session -d -s trading-logs -x 220 -y 50

        # Window 1: daemon agents (3-pane split)
        tmux send-keys -t trading-logs \
            "tail -f '${log_files[executor]}' 2>/dev/null | sed 's/^/[executor] /'" Enter
        tmux split-window -v -t trading-logs
        tmux send-keys -t trading-logs \
            "tail -f '${log_files[portfolio_manager]}' 2>/dev/null | sed 's/^/[portfolio_manager] /'" Enter
        tmux split-window -v -t trading-logs
        tmux send-keys -t trading-logs \
            "tail -f '${log_files[watcher]}' 2>/dev/null | sed 's/^/[watcher] /'" Enter

        # Window 2: cron agents
        tmux new-window -t trading-logs
        tmux send-keys -t trading-logs \
            "tail -f '${log_files[screener]}' 2>/dev/null | sed 's/^/[screener] /'" Enter
        tmux split-window -h -t trading-logs
        tmux send-keys -t trading-logs \
            "tail -f '${log_files[supervisor]}' 2>/dev/null | sed 's/^/[supervisor] /'" Enter

        tmux select-window -t trading-logs:0
        log_info "tmux session 'trading-logs' created. Attaching..."
        tmux attach-session -t trading-logs
    else
        log_info "tmux not found — combined tail:"
        tail -f \
            "${log_files[executor]}" \
            "${log_files[portfolio_manager]}" \
            "${log_files[watcher]}" \
            "${log_files[screener]}" \
            "${log_files[supervisor]}" 2>/dev/null
    fi
}
```

- [ ] **Step 2: Call docker redirectors from start_system and stop_system**

In `start_system()`, add a call to `start_docker_log_redirectors` after the agents-started log message:

```bash
    log_info "Daemon agents started. Logs in ${LOG_DIR}/"
    echo ""
    start_docker_log_redirectors
    echo ""
    echo "════════════════════════════════════════════════════════"
```

In `stop_system()`, add a call to `stop_docker_log_redirectors` after the agents are stopped, before the final status messages:

```bash
    echo ""
    log_info "Daemon agents stopped"
    stop_docker_log_redirectors
    echo ""
```

- [ ] **Step 3: Update mtime cleanup and add --logs case**

Change the log rotation `find` command in `start_system()`:

```bash
    # Rotate logs (keep last 30 days)
    find "$LOG_DIR" -name "*.log*" -mtime +30 -delete 2>/dev/null || true
```

(Was `*.log` and `+7`. Now `*.log*` covers compressed rotated files, `+30` matches logrotate retention.)

In the `case` block at the bottom, add `--logs` before the `*)` catch-all:

```bash
    --logs|-logs|logs)
        tail_logs
        ;;
    --help|-help|-h)
```

Also update the `--help` case body to include the new flags:

```bash
        echo "  --logs      Tail all agent logs (tmux if available, else combined tail)"
```

- [ ] **Step 4: Commit**

```bash
git add start_trading_system.sh
git commit -m "feat: --logs flag, docker log redirectors, 30-day log cleanup"
```

---

## Task 6: scripts/logrotate.conf

**Files:**
- Create: `scripts/logrotate.conf`

- [ ] **Step 1: Create logrotate config**

Create `scripts/logrotate.conf`:

```
# Trading system logrotate config
# Install on VPS: sudo cp scripts/logrotate.conf /etc/logrotate.d/trading-system
# Verify: sudo logrotate --debug /etc/logrotate.d/trading-system

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

Notes on each directive:
- `daily` — rotate once per day
- `rotate 30` — keep 30 rotated files
- `compress` — gzip rotated files
- `delaycompress` — keep yesterday's rotation uncompressed (safe for any redirectors still writing to old fd)
- `missingok` — no error if log file absent
- `notifempty` — skip rotation if file is empty
- `copytruncate` — copy log to rotated name then truncate in-place; running processes keep their file descriptor without needing SIGHUP
- `dateext` + `dateformat -%Y-%m-%d` — rotated files named `executor_2026-04-11.log-2026-04-12.gz` instead of `.1.gz`

- [ ] **Step 2: Commit**

```bash
git add scripts/logrotate.conf
git commit -m "feat: logrotate config — daily, gzip, 30-day retention, copytruncate"
```

---

## Final Verification

- [ ] **Run full test suite and check coverage**

```bash
cd dashboard && mix coveralls
```

Expected: all tests pass, 100% coverage maintained.

- [ ] **Verify no regressions in existing pages**

Start the dashboard locally and visit `/`, `/universe`, `/trades`, `/performance`, `/logs`. Confirm nav "Logs" link appears. Toggle a source on `/logs`, confirm no errors in console.

---

## Self-Review Notes

**Spec coverage check:**

| Spec requirement | Covered in |
|-----------------|-----------|
| New `/logs` LiveView page | Task 2 |
| 3 tabs: Agents, Docker, VPS | Task 2 (LogsLive + template) |
| 9 log sources with labels/colors | Task 1 (LogTailer build_sources) |
| All sources off by default | Task 2 (`active_sources: MapSet.new()`) |
| Per-source toggles | Task 2 (`toggle_source` event) |
| Lines prepended with service name | Task 2 (template `[label]` prefix) |
| Color-coded service names | Task 2 (`color_class/1`) |
| Combined output panel across tabs | Task 2 (shared `log_lines` buffer) |
| Ring buffer capped at 500 lines | Task 2 (`handle_info` trim logic) |
| Auto-scroll to bottom | Task 3 (`ScrollBottom` hook) |
| GenServer polls every 1s | Task 1 (`:timer.send_interval`) |
| Seek to EOF on init (no history dump) | Task 1 (`init/1` offset init) |
| File rotation detection | Task 1 (`read_new_lines` size check) |
| Date-suffix resolution for daemons | Task 1 (`resolve_path` dated type) |
| Docker log redirectors | Task 5 (`start_docker_log_redirectors`) |
| `--logs` flag with tmux | Task 5 (`tail_logs`) |
| Volume mounts in docker-compose | Task 4 |
| logrotate config, 30-day retention | Task 6 |
| mtime cleanup extended to 30 days | Task 5 |
| 100% test coverage | Tasks 1 + 2 + Final |
