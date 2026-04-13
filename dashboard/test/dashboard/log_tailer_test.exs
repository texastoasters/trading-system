defmodule Dashboard.LogTailerTest do
  use ExUnit.Case, async: true

  alias Dashboard.LogTailer

  setup do
    log_dir = Path.join(System.tmp_dir!(), "log_tailer_#{:rand.uniform(1_000_000)}")
    File.mkdir_p!(log_dir)
    on_exit(fn -> File.rm_rf!(log_dir) end)
    %{log_dir: log_dir}
  end

  defp start_tailer(log_dir) do
    start_supervised!(
      # name: nil prevents conflict with the supervisor-started named Dashboard.LogTailer
      {LogTailer, log_dir: log_dir, syslog_path: "/nonexistent", poll_interval: 100_000, name: nil}
    )
  end

  test "does not broadcast pre-existing content on first poll", %{log_dir: log_dir} do
    file = Path.join(log_dir, "screener.log")
    File.write!(file, "old line\n")

    pid = start_tailer(log_dir)
    Phoenix.PubSub.subscribe(Dashboard.PubSub, "logs")

    send(pid, :poll)
    refute_receive {:log_lines, _}, 300
  end

  test "broadcasts new lines written after init", %{log_dir: log_dir} do
    file = Path.join(log_dir, "screener.log")
    File.write!(file, "existing\n")

    pid = start_tailer(log_dir)
    Phoenix.PubSub.subscribe(Dashboard.PubSub, "logs")

    File.write!(file, "new line\n", [:append])
    send(pid, :poll)

    assert_receive {:log_lines, lines}, 1000
    assert length(lines) == 1
    assert hd(lines).source == "screener"
    assert hd(lines).label == "screener"
    assert hd(lines).color == "purple"
    assert hd(lines).line == "new line"
  end

  test "skips missing files without error or broadcast", %{log_dir: log_dir} do
    pid = start_tailer(log_dir)
    Phoenix.PubSub.subscribe(Dashboard.PubSub, "logs")

    send(pid, :poll)
    refute_receive {:log_lines, _}, 300
  end

  test "resets offset to 0 and skips on rotation detection", %{log_dir: log_dir} do
    file = Path.join(log_dir, "screener.log")
    File.write!(file, "long existing content that sets a high offset\n")

    pid = start_tailer(log_dir)
    Phoenix.PubSub.subscribe(Dashboard.PubSub, "logs")

    # Simulate rotation: truncate to shorter content
    File.write!(file, "new\n")

    send(pid, :poll)
    refute_receive {:log_lines, _}, 300

    # Next poll reads from 0 and finds "new"
    send(pid, :poll)
    assert_receive {:log_lines, lines}, 1000
    assert hd(lines).line == "new"
  end

  test "resolves today's date-suffixed file for daemon agents", %{log_dir: log_dir} do
    date = Date.utc_today()
    file = Path.join(log_dir, "executor_#{date}.log")
    File.write!(file, "")

    pid = start_tailer(log_dir)
    Phoenix.PubSub.subscribe(Dashboard.PubSub, "logs")

    File.write!(file, "exec line\n", [:append])
    send(pid, :poll)

    assert_receive {:log_lines, lines}, 1000
    assert Enum.any?(lines, &(&1.source == "executor" and &1.line == "exec line"))
  end

  test "broadcasts lines from multiple active files in one message", %{log_dir: log_dir} do
    screener = Path.join(log_dir, "screener.log")
    supervisor = Path.join(log_dir, "supervisor.log")
    File.write!(screener, "")
    File.write!(supervisor, "")

    pid = start_tailer(log_dir)
    Phoenix.PubSub.subscribe(Dashboard.PubSub, "logs")

    File.write!(screener, "screener line\n", [:append])
    File.write!(supervisor, "supervisor line\n", [:append])
    send(pid, :poll)

    assert_receive {:log_lines, lines}, 1000
    sources = Enum.map(lines, & &1.source)
    assert "screener" in sources
    assert "supervisor" in sources
  end

  test "handles file becoming unreadable between stat and open", %{log_dir: log_dir} do
    file = Path.join(log_dir, "screener.log")
    File.write!(file, "existing\n")

    pid = start_tailer(log_dir)
    Phoenix.PubSub.subscribe(Dashboard.PubSub, "logs")

    File.write!(file, "new line\n", [:append])
    File.chmod!(file, 0o000)

    send(pid, :poll)
    refute_receive {:log_lines, _}, 300

    # Restore so cleanup can delete the tmp dir
    File.chmod!(file, 0o644)
  end
end
