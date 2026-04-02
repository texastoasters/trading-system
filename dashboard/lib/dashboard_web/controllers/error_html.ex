defmodule DashboardWeb.ErrorHTML do
  use DashboardWeb, :html

  # Renders 404 and 500 error pages.
  # Use Phoenix's default templates which render "404.html" etc.
  def render(template, _assigns) do
    Phoenix.Controller.status_message_from_template(template)
  end
end
