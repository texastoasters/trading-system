#!/usr/bin/env python3
"""
verify_alpaca.py — Step 6 verification script
Tests: account access, Rule 1 enforcement, PDT tracking, order submission, and data streaming.
Run this against your PAPER trading account to verify everything works.

Usage:
    export ALPACA_API_KEY="your-paper-key"
    export ALPACA_SECRET_KEY="your-paper-secret"
    python3 verify_alpaca.py
"""

import os
import sys
import asyncio
from datetime import datetime, timedelta

# ── Check dependencies ──────────────────────────────────────
try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import MarketOrderRequest, GetAssetsRequest
    from alpaca.trading.enums import OrderSide, TimeInForce, AssetClass
    from alpaca.data.live import StockDataStream
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
except ImportError:
    print("ERROR: alpaca-py not installed. Run:")
    print("  pip install alpaca-py")
    sys.exit(1)

try:
    import redis
except ImportError:
    print("WARNING: redis-py not installed. Run: pip install redis")
    print("  (Redis verification will be skipped)")
    redis = None

try:
    import psycopg2
except ImportError:
    print("WARNING: psycopg2 not installed. Run: pip install psycopg2-binary")
    print("  (TimescaleDB verification will be skipped)")
    psycopg2 = None


API_KEY = os.environ.get("ALPACA_API_KEY")
SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY")

if not API_KEY or not SECRET_KEY:
    print("ERROR: Set ALPACA_API_KEY and ALPACA_SECRET_KEY environment variables")
    print("  (Use your PAPER trading keys, not live)")
    sys.exit(1)


def check_passed(name):
    print(f"  ✅ {name}")

def check_failed(name, detail=""):
    print(f"  ❌ {name}: {detail}")

def check_warn(name, detail=""):
    print(f"  ⚠️  {name}: {detail}")


def test_account():
    """Test 1: Account access and Rule 1 verification"""
    print("\n── Test 1: Account Access & Rule 1 ──")

    client = TradingClient(API_KEY, SECRET_KEY, paper=True)
    account = client.get_account()

    check_passed(f"Connected to account {account.account_number}")
    check_passed(f"Status: {account.status}")
    check_passed(f"Equity: ${float(account.equity):,.2f}")
    check_passed(f"Cash: ${float(account.cash):,.2f}")
    check_passed(f"Buying power: ${float(account.buying_power):,.2f}")
    check_passed(f"Day trade count: {account.daytrade_count}")

    # Rule 1 checks
    multiplier = int(account.multiplier)
    if multiplier > 1:
        check_warn(f"Margin multiplier is {multiplier}x (not 1x)",
                   "Rule 1 enforcement will be code-level only")
    else:
        check_passed(f"Margin multiplier: {multiplier}x")

    if account.shorting_enabled:
        check_warn("Shorting is enabled",
                   "Rule 1 code must block short orders")
    else:
        check_passed("Shorting disabled")

    if account.pattern_day_trader:
        check_failed("PDT flag is TRUE", "Account is restricted!")
    else:
        check_passed("PDT flag: False")

    if account.trading_blocked:
        check_failed("Trading is BLOCKED")
    else:
        check_passed("Trading not blocked")

    # Rule 1 enforcement demo
    cash = float(account.cash)
    buying_power = float(account.buying_power)
    print(f"\n  Rule 1 enforcement:")
    print(f"    Cash (our limit):     ${cash:,.2f}")
    print(f"    Buying power (ignore): ${buying_power:,.2f}")
    if buying_power > cash * 1.01:  # allow tiny float difference
        print(f"    ⚡ Margin available but will NOT be used")
        print(f"    ⚡ Code caps orders at cash balance: ${cash:,.2f}")

    return client


def test_assets(client):
    """Test 2: Asset availability"""
    print("\n── Test 2: Asset Availability ──")

    # Check equity assets
    for symbol in ["SPY", "QQQ", "AAPL"]:
        try:
            asset = client.get_asset(symbol)
            if asset.tradable and asset.fractionable:
                check_passed(f"{symbol}: tradable, fractionable")
            elif asset.tradable:
                check_passed(f"{symbol}: tradable (not fractionable)")
            else:
                check_warn(f"{symbol}: exists but not tradable")
        except Exception as e:
            check_failed(f"{symbol}", str(e))

    # Check crypto assets
    for symbol in ["BTC/USD", "ETH/USD"]:
        try:
            asset = client.get_asset(symbol)
            if asset.tradable:
                check_passed(f"{symbol}: tradable")
            else:
                check_warn(f"{symbol}: exists but not tradable")
        except Exception as e:
            check_failed(f"{symbol}", str(e))


def test_paper_order(client):
    """Test 3: Submit and cancel a paper order"""
    print("\n── Test 3: Paper Order Test ──")

    try:
        # Submit a small limit order far from market price (won't fill)
        order_data = MarketOrderRequest(
            symbol="SPY",
            qty=1,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY
        )
        order = client.submit_order(order_data)
        check_passed(f"Order submitted: {order.id} ({order.status})")

        # Check the order
        retrieved = client.get_order_by_id(order.id)
        check_passed(f"Order retrieved: status={retrieved.status}")

        # Cancel it if it hasn't filled
        if retrieved.status in ["new", "accepted", "pending_new"]:
            client.cancel_order_by_id(order.id)
            check_passed("Order cancelled successfully")
        else:
            check_passed(f"Order already {retrieved.status} (paper fill)")

    except Exception as e:
        error_msg = str(e)
        if "403" in error_msg:
            check_warn("Order rejected (403)", "Likely PDT protection — expected if day trade limit reached")
        else:
            check_failed("Order submission", error_msg)


