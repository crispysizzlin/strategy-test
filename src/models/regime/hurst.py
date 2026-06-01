"""
Hurst Exponent and related long-memory / market efficiency measures.

The Hurst Exponent H characterises time-series persistence:
  H > 0.5 → trending / persistent (momentum strategies)
  H = 0.5 → random walk (efficient market)
  H < 0.5 → mean-reverting (options premium selling is more reliable)

In equity markets, prices are often near H ≈ 0.5-0.6 on long horizons
but intraday and weekly returns frequently exhibit mean-reverting behaviour.

Estimators:
  1. R/S Analysis (classical Hurst, Lo 1991)
  2. Detrended Fluctuation Analysis (DFA)
  3. Variance ratio test (Lo-MacKinlay 1988)

The mean-reversion strength informs whether iron condors are likely to work:
  H < 0.45 → strong mean-reversion → iron condors / short strangles ideal
  0.45 < H < 0.55 → near-random → moderate confidence
  H > 0.55 → trending → avoid iron condors; consider directional spreads
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

import numpy as np
import pandas as pd

from src.utils.logger import logger


@dataclass
class HurstResult:
    hurst: float           # Hurst exponent estimate
    method: str
    mean_reverting: bool   # True if H < 0.5
    trending: bool         # True if H > 0.55
    confidence: float      # R-squared of log-log regression

    @property
    def market_type(self) -> str:
        if self.hurst < 0.45:
            return "strongly_mean_reverting"
        elif self.hurst < 0.50:
            return "mean_reverting"
        elif self.hurst <= 0.55:
            return "random_walk"
        else:
            return "trending"

    @property
    def options_regime_suitability(self) -> str:
        """Suitability for options premium selling."""
        if self.hurst < 0.50:
            return "high"
        elif self.hurst <= 0.55:
            return "medium"
        else:
            return "low"


class HurstAnalyzer:
    """
    Computes Hurst exponent via multiple methods for robustness.
    """

    # ------------------------------------------------------------------
    # R/S Analysis
    # ------------------------------------------------------------------

    def rs_hurst(
        self,
        series: np.ndarray,
        min_window: int = 10,
        max_window: int = 200,
        n_windows: int = 20,
    ) -> HurstResult:
        """
        Classical R/S (Rescaled Range) analysis — Hurst (1951), Lo (1991).

        E[R/S(n)] ≈ c · n^H

        Algorithm:
          1. Divide series into sub-series of length n
          2. Compute R/S = (max cumulative deviation - min) / std for each sub-series
          3. Regress log(R/S) on log(n) → slope = H
        """
        series = np.asarray(series, dtype=float)
        series = series[~np.isnan(series)]
        N = len(series)

        if N < 2 * min_window:
            return HurstResult(0.5, "rs", False, False, 0.0)

        log_n = []
        log_rs = []

        for n in np.unique(
            np.logspace(np.log10(min_window), np.log10(min(max_window, N // 2)), n_windows).astype(int)
        ):
            if n < 2:
                continue
            rs_values = []
            for start in range(0, N - n + 1, n):
                sub = series[start : start + n]
                mean_sub = np.mean(sub)
                deviations = np.cumsum(sub - mean_sub)
                R = deviations.max() - deviations.min()
                S = np.std(sub, ddof=1)
                if S > 0:
                    rs_values.append(R / S)
            if rs_values:
                log_n.append(np.log(n))
                log_rs.append(np.log(np.mean(rs_values)))

        if len(log_n) < 3:
            return HurstResult(0.5, "rs", False, False, 0.0)

        log_n_arr = np.array(log_n)
        log_rs_arr = np.array(log_rs)

        coeffs = np.polyfit(log_n_arr, log_rs_arr, 1)
        H = float(coeffs[0])

        # R-squared
        y_pred = np.polyval(coeffs, log_n_arr)
        ss_res = np.sum((log_rs_arr - y_pred) ** 2)
        ss_tot = np.sum((log_rs_arr - log_rs_arr.mean()) ** 2)
        r2 = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0

        H = float(np.clip(H, 0.01, 0.99))

        return HurstResult(
            hurst=H,
            method="r/s",
            mean_reverting=H < 0.5,
            trending=H > 0.55,
            confidence=r2,
        )

    # ------------------------------------------------------------------
    # Detrended Fluctuation Analysis
    # ------------------------------------------------------------------

    def dfa_hurst(
        self,
        series: np.ndarray,
        min_window: int = 10,
        max_window: int = 200,
        n_windows: int = 20,
    ) -> HurstResult:
        """
        DFA (Detrended Fluctuation Analysis) — Peng et al. (1994).

        More robust to non-stationarity than R/S.
        DFA exponent α ≈ H for long-memory processes.

        F(n) = sqrt[ 1/N · Σ [y(i) - y_n(i)]² ]
        E[F(n)] ≈ c · n^α
        """
        series = np.asarray(series, dtype=float)
        series = series[~np.isnan(series)]
        N = len(series)

        if N < 2 * min_window:
            return HurstResult(0.5, "dfa", False, False, 0.0)

        # Integrated series (cumsum of mean-removed)
        y = np.cumsum(series - np.mean(series))

        log_n = []
        log_f = []

        for n in np.unique(
            np.logspace(np.log10(min_window), np.log10(min(max_window, N // 2)), n_windows).astype(int)
        ):
            if n < 4:
                continue
            segments = N // n
            if segments < 1:
                continue
            flucts = []
            for seg in range(segments):
                sub = y[seg * n : (seg + 1) * n]
                # Linear detrending within segment
                t = np.arange(len(sub))
                trend = np.polyfit(t, sub, 1)
                detrended = sub - np.polyval(trend, t)
                flucts.append(np.sqrt(np.mean(detrended ** 2)))
            if flucts:
                log_n.append(np.log(n))
                log_f.append(np.log(np.mean(flucts)))

        if len(log_n) < 3:
            return HurstResult(0.5, "dfa", False, False, 0.0)

        log_n_arr = np.array(log_n)
        log_f_arr = np.array(log_f)

        coeffs = np.polyfit(log_n_arr, log_f_arr, 1)
        H = float(np.clip(coeffs[0], 0.01, 0.99))

        y_pred = np.polyval(coeffs, log_n_arr)
        ss_res = np.sum((log_f_arr - y_pred) ** 2)
        ss_tot = np.sum((log_f_arr - log_f_arr.mean()) ** 2)
        r2 = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0.0

        return HurstResult(
            hurst=H,
            method="dfa",
            mean_reverting=H < 0.5,
            trending=H > 0.55,
            confidence=r2,
        )

    # ------------------------------------------------------------------
    # Variance Ratio Test
    # ------------------------------------------------------------------

    def variance_ratio(
        self, series: np.ndarray, q: int = 4
    ) -> Tuple[float, float, bool]:
        """
        Lo-MacKinlay (1988) Variance Ratio Test.

        VR(q) = Var(q-period return) / (q · Var(1-period return))
        H₀: VR = 1 (random walk)
        VR > 1 → positive autocorrelation (trending)
        VR < 1 → negative autocorrelation (mean-reverting)

        Returns
        -------
        vr     : variance ratio
        z_stat : heteroskedasticity-robust z-statistic
        reject_rw : True if we reject the random walk hypothesis at 5%
        """
        series = np.asarray(series, dtype=float)
        r = np.diff(np.log(series[~np.isnan(series)]))
        n = len(r)
        if n < 2 * q:
            return 1.0, 0.0, False

        # Overlapping q-period returns
        r_q = np.array([r[i:i+q].sum() for i in range(n - q + 1)])

        var_1 = np.var(r, ddof=1)
        var_q = np.var(r_q, ddof=1)

        if var_1 == 0:
            return 1.0, 0.0, False

        vr = var_q / (q * var_1)

        # Heteroskedasticity-robust z-statistic (Lo-MacKinlay)
        delta = 0.0
        for k in range(1, q):
            numer = sum((r[j] ** 2) * (r[j-k] ** 2) for j in range(k, n))
            denom = (sum(r[j] ** 2 for j in range(n))) ** 2
            delta += ((2 * (q - k) / q) ** 2) * (numer / denom if denom > 0 else 0)

        z_stat = (vr - 1) / np.sqrt(delta) if delta > 0 else 0.0
        reject_rw = abs(z_stat) > 1.96  # 5% significance

        return float(vr), float(z_stat), reject_rw

    # ------------------------------------------------------------------
    # Composite analysis
    # ------------------------------------------------------------------

    def analyze(self, close: np.ndarray) -> dict:
        """
        Run all Hurst analysis methods and return a summary dict.

        Also includes variance ratio test and an overall market regime assessment.
        """
        log_returns = np.log(close[1:] / close[:-1])

        rs_result = self.rs_hurst(log_returns)
        dfa_result = self.dfa_hurst(log_returns)
        vr, z_stat, reject_rw = self.variance_ratio(close)

        # Weighted average (DFA has higher weight as more robust)
        avg_hurst = 0.4 * rs_result.hurst + 0.6 * dfa_result.hurst

        logger.debug(
            f"Hurst analysis: R/S={rs_result.hurst:.3f} "
            f"DFA={dfa_result.hurst:.3f} avg={avg_hurst:.3f} "
            f"VR={vr:.3f} (reject_rw={reject_rw})"
        )

        return {
            "rs_hurst": rs_result.hurst,
            "dfa_hurst": dfa_result.hurst,
            "avg_hurst": avg_hurst,
            "variance_ratio": vr,
            "vr_z_stat": z_stat,
            "reject_random_walk": reject_rw,
            "market_type": rs_result.market_type if avg_hurst == rs_result.hurst else (
                "mean_reverting" if avg_hurst < 0.5 else
                "random_walk" if avg_hurst <= 0.55 else "trending"
            ),
            "premium_sell_suitability": (
                "high" if avg_hurst < 0.50 else
                "medium" if avg_hurst <= 0.55 else "low"
            ),
        }
