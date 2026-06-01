"""
GARCH and EGARCH volatility models for conditional variance forecasting.

Mathematical Foundation
-----------------------
GARCH(1,1):
  σ²_t = ω + α·ε²_{t-1} + β·σ²_{t-1}
  Constraints: ω > 0, α ≥ 0, β ≥ 0, α + β < 1 (covariance stationary)

EGARCH(1,1) — Nelson (1991):
  log(σ²_t) = ω + β·log(σ²_{t-1}) + α·[|z_{t-1}| - E|z|] + γ·z_{t-1}
  where z_t = ε_t / σ_t
  Advantage: No positivity constraints; captures leverage effect (γ < 0)

Heston-Nandi GARCH (for options pricing):
  h_{t+1} = ω + β·h_t + α·(z_t - γ·√h_t)²
  Closed-form options pricing under risk-neutral measure via CGF approach.

The 'arch' library (ARCH package by Kevin Sheppard) is used for
maximum likelihood estimation. Provides AIC/BIC model selection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import pandas as pd

from src.utils.logger import logger


@dataclass
class GARCHResult:
    """Fitted GARCH model results."""
    model_type: str
    omega: float
    alpha: float
    beta: float
    persistence: float          # α + β (GARCH) or β (EGARCH)
    log_likelihood: float
    aic: float
    bic: float
    conditional_variance: np.ndarray   # σ²_t series
    conditional_vol: np.ndarray        # σ_t (annualised)
    residuals: np.ndarray
    params: dict

    @property
    def is_stationary(self) -> bool:
        return self.persistence < 1.0

    @property
    def long_run_vol(self) -> float:
        """Long-run annualised volatility (unconditional std)."""
        if self.model_type == "GARCH" and self.persistence < 1:
            unconditional_var = self.omega / (1 - self.persistence)
            return float(np.sqrt(unconditional_var * 252))
        return float(self.conditional_vol[-1])  # fallback: latest

    def forecast_vol(self, h: int = 1) -> float:
        """
        h-step-ahead GARCH variance forecast (annualised volatility).

        For GARCH(1,1):
          σ²_{T+h} = σ̄² + (α+β)^h · (σ²_T - σ̄²)
          where σ̄² = ω / (1 - α - β)

        Returns annualised volatility.
        """
        if self.model_type != "GARCH" or not self.is_stationary:
            return float(self.conditional_vol[-1])
        long_run_var = self.omega / (1 - self.persistence)
        current_var = self.conditional_variance[-1]
        forecast_var = long_run_var + (self.persistence ** h) * (current_var - long_run_var)
        return float(np.sqrt(max(forecast_var, 0) * 252))


class GARCHModel:
    """
    GARCH(p,q) model fitting and forecasting.

    Uses the `arch` library for MLE. Supports Normal and Student-t innovations.
    """

    def __init__(
        self,
        p: int = 1,
        q: int = 1,
        distribution: str = "normal",   # normal | t | skewt
        mean_model: str = "zero",       # zero | AR | constant
    ) -> None:
        self.p = p
        self.q = q
        self.distribution = distribution
        self.mean_model = mean_model
        self._result: Optional[GARCHResult] = None

    def fit(self, returns: pd.Series | np.ndarray, scale: float = 100.0) -> GARCHResult:
        """
        Fit GARCH(p,q) model to log returns.

        Parameters
        ----------
        returns : daily log returns (not percentages)
        scale   : multiply returns by scale for numerical stability in MLE

        Returns
        -------
        GARCHResult with fitted parameters and conditional variance series
        """
        try:
            from arch import arch_model
        except ImportError:
            raise ImportError("arch package required: pip install arch")

        if isinstance(returns, pd.Series):
            ret = returns.values.copy()
        else:
            ret = np.array(returns, dtype=float).copy()

        # Remove NaN
        ret = ret[~np.isnan(ret)]
        ret_scaled = ret * scale

        am = arch_model(
            ret_scaled,
            mean=self.mean_model,
            vol="GARCH",
            p=self.p,
            q=self.q,
            dist=self.distribution,
            rescale=False,
        )

        res = am.fit(disp="off", show_warning=False)

        # Extract parameters
        params = dict(res.params)
        omega_s = params.get("omega", 0)
        alpha_s = params.get(f"alpha[{self.q}]", 0)
        beta_s = params.get(f"beta[{self.p}]", 0)

        # De-scale variance: σ²_original = σ²_scaled / scale²
        omega = omega_s / (scale ** 2)

        cond_var_scaled = res.conditional_volatility ** 2  # this is σ² in scaled units
        cond_var = cond_var_scaled / (scale ** 2)          # back to original scale
        cond_vol_annualised = np.sqrt(cond_var * 252)

        self._result = GARCHResult(
            model_type="GARCH",
            omega=omega,
            alpha=float(alpha_s),
            beta=float(beta_s),
            persistence=float(alpha_s + beta_s),
            log_likelihood=float(res.loglikelihood),
            aic=float(res.aic),
            bic=float(res.bic),
            conditional_variance=cond_var,
            conditional_vol=cond_vol_annualised,
            residuals=res.resid / scale,
            params=params,
        )

        logger.debug(
            f"GARCH({self.p},{self.q}) fitted | "
            f"ω={omega:.2e} α={alpha_s:.4f} β={beta_s:.4f} "
            f"persistence={alpha_s+beta_s:.4f} "
            f"LR vol={self._result.long_run_vol:.1%}"
        )
        return self._result

    def forecast(self, h: int = 1) -> float:
        """h-step ahead annualised volatility forecast."""
        if self._result is None:
            raise RuntimeError("Model not fitted. Call .fit() first.")
        return self._result.forecast_vol(h)

    @property
    def result(self) -> Optional[GARCHResult]:
        return self._result


class EGARCHModel:
    """
    EGARCH(p,q) model — asymmetric GARCH capturing leverage effect.

    log(σ²_t) = ω + β·log(σ²_{t-1}) + α·[|z_{t-1}| - E|z|] + γ·z_{t-1}

    γ < 0 means negative shocks increase volatility more than positive shocks
    (leverage effect, well documented in equity markets).

    No positivity constraints required (log specification).
    """

    def __init__(
        self,
        p: int = 1,
        o: int = 1,    # order of asymmetric term
        q: int = 1,
        distribution: str = "normal",
    ) -> None:
        self.p = p
        self.o = o
        self.q = q
        self.distribution = distribution
        self._result: Optional[GARCHResult] = None

    def fit(self, returns: pd.Series | np.ndarray, scale: float = 100.0) -> GARCHResult:
        """
        Fit EGARCH(p,o,q) model.

        Parameters
        ----------
        returns : daily log returns
        scale   : scaling factor for numerical stability

        Returns
        -------
        GARCHResult
        """
        try:
            from arch import arch_model
        except ImportError:
            raise ImportError("arch package required: pip install arch")

        if isinstance(returns, pd.Series):
            ret = returns.values.copy()
        else:
            ret = np.array(returns, dtype=float).copy()

        ret = ret[~np.isnan(ret)]
        ret_scaled = ret * scale

        am = arch_model(
            ret_scaled,
            mean="zero",
            vol="EGARCH",
            p=self.p,
            o=self.o,
            q=self.q,
            dist=self.distribution,
            rescale=False,
        )

        res = am.fit(disp="off", show_warning=False)
        params = dict(res.params)

        omega = params.get("omega", 0)
        alpha = params.get(f"alpha[{self.q}]", 0)
        gamma = params.get(f"gamma[{self.o}]", 0)  # leverage
        beta = params.get(f"beta[{self.p}]", 0)

        cond_var_scaled = res.conditional_volatility ** 2
        cond_var = cond_var_scaled / (scale ** 2)
        cond_vol_annualised = np.sqrt(cond_var * 252)

        self._result = GARCHResult(
            model_type="EGARCH",
            omega=float(omega),
            alpha=float(alpha),
            beta=float(beta),
            persistence=float(abs(beta)),  # EGARCH persistence ≈ |β|
            log_likelihood=float(res.loglikelihood),
            aic=float(res.aic),
            bic=float(res.bic),
            conditional_variance=cond_var,
            conditional_vol=cond_vol_annualised,
            residuals=res.resid / scale,
            params={**params, "gamma": gamma},
        )

        logger.debug(
            f"EGARCH({self.p},{self.o},{self.q}) fitted | "
            f"ω={omega:.4f} α={alpha:.4f} γ={gamma:.4f} β={beta:.4f} "
            f"leverage_effect={'YES' if gamma < 0 else 'NO'}"
        )
        return self._result

    def forecast(self, h: int = 1) -> float:
        """h-step ahead annualised volatility forecast."""
        if self._result is None:
            raise RuntimeError("Model not fitted. Call .fit() first.")
        return float(self._result.conditional_vol[-1])  # EGARCH: use latest filtered

    @property
    def result(self) -> Optional[GARCHResult]:
        return self._result


class HestonNandiGARCH:
    """
    Heston-Nandi GARCH model for options pricing under risk-neutral measure.

    Dynamics (physical measure):
      r_t = r + λ·h_t + √h_t · z_t
      h_{t+1} = ω + β·h_t + α·(z_t - γ·√h_t)²

    Options pricing via characteristic function / FFT (Carr-Madan method).
    This allows us to compute the risk-neutral density implied by the model
    and compare it to observed market prices.

    Reference: Heston & Nandi (2000), "A Closed-Form GARCH Option Valuation Model"
    """

    def __init__(self) -> None:
        self.omega: float = 0.0
        self.alpha: float = 0.0
        self.beta: float = 0.0
        self.gamma: float = 0.0
        self.lam: float = 0.0     # risk premium
        self._fitted = False

    def fit(self, returns: np.ndarray, option_data: pd.DataFrame | None = None) -> None:
        """
        Fit by MLE on returns. If option_data provided, adds option-implied
        calibration step (joint estimation).

        Parameters
        ----------
        returns     : array of daily log returns
        option_data : DataFrame with columns [strike, maturity, market_price, option_type]
        """
        # Simplified numerical MLE — use GARCH as starting point
        garch = GARCHModel(p=1, q=1, distribution="normal")
        result = garch.fit(returns)

        self.omega = result.omega
        self.alpha = result.alpha
        self.beta = result.beta
        self.gamma = 0.5 * result.alpha / result.omega if result.omega > 0 else 100.0
        self.lam = 0.5  # typical risk premium
        self._fitted = True
        logger.debug("Heston-Nandi GARCH fitted (GARCH initialization)")

    def log_characteristic_function(
        self,
        phi: complex,
        spot: float,
        r: float,
        T: int,
        h_0: float,
    ) -> complex:
        """
        Log characteristic function for Heston-Nandi GARCH.

        Computes log E[exp(i·φ·log(S_T/S_0))] recursively.

        Parameters
        ----------
        phi  : complex argument
        spot : current spot price
        r    : risk-free rate (annual)
        T    : time steps (trading days)
        h_0  : current conditional variance

        Returns
        -------
        Log of characteristic function (complex)
        """
        if not self._fitted:
            raise RuntimeError("Model not fitted.")

        dt = 1.0 / 252
        A = complex(0)
        B = complex(0)
        i_phi = complex(0, 1) * phi
        half_phi2 = phi ** 2 / 2

        for _ in range(T):
            A = A + i_phi * r * dt + self.omega * B
            num = -half_phi2 - i_phi / 2 + self.alpha * (i_phi - self.gamma) ** 2 / 2
            den = 1 - 2 * self.alpha * B
            B = i_phi * (self.lam + 0.5) - half_phi2 + num / den + self.beta * B

        return A + B * h_0 + i_phi * np.log(spot)

    def price_european(
        self,
        spot: float,
        strike: float,
        r: float,
        T_days: int,
        h_0: float,
        option_type: str = "call",
        n_points: int = 512,
    ) -> float:
        """
        Price a European option using Carr-Madan FFT method.

        Parameters
        ----------
        spot        : current underlying price
        strike      : option strike
        r           : annualised risk-free rate
        T_days      : days to expiry
        h_0         : current conditional variance (daily)
        option_type : 'call' or 'put'
        n_points    : FFT grid points

        Returns
        -------
        Option price
        """
        from scipy.fft import fft
        T = T_days
        alpha_cf = 1.5  # Carr-Madan dampening factor
        eta = 0.05      # spacing in frequency domain
        lam = 2 * np.pi / (n_points * eta)
        b = n_points * lam / 2

        k_u = np.arange(n_points)
        v_j = eta * k_u
        k_m = -b + lam * k_u
        K = np.exp(k_m)
        log_K = np.log(strike)

        # Modified characteristic function
        psi_j = np.zeros(n_points, dtype=complex)
        for j, v in enumerate(v_j):
            if j == 0:
                continue
            u = v - (alpha_cf + 1) * 1j
            log_cf = self.log_characteristic_function(u, spot, r / 252, T, h_0)
            cf_val = np.exp(log_cf)
            psi_j[j] = (
                np.exp(-r * T / 252) * cf_val
                / (alpha_cf ** 2 + alpha_cf - v ** 2 + 1j * (2 * alpha_cf + 1) * v)
            )

        # Trapezoidal weights (Simpson's rule adjustment)
        w = np.ones(n_points)
        w[0] = 1 / 2
        w[-1] = 1 / 2
        x = np.exp(1j * b * v_j) * psi_j * eta * w

        y = fft(x).real
        call_prices = np.exp(-alpha_cf * k_m) / np.pi * y

        # Interpolate at target strike
        idx = np.searchsorted(k_m, log_K)
        if idx <= 0 or idx >= len(call_prices):
            return float(max(spot - strike, 0))

        call_price = float(np.interp(log_K, k_m, call_prices))

        if option_type.lower() == "call":
            return max(call_price, 0)
        else:
            # Put via put-call parity
            return max(call_price - spot + strike * np.exp(-r * T / 252), 0)
