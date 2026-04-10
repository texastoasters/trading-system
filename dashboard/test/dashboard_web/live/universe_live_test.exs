defmodule DashboardWeb.UniverseLiveTest do
  use DashboardWeb.ConnCase

  describe "mount" do
    test "renders page title", %{conn: conn} do
      {:ok, _view, html} = live(conn, "/universe")
      assert html =~ "Universe"
    end

    test "initial assigns are empty", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.universe == nil
      assert assigns.watchlist == []
      assert assigns.redis_positions == %{}
    end
  end

  describe "handle_info :state_update" do
    test "populates universe assign", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      universe = %{
        "tier1" => ["SPY", "QQQ"],
        "tier2" => ["GOOGL"],
        "tier3" => ["IWM"]
      }

      state = %{
        "trading:universe" => universe,
        "trading:watchlist" => [],
        "trading:positions" => %{}
      }

      send(view.pid, {:state_update, state})
      _ = render(view)
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.universe == universe
    end

    test "shows symbols from universe in rendered html", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      state = %{
        "trading:universe" => %{"tier1" => ["SPY"], "tier2" => [], "tier3" => []},
        "trading:watchlist" => [],
        "trading:positions" => %{}
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      assert html =~ "SPY"
    end

    test "uses empty lists when watchlist/positions absent", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")
      send(view.pid, {:state_update, %{}})
      assigns = :sys.get_state(view.pid).socket.assigns
      assert assigns.watchlist == []
      assert assigns.redis_positions == %{}
    end

    test "ignores unrelated pubsub messages", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")
      send(view.pid, {:some_other_message, "ignored"})
      assert render(view) =~ "Symbol Universe"
    end
  end

  describe "universe_live helpers" do
    test "build_tiers returns empty list when universe is nil", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      state = %{
        "trading:universe" => nil,
        "trading:watchlist" => [],
        "trading:positions" => %{}
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      # When universe is nil, no tier sections should render
      refute html =~ "Tier 1 — Core"
      refute html =~ "Tier 2 — Extended"
      refute html =~ "Tier 3 — Satellite"
    end

    test "build_tiers with all three tiers populated", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      state = %{
        "trading:universe" => %{
          "tier1" => ["SPY", "QQQ"],
          "tier2" => ["GOOGL", "TSLA"],
          "tier3" => ["IWM", "XLE"]
        },
        "trading:watchlist" => [],
        "trading:positions" => %{}
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      # All three tier labels should appear
      assert html =~ "Tier 1 — Core"
      assert html =~ "Tier 2 — Extended"
      assert html =~ "Tier 3 — Satellite"
      # All symbols should appear
      assert html =~ "SPY"
      assert html =~ "QQQ"
      assert html =~ "GOOGL"
      assert html =~ "TSLA"
      assert html =~ "IWM"
      assert html =~ "XLE"
    end

    test "nil universe renders without crashing", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      state = %{
        "trading:universe" => nil,
        "trading:watchlist" => [],
        "trading:positions" => %{}
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      assert is_binary(html)
    end

    test "total_count returns correct sum across all tiers", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      state = %{
        "trading:universe" => %{
          "tier1" => ["SPY", "QQQ", "NVDA"],
          "tier2" => ["GOOGL"],
          "tier3" => ["IWM", "XLE"]
        },
        "trading:watchlist" => [],
        "trading:positions" => %{}
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      # 6 total instruments (3 + 1 + 2) - displayed as "6 symbols tracked"
      assert html =~ "<span class=\"text-white font-semibold\">6</span>"
    end

    test "tier_badge renders T1 with yellow styling", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      state = %{
        "trading:universe" => %{"tier1" => ["SPY"], "tier2" => [], "tier3" => []},
        "trading:watchlist" => [],
        "trading:positions" => %{}
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      assert html =~ "T1"
      assert html =~ "bg-yellow-900/40"
      assert html =~ "text-yellow-400"
    end

    test "tier_badge renders T2 with blue styling", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      state = %{
        "trading:universe" => %{"tier1" => [], "tier2" => ["GOOGL"], "tier3" => []},
        "trading:watchlist" => [],
        "trading:positions" => %{}
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      assert html =~ "T2"
      assert html =~ "bg-blue-900/40"
      assert html =~ "text-blue-400"
    end

    test "tier_badge renders T3 with gray styling", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      state = %{
        "trading:universe" => %{"tier1" => [], "tier2" => [], "tier3" => ["IWM"]},
        "trading:watchlist" => [],
        "trading:positions" => %{}
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      assert html =~ "T3"
      assert html =~ "bg-gray-900/40"
      assert html =~ "text-gray-400"
    end

    test "symbol_status shows HELD when position exists", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      state = %{
        "trading:universe" => %{"tier1" => ["SPY"], "tier2" => [], "tier3" => []},
        "trading:watchlist" => [],
        "trading:positions" => %{"SPY" => %{"quantity" => 10}}
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      assert html =~ "HELD"
      assert html =~ "bg-orange-900/40"
    end

    test "symbol_status shows STRONG signal when priority is strong_signal", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      state = %{
        "trading:universe" => %{"tier1" => ["SPY"], "tier2" => [], "tier3" => []},
        "trading:watchlist" => [
          %{
            "symbol" => "SPY",
            "priority" => "strong_signal",
            "rsi2" => 5.0,
            "close" => 100.0,
            "sma200" => 99.0,
            "above_sma" => true
          }
        ],
        "trading:positions" => %{}
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      assert html =~ "STRONG"
      assert html =~ "bg-green-900/40"
    end

    test "symbol_status shows SIGNAL when priority is signal", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      state = %{
        "trading:universe" => %{"tier1" => ["SPY"], "tier2" => [], "tier3" => []},
        "trading:watchlist" => [
          %{
            "symbol" => "SPY",
            "priority" => "signal",
            "rsi2" => 10.0,
            "close" => 100.0,
            "sma200" => 99.0,
            "above_sma" => true
          }
        ],
        "trading:positions" => %{}
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      assert html =~ "SIGNAL"
      assert html =~ "bg-blue-900/40"
    end

    test "symbol_status shows WATCH when priority is watch", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      state = %{
        "trading:universe" => %{"tier1" => ["SPY"], "tier2" => [], "tier3" => []},
        "trading:watchlist" => [
          %{
            "symbol" => "SPY",
            "priority" => "watch",
            "rsi2" => 50.0,
            "close" => 100.0,
            "sma200" => 99.0,
            "above_sma" => true
          }
        ],
        "trading:positions" => %{}
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      assert html =~ "WATCH"
      assert html =~ "bg-gray-800"
    end

    test "symbol_status shows nothing when symbol not in watchlist", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      state = %{
        "trading:universe" => %{"tier1" => ["SPY", "QQQ"], "tier2" => [], "tier3" => []},
        "trading:watchlist" => [
          %{
            "symbol" => "QQQ",
            "priority" => "signal",
            "rsi2" => 25.0,
            "close" => 350.0,
            "sma200" => 345.0,
            "above_sma" => true
          }
        ],
        "trading:positions" => %{}
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      # SPY should still render but without signal badge (it's not in watchlist)
      assert html =~ "SPY"
      # QQQ should have the SIGNAL badge
      assert html =~ "SIGNAL"
    end

    test "format_float displays nil as dash", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      state = %{
        "trading:universe" => %{"tier1" => ["SPY"], "tier2" => [], "tier3" => []},
        "trading:watchlist" => [
          %{
            "symbol" => "SPY",
            "priority" => nil,
            "rsi2" => nil,
            "close" => 100.0,
            "sma200" => 99.0,
            "above_sma" => true
          }
        ],
        "trading:positions" => %{}
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      # RSI2 should show dash when nil
      assert html =~ "—"
    end

    test "format_float displays float with one decimal place", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      state = %{
        "trading:universe" => %{"tier1" => ["SPY"], "tier2" => [], "tier3" => []},
        "trading:watchlist" => [
          %{
            "symbol" => "SPY",
            "priority" => "signal",
            "rsi2" => 15.555,
            "close" => 100.0,
            "sma200" => 99.0,
            "above_sma" => true
          }
        ],
        "trading:positions" => %{}
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      # RSI2 15.6 (erlang binary rounds to 1 decimal)
      assert html =~ "15.6"
    end

    test "format_price displays nil as dash", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      state = %{
        "trading:universe" => %{"tier1" => ["SPY"], "tier2" => [], "tier3" => []},
        "trading:watchlist" => [
          %{
            "symbol" => "SPY",
            "priority" => nil,
            "rsi2" => nil,
            "close" => nil,
            "sma200" => nil,
            "above_sma" => false
          }
        ],
        "trading:positions" => %{}
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      # When close and sma200 are nil, should show dashes
      assert html =~ "—"
    end

    test "format_price displays float as currency with 2 decimals", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      state = %{
        "trading:universe" => %{"tier1" => ["SPY"], "tier2" => [], "tier3" => []},
        "trading:watchlist" => [
          %{
            "symbol" => "SPY",
            "priority" => "signal",
            "rsi2" => 10.0,
            "close" => 150.456,
            "sma200" => 149.999,
            "above_sma" => true
          }
        ],
        "trading:positions" => %{}
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      # Close price should show as $150.46
      assert html =~ "$150.46"
      # SMA200 should show as $150.00
      assert html =~ "$150.00"
    end

    test "enrich_tier includes all watchlist fields", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      state = %{
        "trading:universe" => %{"tier1" => ["SPY", "QQQ"], "tier2" => [], "tier3" => []},
        "trading:watchlist" => [
          %{
            "symbol" => "SPY",
            "priority" => "strong_signal",
            "rsi2" => 8.5,
            "close" => 455.12,
            "sma200" => 450.0,
            "above_sma" => true
          },
          %{
            "symbol" => "QQQ",
            "priority" => "watch",
            "rsi2" => 35.2,
            "close" => 385.0,
            "sma200" => 390.0,
            "above_sma" => false
          }
        ],
        "trading:positions" => %{"SPY" => %{"quantity" => 5}}
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      # Check symbols appear with their data
      assert html =~ "SPY"
      assert html =~ "QQQ"
      assert html =~ "$455.12"
      assert html =~ "$385.00"
      assert html =~ "8.5"
      assert html =~ "35.2"
      # SPY should show HELD because it's in positions
      assert html =~ "HELD"
    end

    test "build_tiers filters out empty tier arrays", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      state = %{
        "trading:universe" => %{
          "tier1" => ["SPY"],
          "tier2" => [],
          "tier3" => []
        },
        "trading:watchlist" => [],
        "trading:positions" => %{}
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      # Only Tier 1 should appear (tier 2 and 3 are empty)
      assert html =~ "Tier 1 — Core"
      refute html =~ "Tier 2 — Extended"
      refute html =~ "Tier 3 — Satellite"
    end

    test "above_sma indicator renders correctly", %{conn: conn} do
      {:ok, view, _} = live(conn, "/universe")

      state = %{
        "trading:universe" => %{
          "tier1" => ["SPY", "QQQ"],
          "tier2" => [],
          "tier3" => []
        },
        "trading:watchlist" => [
          %{
            "symbol" => "SPY",
            "priority" => "signal",
            "rsi2" => 10.0,
            "close" => 455.12,
            "sma200" => 450.0,
            "above_sma" => true
          },
          %{
            "symbol" => "QQQ",
            "priority" => "signal",
            "rsi2" => 35.0,
            "close" => 385.0,
            "sma200" => 390.0,
            "above_sma" => false
          }
        ],
        "trading:positions" => %{}
      }

      send(view.pid, {:state_update, state})
      html = render(view)
      # Both symbols should render
      assert html =~ "SPY"
      assert html =~ "QQQ"
    end
  end
end
