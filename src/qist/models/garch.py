"""GARCH(1,1) volatility model estimated by maximum likelihood.

The GARCH(1,1) of Bollerslev (1986) captures volatility clustering:

    sigma^2_t = omega + alpha * eps^2_{t-1} + beta * sigma^2_{t-1}

with stationarity requiring alpha + beta < 1 and the long-run (unconditional)
daily variance equal to omega / (1 - alpha - beta).  We fit by maximizing the
Gaussian quasi-log-likelihood.  HAR-RV is our primary forecaster; GARCH is kept
as an ensemble member and a sanity cross-check, since the two disagree most
exactly when regime risk is rising.

Reference
---------
Bollerslev, T. (1986). Generalized Autoregressive Conditional
Heteroskedasticity. *Journal of Econometrics*, 31(3), 307-327.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize

TRADING_DAYS = 252


@dataclass
class GARCHFit:
    omega: float
    alpha: float
    beta: float
    loglik: float
    last_variance: float    # filtered daily variance at end of sample
    last_resid_sq: float

    @property
    def persistence(self) -> float:
        return self.alpha + self.beta

    @property
    def unconditional_daily_var(self) -> float:
        denom = 1.0 - self.persistence
        return self.omega / denom if denom > 1e-9 else float("nan")


def _neg_loglik(params: np.ndarray, r: np.ndarray) -> float:
    omega, alpha, beta = params
    if omega <= 0 or alpha < 0 or beta < 0 or alpha + beta >= 1.0:
        return 1e12
    n = len(r)
    var = np.empty(n)
    var[0] = np.var(r)
    for t in range(1, n):
        var[t] = omega + alpha * r[t - 1] ** 2 + beta * var[t - 1]
        if var[t] <= 0:
            return 1e12
    ll = -0.5 * np.sum(np.log(2 * np.pi * var) + r ** 2 / var)
    return -ll


class GARCH11:
    def __init__(self) -> None:
        self.fit_: GARCHFit | None = None

    def fit(self, returns: np.ndarray) -> "GARCH11":
        """Fit on *daily* (de-meaned) log returns."""
        r = np.asarray(returns, dtype=float)
        r = r[np.isfinite(r)]
        r = r - r.mean()
        if len(r) < 50:
            raise ValueError("Need >=50 returns to fit GARCH(1,1)")
        sample_var = float(np.var(r))
        x0 = np.array([sample_var * 0.1, 0.08, 0.90])
        bounds = [(1e-12, None), (0.0, 0.999), (0.0, 0.999)]
        cons = ({"type": "ineq", "fun": lambda p: 0.999 - (p[1] + p[2])},)
        res = minimize(_neg_loglik, x0, args=(r,), method="SLSQP",
                       bounds=bounds, constraints=cons,
                       options={"maxiter": 500, "ftol": 1e-9})
        omega, alpha, beta = res.x
        # Recompute terminal filtered variance.
        n = len(r)
        var = sample_var
        for t in range(1, n):
            var = omega + alpha * r[t - 1] ** 2 + beta * var
        self.fit_ = GARCHFit(omega=float(omega), alpha=float(alpha),
                             beta=float(beta), loglik=float(-res.fun),
                             last_variance=float(var),
                             last_resid_sq=float(r[-1] ** 2))
        return self

    def forecast_vol(self, horizon_days: int = 1, annualize: bool = True) -> float:
        """Forecast volatility over the next ``horizon_days`` (path-aggregated).

        Uses the analytic multi-step variance forecast and aggregates to a single
        annualized sigma for the horizon.
        """
        if self.fit_ is None:
            raise RuntimeError("GARCH must be fit before forecasting")
        f = self.fit_
        uncond = f.unconditional_daily_var
        # One-step-ahead conditional variance.
        v1 = f.omega + f.alpha * f.last_resid_sq + f.beta * f.last_variance
        persistence = f.persistence
        total = 0.0
        for h in range(horizon_days):
            vh = uncond + (persistence ** h) * (v1 - uncond)
            total += vh
        avg_daily_var = total / horizon_days
        scale = TRADING_DAYS if annualize else 1.0
        return math.sqrt(max(avg_daily_var * scale, 0.0))
