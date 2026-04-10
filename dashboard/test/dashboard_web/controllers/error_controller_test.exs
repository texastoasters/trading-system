defmodule DashboardWeb.ErrorControllerTest do
  use ExUnit.Case, async: true

  describe "ErrorHTML" do
    test "render 404.html returns status message string" do
      result = DashboardWeb.ErrorHTML.render("404.html", %{})
      assert is_binary(result)
    end

    test "render 500.html returns status message string" do
      result = DashboardWeb.ErrorHTML.render("500.html", %{})
      assert is_binary(result)
    end
  end

  describe "ErrorJSON" do
    test "render 404.json returns correct error response" do
      result = DashboardWeb.ErrorJSON.render("404.json", %{})
      assert result == %{errors: %{detail: "Not Found"}}
    end

    test "render 500.json returns correct error response" do
      result = DashboardWeb.ErrorJSON.render("500.json", %{})
      assert result == %{errors: %{detail: "Internal Server Error"}}
    end
  end
end
