"""
Tests for validate_env.py — 100% coverage target.

Run from repo root:
    PYTHONPATH=scripts pytest scripts/test_validate_env.py -v
"""
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "scripts")
import validate_env


# ── check_env_vars ───────────────────────────────────────────

class TestCheckEnvVars:
    def test_all_present_returns_true(self, monkeypatch, capsys):
        for v in validate_env.REQUIRED_ENV_VARS:
            monkeypatch.setenv(v, "fake-value")
        assert validate_env.check_env_vars() is True

    def test_all_missing_returns_false(self, monkeypatch, capsys):
        for v in validate_env.REQUIRED_ENV_VARS:
            monkeypatch.delenv(v, raising=False)
        assert validate_env.check_env_vars() is False

    def test_one_missing_returns_false(self, monkeypatch, capsys):
        for v in validate_env.REQUIRED_ENV_VARS:
            monkeypatch.setenv(v, "fake-value")
        monkeypatch.delenv("ALPACA_API_KEY")
        assert validate_env.check_env_vars() is False

    def test_missing_var_named_in_output(self, monkeypatch, capsys):
        for v in validate_env.REQUIRED_ENV_VARS:
            monkeypatch.delenv(v, raising=False)
        validate_env.check_env_vars()
        out = capsys.readouterr().out
        assert "ALPACA_API_KEY" in out


# ── check_redis ──────────────────────────────────────────────

class TestCheckRedis:
    def _mock_redis(self, ping_error=None):
        mock_lib = MagicMock()
        mock_r = MagicMock()
        if ping_error:
            mock_r.ping.side_effect = ping_error
        mock_lib.Redis.return_value = mock_r
        return mock_lib

    def test_success_returns_true(self, capsys):
        with patch.object(validate_env, "redis_lib", self._mock_redis()):
            assert validate_env.check_redis() is True

    def test_constructor_error_returns_false(self, capsys):
        mock_lib = MagicMock()
        mock_lib.Redis.side_effect = Exception("refused")
        with patch.object(validate_env, "redis_lib", mock_lib):
            assert validate_env.check_redis() is False

    def test_ping_error_returns_false(self, capsys):
        with patch.object(validate_env, "redis_lib", self._mock_redis(ping_error=Exception("timeout"))):
            assert validate_env.check_redis() is False

    def test_success_prints_ok(self, capsys):
        with patch.object(validate_env, "redis_lib", self._mock_redis()):
            validate_env.check_redis()
        assert "✅" in capsys.readouterr().out

    def test_failure_prints_fail(self, capsys):
        mock_lib = MagicMock()
        mock_lib.Redis.side_effect = Exception("boom")
        with patch.object(validate_env, "redis_lib", mock_lib):
            validate_env.check_redis()
        assert "❌" in capsys.readouterr().out


# ── check_alpaca ─────────────────────────────────────────────

