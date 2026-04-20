defmodule Dashboard.ScreenerStatus do
  @timezone "America/New_York"
  @run_hour 16
  @run_minute 15
  @grace_seconds 30 * 60

  @doc """
  Returns the most recent weekday 4:15 PM America/New_York as a UTC DateTime.
  """
  def most_recent_run_utc(now_utc \\ DateTime.utc_now()) do
    {:ok, now_ny} = DateTime.shift_zone(now_utc, @timezone)

    candidate_ny =
      now_ny
      |> DateTime.to_date()
      |> then(&NaiveDateTime.new!(&1, Time.new!(@run_hour, @run_minute, 0)))
      |> then(&DateTime.from_naive!(&1, @timezone))

    candidate_ny =
      if DateTime.compare(candidate_ny, now_ny) == :gt,
        do: prev_day(candidate_ny),
        else: candidate_ny

    candidate_ny = last_weekday(candidate_ny)
    {:ok, utc} = DateTime.shift_zone(candidate_ny, "Etc/UTC")
    utc
  end

  @doc """
  Returns true if the screener missed its most recent expected run.
  nil heartbeat = awaiting first run = not stale.
  """
  def stale?(nil, _now_utc), do: false

  def stale?(last_run_str, now_utc) do
    with {:ok, ndt} <- NaiveDateTime.from_iso8601(last_run_str),
         last_utc <- DateTime.from_naive!(ndt, "Etc/UTC"),
         expected_utc <- most_recent_run_utc(now_utc) do
      DateTime.diff(last_utc, expected_utc, :second) < -@grace_seconds
    else
      _ -> true
    end
  end

  defp prev_day(dt) do
    new_date = Date.add(DateTime.to_date(dt), -1)
    NaiveDateTime.new!(new_date, Time.new!(@run_hour, @run_minute, 0))
    |> DateTime.from_naive!(@timezone)
  end

  defp last_weekday(dt) do
    if Date.day_of_week(DateTime.to_date(dt)) >= 6,
      do: last_weekday(prev_day(dt)),
      else: dt
  end
end
