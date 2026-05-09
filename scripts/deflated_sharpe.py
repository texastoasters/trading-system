"""
deflated_sharpe.py — Bailey/Lopez de Prado Deflated Sharpe Ratio.

The DSR adjusts an observed Sharpe ratio for:
  - Selection bias from running multiple strategy trials (when only the
    winner is reported, the headline SR overstates the true edge).
  - Non-normal returns (sample skewness and kurtosis).
  - Finite sample size.

Reference: Bailey, D. & Lopez de Prado, M. (2014). "The Deflated Sharpe
Ratio: Correcting for Selection Bias, Backtest Overfitting, and
Non-Normality." Journal of Portfolio Management 40 (5), 94-107.

Pure-Python implementation: only depends on numpy + math (no scipy).
"""
from __future__ import annotations

import math
from typing import Sequence, Dict, Any

import numpy as np


GAMMA = 0.5772156649015329  # Euler-Mascheroni constant


# ── Standard normal CDF / inverse CDF ────────────────────────

def _norm_cdf(x: float) -> float:
    """Standard normal CDF via math.erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# Acklam's rational approximation for Φ⁻¹ (1995). Public domain.
# Accurate to 1.15e-9 in absolute terms for p ∈ (0, 1).
_ACKLAM_A = (
    -3.969683028665376e+01,  2.209460984245205e+02,
    -2.759285104469687e+02,  1.383577518672690e+02,
    -3.066479806614716e+01,  2.506628277459239e+00,
)
_ACKLAM_B = (
    -5.447609879822406e+01,  1.615858368580409e+02,
    -1.556989798598866e+02,  6.680131188771972e+01,
    -1.328068155288572e+01,
)
_ACKLAM_C = (
    -7.784894002430293e-03, -3.223964580411365e-01,
    -2.400758277161838e+00, -2.549732539343734e+00,
     4.374664141464968e+00,  2.938163982698783e+00,
)
_ACKLAM_D = (
     7.784695709041462e-03,  3.224671290700398e-01,
     2.445134137142996e+00,  3.754408661907416e+00,
)
_ACKLAM_PLOW = 0.02425
_ACKLAM_PHIGH = 1.0 - _ACKLAM_PLOW


def _norm_ppf(p: float) -> float:
    """Standard normal inverse CDF (quantile) via Acklam's rational approx."""
    if not (0.0 < p < 1.0):
        raise ValueError(f"p must be in (0, 1), got {p}")
    if p < _ACKLAM_PLOW:
        q = math.sqrt(-2.0 * math.log(p))
        return (
            (((((_ACKLAM_C[0]*q + _ACKLAM_C[1])*q + _ACKLAM_C[2])*q
              + _ACKLAM_C[3])*q + _ACKLAM_C[4])*q + _ACKLAM_C[5])
            / ((((_ACKLAM_D[0]*q + _ACKLAM_D[1])*q + _ACKLAM_D[2])*q
                + _ACKLAM_D[3])*q + 1.0)
        )
    if p > _ACKLAM_PHIGH:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(
            (((((_ACKLAM_C[0]*q + _ACKLAM_C[1])*q + _ACKLAM_C[2])*q
              + _ACKLAM_C[3])*q + _ACKLAM_C[4])*q + _ACKLAM_C[5])
            / ((((_ACKLAM_D[0]*q + _ACKLAM_D[1])*q + _ACKLAM_D[2])*q
                + _ACKLAM_D[3])*q + 1.0)
        )
    q = p - 0.5
    r = q * q
    return (
        (((((_ACKLAM_A[0]*r + _ACKLAM_A[1])*r + _ACKLAM_A[2])*r
          + _ACKLAM_A[3])*r + _ACKLAM_A[4])*r + _ACKLAM_A[5])
        * q
        / (((((_ACKLAM_B[0]*r + _ACKLAM_B[1])*r + _ACKLAM_B[2])*r
            + _ACKLAM_B[3])*r + _ACKLAM_B[4])*r + 1.0)
    )


# ── Expected max SR under null ───────────────────────────────

def expected_max_sharpe(n_trials: int) -> float:
    """
    E[max SR_n for n=1..N] when each SR_n is drawn IID from N(0, 1) under
    the null. From Bailey-LdP 2014 eq. (8). Used as the bias-corrected
    threshold in the DSR test statistic.
    """
    if n_trials < 2:
        raise ValueError(f"n_trials must be ≥ 2, got {n_trials}")
    return (
        (1.0 - GAMMA) * _norm_ppf(1.0 - 1.0 / n_trials)
        + GAMMA * _norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    )


# ── Helpers ──────────────────────────────────────────────────

