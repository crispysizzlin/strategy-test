"""
Implied Volatility Surface fitting using SSVI (Surface SVI) parametrization.

Mathematical Foundation
-----------------------
SVI (Stochastic Volatility Inspired) — Gatheral (2004):
  w(k; a, b, ρ, m, σ) = a + b·[ρ·(k-m) + √((k-m)² + σ²)]
  where:
    k = log(K/F) = log-moneyness
    w = total implied variance = σ²_IV · T
    a = vertical translation (overall level)
    b = wings slope (slope of asymptotes)
    ρ ∈ (-1,1) = correlation (skew)
    m = horizontal translation (peak location)
    σ = smoothness parameter (curvature)

SSVI (Surface SVI) — Gatheral & Jacquier (2014):
  w(k,θ) = (θ/2) · {1 + ρ·φ(θ)·k + √[(φ(θ)·k + ρ)² + (1-ρ²)]}
  where:
    θ = ATM total variance (θ_t = σ²_ATM · t)
    φ(θ) = η / (θ^γ · (1+θ)^(1-γ))  [power law or Heston form]

Arbitrage-free conditions (Gatheral & Jacquier 2014):
  Calendar spread: ∂θ_t/∂t ≥ 0 (monotone in time)
  Butterfly: θ·φ(θ)·(1+|ρ|) ≤ 4 and θ·φ(θ)²·(1+|ρ|) ≤ 4

Usage in AVRPE:
  1. Fit the surface to live options chain data
  2. Identify mispricings (model vs. market)
  3. Compute vol surface slope/skew features for regime signal
  4. Ensure consistency when pricing spread structures
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import minimize, differential_evolution
from scipy.interpolate import CubicSpline

from src.utils.logger import logger


@dataclass
class SVIParams:
    """SVI parameterization for a single maturity slice."""
    a: float
    b: float
    rho: float
    m: float
    sigma: float
    maturity: float    # time to expiry in years

    def total_variance(self, k: float | np.ndarray) -> float | np.ndarray:
        """w(k) = a + b·[ρ·(k-m) + √((k-m)²+σ²)]"""
        diff = np.asarray(k) - self.m
        return self.a + self.b * (self.rho * diff + np.sqrt(diff ** 2 + self.sigma ** 2))

    def implied_vol(self, k: float | np.ndarray) -> float | np.ndarray:
        """σ(k) = √(w(k)/T)"""
        w = self.total_variance(k)
        return np.sqrt(np.maximum(w, 1e-10) / self.maturity)


@dataclass
class SSVIParams:
    """SSVI surface parametrization."""
    rho: float      # global correlation (or per-slice)
    eta: float      # power-law curvature scale
    gamma: float    # power-law exponent (0.5 for Heston-like)

    def phi(self, theta: float) -> float:
        """Curvature function: φ(θ) = η / (θ^γ · (1+θ)^(1-γ))"""
        if theta <= 0:
            return self.eta
        return self.eta / (theta ** self.gamma * (1 + theta) ** (1 - self.gamma))

    def total_variance(self, k: float | np.ndarray, theta: float) -> float | np.ndarray:
        """w(k,θ) = θ/2 · {1 + ρ·φ(θ)·k + √[(φ(θ)·k + ρ)² + (1-ρ²)]}"""
        ph = self.phi(theta)
        inner = (ph * np.asarray(k) + self.rho) ** 2 + (1 - self.rho ** 2)
        return (theta / 2) * (1 + self.rho * ph * np.asarray(k) + np.sqrt(np.maximum(inner, 1e-12)))

    def implied_vol(self, k: float | np.ndarray, T: float, theta: float) -> float | np.ndarray:
        """σ(k,T) = √(w(k,θ)/T)"""
        w = self.total_variance(k, theta)
        return np.sqrt(np.maximum(w, 1e-10) / T)

    def is_arbitrage_free(self, theta: float) -> bool:
        """Check Gatheral-Jacquier arbitrage-free conditions."""
        ph = self.phi(theta)
        c1 = theta * ph * (1 + abs(self.rho)) <= 4
        c2 = theta * ph ** 2 * (1 + abs(self.rho)) <= 4
        return bool(c1 and c2)


class VolatilitySurface:
    """
    Calibrates and provides query interface for the implied volatility surface.

    Supports:
      - SSVI global surface fitting
      - SVI slice-by-slice fitting (with calendar spread penalty)
      - Cubic spline interpolation fallback
      - Vol surface analytics (skew, term structure slope, kurtosis)
    """

    def __init__(self, model: str = "ssvi") -> None:
        """
        Parameters
        ----------
        model : 'ssvi' | 'svi' | 'cubic_spline'
        """
        self.model_type = model
        self._svi_slices: Dict[float, SVIParams] = {}   # maturity → params
        self._ssvi_params: Optional[SSVIParams] = None
        self._atm_term_structure: Optional[CubicSpline] = None
        self._maturities: List[float] = []
        self._fitted = False

    # ------------------------------------------------------------------
    # Fitting
    # ------------------------------------------------------------------

    def fit(self, options_df: pd.DataFrame, spot: float, r: float = 0.05) -> "VolatilitySurface":
        """
        Calibrate the vol surface to market option data.

        Parameters
        ----------
        options_df : DataFrame with columns:
                     [strike, maturity (years), iv (implied vol), option_type,
                      bid, ask, mid]
        spot       : current underlying spot price
        r          : risk-free rate (annualised)

        Returns self for chaining.
        """
        if options_df.empty:
            logger.warning("Empty options data — surface not fitted")
            return self

        options_df = options_df.copy()
        options_df["forward"] = spot * np.exp(r * options_df["maturity"])
        options_df["k"] = np.log(options_df["strike"] / options_df["forward"])
        options_df["w_market"] = options_df["iv"] ** 2 * options_df["maturity"]
        options_df = options_df.dropna(subset=["k", "w_market"])
        options_df = options_df[options_df["w_market"] > 0]

        self._maturities = sorted(options_df["maturity"].unique())

        if self.model_type == "ssvi":
            self._fit_ssvi(options_df)
        elif self.model_type == "svi":
            self._fit_svi_slices(options_df)
        else:
            self._fit_cubic_spline(options_df, spot, r)

        self._build_atm_term_structure(options_df)
        self._fitted = True
        logger.info(
            f"Vol surface fitted ({self.model_type}) | "
            f"{len(self._maturities)} maturities"
        )
        return self

    def _fit_ssvi(self, df: pd.DataFrame) -> None:
        """Global SSVI calibration across all maturities simultaneously."""
        # ATM variance per maturity (used as θ_t)
        atm_thetas = {}
        for T in self._maturities:
            slice_df = df[np.abs(df["maturity"] - T) < 1e-6]
            if len(slice_df) == 0:
                continue
            # ATM = closest to k=0
            atm_row = slice_df.iloc[(slice_df["k"].abs()).argsort().iloc[0]]
            atm_thetas[T] = float(atm_row["w_market"])

        if not atm_thetas:
            self._fit_cubic_spline(df, 1.0, 0.05)
            return

        def objective(params):
            rho, eta, gamma = params
            if not (-0.999 < rho < 0.0) or eta <= 0 or not (0 < gamma < 1):
                return 1e10
            ssvi = SSVIParams(rho=rho, eta=eta, gamma=gamma)
            total_err = 0.0
            for T, theta in atm_thetas.items():
                if not ssvi.is_arbitrage_free(theta):
                    total_err += 1e6
                slice_df = df[np.abs(df["maturity"] - T) < 1e-6]
                for _, row in slice_df.iterrows():
                    w_model = ssvi.total_variance(row["k"], theta)
                    total_err += (w_model - row["w_market"]) ** 2
            return total_err

        result = differential_evolution(
            objective,
            bounds=[(-0.999, -0.001), (0.01, 5.0), (0.01, 0.99)],
            maxiter=500,
            tol=1e-8,
            seed=42,
            disp=False,
        )

        rho_opt, eta_opt, gamma_opt = result.x
        self._ssvi_params = SSVIParams(rho=rho_opt, eta=eta_opt, gamma=gamma_opt)
        self._atm_variances = atm_thetas
        logger.debug(f"SSVI params: ρ={rho_opt:.4f} η={eta_opt:.4f} γ={gamma_opt:.4f}")

    def _fit_svi_slices(self, df: pd.DataFrame) -> None:
        """Fit independent SVI slices per maturity with calendar penalty."""
        prev_a = None
        for T in sorted(self._maturities):
            slice_df = df[np.abs(df["maturity"] - T) < 1e-6].copy()
            if len(slice_df) < 3:
                continue

            k_arr = slice_df["k"].values
            w_arr = slice_df["w_market"].values

            def svi_objective(params, penalty_a=None):
                a, b, rho, m, sigma = params
                if b < 0 or sigma <= 0 or abs(rho) >= 1:
                    return 1e10
                if a + b * sigma * np.sqrt(1 - rho ** 2) < 0:
                    return 1e10
                diff = k_arr - m
                w_model = a + b * (rho * diff + np.sqrt(diff ** 2 + sigma ** 2))
                err = np.sum((w_model - w_arr) ** 2)
                # Calendar spread penalty: new_a >= prev_a
                if penalty_a is not None and a < penalty_a:
                    err += 1e6 * (penalty_a - a) ** 2
                return err

            # Initial guess
            atm_w = np.interp(0, sorted(k_arr), w_arr[np.argsort(k_arr)])
            x0 = [atm_w * 0.8, 0.1, -0.3, 0.0, 0.1]

            result = minimize(
                lambda p: svi_objective(p, penalty_a=prev_a),
                x0,
                method="Nelder-Mead",
                options={"maxiter": 5000, "xatol": 1e-7, "fatol": 1e-7},
            )

            if result.success or result.fun < 1.0:
                a, b, rho, m, sigma = result.x
                self._svi_slices[T] = SVIParams(
                    a=float(a), b=float(abs(b)), rho=float(np.clip(rho, -0.999, 0.999)),
                    m=float(m), sigma=float(abs(sigma)), maturity=float(T)
                )
                prev_a = float(a)

    def _fit_cubic_spline(self, df: pd.DataFrame, spot: float, r: float) -> None:
        """Fallback: cubic spline interpolation of market IVs."""
        logger.debug("Falling back to cubic spline vol surface")

    def _build_atm_term_structure(self, df: pd.DataFrame) -> None:
        """Build ATM implied vol vs. maturity spline."""
        atm_data = []
        for T in self._maturities:
            slice_df = df[np.abs(df["maturity"] - T) < 1e-6]
            if len(slice_df) == 0:
                continue
            atm_row = slice_df.iloc[(slice_df["k"].abs()).argsort().iloc[0]]
            atm_data.append((T, float(atm_row["iv"])))

        if len(atm_data) >= 3:
            mats, ivs = zip(*sorted(atm_data))
            self._atm_term_structure = CubicSpline(mats, ivs)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_implied_vol(self, k: float, T: float) -> float:
        """
        Return the model-implied volatility at log-moneyness k and maturity T.

        Parameters
        ----------
        k : log(K/F) log-moneyness
        T : time to expiry in years
        """
        if not self._fitted:
            return np.nan

        if self.model_type == "ssvi" and self._ssvi_params is not None:
            # Interpolate ATM variance
            if hasattr(self, "_atm_variances") and self._atm_variances:
                mats = sorted(self._atm_variances.keys())
                thetas = [self._atm_variances[m] for m in mats]
                theta = float(np.interp(T, mats, thetas))
                return float(self._ssvi_params.implied_vol(k, T, theta))

        if self.model_type == "svi" and self._svi_slices:
            slice_mats = sorted(self._svi_slices.keys())
            if len(slice_mats) == 1:
                return float(self._svi_slices[slice_mats[0]].implied_vol(k))
            # Linear interpolation between adjacent slices
            if T <= slice_mats[0]:
                return float(self._svi_slices[slice_mats[0]].implied_vol(k))
            if T >= slice_mats[-1]:
                return float(self._svi_slices[slice_mats[-1]].implied_vol(k))
            for i in range(len(slice_mats) - 1):
                t1, t2 = slice_mats[i], slice_mats[i + 1]
                if t1 <= T <= t2:
                    w1 = self._svi_slices[t1].total_variance(k)
                    w2 = self._svi_slices[t2].total_variance(k)
                    alpha = (T - t1) / (t2 - t1)
                    w = w1 + alpha * (w2 - w1)  # linear interp in total var
                    return float(np.sqrt(max(w, 0) / T))

        if self._atm_term_structure is not None:
            return float(self._atm_term_structure(T))

        return np.nan

    def get_atm_vol(self, T: float) -> float:
        """Return ATM implied vol at maturity T."""
        return self.get_implied_vol(k=0.0, T=T)

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def skew(self, T: float, dk: float = 0.1) -> float:
        """
        Vol skew: ∂σ/∂k at k=0 for maturity T.
        Negative skew means puts more expensive than calls (normal for equity).
        """
        vol_plus = self.get_implied_vol(dk, T)
        vol_minus = self.get_implied_vol(-dk, T)
        return (vol_plus - vol_minus) / (2 * dk)

    def term_structure_slope(self) -> float:
        """
        Slope of ATM vol term structure (short-term vs long-term).
        Negative = backwardation (short-term > long-term) → elevated near-term fear.
        Positive = contango (normal state, carry trade positive).
        """
        if self._atm_term_structure is None or len(self._maturities) < 2:
            return 0.0
        short_t = min(self._maturities)
        long_t = max(self._maturities)
        short_vol = self.get_atm_vol(short_t)
        long_vol = self.get_atm_vol(long_t)
        return float((long_vol - short_vol) / max(long_t - short_t, 1e-6))

    def vix_term_structure_slope(self, vix_1m: float, vix_3m: float) -> float:
        """
        VIX term structure: VIX3M/VIX ratio.
        > 1 = contango (sell premium → favourable)
        < 1 = backwardation (sell premium → unfavourable)
        """
        return vix_3m / vix_1m if vix_1m > 0 else 1.0

    def surface_kurtosis(self, T: float, dk: float = 0.1) -> float:
        """Measure of vol smile curvature (proxy for tail risk pricing)."""
        vol_itm = self.get_implied_vol(-dk, T)
        vol_atm = self.get_implied_vol(0.0, T)
        vol_otm = self.get_implied_vol(dk, T)
        if any(np.isnan([vol_itm, vol_atm, vol_otm])) or vol_atm == 0:
            return 0.0
        return (vol_itm + vol_otm - 2 * vol_atm) / (dk ** 2)

    def mispricing_z_score(
        self,
        market_iv: float,
        model_iv: float,
        historical_spread_std: float,
    ) -> float:
        """
        Z-score of market vs model IV deviation.
        Large positive z → market overpricing vol → sell premium opportunity.
        """
        spread = market_iv - model_iv
        return spread / max(historical_spread_std, 1e-6)

    def summary_stats(self, T_target: float = 30 / 365) -> Dict[str, float]:
        """Return key surface metrics for a target maturity."""
        if not self._fitted:
            return {}
        T = T_target
        return {
            "atm_vol": self.get_atm_vol(T),
            "put_25d_vol": self.get_implied_vol(-0.3, T),
            "call_25d_vol": self.get_implied_vol(0.3, T),
            "skew_25d": self.skew(T, dk=0.3),
            "kurtosis": self.surface_kurtosis(T),
            "term_structure_slope": self.term_structure_slope(),
        }
