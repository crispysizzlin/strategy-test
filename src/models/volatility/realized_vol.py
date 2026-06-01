"""
Realized volatility estimators for Volatility Risk Premium (VRP) computation.

Estimators implemented:
  1. Close-to-Close (CC)     — Standard historical vol; biased by overnight gaps
  2. Parkinson (1980)        — Range-based; uses High/Low; unbiased in continuous time
  3. Garman-Klass (1980)     — OHLC; more efficient than Parkinson
  4. Yang-Zhang (2000)       — OHLC + overnight; minimum variance, unbiased
  5. Rogers-Satchell (1994)  — Intraday RS; drift-independent
  6. EWMA                    — Exponentially-weighted; RiskMetrics approach

VRP Computation:
  VRP_t = IV_t - RV_t  (Implied minus Realized volatility)
  When VRP > 0: Market overprices volatility → systematic premium sellers profit
  Historical average VRP for SPX is approximately 2-4 vol points (positive)

Composite estimator:
  Combines CC, Parkinson, and Yang-Zhang with equal weights for a robust
  estimate less sensitive to any single estimator's assumptions.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.utils.logger import logger


class RealizedVolatility:
    """
    Multi-estimator realized volatility calculator.

    All estimators return annualised volatility as a fraction (not percent).
    """

    TRADING_DAYS = 252

    def __init__(self, window: int = 21) -> None:
        """
        Parameters
        ----------
        window : rolling window in trading days (default: 21 ≈ 1 month)
        """
        self.window = window

    # ------------------------------------------------------------------
    # Point estimators (single value)
    # ------------------------------------------------------------------

    def close_to_close(self, close: np.ndarray) -> float:
        """
        Standard close-to-close historical volatility.

        σ_CC = √(252 / (n-1) · Σ(r_i - r̄)²)
        """
        if len(close) < 2:
            return np.nan
        log_returns = np.log(close[1:] / close[:-1])
        return float(np.std(log_returns, ddof=1) * np.sqrt(self.TRADING_DAYS))

    def parkinson(self, high: np.ndarray, low: np.ndarray) -> float:
        """
        Parkinson (1980) range estimator.

        σ_P² = 1/(4·ln2·n) · Σ [ln(H_i/L_i)]²

        4x more efficient than CC; assumes zero drift, continuous prices.
        """
        if len(high) < 1:
            return np.nan
        n = len(high)
        log_hl = np.log(np.maximum(high, 1e-10) / np.maximum(low, 1e-10))
        variance = np.sum(log_hl ** 2) / (4 * np.log(2) * n)
        return float(np.sqrt(variance * self.TRADING_DAYS))

    def garman_klass(
        self,
        open_: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
    ) -> float:
        """
        Garman-Klass (1980) OHLC estimator.

        σ_GK² = [0.5·ln(H/L)² - (2·ln2-1)·ln(C/O)²] / n

        More efficient than Parkinson; uses close and open.
        """
        if len(high) < 1:
            return np.nan
        log_hl = np.log(high / low)
        log_co = np.log(close / open_)
        variance = np.mean(0.5 * log_hl ** 2 - (2 * np.log(2) - 1) * log_co ** 2)
        return float(np.sqrt(max(variance, 0) * self.TRADING_DAYS))

    def rogers_satchell(
        self,
        open_: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
    ) -> float:
        """
        Rogers-Satchell (1994) estimator.

        σ_RS² = E[ln(H/C)·ln(H/O) + ln(L/C)·ln(L/O)]

        Drift-independent (handles trending series correctly).
        """
        if len(high) < 1:
            return np.nan
        log_hc = np.log(high / close)
        log_ho = np.log(high / open_)
        log_lc = np.log(low / close)
        log_lo = np.log(low / open_)
        variance = np.mean(log_hc * log_ho + log_lc * log_lo)
        return float(np.sqrt(max(variance, 0) * self.TRADING_DAYS))

    def yang_zhang(
        self,
        open_: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        k: float = 0.34,
    ) -> float:
        """
        Yang-Zhang (2000) estimator.

        σ_YZ² = σ_o² + k·σ_c² + (1-k)·σ_RS²

        Minimum variance, unbiased. Handles drift AND overnight gaps.
        Optimal k ≈ 0.34 (minimizes variance of estimator).
        """
        if len(close) < 2:
            return np.nan

        prev_close = close[:-1]
        o = open_[1:]
        h = high[1:]
        l = low[1:]
        c = close[1:]
        n = len(c)
        if n < 1:
            return np.nan

        log_oc = np.log(o / prev_close)
        log_co = np.log(c / o)
        log_hc = np.log(h / c)
        log_lc = np.log(l / c)
        log_ho = np.log(h / o)
        log_lo = np.log(l / o)

        sigma_o2 = np.sum((log_oc - log_oc.mean()) ** 2) / max(n - 1, 1)
        sigma_c2 = np.sum((log_co - log_co.mean()) ** 2) / max(n - 1, 1)
        sigma_rs2 = np.mean(log_hc * log_ho + log_lc * log_lo)

        sigma_yz2 = sigma_o2 + k * sigma_c2 + (1 - k) * sigma_rs2
        return float(np.sqrt(max(sigma_yz2, 0) * self.TRADING_DAYS))

    def ewma(self, close: np.ndarray, lam: float = 0.94) -> float:
        """
        RiskMetrics EWMA volatility estimator.

        σ²_t = λ·σ²_{t-1} + (1-λ)·r²_{t-1}
        λ = 0.94 for daily data (J.P. Morgan RiskMetrics standard)
        """
        if len(close) < 2:
            return np.nan
        log_returns = np.log(close[1:] / close[:-1])
        if len(log_returns) == 0:
            return np.nan
        variance = log_returns[0] ** 2
        for r in log_returns[1:]:
            variance = lam * variance + (1 - lam) * r ** 2
        return float(np.sqrt(variance * self.TRADING_DAYS))

    # ------------------------------------------------------------------
    # Composite estimator
    # ------------------------------------------------------------------

    def composite(
        self,
        open_: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        weights: Optional[Dict[str, float]] = None,
    ) -> float:
        """
        Weighted composite of multiple estimators for a robust RV estimate.

        Default weights: CC=0.25, Parkinson=0.25, GK=0.25, YZ=0.25
        """
        if weights is None:
            weights = {
                "cc": 0.25,
                "parkinson": 0.25,
                "garman_klass": 0.25,
                "yang_zhang": 0.25,
            }

        estimates = {
            "cc": self.close_to_close(close),
            "parkinson": self.parkinson(high, low),
            "garman_klass": self.garman_klass(open_, high, low, close),
            "yang_zhang": self.yang_zhang(open_, high, low, close),
        }

        total_weight = 0.0
        weighted_sum = 0.0
        for name, w in weights.items():
            val = estimates.get(name, np.nan)
            if np.isfinite(val) and val > 0:
                weighted_sum += w * val
                total_weight += w

        if total_weight == 0:
            return np.nan
        return float(weighted_sum / total_weight)

    # ------------------------------------------------------------------
    # Rolling series
    # ------------------------------------------------------------------

    def rolling_realized_vol(
        self,
        df: pd.DataFrame,
        method: str = "yang_zhang",
    ) -> pd.Series:
        """
        Compute a rolling time series of realized volatility.

        Parameters
        ----------
        df     : DataFrame with columns [open, high, low, close]
        method : estimator name (yang_zhang | cc | parkinson | garman_klass | composite)

        Returns
        -------
        pd.Series of annualised volatility indexed like df
        """
        results = []
        idx = []

        for i in range(self.window, len(df) + 1):
            chunk = df.iloc[i - self.window : i]
            o = chunk["open"].values
            h = chunk["high"].values
            l = chunk["low"].values
            c = chunk["close"].values

            if method == "yang_zhang":
                vol = self.yang_zhang(o, h, l, c)
            elif method == "cc":
                vol = self.close_to_close(c)
            elif method == "parkinson":
                vol = self.parkinson(h, l)
            elif method == "garman_klass":
                vol = self.garman_klass(o, h, l, c)
            elif method == "composite":
                vol = self.composite(o, h, l, c)
            else:
                vol = self.yang_zhang(o, h, l, c)

            results.append(vol)
            idx.append(df.index[i - 1])

        return pd.Series(results, index=idx, name=f"rv_{method}")

    # ------------------------------------------------------------------
    # VRP computation
    # ------------------------------------------------------------------

    def compute_vrp(
        self,
        realized_vol: pd.Series,
        implied_vol: pd.Series,
    ) -> pd.Series:
        """
        Compute Volatility Risk Premium (VRP) time series.

        VRP_t = IV_t - RV_t

        A persistently positive VRP (IV > RV) is the theoretical basis
        for systematic premium selling. The VRP represents the compensation
        the market pays for bearing variance risk.

        Returns
        -------
        pd.Series of VRP values (positive = IV overpriced)
        """
        common_idx = realized_vol.index.intersection(implied_vol.index)
        rv = realized_vol.loc[common_idx]
        iv = implied_vol.loc[common_idx]
        vrp = iv - rv
        return vrp.rename("vrp")

    def vrp_signal_strength(
        self,
        vrp_series: pd.Series,
        lookback: int = 20,
    ) -> float:
        """
        Compute the percentile rank of the current VRP vs recent history.

        Returns a value in [0, 1]:
          > 0.6 → strong signal to sell premium
          0.4–0.6 → neutral
          < 0.4 → weak / avoid selling
        """
        if len(vrp_series) < lookback:
            return 0.5
        recent = vrp_series.iloc[-lookback:]
        current = vrp_series.iloc[-1]
        rank = float((recent < current).mean())
        return rank

    # ------------------------------------------------------------------
    # All estimators report
    # ------------------------------------------------------------------

    def all_estimates(
        self,
        open_: np.ndarray,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
    ) -> Dict[str, float]:
        """Return all estimator values as a dict."""
        return {
            "close_to_close": self.close_to_close(close),
            "parkinson": self.parkinson(high, low),
            "garman_klass": self.garman_klass(open_, high, low, close),
            "rogers_satchell": self.rogers_satchell(open_, high, low, close),
            "yang_zhang": self.yang_zhang(open_, high, low, close),
            "ewma": self.ewma(close),
            "composite": self.composite(open_, high, low, close),
        }
