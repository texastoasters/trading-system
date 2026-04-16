defmodule Dashboard.FakeRedix do
  @moduledoc """
  GenServer stub for :redix that returns {:error, reason} for all pipeline calls.

  Redix.Connection.pipeline/4 casts {:pipeline, commands, {from_pid, ref}, timeout} via
  :gen_statem.cast, then blocks waiting for {ref, response}. Since :gen_statem.cast uses
  the same $gen_cast wire protocol as GenServer.cast, a GenServer handle_cast can handle
  the message and send the expected reply directly to from_pid.

  Used to exercise error paths in RedisPoller when Redis is unavailable.
  """
  use GenServer

  def start_link(_opts \\ []) do
    GenServer.start_link(__MODULE__, :error)
  end

  @impl true
  def init(mode), do: {:ok, mode}

  @impl true
  def handle_cast({:pipeline, _commands, {from_pid, request_id}, _timeout}, state) do
    send(from_pid, {request_id, {:error, :test_pipeline_error}})
    {:noreply, state}
  end

  @impl true
  def handle_call({:command, _commands, _timeout}, _from, state) do
    {:reply, {:error, :test_command_error}, state}
  end
end