def compute_sharpe(returns: Sequence[float],
                   periods_per_year: int = 252) -> float:
    """Annualised Sharpe = mean / std × sqrt(periods_per_year). Returns 0
    on empty / singleton / zero-vol inputs (treats float-precision noise
    on identical returns as zero variance)."""
    if returns is None or len(returns) < 2:
        return 0.0
    arr = np.asarray(returns, dtype=float)
    sd = arr.std(ddof=1)
    # Guard against float precision noise on exactly-equal inputs: any
    # std smaller than mean magnitude × 1e-12 is numerically zero.
    if sd <= max(1e-15, abs(float(arr.mean())) * 1e-12):
        return 0.0
    return float(arr.mean() / sd * math.sqrt(periods_per_year))


def _sample_skew(arr: np.ndarray) -> float:
    """Sample skewness (3rd standardized moment, biased estimator)."""
    if len(arr) < 2:
        return 0.0
    sd = arr.std(ddof=0)
    if sd <= 0:
        return 0.0
    return float(((arr - arr.mean()) ** 3).mean() / (sd ** 3))


def _sample_kurt(arr: np.ndarray) -> float:
    """Sample (non-Fisher) kurtosis (4th moment / std^4). Normal = 3."""
    if len(arr) < 2:
        return 3.0
    sd = arr.std(ddof=0)
    if sd <= 0:
        return 3.0
    return float(((arr - arr.mean()) ** 4).mean() / (sd ** 4))


# ── DSR core ─────────────────────────────────────────────────

def _sentinel_result(n_obs: int, n_trials: int,
                     periods_per_year: int) -> Dict[str, Any]:
    return {
        "n_obs": n_obs,
        "n_trials": n_trials,
        "sr_raw": 0.0,
        "sr_annualized": 0.0,
        "sr_expected_max": 0.0,
        "skew": 0.0,
        "kurt": 3.0,
        "test_statistic": 0.0,
        "dsr": 0.0,
        "p_value": 1.0,
        "passes_threshold": False,
    }


def compute_deflated_sharpe(
    returns: Sequence[float],
    n_trials: int,
    periods_per_year: int = 252,
    threshold: float = 0.95,
) -> Dict[str, Any]:
    """
    Deflated Sharpe Ratio per Bailey/Lopez de Prado (2014).

    Inputs:
        returns: per-period (or per-trade) returns.
        n_trials: number of independent strategy trials evaluated. Must be ≥ 2.
        periods_per_year: scaling for annualised SR display (default 252).
        threshold: DSR cutoff for the `passes_threshold` flag (default 0.95).

    Returns dict with raw + deflated metrics. DSR ∈ [0, 1] is the
    probability that the observed SR exceeds the expected null max across
    n_trials, given sample skew + kurt.
    """
    if n_trials < 2:
        raise ValueError(f"n_trials must be ≥ 2, got {n_trials}")

    n_obs = 0 if returns is None else len(returns)
    if n_obs < 2:
        return _sentinel_result(n_obs, n_trials, periods_per_year)

    arr = np.asarray(returns, dtype=float)
    sd = arr.std(ddof=1)
    # Treat float precision noise on identical returns as zero variance.
    if sd <= max(1e-15, abs(float(arr.mean())) * 1e-12):
        return _sentinel_result(n_obs, n_trials, periods_per_year)

    sr_raw = float(arr.mean() / sd)
    sr_annualized = sr_raw * math.sqrt(periods_per_year)
    skew_val = _sample_skew(arr)
    kurt_val = _sample_kurt(arr)

    # `expected_max_sharpe(n_trials)` is in z-score units (max of IID
    # N(0,1) samples). Convert to periodic SR units by scaling 1/sqrt(T-1):
    # under the null with Gaussian returns SE(SR̂) = 1/sqrt(T-1), so the
    # expected max in SR units is z / sqrt(T-1).
    expected_z = expected_max_sharpe(n_trials)
    sr_expected = expected_z / math.sqrt(n_obs - 1)

    # DSR test statistic per LdP 2014 eq. (9). The denominator is the
    # standard error factor accounting for skew + kurtosis.
    se_factor_sq = (
        1.0
        - skew_val * sr_raw
        + (kurt_val - 1.0) / 4.0 * sr_raw ** 2
    )
    if se_factor_sq <= 0 or not math.isfinite(se_factor_sq):
        return _sentinel_result(n_obs, n_trials, periods_per_year)
    test_statistic = (
        (sr_raw - sr_expected) * math.sqrt(n_obs - 1)
        / math.sqrt(se_factor_sq)
    )
    dsr = _norm_cdf(test_statistic)

    return {
        "n_obs": n_obs,
        "n_trials": n_trials,
        "sr_raw": sr_raw,
        "sr_annualized": sr_annualized,
        "sr_expected_max": sr_expected,
        "skew": skew_val,
        "kurt": kurt_val,
        "test_statistic": test_statistic,
        "dsr": float(dsr),
        "p_value": float(1.0 - dsr),
        "passes_threshold": bool(dsr > threshold),
    }
