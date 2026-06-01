"""
Model-free implied variance and variance risk premium (Carr & Wu, 2009).

Discrete strike integration from listed OTM options.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np


@dataclass(frozen=True)
class OptionQuote:
    strike: float
    mid: float
    option_type: str  # "call" | "put"


@dataclass(frozen=True)
class VRPResult:
    implied_variance: float
    forecast_realized_variance: float
    vrp: float
    vrp_z: float


def model_free_implied_variance(
    spot: float,
    forward: float,
    maturity_years: float,
    options: Iterable[OptionQuote],
) -> float:
    """
    Carr-Wu (2009) synthetic variance swap rate (annualized variance).

    Integrates OTM puts below forward and OTM calls above forward.
    """
    if maturity_years <= 0:
        raise ValueError("maturity_years must be positive")

    puts = sorted(
        [o for o in options if o.option_type == "put" and o.strike < forward and o.mid > 0],
        key=lambda x: x.strike,
    )
    calls = sorted(
        [o for o in options if o.option_type == "call" and o.strike > forward and o.mid > 0],
        key=lambda x: x.strike,
    )
    if len(puts) < 2 or len(calls) < 2:
        raise ValueError("need at least two OTM puts and calls for integration")

    def _integrate(strikes: list[float], mids: list[float]) -> float:
        total = 0.0
        for i in range(len(strikes)):
            k_low = strikes[i - 1] if i > 0 else strikes[0]
            k_high = strikes[i + 1] if i < len(strikes) - 1 else strikes[i]
            dk = (k_high - k_low) / 2.0 if i > 0 and i < len(strikes) - 1 else (k_high - k_low)
            total += (mids[i] / (strikes[i] ** 2)) * dk
        return total

    put_k = [p.strike for p in puts]
    put_m = [p.mid for p in puts]
    call_k = [c.strike for c in calls]
    call_m = [c.mid for c in calls]

    integral = _integrate(put_k, put_m) + _integrate(call_k, call_m)
    forward_adj = (forward / spot - 1.0) ** 2
    return max((2.0 / maturity_years) * integral - forward_adj / maturity_years, 1e-8)


def vrp_zscore(
    implied_var: float,
    forecast_realized_var: float,
    history_vrp: np.ndarray,
) -> VRPResult:
    """VRP = implied - forecast realized; z-score vs rolling history."""
    vrp = implied_var - forecast_realized_var
    if history_vrp.size < 5:
        z = 0.0
    else:
        mu = float(np.mean(history_vrp))
        sigma = float(np.std(history_vrp, ddof=1))
        z = (vrp - mu) / sigma if sigma > 1e-12 else 0.0
    return VRPResult(
        implied_variance=implied_var,
        forecast_realized_variance=forecast_realized_var,
        vrp=vrp,
        vrp_z=z,
    )
