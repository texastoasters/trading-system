defmodule DashboardWeb.LogsLive do
  use DashboardWeb, :live_view

  @max_lines 500

  @source_tabs %{
    agents: ~w[executor portfolio_manager watcher screener supervisor],
    docker: ~w[docker_redis docker_timescaledb docker_dashboard],
    vps: ~w[vps_syslog]
  }

  @source_meta %{
    "executor"           => %{label: "executor",          color: "blue"},
    "portfolio_manager"  => %{label: "portfolio_manager",  color: "green"},
    "watcher"            => %{label: "watcher",            color: "yellow"},
    "screener"           => %{label: "screener",           color: "purple"},
    "supervisor"         => %{label: "supervisor",         color: "cyan"},
    "docker_redis"       => %{label: "redis",              color: "red"},
    "docker_timescaledb" => %{label: "timescaledb",        color: "orange"},
    "docker_dashboard"   => %{label: "dashboard",          color: "gray"},
    "vps_syslog"         => %{label: "syslog",             color: "white"}
  }

  def source_tabs, do: @source_tabs
  def source_meta, do: @source_meta

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
     |> assign(:log_lines, [])}
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
    {:noreply, assign(socket, :log_lines, [])}
  end

  @impl true
  def handle_info({:log_lines, lines}, socket) do
    active = socket.assigns.active_sources
    filtered = Enum.filter(lines, &MapSet.member?(active, &1.source))

    if filtered == [] do
      {:noreply, socket}
    else
      current = socket.assigns.log_lines
      combined = current ++ filtered

      trimmed =
        if length(combined) > @max_lines do
          drop = length(combined) - @max_lines
          Enum.drop(combined, drop)
        else
          combined
        end

      {:noreply, assign(socket, :log_lines, trimmed)}
    end
  end

  @impl true
  def handle_info(_, socket), do: {:noreply, socket}

  defp color_class("blue"),   do: "text-blue-400"
  defp color_class("green"),  do: "text-green-400"
  defp color_class("yellow"), do: "text-yellow-400"
  defp color_class("purple"), do: "text-purple-400"
  defp color_class("cyan"),   do: "text-cyan-400"
  defp color_class("red"),    do: "text-red-400"
  defp color_class("orange"), do: "text-orange-400"
  defp color_class("gray"),   do: "text-gray-400"
  defp color_class("white"),  do: "text-white"
  defp color_class(_),        do: "text-gray-400"
end
