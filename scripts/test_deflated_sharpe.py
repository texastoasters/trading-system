"""Tests for scripts/deflated_sharpe.py.

Bailey/Lopez de Prado (2014) Deflated Sharpe Ratio: adjusts an observed
Sharpe ratio for selection bias from running multiple trials, plus
non-normal returns (skew + excess kurtosis) and finite sample size.

DSR ∈ [0, 1] is the probability that the true SR exceeds 0 given the
observed SR exceeds the expected null max SR_0* across N trials.
"""
import math
import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

sys.path.insert(0, "scripts")

for mod in [
    "alpaca", "alpaca.data", "alpaca.data.historical",
    "alpaca.data.requests", "alpaca.data.timeframe",
    "alpaca.trading", "alpaca.trading.client", "alpaca.trading.requests",
    "alpaca.trading.enums", "redis", "psycopg2",
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

import config as _config  # noqa: F401


# ── Norm helpers ─────────────────────────────────────────────

class TestNormHelpers:
    def test_norm_cdf_at_zero(self):
        from deflated_sharpe import _norm_cdf
        assert _norm_cdf(0.0) == pytest.approx(0.5, abs=1e-12)

    def test_norm_cdf_at_minus_infinity(self):
        from deflated_sharpe import _norm_cdf
        assert _norm_cdf(-10.0) == pytest.approx(0.0, abs=1e-9)

    def test_norm_cdf_at_plus_infinity(self):
        from deflated_sharpe import _norm_cdf
        assert _norm_cdf(10.0) == pytest.approx(1.0, abs=1e-9)

    def test_norm_cdf_at_1_96_is_0_975(self):
        """Standard 95% confidence reference point."""
        from deflated_sharpe import _norm_cdf
        assert _norm_cdf(1.96) == pytest.approx(0.975, abs=1e-3)

    def test_norm_ppf_at_0_5_is_zero(self):
        from deflated_sharpe import _norm_ppf
        assert _norm_ppf(0.5) == pytest.approx(0.0, abs=1e-9)

    def test_norm_ppf_at_0_975_is_1_96(self):
        from deflated_sharpe import _norm_ppf
        assert _norm_ppf(0.975) == pytest.approx(1.96, abs=1e-3)

    def test_norm_ppf_round_trips_via_cdf(self):
        from deflated_sharpe import _norm_cdf, _norm_ppf
        for q in [0.01, 0.1, 0.3, 0.7, 0.9, 0.99]:
            assert _norm_cdf(_norm_ppf(q)) == pytest.approx(q, abs=1e-6)

    def test_norm_ppf_rejects_out_of_range(self):
        from deflated_sharpe import _norm_ppf
        with pytest.raises(ValueError):
            _norm_ppf(0.0)
        with pytest.raises(ValueError):
            _norm_ppf(1.0)


# ── expected_max_sharpe ──────────────────────────────────────

class TestExpectedMaxSharpe:
    def test_n_trials_must_be_at_least_two(self):
        from deflated_sharpe import expected_max_sharpe
        with pytest.raises(ValueError):
            expected_max_sharpe(1)
        with pytest.raises(ValueError):
            expected_max_sharpe(0)

    def test_monotonically_increases_with_n_trials(self):
        from deflated_sharpe import expected_max_sharpe
        seq = [expected_max_sharpe(n) for n in (2, 5, 10, 50, 200)]
        for a, b in zip(seq, seq[1:]):
            assert a < b, f"expected max should increase: {seq}"

    def test_returns_finite_positive(self):
        from deflated_sharpe import expected_max_sharpe
        for n in (2, 12, 100, 1000):
            v = expected_max_sharpe(n)
            assert math.isfinite(v) and v > 0


# ── compute_deflated_sharpe ──────────────────────────────────

class TestComputeDeflatedSharpe:
    def test_empty_returns_yields_sentinel(self):
        from deflated_sharpe import compute_deflated_sharpe
        out = compute_deflated_sharpe([], n_trials=12)
        assert out["n_obs"] == 0
        assert out["dsr"] == 0.0
        assert out["passes_threshold"] is False

    def test_single_observation_yields_sentinel(self):
        from deflated_sharpe import compute_deflated_sharpe
        out = compute_deflated_sharpe([0.01], n_trials=12)
        assert out["n_obs"] == 1
        assert out["dsr"] == 0.0
        assert out["passes_threshold"] is False

    def test_zero_volatility_returns_yields_sentinel(self):
        """All-equal returns → std=0 → SR undefined."""
        from deflated_sharpe import compute_deflated_sharpe
        out = compute_deflated_sharpe([0.01] * 50, n_trials=12)
        assert out["dsr"] == 0.0
        assert out["passes_threshold"] is False

    def test_returns_required_keys(self):
        from deflated_sharpe import compute_deflated_sharpe
        rng = np.random.default_rng(1)
        rets = rng.normal(0.001, 0.01, 100).tolist()
        out = compute_deflated_sharpe(rets, n_trials=12)
        for k in ("n_obs", "n_trials", "sr_raw", "sr_annualized",
                  "sr_expected_max", "skew", "kurt",
                  "test_statistic", "dsr", "p_value",
                  "passes_threshold"):
            assert k in out, f"missing key {k}"

    def test_dsr_in_unit_interval(self):
        from deflated_sharpe import compute_deflated_sharpe
        rng = np.random.default_rng(7)
        rets = rng.normal(0.002, 0.01, 200).tolist()
        out = compute_deflated_sharpe(rets, n_trials=10)
        assert 0.0 <= out["dsr"] <= 1.0
        assert out["p_value"] == pytest.approx(1.0 - out["dsr"], abs=1e-12)

    def test_higher_n_trials_yields_lower_dsr(self):
        """Same returns evaluated against more trials → lower DSR (selection
        bias correction tightens)."""
        from deflated_sharpe import compute_deflated_sharpe
        rng = np.random.default_rng(42)
        rets = rng.normal(0.001, 0.01, 200).tolist()
        a = compute_deflated_sharpe(rets, n_trials=2)
        b = compute_deflated_sharpe(rets, n_trials=200)
        assert a["dsr"] >= b["dsr"], (
            f"DSR should not increase with more trials; "
            f"n=2 → {a['dsr']:.4f}, n=200 → {b['dsr']:.4f}"
        )

    def test_higher_sample_sr_yields_higher_dsr(self):
        """Holding n_trials and sample size constant, a higher observed SR
        produces a larger DSR."""
        from deflated_sharpe import compute_deflated_sharpe
        rng = np.random.default_rng(11)
        weak = rng.normal(0.0001, 0.01, 200).tolist()
        strong = rng.normal(0.005, 0.01, 200).tolist()
        a = compute_deflated_sharpe(weak, n_trials=12)
        b = compute_deflated_sharpe(strong, n_trials=12)
        assert b["sr_raw"] > a["sr_raw"]
        assert b["dsr"] > a["dsr"]

    def test_passes_threshold_above_default_0_95(self):
        """Strong, robust SR with low n_trials → passes threshold."""
        from deflated_sharpe import compute_deflated_sharpe
        rng = np.random.default_rng(2)
        rets = rng.normal(0.01, 0.01, 500).tolist()  # ~daily SR ≈ 1.0
        out = compute_deflated_sharpe(rets, n_trials=2)
        assert out["dsr"] > 0.95
        assert out["passes_threshold"] is True

    def test_threshold_param_overrides_default(self):
        from deflated_sharpe import compute_deflated_sharpe
        rng = np.random.default_rng(2)
        rets = rng.normal(0.0001, 0.01, 100).tolist()  # weak edge
        out = compute_deflated_sharpe(rets, n_trials=12, threshold=0.5)
        # weak edge fails 0.95 but might pass 0.5; either way the flag
        # must reflect the override threshold
        assert out["passes_threshold"] == (out["dsr"] > 0.5)

    def test_annualized_sr_equals_raw_times_sqrt_periods(self):
        from deflated_sharpe import compute_deflated_sharpe
        rng = np.random.default_rng(3)
        rets = rng.normal(0.001, 0.01, 100).tolist()
        out = compute_deflated_sharpe(rets, n_trials=12, periods_per_year=252)
        assert out["sr_annualized"] == pytest.approx(
            out["sr_raw"] * math.sqrt(252), rel=1e-9
        )

    def test_seed_reproducibility(self):
        """Pure function: same inputs → same outputs."""
        from deflated_sharpe import compute_deflated_sharpe
        rng = np.random.default_rng(99)
        rets = rng.normal(0.001, 0.01, 100).tolist()
        a = compute_deflated_sharpe(rets, n_trials=12)
        b = compute_deflated_sharpe(rets, n_trials=12)
        for k in a:
            assert a[k] == b[k], f"non-deterministic: {k}"

    def test_n_trials_must_be_at_least_two(self):
        from deflated_sharpe import compute_deflated_sharpe
        with pytest.raises(ValueError):
            compute_deflated_sharpe([0.01, 0.02, -0.01], n_trials=1)


# ── annualized SR helper ─────────────────────────────────────

class TestSampleMomentGuards:
    """Defensive guards in _sample_skew / _sample_kurt for inputs that
    bypass compute_deflated_sharpe's outer sentinel handling."""

    def test_skew_handles_singleton_array(self):
        from deflated_sharpe import _sample_skew
        assert _sample_skew(np.array([1.0])) == 0.0

    def test_skew_handles_zero_std(self):
        from deflated_sharpe import _sample_skew
        assert _sample_skew(np.array([5.0, 5.0, 5.0])) == 0.0

    def test_kurt_handles_singleton_array(self):
        from deflated_sharpe import _sample_kurt
        assert _sample_kurt(np.array([1.0])) == 3.0

    def test_kurt_handles_zero_std(self):
        from deflated_sharpe import _sample_kurt
        assert _sample_kurt(np.array([5.0, 5.0, 5.0])) == 3.0


class TestComputeDeflatedSharpePathological:
    def test_negative_se_factor_yields_sentinel(self, monkeypatch):
        """If the skew/kurt-corrected variance factor goes non-positive,
        the test statistic is undefined → fall back to sentinel result.
        The Cauchy-Schwarz-like inequality kurt ≥ skew² + 1 makes this
        unreachable for any real return distribution, so we force it via
        monkey-patching the moment helpers."""
        import deflated_sharpe as ds
        # Force inconsistent moments so the se_factor goes negative:
        # kurt=1, skew=5 → factor = 1 - 5·SR + 0·SR² = 1 - 5·SR < 0 for SR > 0.2
        monkeypatch.setattr(ds, "_sample_skew", lambda arr: 5.0)
        monkeypatch.setattr(ds, "_sample_kurt", lambda arr: 1.0)
        rng = np.random.default_rng(1)
        rets = rng.normal(0.005, 0.01, 200).tolist()  # SR ~ 0.5
        out = ds.compute_deflated_sharpe(rets, n_trials=12)
        # Sentinel returned (dsr=0, no nan/inf escape)
        assert out["dsr"] == 0.0
        assert out["passes_threshold"] is False
        assert out["sr_raw"] == 0.0  # sentinel zeroes raw too


class TestComputeSharpe:
    def test_returns_zero_for_empty_or_singleton(self):
        from deflated_sharpe import compute_sharpe
        assert compute_sharpe([]) == 0.0
        assert compute_sharpe([0.01]) == 0.0

    def test_returns_zero_for_zero_volatility(self):
        from deflated_sharpe import compute_sharpe
        assert compute_sharpe([0.005] * 30) == 0.0

    def test_annualizes_by_sqrt_periods(self):
        from deflated_sharpe import compute_sharpe
        rng = np.random.default_rng(5)
        rets = rng.normal(0.001, 0.01, 252).tolist()
        sr_252 = compute_sharpe(rets, periods_per_year=252)
        sr_1 = compute_sharpe(rets, periods_per_year=1)
        assert sr_252 == pytest.approx(sr_1 * math.sqrt(252), rel=1e-9)
