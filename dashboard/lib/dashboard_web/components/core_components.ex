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
end
