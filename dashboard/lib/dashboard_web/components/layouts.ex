defmodule DashboardWeb.Layouts do
  use DashboardWeb, :html

  embed_templates "../layouts/*"

  @nav_items [
    {"Dashboard", "/"},
    {"Universe", "/universe"},
    {"Trades", "/trades"},
    {"Performance", "/performance"},
    {"Logs", "/logs"},
    {"Settings", "/settings"}
  ]

  attr :link_class, :string,
    default: "px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200 rounded hover:bg-gray-800 transition-colors"

  def nav_links(assigns) do
    assigns = assign(assigns, :items, @nav_items)

    ~H"""
    <a :for={{label, path} <- @items} href={path} class={@link_class}>{label}</a>
    """
  end
end