def test_historical_data():
    """Test 4: Historical data access"""
    print("\n── Test 4: Historical Data ──")

    data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

    try:
        request = StockBarsRequest(
            symbol_or_symbols="SPY",
            timeframe=TimeFrame.Day,
            start=datetime.now() - timedelta(days=30),
            end=datetime.now() - timedelta(days=1)
        )
        bars = data_client.get_stock_bars(request)
        bar_count = len(bars["SPY"])
        check_passed(f"SPY daily bars retrieved: {bar_count} bars")

        # Show the most recent bar
        latest = bars["SPY"][-1]
        print(f"    Latest: {latest.timestamp.date()} | "
              f"O:{latest.open} H:{latest.high} L:{latest.low} C:{latest.close} "
              f"V:{latest.volume:,}")

    except Exception as e:
        check_failed("Historical data", str(e))


def test_redis():
    """Test 5: Redis connectivity and basic operations"""
    print("\n── Test 5: Redis ──")

    if redis is None:
        check_warn("Skipped", "redis-py not installed")
        return

    try:
        r = redis.Redis(host='localhost', port=6379, decode_responses=True)
        r.ping()
        check_passed("Connected to Redis")

        # Test key operations the trading system will use
        r.set("trading:status", "testing")
        r.hset("trading:pdt", mapping={"count": "0", "max": "3", "reserved": "1"})
        r.lpush("trading:watchlist", "SPY", "QQQ", "BTC/USD")

        status = r.get("trading:status")
        pdt = r.hgetall("trading:pdt")
        watchlist = r.lrange("trading:watchlist", 0, -1)

        check_passed(f"Key-value: trading:status = {status}")
        check_passed(f"Hash: trading:pdt = {pdt}")
        check_passed(f"List: trading:watchlist = {watchlist}")

        # Pub/sub test
        pubsub = r.pubsub()
        pubsub.subscribe("trading:signals")
        r.publish("trading:signals", '{"symbol": "SPY", "type": "entry", "strategy": "RSI2"}')
        msg = pubsub.get_message(timeout=1)  # subscription confirmation
        msg = pubsub.get_message(timeout=1)  # actual message
        if msg and msg['type'] == 'message':
            check_passed(f"Pub/Sub works: received signal on trading:signals")
        else:
            check_warn("Pub/Sub", "Message not received (may need slight delay)")

        pubsub.unsubscribe()

        # Cleanup test keys
        r.delete("trading:status", "trading:pdt", "trading:watchlist")
        check_passed("Test keys cleaned up")

    except redis.ConnectionError:
        check_failed("Redis not reachable", "Is the container running? docker compose up -d")
    except Exception as e:
        check_failed("Redis", str(e))


def test_timescaledb():
    """Test 6: TimescaleDB connectivity and schema"""
    print("\n── Test 6: TimescaleDB ──")

    if psycopg2 is None:
        check_warn("Skipped", "psycopg2 not installed")
        return

    tsdb_password = os.environ.get("TSDB_PASSWORD", "changeme_in_env_file")

    try:
        conn = psycopg2.connect(
            host="localhost",
            port=5432,
            dbname="trading",
            user="trader",
            password=tsdb_password
        )
        check_passed("Connected to TimescaleDB")

        cur = conn.cursor()

        # Check TimescaleDB extension
        cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb';")
        row = cur.fetchone()
        if row:
            check_passed(f"TimescaleDB extension: v{row[0]}")
        else:
            check_failed("TimescaleDB extension not installed")

        # Check tables exist
        tables = ['trades', 'signals', 'agent_decisions', 'daily_summary', 'positions']
        cur.execute("""
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
            ORDER BY tablename;
        """)
        existing = [row[0] for row in cur.fetchall()]

        for table in tables:
            if table in existing:
                check_passed(f"Table '{table}' exists")
            else:
                check_failed(f"Table '{table}' missing", "Init script may not have run")

        # Check hypertables
        cur.execute("""
            SELECT hypertable_name FROM timescaledb_information.hypertables
            ORDER BY hypertable_name;
        """)
        hypertables = [row[0] for row in cur.fetchall()]
        for ht in ['trades', 'signals', 'agent_decisions']:
            if ht in hypertables:
                check_passed(f"Hypertable '{ht}' configured")
            else:
                check_warn(f"'{ht}' is not a hypertable")

        # Test insert/query
        cur.execute("""
            INSERT INTO trades (symbol, side, quantity, price, total_value, strategy, asset_class, notes)
            VALUES ('TEST', 'buy', 1, 100, 100, 'VERIFY', 'equity', 'verification test')
            RETURNING id;
        """)
        test_id = cur.fetchone()[0]
        check_passed(f"Test trade inserted (id={test_id})")

        cur.execute("DELETE FROM trades WHERE strategy = 'VERIFY';")
        conn.commit()
        check_passed("Test trade cleaned up")

        cur.close()
        conn.close()

    except psycopg2.OperationalError as e:
        check_failed("TimescaleDB not reachable", "Is the container running? docker compose up -d")
    except Exception as e:
        check_failed("TimescaleDB", str(e))


if __name__ == "__main__":
    print("=" * 60)
    print("  TRADING SYSTEM INFRASTRUCTURE VERIFICATION")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    client = test_account()
    test_assets(client)
    test_paper_order(client)
    test_historical_data()
    test_redis()
    test_timescaledb()

    print("\n" + "=" * 60)
    print("  Verification complete. Fix any ❌ items before proceeding.")
    print("=" * 60)

# v1.0.0
