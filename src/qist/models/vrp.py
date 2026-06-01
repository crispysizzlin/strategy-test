"""Variance Risk Premium (VRP) estimation.

The VRP is the compensation earned for selling variance: the gap between the
risk-neutral expected variance (what option buyers pay, ~ IV^2) and the
physical expected variance (what actually realizes, ~ E[RV^2]).  It is positive
~86% of the time and is the economic engine of every premium-selling book.

We provide two estimators:

1. ``forecast_vrp`` - the practical daily signal:
       VRP = IV^2 - E_P[RV^2]
   where IV is the option-implied vol for the horizon and E_P[RV^2] is the
   HAR-RV / GARCH forecast of realized variance.

2. ``carr_wu_variance_swap_rate`` - the model-free risk-neutral variance,
   replicated from a strip of OTM options (Carr & Wu 2009 / the VIX formula):

       KVar = (2/T) * sum_i (dK_i / K_i^2) * e^{rT} * Q(K_i) - (1/T)*(F/K0 - 1)^2

   Subtracting realized variance from this gives the *realized* VRP, the gold
   standard used to validate the forecast signal.

References
----------
Carr, P. & Wu, L. (2009). Variance Risk Premiums. *Review of Financial Studies*.
Bollerslev, T., Tauchen, G. & Zhou, H. (2009). Expected Stock Returns and
Variance Risk Premia. *Review of Financial Studies*.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


@dataclass
class VRPSignal:
    iv: float                 # implied vol for the horizon (annualized)
    rv_forecast: float        # forecast realized vol (annualized)
    vrp_var: float            # IV^2 - RV_forecast^2 (variance units)
    vrp_vol_points: float     # IV - RV_forecast (vol points, intuitive)
    vrp_ratio: float          # IV / RV_forecast
    z_score: float = float("nan")   # standardized vs history, if provided

    @property
    def is_favorable(self) -> bool:
        return self.vrp_var > 0


def forecast_vrp(iv: float, rv_forecast: float,
                 vrp_history: Sequence[float] | None = None) -> VRPSignal:
    """Compute the forward VRP signal from implied and forecast realized vol.

    If ``vrp_history`` (past ``vrp_var`` values) is supplied, also returns a
    z-score so the engine can demand a *statistically rich* premium rather than
    merely a positive one.
    """
    vrp_var = iv * iv - rv_forecast * rv_forecast
    ratio = iv / rv_forecast if rv_forecast > 0 else float("inf")
    z = float("nan")
    if vrp_history is not None and len(vrp_history) >= 20:
        import numpy as np
        arr = np.asarray(vrp_history, dtype=float)
        mu, sd = float(arr.mean()), float(arr.std(ddof=1))
        if sd > 0:
            z = (vrp_var - mu) / sd
    return VRPSignal(iv=iv, rv_forecast=rv_forecast, vrp_var=vrp_var,
                     vrp_vol_points=iv - rv_forecast, vrp_ratio=ratio, z_score=z)


@dataclass
class OptionQuote:
    strike: float
    is_call: bool
    mid: float          # mid price


def carr_wu_variance_swap_rate(forward: float, t: float, r: float,
                               quotes: Sequence[OptionQuote]) -> float:
    """Model-free annualized variance swap rate (risk-neutral E[RV^2]).

    Implements the discretized replication used by the VIX (Carr-Madan / Demeterfi
    et al.) over a strip of OTM options.  ``quotes`` should be OTM puts below the
    forward and OTM calls above it.  Returns annualized variance (sigma^2 units).
    """
    if t <= 0 or not quotes:
        return float("nan")
    qs = sorted(quotes, key=lambda x: x.strike)
    strikes = [q.strike for q in qs]

    # K0 = first strike below the forward.
    k0 = strikes[0]
    for k in strikes:
        if k <= forward:
            k0 = k
        else:
            break

    disc = math.exp(r * t)
    contrib = 0.0
    n = len(qs)
    for i, q in enumerate(qs):
        # Use OTM option for each strike: puts for K<=K0, calls for K>K0.
        use_call = q.strike > k0
        if use_call != q.is_call:
            # Skip ITM duplicates; the OTM wing carries the information.
            continue
        if i == 0:
            dk = strikes[1] - strikes[0]
        elif i == n - 1:
            dk = strikes[-1] - strikes[-2]
        else:
            dk = 0.5 * (strikes[i + 1] - strikes[i - 1])
        contrib += (dk / (q.strike ** 2)) * disc * q.mid

    var = (2.0 / t) * contrib - (1.0 / t) * (forward / k0 - 1.0) ** 2
    return var


def realized_vrp(variance_swap_rate: float, realized_variance: float) -> float:
    """Realized VRP = risk-neutral variance - realized variance (annualized)."""
    return variance_swap_rate - realized_variance
