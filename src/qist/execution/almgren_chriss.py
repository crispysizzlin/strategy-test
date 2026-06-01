"""Almgren-Chriss optimal execution schedule.

When a position must be unwound across an interval (e.g. rolling a tested side or
exiting a multi-contract structure into limited option liquidity), trading too
fast pays market impact while trading too slow pays volatility risk.  The
Almgren-Chriss (2000) framework gives the optimal trajectory that minimizes a
mean-variance cost functional, controlled by risk aversion ``lambda``:

    x_j = X * sinh(kappa (T - t_j)) / sinh(kappa T),
    kappa = arccosh(1 + lambda * sigma^2 * tau^2 / (2 eta)) / tau

where ``eta`` is temporary impact and ``tau`` the slice length.  Higher risk
aversion -> front-loaded (faster) liquidation.

Reference
---------
Almgren, R. & Chriss, N. (2000). Optimal execution of portfolio transactions.
*Journal of Risk*, 3, 5-40.
"""

from __future__ import annotations

import math

import numpy as np


def optimal_trajectory(total_qty: float, n_slices: int, sigma: float,
                       eta: float, risk_aversion: float, tau: float = 1.0) -> np.ndarray:
    """Return remaining-inventory trajectory x_0..x_N (x_0 = total_qty, x_N = 0)."""
    if n_slices < 1:
        raise ValueError("n_slices must be >= 1")
    T = n_slices * tau
    if risk_aversion <= 0 or sigma <= 0:
        # Risk-neutral: liquidate linearly (TWAP).
        return total_qty * (1.0 - np.arange(n_slices + 1) / n_slices)
    arg = 1.0 + risk_aversion * sigma ** 2 * tau ** 2 / (2.0 * eta)
    kappa = math.acosh(arg) / tau
    j = np.arange(n_slices + 1)
    x = total_qty * np.sinh(kappa * (T - j * tau)) / math.sinh(kappa * T)
    x[-1] = 0.0
    return x


def slice_sizes(total_qty: float, n_slices: int, sigma: float, eta: float,
                risk_aversion: float, tau: float = 1.0) -> np.ndarray:
    """Per-slice trade sizes (the negative diff of the inventory trajectory)."""
    x = optimal_trajectory(total_qty, n_slices, sigma, eta, risk_aversion, tau)
    return -np.diff(x)
