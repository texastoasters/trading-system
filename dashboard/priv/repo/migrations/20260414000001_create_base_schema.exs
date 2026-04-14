defmodule Dashboard.Repo.Migrations.CreateBaseSchema do
  use Ecto.Migration

  # create_hypertable() cannot run inside a transaction block.
  # IF NOT EXISTS guards on every statement make this migration re-runnable.
  @disable_ddl_transaction true

  def up do
    create_tables()
    create_indexes()
    setup_timescaledb_if_available()
  end

  def down do
    execute "DROP TABLE IF EXISTS positions CASCADE"
    execute "DROP TABLE IF EXISTS daily_summary CASCADE"
    execute "DROP TABLE IF EXISTS agent_decisions CASCADE"
    execute "DROP TABLE IF EXISTS signals CASCADE"
    execute "DROP TABLE IF EXISTS trades CASCADE"
  end

  # ── Table creation ──────────────────────────────────────────────────────────

  defp create_tables do
    execute """
    CREATE TABLE IF NOT EXISTS trades (
        id              BIGSERIAL,
        time            TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
        symbol          TEXT            NOT NULL,
        side            TEXT            NOT NULL CHECK (side IN ('buy', 'sell')),
        quantity        NUMERIC(18,8)   NOT NULL,
        price           NUMERIC(18,8)   NOT NULL,
        total_value     NUMERIC(18,4)   NOT NULL,
        fees            NUMERIC(18,4)   NOT NULL DEFAULT 0,
        order_id        TEXT,
        strategy        TEXT            NOT NULL,
        asset_class     TEXT            NOT NULL CHECK (asset_class IN ('equity', 'crypto', 'option')),
        realized_pnl    NUMERIC(18,4),
        notes           TEXT,
        exit_reason     TEXT,
        PRIMARY KEY (id, time)
    )
    """

    execute """
    CREATE TABLE IF NOT EXISTS signals (
        id              BIGSERIAL,
        time            TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
        symbol          TEXT            NOT NULL,
        strategy        TEXT            NOT NULL,
        signal_type     TEXT            NOT NULL CHECK (signal_type IN ('entry', 'exit', 'stop_loss', 'take_profit', 'time_stop')),
        direction       TEXT            NOT NULL CHECK (direction IN ('long', 'short', 'close')),
        confidence      NUMERIC(5,4),
        regime          TEXT,
        indicators      JSONB,
        acted_on        BOOLEAN         NOT NULL DEFAULT FALSE,
        rejection_reason TEXT,
        PRIMARY KEY (id, time)
    )
    """

    execute """
    CREATE TABLE IF NOT EXISTS agent_decisions (
        id              BIGSERIAL,
        time            TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
        agent           TEXT            NOT NULL,
        decision_type   TEXT            NOT NULL,
        input_summary   TEXT,
        reasoning       TEXT,
        output          JSONB,
        model_used      TEXT,
        tokens_in       INTEGER,
        tokens_out      INTEGER,
        cost_usd        NUMERIC(10,6),
        PRIMARY KEY (id, time)
    )
    """

    execute """
    CREATE TABLE IF NOT EXISTS daily_summary (
        date            DATE            PRIMARY KEY,
        starting_equity NUMERIC(18,4)   NOT NULL,
        ending_equity   NUMERIC(18,4)   NOT NULL,
        daily_pnl       NUMERIC(18,4)   NOT NULL,
        daily_pnl_pct   NUMERIC(8,4)    NOT NULL,
        peak_equity     NUMERIC(18,4)   NOT NULL,
        drawdown_pct    NUMERIC(8,4)    NOT NULL,
        trades_executed INTEGER         NOT NULL DEFAULT 0,
        day_trades_used INTEGER         NOT NULL DEFAULT 0,
        winning_trades  INTEGER         NOT NULL DEFAULT 0,
        losing_trades   INTEGER         NOT NULL DEFAULT 0,
        total_fees      NUMERIC(18,4)   NOT NULL DEFAULT 0,
        total_llm_cost  NUMERIC(10,6)   NOT NULL DEFAULT 0,
        strategies_active TEXT[],
        supervisor_notes TEXT,
        regime          TEXT
    )
    """

    execute """
    CREATE TABLE IF NOT EXISTS positions (
        id              BIGSERIAL       PRIMARY KEY,
        opened_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
        closed_at       TIMESTAMPTZ,
        symbol          TEXT            NOT NULL,
        side            TEXT            NOT NULL DEFAULT 'long',
        quantity        NUMERIC(18,8)   NOT NULL,
        entry_price     NUMERIC(18,8)   NOT NULL,
        exit_price      NUMERIC(18,8),
        stop_price      NUMERIC(18,8)   NOT NULL,
        target_price    NUMERIC(18,8),
        strategy        TEXT            NOT NULL,
        asset_class     TEXT            NOT NULL,
        is_day_trade    BOOLEAN         NOT NULL DEFAULT FALSE,
        alpaca_order_id TEXT,
        realized_pnl    NUMERIC(18,4),
        status          TEXT            NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'stopped_out'))
    )
    """
  end

  # ── Indexes ─────────────────────────────────────────────────────────────────

  defp create_indexes do
    execute "CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades (symbol, time DESC)"
    execute "CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades (strategy, time DESC)"
    execute "CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals (symbol, time DESC)"
    execute "CREATE INDEX IF NOT EXISTS idx_positions_open ON positions (status) WHERE status = 'open'"
    execute "CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions (symbol, opened_at DESC)"
  end

  # ── TimescaleDB setup ────────────────────────────────────────────────────────
  # Skipped gracefully when TimescaleDB is not installed (e.g. test environment).

  defp setup_timescaledb_if_available do
    result =
      repo().query!(
        "SELECT COUNT(*) FROM pg_available_extensions WHERE name = 'timescaledb'"
      )

    case result.rows do
      [[count]] when count > 0 -> do_timescaledb_setup()
      _ -> :ok
    end
  end

  defp do_timescaledb_setup do
    execute "CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE"

    execute "SELECT create_hypertable('trades', 'time', if_not_exists => TRUE)"
    execute "SELECT create_hypertable('signals', 'time', if_not_exists => TRUE)"
    execute "SELECT create_hypertable('agent_decisions', 'time', if_not_exists => TRUE)"

    execute """
    ALTER TABLE trades SET (
        timescaledb.compress,
        timescaledb.compress_segmentby = 'symbol,strategy'
    )
    """

    execute "SELECT add_compression_policy('trades', INTERVAL '30 days', if_not_exists => TRUE)"

    execute """
    ALTER TABLE signals SET (
        timescaledb.compress,
        timescaledb.compress_segmentby = 'symbol,strategy'
    )
    """

    execute "SELECT add_compression_policy('signals', INTERVAL '30 days', if_not_exists => TRUE)"

    execute """
    ALTER TABLE agent_decisions SET (
        timescaledb.compress,
        timescaledb.compress_segmentby = 'agent'
    )
    """

    execute "SELECT add_compression_policy('agent_decisions', INTERVAL '30 days', if_not_exists => TRUE)"
  end
end
