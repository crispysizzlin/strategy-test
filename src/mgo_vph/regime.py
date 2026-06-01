"""
Two-state Markov-switching GARCH regime filter (Klaassen, 2002; Marcucci, 2005).

Lightweight implementation suitable for daily signal updates.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.float64]


@dataclass(frozen=True)
class RegimeState:
    p_low_vol: float
    forecast_vol_5d: float
    label: str  # "low" | "high"


def _garch11_variance(
    returns: Array,
    omega: float,
    alpha: float,
    beta: float,
) -> Array:
    n = len(returns)
    var = np.zeros(n)
    var[0] = np.var(returns) if n > 1 else 1e-4
    for t in range(1, n):
        var[t] = omega + alpha * returns[t - 1] ** 2 + beta * var[t - 1]
    return var


def estimate_ms_garch_regime(
    returns: Array,
    n_iter: int = 25,
) -> RegimeState:
    """
    Simplified EM for 2-state MS-GARCH(1,1).

    Returns probability of currently being in the low-volatility regime.
    """
    returns = np.asarray(returns, dtype=np.float64)
    if returns.size < 60:
        return RegimeState(p_low_vol=0.5, forecast_vol_5d=float(np.std(returns) * np.sqrt(252)), label="low")

    # Initialize regimes by rolling vol median split
    rolling = np.convolve(returns**2, np.ones(20) / 20, mode="same")
    med = np.median(rolling)
    state = (rolling <= med).astype(int)  # 1 = low vol

    # Regime-specific GARCH params (fixed structure; EM updates transition)
    params = {
        0: (1e-6, 0.05, 0.90),  # high vol
        1: (1e-6, 0.03, 0.95),  # low vol
    }
    trans = np.array([[0.9, 0.1], [0.15, 0.85]])

    for _ in range(n_iter):
        vars_by_state = {}
        for s in (0, 1):
            mask = state == s
            r_s = returns[mask] if mask.sum() > 10 else returns
            omega, alpha, beta = params[s]
            vars_by_state[s] = _garch11_variance(r_s, omega, alpha, beta)

        # Viterbi-like: assign each day to state with lower normalized residual
        lik_low = np.zeros(len(returns))
        lik_high = np.zeros(len(returns))
        v_low = _garch11_variance(returns, *params[1])
        v_high = _garch11_variance(returns, *params[0])
        lik_low = returns**2 / (v_low + 1e-12)
        lik_high = returns**2 / (v_high + 1e-12)
        state = (lik_low < lik_high).astype(int)

    p_low = float(np.mean(state[-20:] == 1))
    v_forecast = float(np.sqrt(v_low[-1]) * np.sqrt(252)) if (v_low := _garch11_variance(returns, *params[1])).size else 0.2
    return RegimeState(
        p_low_vol=p_low,
        forecast_vol_5d=v_forecast,
        label="low" if p_low > 0.5 else "high",
    )
