defmodule Dashboard.FakeRedix do
  @moduledoc """
  GenServer stub for :redix that returns {:error, reason} for all commands.

  Redix.command/2 and Redix.pipeline/2 call Redix.Connection.pipeline/4, which
  casts via :gen_statem.cast. Wire format changed across Redix minors:

    * Redix ≤1.5: `{:pipeline, commands, {from_pid, ref}, timeout}`
    * Redix 1.8+: `{:pipeline, commands, request_id}` where request_id is a
      monitor/alias reference; reply is `{request_id, resp}` sent to that alias.

  mix.lock is gitignored, so CI floats on `~> 1.5`. Handle both shapes so the
  stub cannot FunctionClauseError and take RedisPoller / LiveView down with it.
  """
  use GenServer

  def start_link(_opts \\ []) do
    GenServer.start_link(__MODULE__, :error)
  end

  @impl true
  def init(mode), do: {:ok, mode}

  # Redix 1.8+: {:pipeline, commands, request_id}
  @impl true
  def handle_cast({:pipeline, _commands, from}, state) when is_reference(from) do
    send(from, {from, {:error, :test_pipeline_error}})
    {:noreply, state}
  end

  # Redix ≤1.5: {:pipeline, commands, {from_pid, ref}, timeout}
  def handle_cast({:pipeline, _commands, {from_pid, request_id}, _timeout}, state) do
    send(from_pid, {request_id, {:error, :test_pipeline_error}})
    {:noreply, state}
  end
end
