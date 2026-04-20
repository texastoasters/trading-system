defmodule Dashboard.ScreenerStatusTest do
  use ExUnit.Case, async: true

  alias Dashboard.ScreenerStatus

  # All test times use 2026-04-20 (Monday, EDT = UTC-4) as base.

  defp utc(year, month, day, hour, minute \\ 0) do
    DateTime.new!(Date.new!(year, month, day), Time.new!(hour, minute, 0), "Etc/UTC")
  end

  describe "most_recent_run_utc/1" do
    test "monday morning returns friday 4:15 PM ET" do
      # Mon 9:00 AM ET = 13:00 UTC
      assert ScreenerStatus.most_recent_run_utc(utc(2026, 4, 20, 13, 0)) == utc(2026, 4, 17, 20, 15)
    end

    test "monday evening after 4:15 PM ET returns monday" do
      # Mon 5:00 PM ET = 21:00 UTC
      assert ScreenerStatus.most_recent_run_utc(utc(2026, 4, 20, 21, 0)) == utc(2026, 4, 20, 20, 15)
    end

    test "monday before 4:15 PM ET returns friday" do
      # Mon 4:10 PM ET = 20:10 UTC
      assert ScreenerStatus.most_recent_run_utc(utc(2026, 4, 20, 20, 10)) == utc(2026, 4, 17, 20, 15)
    end

    test "saturday returns friday" do
      assert ScreenerStatus.most_recent_run_utc(utc(2026, 4, 18, 13, 0)) == utc(2026, 4, 17, 20, 15)
    end

    test "sunday returns friday" do
      assert ScreenerStatus.most_recent_run_utc(utc(2026, 4, 19, 21, 0)) == utc(2026, 4, 17, 20, 15)
    end
  end

  describe "stale?/2" do
    test "not stale when ran after most recent expected run" do
      # now=Mon 9am ET, hb=Fri 4:30 PM ET (20:30 UTC)
      refute ScreenerStatus.stale?("2026-04-17T20:30:00", utc(2026, 4, 20, 13, 0))
    end

    test "stale when missed most recent run" do
      # now=Mon 9am ET, hb=Thu 4:30 PM ET (missed Friday)
      assert ScreenerStatus.stale?("2026-04-16T20:30:00", utc(2026, 4, 20, 13, 0))
    end

    test "not stale monday evening after today run" do
      # now=Mon 5pm ET, hb=Mon 4:30 PM ET
      refute ScreenerStatus.stale?("2026-04-20T20:30:00", utc(2026, 4, 20, 21, 0))
    end

    test "not stale saturday after friday run" do
      refute ScreenerStatus.stale?("2026-04-17T20:30:00", utc(2026, 4, 18, 13, 0))
    end

    test "not stale within grace period" do
      # now=Mon 4:30 PM ET, hb=Mon 4:20 PM ET (5 min after cron fired)
      refute ScreenerStatus.stale?("2026-04-20T20:20:00", utc(2026, 4, 20, 20, 30))
    end

    test "stale just outside grace period" do
      # expected=Mon 20:15 UTC, grace threshold=19:45 UTC, hb=19:44 UTC
      assert ScreenerStatus.stale?("2026-04-20T19:44:00", utc(2026, 4, 20, 21, 0))
    end

    test "nil heartbeat is not stale (awaiting first run)" do
      refute ScreenerStatus.stale?(nil, utc(2026, 4, 20, 21, 0))
    end
  end
end
