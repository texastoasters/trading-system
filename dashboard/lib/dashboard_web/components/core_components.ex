defmodule DashboardWeb.CoreComponents do
  @moduledoc """
  Core UI components for the trading dashboard.
  """

  use Phoenix.Component

  @doc "Renders a simple card container with a title."
  attr :title, :string, required: true
  attr :class, :string, default: ""
  slot :inner_block, required: true

  def card(assigns) do
    ~H"""
    <div class={["bg-gray-800 rounded-lg border border-gray-700 p-4", @class]}>
      <h2 class="text-sm font-semibold text-gray-400 uppercase tracking-wider mb-3">{@title}</h2>
      {render_slot(@inner_block)}
    </div>
    """
  end

  @doc "Renders a status badge."
  attr :status, :string, required: true

  def status_badge(assigns) do
    {bg, text} =
      case assigns.status do
        "active" -> {"bg-green-900/50 border-green-700", "text-green-400"}
        "halted" -> {"bg-red-900/50 border-red-700", "text-red-400"}
        "caution" -> {"bg-yellow-900/50 border-yellow-700", "text-yellow-400"}
        "paused" -> {"bg-blue-900/50 border-blue-700", "text-blue-400"}
        _ -> {"bg-gray-900/50 border-gray-700", "text-gray-400"}
      end

    assigns = assign(assigns, :bg, bg) |> assign(:text, text)

    ~H"""
    <span class={[
      "inline-flex items-center px-2 py-0.5 rounded border text-xs font-medium uppercase",
      @bg,
      @text
    ]}>
      {@status}
    </span>
    """
  end

  @doc "Renders a P&L value with appropriate color."
  attr :value, :any, required: true
  attr :suffix, :string, default: ""

  def pnl(assigns) do
    color =
      cond do
        is_nil(assigns.value) -> "text-gray-400"
        Decimal.compare(Decimal.new("0"), to_decimal(assigns.value)) == :gt -> "text-red-400"
        Decimal.compare(to_decimal(assigns.value), Decimal.new("0")) == :gt -> "text-green-400"
        true -> "text-gray-400"
      end

    assigns = assign(assigns, :color, color)

    ~H"""
    <span class={@color}>
      {format_pnl(@value)}{@suffix}
    </span>
    """
  end

  defp to_decimal(v) when is_float(v), do: Decimal.from_float(v)
  defp to_decimal(v) when is_integer(v), do: Decimal.new(v)
  defp to_decimal(%Decimal{} = v), do: v
  defp to_decimal(v) when is_binary(v), do: Decimal.new(v)
  defp to_decimal(_), do: Decimal.new("0")

  defp format_pnl(nil), do: "—"

  defp format_pnl(v) do
    d = to_decimal(v)
    prefix = if Decimal.compare(d, Decimal.new("0")) == :gt, do: "+", else: ""
    "#{prefix}#{Decimal.round(d, 2)}"
  end

  @doc """
  Renders an ⓘ info icon with a CSS-only hover tooltip explaining a term.

  Use `direction="above"` (default) to show the popup above the icon — works
  everywhere except inside `overflow-x-auto` table wrappers.
  Use `direction="below"` for table column headers so the popup floats into
  the table body area and is not clipped by the overflow container.
  """
  attr :text, :string, required: true
  attr :direction, :string, default: "above"

  def tooltip(assigns) do
    popup_class =
      if assigns.direction == "below",
        do: "top-full mt-1",
        else: "bottom-full mb-1.5"

    assigns = assign(assigns, :popup_class, popup_class)

    ~H"""
    <span class="group relative inline-flex items-center align-middle ml-0.5">
      <span class="inline-flex items-center justify-center w-3.5 h-3.5 rounded-full border border-gray-500 text-gray-500 text-[9px] leading-none cursor-help hover:border-gray-300 hover:text-gray-300 transition-colors select-none font-normal not-italic normal-case">i</span>
      <span class={[
        "pointer-events-none absolute left-1/2 z-50 w-52 -translate-x-1/2 rounded border border-gray-600 bg-gray-900 p-2 text-xs font-normal text-gray-200 opacity-0 transition-opacity group-hover:opacity-100 normal-case tracking-normal whitespace-normal",
        @popup_class
      ]}>
        {@text}
      </span>
    </span>
    """
  end

  attr :points, :list, required: true

  def equity_sparkline(assigns) do
    svg = if length(assigns.points) >= 2, do: build_sparkline_svg(assigns.points), else: nil
    assigns = assign(assigns, :svg, svg)

    ~H"""
    <div class="bg-gray-800 rounded-lg border border-gray-700 p-4">
      <h2 class="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Intraday Equity Trend</h2>
      <%= if @svg do %>
        <div class="h-16 w-full">
          {@svg}
        </div>
      <% else %>
        <p class="text-gray-600 text-sm text-center py-4">Collecting data…</p>
      <% end %>
    </div>
    """
  end

  defp build_sparkline_svg(points) do
    ordered = Enum.reverse(points)
    min_val = Enum.min(ordered)
    max_val = Enum.max(ordered)
    range = max(max_val - min_val, 1.0)
    width = 600
    height = 60
    n = length(ordered)

    coords =
      ordered
      |> Enum.with_index()
      |> Enum.map(fn {v, i} ->
        x = i / (n - 1) * width
        y = height - (v - min_val) / range * (height - 4) - 2
        "#{:erlang.float_to_binary(x + 0.0, decimals: 1)},#{:erlang.float_to_binary(y + 0.0, decimals: 1)}"
      end)
      |> Enum.join(" ")

    color = if List.last(ordered) >= List.first(ordered), do: "#3b82f6", else: "#ef4444"

    {:safe,
     ~s(<svg viewBox="0 0 #{width} #{height}" class="w-full h-full" preserveAspectRatio="none"><polyline points="#{coords}" fill="none" stroke="#{color}" stroke-width="2"/></svg>)}
  end

  @doc """
  Equity curve SVG panel. Renders a server-side ContEx SVG line chart,
  or a no-data fallback if fewer than 2 data points.

  Attrs:
    - points: list of maps with :date, :ending_equity, :peak_equity, :drawdown_pct
    - range: current range string — "30d" | "90d" | "all"
    - chart_id: unique DOM id for the wrapper div
    - show_range_toggle: boolean — whether to render the 30D/90D/All toggle
    - range_event: phx-click event name for the toggle buttons (only used when show_range_toggle: true)
  """
  attr :points, :list, required: true
  attr :range, :string, required: true
  attr :chart_id, :string, required: true
  attr :show_range_toggle, :boolean, default: false
  attr :range_event, :string, default: "set_equity_range"

  def equity_chart(assigns) do
    svg = if length(assigns.points) > 1, do: build_equity_svg(assigns.points), else: nil
    assigns = assign(assigns, :svg, svg)

    ~H"""
    <div class="bg-gray-800 rounded-lg border border-gray-700 p-4">
      <div class="flex items-center justify-between mb-3">
        <h2 class="text-xs font-semibold text-gray-400 uppercase tracking-wider">Equity Curve</h2>
        <%= if @show_range_toggle do %>
          <div class="flex rounded border border-gray-700 overflow-hidden text-xs">
            <%= for r <- ["30d", "90d", "all"] do %>
              <button
                phx-click={@range_event}
                phx-value-range={r}
                class={[
                  "px-3 py-1.5 transition-colors border-r border-gray-700 last:border-r-0",
                  if(@range == r,
                    do: "bg-blue-900/60 text-blue-300",
                    else: "bg-transparent text-gray-500 hover:text-gray-300"
                  )
                ]}
              >
                {String.upcase(r)}
              </button>
            <% end %>
          </div>
        <% end %>
      </div>
      <%= if @svg do %>
        <div id={@chart_id} class="w-full equity-chart">
          {@svg}
        </div>
        <div class="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-[10px] text-gray-500">
          <span class="flex items-center gap-1.5"><span class="inline-block w-4 h-0.5 bg-blue-500"></span>Equity</span>
          <span class="flex items-center gap-1.5"><span class="inline-block w-4 h-0.5 bg-gray-500"></span>Peak</span>
          <span class="flex items-center gap-1.5"><span class="inline-block w-4 h-0.5" style="background:#fbbf24"></span>−10% caution</span>
          <span class="flex items-center gap-1.5"><span class="inline-block w-4 h-0.5" style="background:#f97316"></span>−15% halt T2</span>
          <span class="flex items-center gap-1.5"><span class="inline-block w-4 h-0.5" style="background:#ef4444"></span>−20% halt all</span>
        </div>
      <% else %>
        <p class="text-gray-600 text-sm text-center py-8">No equity data yet.</p>
      <% end %>
    </div>
    """
  end

  defp build_equity_svg(points) do
    max_peak = points |> Enum.map(&to_float(&1.peak_equity)) |> Enum.max()
    cb10 = max_peak * 0.90
    cb15 = max_peak * 0.85
    cb20 = max_peak * 0.80

    data =
      Enum.map(points, fn p ->
        {
          NaiveDateTime.new!(p.date, ~T[00:00:00]),
          to_float(p.ending_equity),
          to_float(p.peak_equity),
          cb10,
          cb15,
          cb20
        }
      end)

    dataset = Contex.Dataset.new(data, ["Date", "Equity", "Peak", "CB10", "CB15", "CB20"])

    line_plot =
      Contex.LinePlot.new(dataset,
        mapping: %{x_col: "Date", y_cols: ["Equity", "Peak", "CB10", "CB15", "CB20"]},
        colour_palette: ["3b82f6", "6b7280", "fbbf24", "f97316", "ef4444"],
        smoothed: false
      )

    Contex.Plot.new(600, 200, line_plot)
    |> Contex.Plot.to_svg()
  end

  defp to_float(v) when is_float(v), do: v
  defp to_float(v) when is_integer(v), do: v * 1.0
  defp to_float(%Decimal{} = v), do: Decimal.to_float(v)
  # coveralls-ignore-next-line
  defp to_float(_), do: 0.0
end
