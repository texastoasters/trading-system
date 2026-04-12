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
  Equity curve canvas panel. Renders a Chart.js canvas with JSON-encoded points,
  or a no-data fallback if fewer than 2 data points.

  Attrs:
    - points: list of maps with :date, :ending_equity, :peak_equity, :drawdown_pct
    - range: current range string — "30d" | "90d" | "all"
    - chart_id: unique DOM id for the canvas element
    - show_range_toggle: boolean — whether to render the 30D/90D/All toggle
    - range_event: phx-click event name for the toggle buttons (only used when show_range_toggle: true)
  """
  attr :points, :list, required: true
  attr :range, :string, required: true
  attr :chart_id, :string, required: true
  attr :show_range_toggle, :boolean, default: false
  attr :range_event, :string, default: "set_equity_range"

  def equity_chart(assigns) do
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
      <%= if length(@points) > 1 do %>
        <canvas
          id={@chart_id}
          phx-hook="EquityChart"
          data-points={Jason.encode!(@points)}
          class="w-full"
          style="height: 200px;"
        ></canvas>
      <% else %>
        <p class="text-gray-600 text-sm text-center py-8">No equity data yet.</p>
      <% end %>
    </div>
    """
  end
end