class TestCheckAlpaca:
    def test_success_returns_true(self, monkeypatch, capsys):
        monkeypatch.setenv("ALPACA_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("validate_env.requests.get", return_value=mock_resp):
            assert validate_env.check_alpaca() is True

    def test_non_200_returns_false(self, monkeypatch, capsys):
        monkeypatch.setenv("ALPACA_API_KEY", "test-key")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        with patch("validate_env.requests.get", return_value=mock_resp):
            assert validate_env.check_alpaca() is False

    def test_missing_keys_skips_and_returns_false(self, monkeypatch, capsys):
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        assert validate_env.check_alpaca() is False

    def test_exception_returns_false(self, monkeypatch, capsys):
        monkeypatch.setenv("ALPACA_API_KEY", "k")
        monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
        with patch("validate_env.requests.get", side_effect=Exception("network error")):
            assert validate_env.check_alpaca() is False


# ── check_telegram ───────────────────────────────────────────

class TestCheckTelegram:
    def test_success_returns_true(self, monkeypatch, capsys):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("validate_env.requests.get", return_value=mock_resp):
            assert validate_env.check_telegram() is True

    def test_non_200_returns_false(self, monkeypatch, capsys):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bad-token")
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        with patch("validate_env.requests.get", return_value=mock_resp):
            assert validate_env.check_telegram() is False

    def test_missing_token_returns_false(self, monkeypatch, capsys):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        assert validate_env.check_telegram() is False

    def test_exception_returns_false(self, monkeypatch, capsys):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
        with patch("validate_env.requests.get", side_effect=Exception("timeout")):
            assert validate_env.check_telegram() is False


# ── check_timescaledb ────────────────────────────────────────

class TestCheckTimescaleDB:
    def _mock_pg(self, connect_error=None):
        mock_lib = MagicMock()
        if connect_error:
            mock_lib.connect.side_effect = connect_error
        return mock_lib

    def test_success_returns_true(self, capsys):
        with patch.object(validate_env, "psycopg2", self._mock_pg()):
            assert validate_env.check_timescaledb() is True

    def test_connection_error_returns_false(self, capsys):
        with patch.object(validate_env, "psycopg2", self._mock_pg(connect_error=Exception("refused"))):
            assert validate_env.check_timescaledb() is False

    def test_psycopg2_unavailable_returns_false(self, capsys):
        with patch.object(validate_env, "psycopg2", None):
            assert validate_env.check_timescaledb() is False

    def test_success_prints_ok(self, capsys):
        with patch.object(validate_env, "psycopg2", self._mock_pg()):
            validate_env.check_timescaledb()
        assert "✅" in capsys.readouterr().out


# ── main ─────────────────────────────────────────────────────

class TestImportFallbacks:
    def test_redis_lib_none_when_redis_not_installed(self):
        import importlib
        saved_redis = sys.modules.get("redis")
        saved_ve = sys.modules.get("validate_env")
        sys.modules["redis"] = None  # triggers ImportError on `import redis`
        sys.modules.pop("validate_env", None)
        try:
            import validate_env as ve_fresh
            assert ve_fresh.redis_lib is None
        finally:
            if saved_redis is not None:
                sys.modules["redis"] = saved_redis
            else:
                sys.modules.pop("redis", None)
            if saved_ve is not None:
                sys.modules["validate_env"] = saved_ve
            else:
                sys.modules.pop("validate_env", None)

    def test_psycopg2_none_when_psycopg2_not_installed(self):
        saved_pg = sys.modules.get("psycopg2")
        saved_ve = sys.modules.get("validate_env")
        sys.modules["psycopg2"] = None  # triggers ImportError on `import psycopg2`
        sys.modules.pop("validate_env", None)
        try:
            import validate_env as ve_fresh
            assert ve_fresh.psycopg2 is None
        finally:
            if saved_pg is not None:
                sys.modules["psycopg2"] = saved_pg
            else:
                sys.modules.pop("psycopg2", None)
            if saved_ve is not None:
                sys.modules["validate_env"] = saved_ve
            else:
                sys.modules.pop("validate_env", None)


class TestMain:
    def _patch_all(self, results: dict):
        defaults = {
            "check_env_vars": True,
            "check_redis": True,
            "check_alpaca": True,
            "check_telegram": True,
            "check_timescaledb": True,
        }
        defaults.update(results)
        return [
            patch(f"validate_env.{fn}", return_value=v)
            for fn, v in defaults.items()
        ]

    def test_all_pass_returns_zero(self, capsys):
        patches = self._patch_all({})
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            assert validate_env.main() == 0

    def test_env_fail_returns_one(self, capsys):
        patches = self._patch_all({"check_env_vars": False})
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            assert validate_env.main() == 1

    def test_redis_fail_returns_one(self, capsys):
        patches = self._patch_all({"check_redis": False})
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            assert validate_env.main() == 1

    def test_summary_shows_pass_count(self, capsys):
        patches = self._patch_all({})
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            validate_env.main()
        out = capsys.readouterr().out
        assert "5/5" in out
