"""Ornstein-Uhlenbeck mean-reversion estimation.

A short-vol book benefits from entering when the underlying (or a relative-value
spread) is *stretched* and likely to revert, improving the realized path of the
short strikes.  We model a (de-trended) series as an OU process

    dX_t = kappa (theta - X_t) dt + sigma dW_t

estimated by exact AR(1) regression of X_{t+1} on X_t.  We report the
half-life of mean reversion and a standardized deviation ("s-score", in the
spirit of Avellaneda & Lee 2010) used to time entries and bias strike skew.

Reference
---------
Avellaneda, M. & Lee, J. (2010). Statistical Arbitrage in the US Equities
Market. *Quantitative Finance*, 10(7), 761-782.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class OUParams:
    kappa: float        # mean-reversion speed (per step)
    theta: float        # long-run mean
    sigma: float        # diffusion (per step)
    half_life: float    # ln(2)/kappa, in steps
    s_score: float      # standardized current deviation from theta

    @property
    def is_mean_reverting(self) -> bool:
        return self.kappa > 0 and math.isfinite(self.half_life)


def fit_ou(x: np.ndarray, dt: float = 1.0) -> OUParams:
    """Fit OU parameters via AR(1) regression: X_{t+1} = a + b X_t + eps.

    Mapping: b = exp(-kappa*dt), theta = a/(1-b),
             sigma^2 = var(eps) * 2 kappa / (1 - b^2).
    """
    x = np.asarray(x, dtype=float).ravel()
    x = x[np.isfinite(x)]
    if len(x) < 20:
        raise ValueError("Need >=20 observations to fit OU")
    x0 = x[:-1]
    x1 = x[1:]
    X = np.column_stack([np.ones_like(x0), x0])
    coef, _, _, _ = np.linalg.lstsq(X, x1, rcond=None)
    a, b = float(coef[0]), float(coef[1])
    resid = x1 - X @ coef
    resid_var = float(resid @ resid) / max(len(resid) - 2, 1)

    b = min(max(b, 1e-6), 1 - 1e-6)        # keep stable / stationary
    kappa = -math.log(b) / dt
    theta = a / (1.0 - b)
    sigma_eq = math.sqrt(resid_var * 2 * kappa / (1 - b * b)) if kappa > 0 else float("nan")
    half_life = math.log(2.0) / kappa if kappa > 0 else float("inf")

    equil_std = math.sqrt(resid_var / (1 - b * b)) if (1 - b * b) > 0 else float("nan")
    s_score = (x[-1] - theta) / equil_std if equil_std and equil_std > 0 else float("nan")

    return OUParams(kappa=kappa, theta=theta, sigma=sigma_eq,
                    half_life=half_life, s_score=s_score)
