-- init-db/001_create_schema.sql
-- This runs automatically on first container start via docker-entrypoint-initdb.d
-- Creates the trading database schema with TimescaleDB hypertables

-- Enable TimescaleDB
CREATE EXTENSION IF NOT EXISTS timescaledb;

-------------------------------------------------------------------
-- TRADES: Every trade the system executes (the tax/audit log)
-------------------------------------------------------------------
CREATE TABLE trades (
    id              BIGSERIAL,
    time            TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    symbol          TEXT            NOT NULL,
    side            TEXT            NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity        NUMERIC(18,8)   NOT NULL,
    price           NUMERIC(18,8)   NOT NULL,
    total_value     NUMERIC(18,4)   NOT NULL,
    fees            NUMERIC(18,4)   NOT NULL DEFAULT 0,
    order_id        TEXT,                           -- Alpaca order ID
    strategy        TEXT            NOT NULL,        -- RSI2, ORB, CRYPTO_MOMENTUM, PEAD
    asset_class     TEXT            NOT NULL CHECK (asset_class IN ('equity', 'crypto', 'option')),
    realized_pnl    NUMERIC(18,4),                  -- filled on exit trades
    notes           TEXT,                            -- agent reasoning summary
    exit_reason     TEXT,                            -- stop_loss | take_profit | time_stop | stop_loss_auto | manual_liquidation
    PRIMARY KEY (id, time)
);

SELECT create_hypertable('trades', 'time');
CREATE INDEX idx_trades_symbol ON trades (symbol, time DESC);
CREATE INDEX idx_trades_strategy ON trades (strategy, time DESC);

-------------------------------------------------------------------
-- SIGNALS: Every signal the Watcher generates (even those not acted on)
-------------------------------------------------------------------
CREATE TABLE signals (
    id              BIGSERIAL,
    time            TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    symbol          TEXT            NOT NULL,
    strategy        TEXT            NOT NULL,
    signal_type     TEXT            NOT NULL CHECK (signal_type IN ('entry', 'exit', 'stop_loss', 'take_profit', 'time_stop')),
    direction       TEXT            NOT NULL CHECK (direction IN ('long', 'short', 'close')),
    confidence      NUMERIC(5,4),                   -- 0.0000 to 1.0000
    regime          TEXT,                            -- RANGING, EMERGING_TREND, STRONG_UPTREND, STRONG_DOWNTREND
    indicators      JSONB,                           -- snapshot of indicator values at signal time
    acted_on        BOOLEAN         NOT NULL DEFAULT FALSE,
    rejection_reason TEXT,                           -- why Portfolio Manager rejected it, if applicable
    PRIMARY KEY (id, time)
);

SELECT create_hypertable('signals', 'time');
CREATE INDEX idx_signals_symbol ON signals (symbol, time DESC);

-------------------------------------------------------------------
-- AGENT_DECISIONS: Portfolio Manager reasoning log
-------------------------------------------------------------------
CREATE TABLE agent_decisions (
    id              BIGSERIAL,
    time            TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    agent           TEXT            NOT NULL,        -- screener, watcher, portfolio_manager, supervisor
    decision_type   TEXT            NOT NULL,        -- approve_trade, reject_trade, adjust_allocation, halt, etc.
    input_summary   TEXT,                            -- what the agent was evaluating
    reasoning       TEXT,                            -- LLM output or code-path explanation
    output          JSONB,                           -- structured decision output
    model_used      TEXT,                            -- gpt-oss-120b, claude-sonnet-4, etc.
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    cost_usd        NUMERIC(10,6),                  -- estimated cost of this LLM call
    PRIMARY KEY (id, time)
);

SELECT create_hypertable('agent_decisions', 'time');

-------------------------------------------------------------------
-- DAILY_SUMMARY: End-of-day performance snapshot (Supervisor output)
-------------------------------------------------------------------
CREATE TABLE daily_summary (
    date            DATE            PRIMARY KEY,
    starting_equity NUMERIC(18,4)   NOT NULL,
    ending_equity   NUMERIC(18,4)   NOT NULL,
    daily_pnl       NUMERIC(18,4)   NOT NULL,
    daily_pnl_pct   NUMERIC(8,4)    NOT NULL,
    peak_equity     NUMERIC(18,4)   NOT NULL,       -- all-time high equity
    drawdown_pct    NUMERIC(8,4)    NOT NULL,        -- current drawdown from peak
    trades_executed INTEGER         NOT NULL DEFAULT 0,
    day_trades_used INTEGER         NOT NULL DEFAULT 0,
    winning_trades  INTEGER         NOT NULL DEFAULT 0,
    losing_trades   INTEGER         NOT NULL DEFAULT 0,
    total_fees      NUMERIC(18,4)   NOT NULL DEFAULT 0,
    total_llm_cost  NUMERIC(10,6)   NOT NULL DEFAULT 0,
    strategies_active TEXT[],                        -- which strategies ran today
    supervisor_notes TEXT,                           -- end-of-day review summary
    regime          TEXT                             -- market regime for the day
);

-------------------------------------------------------------------
-- POSITIONS: Current open positions (also tracked in Redis for speed)
-------------------------------------------------------------------
CREATE TABLE positions (
    id              BIGSERIAL       PRIMARY KEY,
    opened_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    closed_at       TIMESTAMPTZ,
    symbol          TEXT            NOT NULL,
    side            TEXT            NOT NULL DEFAULT 'long',
    quantity        NUMERIC(18,8)   NOT NULL,
    entry_price     NUMERIC(18,8)   NOT NULL,
    exit_price      NUMERIC(18,8),
    stop_price      NUMERIC(18,8)   NOT NULL,       -- current stop-loss level
    target_price    NUMERIC(18,8),                   -- take-profit target
    strategy        TEXT            NOT NULL,
    asset_class     TEXT            NOT NULL,
    is_day_trade    BOOLEAN         NOT NULL DEFAULT FALSE,
    alpaca_order_id TEXT,
    realized_pnl    NUMERIC(18,4),
    status          TEXT            NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'stopped_out'))
);

CREATE INDEX idx_positions_open ON positions (status) WHERE status = 'open';
CREATE INDEX idx_positions_symbol ON positions (symbol, opened_at DESC);

-------------------------------------------------------------------
-- COMPRESSION POLICY: Compress old data to save disk space
-- Trades older than 30 days get compressed (still queryable, just smaller)
-------------------------------------------------------------------
ALTER TABLE trades SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol,strategy'
);
SELECT add_compression_policy('trades', INTERVAL '30 days');

ALTER TABLE signals SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol,strategy'
);
SELECT add_compression_policy('signals', INTERVAL '30 days');

ALTER TABLE agent_decisions SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'agent'
);
SELECT add_compression_policy('agent_decisions', INTERVAL '30 days');
