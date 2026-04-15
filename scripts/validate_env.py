#!/usr/bin/env python3
"""
validate_env.py — Fast preflight check for the trading system.

Verifies env vars, Redis, Alpaca API, Telegram bot, and TimescaleDB.
Exits 0 if all checks pass, 1 if any fail. No test orders are submitted.

Usage:
    PYTHONPATH=scripts python3 scripts/validate_env.py
"""

import os
import sys

import requests

try:
    import redis as redis_lib
except ImportError:
    redis_lib = None

try:
    import psycopg2
except ImportError:
    psycopg2 = None

REQUIRED_ENV_VARS = [
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
]


def _ok(label: str):
    print(f"  ✅ {label}")


def _fail(label: str, detail: str = ""):
    print(f"  ❌ {label}" + (f" — {detail}" if detail else ""))


def check_env_vars() -> bool:
    print("\n── Env Vars ──")
    missing = [v for v in REQUIRED_ENV_VARS if not os.environ.get(v)]
    if missing:
        for v in missing:
            _fail(v, "not set")
        return False
    for v in REQUIRED_ENV_VARS:
        _ok(v)
    return True


def check_redis() -> bool:
    print("\n── Redis ──")
    try:
        r = redis_lib.Redis(host="localhost", port=6379, socket_connect_timeout=3)
        r.ping()
        _ok("Redis reachable (localhost:6379)")
        return True
    except Exception as e:
        _fail("Redis not reachable", str(e))
        return False


def check_alpaca() -> bool:
    print("\n── Alpaca API ──")
    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    if not api_key or not secret_key:
        _fail("Skipped", "ALPACA_API_KEY or ALPACA_SECRET_KEY not set")
        return False
    try:
        resp = requests.get(
            "https://paper-api.alpaca.markets/v2/account",
            headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key},
            timeout=10,
        )
        if resp.status_code == 200:
            _ok("Alpaca paper API reachable")
            return True
        _fail("Alpaca API error", f"HTTP {resp.status_code}")
        return False
    except Exception as e:
        _fail("Alpaca API not reachable", str(e))
        return False


def check_telegram() -> bool:
    print("\n── Telegram ──")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        _fail("Skipped", "TELEGRAM_BOT_TOKEN not set")
        return False
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=10,
        )
        if resp.status_code == 200:
            _ok("Telegram bot token valid")
            return True
        _fail("Telegram bot token invalid", f"HTTP {resp.status_code}")
        return False
    except Exception as e:
        _fail("Telegram API not reachable", str(e))
        return False


def check_timescaledb() -> bool:
    print("\n── TimescaleDB ──")
    if psycopg2 is None:
        _fail("psycopg2 not installed", "pip install psycopg2-binary")
        return False
    password = os.environ.get("TSDB_PASSWORD", "changeme_in_env_file")
    try:
        conn = psycopg2.connect(
            host="localhost", port=5432,
            dbname="trading", user="trader", password=password,
            connect_timeout=3,
        )
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        _ok("TimescaleDB reachable (localhost:5432)")
        return True
    except Exception as e:
        _fail("TimescaleDB not reachable", str(e))
        return False


def main() -> int:
    print("=" * 50)
    print("  TRADING SYSTEM PREFLIGHT VALIDATION")
    print("=" * 50)

    results = [
        check_env_vars(),
        check_redis(),
        check_alpaca(),
        check_telegram(),
        check_timescaledb(),
    ]

    passed = sum(results)
    total = len(results)
    print(f"\n{'=' * 50}")
    print(f"  {passed}/{total} checks passed")
    print("=" * 50)

    return 0 if all(results) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
