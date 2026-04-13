defmodule Dashboard.LogTailer do
  use GenServer

  alias Phoenix.PubSub

  @pubsub Dashboard.PubSub
  @topic "logs"
  @default_poll_interval 1_000

  # ---------------------------------------------------------------------------
  # Public API
  # ---------------------------------------------------------------------------

  def start_link(opts \\ []) do
    {name, opts} = Keyword.pop(opts, :name, __MODULE__)
    server_opts = if name, do: [name: name], else: []
    GenServer.start_link(__MODULE__, opts, server_opts)
  end

  # ---------------------------------------------------------------------------
  # Callbacks
  # ---------------------------------------------------------------------------

  @impl true
  def init(opts) do
    log_dir = Keyword.get(opts, :log_dir, "/app/logs")
    syslog_path = Keyword.get(opts, :syslog_path, "/var/log/host/syslog")
    poll_interval = Keyword.get(opts, :poll_interval, @default_poll_interval)

    sources =
      log_dir
      |> build_sources(syslog_path)
      |> Map.new(fn {id, source} ->
        offset =
          case File.stat(resolve_path(source)) do
            {:ok, %{size: size}} -> size
            {:error, _} -> 0
          end

        {id, Map.put(source, :offset, offset)}
      end)

    Process.send_after(self(), :poll, poll_interval)
    {:ok, %{sources: sources, poll_interval: poll_interval}}
  end

  @impl true
  def handle_info(:poll, state) do
    {new_sources, all_lines} =
      Enum.reduce(state.sources, {state.sources, []}, fn {id, source}, {acc_sources, acc_lines} ->
        path = resolve_path(source)

        case read_new_lines(path, source.offset) do
          {:ok, lines, new_offset} ->
            tagged =
              Enum.map(lines, fn line ->
                %{source: id, label: source.label, color: source.color, line: line}
              end)

            {Map.put(acc_sources, id, %{source | offset: new_offset}), [tagged | acc_lines]}

          {:skip, new_offset} ->
            {Map.put(acc_sources, id, %{source | offset: new_offset}), acc_lines}
        end
      end)

    all_lines = List.flatten(all_lines)

    if all_lines != [] do
      PubSub.broadcast(@pubsub, @topic, {:log_lines, all_lines})
    end

    Process.send_after(self(), :poll, state.poll_interval)
    {:noreply, %{state | sources: new_sources}}
  end

  # ---------------------------------------------------------------------------
  # Private helpers
  # ---------------------------------------------------------------------------

  defp build_sources(log_dir, syslog_path) do
    %{
      "executor" =>
        %{label: "executor", color: "blue", type: :dated, dir: log_dir, name: "executor"},
      "portfolio_manager" =>
        %{label: "portfolio_manager", color: "green", type: :dated, dir: log_dir, name: "portfolio_manager"},
      "watcher" =>
        %{label: "watcher", color: "yellow", type: :dated, dir: log_dir, name: "watcher"},
      # Cron agents write to a single named file (not date-suffixed) because their
      # crontab entries redirect to a fixed path (e.g. >> screener.log 2>&1).
      "screener" =>
        %{label: "screener", color: "purple", type: :static, path: Path.join(log_dir, "screener.log")},
      "supervisor" =>
        %{label: "supervisor", color: "cyan", type: :static, path: Path.join(log_dir, "supervisor.log")},
      "docker_redis" =>
        %{label: "redis", color: "red", type: :static, path: Path.join(log_dir, "docker_redis.log")},
      "docker_timescaledb" =>
        %{label: "timescaledb", color: "orange", type: :static, path: Path.join(log_dir, "docker_timescaledb.log")},
      "docker_dashboard" =>
        %{label: "dashboard", color: "gray", type: :static, path: Path.join(log_dir, "docker_dashboard.log")},
      "vps_syslog" =>
        %{label: "syslog", color: "white", type: :static, path: syslog_path}
    }
  end

  defp resolve_path(%{type: :dated, dir: dir, name: name}) do
    Path.join(dir, "#{name}_#{Date.utc_today()}.log")
  end

  defp resolve_path(%{type: :static, path: path}), do: path

  defp read_new_lines(path, offset) do
    case File.stat(path) do
      {:error, _} ->
        {:skip, offset}

      {:ok, %{size: size}} when size < offset ->
        # File rotated — reset; content will be read next poll
        {:skip, 0}

      {:ok, %{size: size}} when size == offset ->
        {:skip, offset}

      {:ok, _} ->
        case File.open(path, [:read, :binary]) do
          {:ok, file} ->
            :file.position(file, offset)

            case IO.read(file, :eof) do
              {:error, _} ->
                File.close(file)
                {:skip, offset}

              content when is_binary(content) ->
                File.close(file)
                new_offset = offset + byte_size(content)
                lines = content |> String.split("\n") |> Enum.reject(&(&1 == ""))
                {:ok, lines, new_offset}
            end

          {:error, _} ->
            {:skip, offset}
        end
    end
  end
end
