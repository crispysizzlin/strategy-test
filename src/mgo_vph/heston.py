"""
Heston (1993) stochastic volatility — characteristic function pricing.

Used for wing strike selection via tail probability under calibrated params.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

Array = NDArray[np.floating]


@dataclass(frozen=True)
class HestonParams:
    v0: float  # initial variance
    kappa: float  # mean reversion
    theta: float  # long-run variance
    xi: float  # vol of vol
    rho: float  # correlation
    r: float = 0.05  # risk-free


def _heston_cf(
    u: complex,
    tau: float,
    spot: float,
    params: HestonParams,
) -> complex:
    """Characteristic function for log-spot (Heston, 1993; Gatheral formulation)."""
    i = 1j
    kappa, theta, xi, rho, v0, r = (
        params.kappa,
        params.theta,
        params.xi,
        params.rho,
        params.v0,
        params.r,
    )
    d = np.sqrt((rho * xi * i * u - kappa) ** 2 + xi**2 * (i * u + u**2))
    g = (kappa - rho * xi * i * u - d) / (kappa - rho * xi * i * u + d)
    c1 = r * i * u * tau + (kappa * theta / xi**2) * (
        (kappa - rho * xi * i * u - d) * tau - 2 * np.log((1 - g * np.exp(-d * tau)) / (1 - g))
    )
    c2 = (kappa - rho * xi * i * u - d) / xi**2 * (1 - np.exp(-d * tau)) / (1 - g * np.exp(-d * tau))
    return np.exp(c1 + c2 * v0 + i * u * np.log(spot))


def heston_call_price(
    spot: float,
    strike: float,
    tau: float,
    params: HestonParams,
    n_points: int = 128,
) -> float:
    """European call via Lewis/Gatheral Fourier inversion."""
    if tau <= 0:
        return max(spot - strike, 0.0)

    def integrand(u: float) -> float:
        phi = _heston_cf(u - 1j, tau, spot, params)
        return np.real(np.exp(-1j * u * np.log(strike)) * phi / (1j * u * strike))

    # Simpson on [0, U_max]
    u_max = 100.0
    x = np.linspace(1e-6, u_max, n_points)
    vals = np.array([integrand(float(u)) for u in x])
    integral = np.trapezoid(vals, x)
    return spot - strike * (0.5 + integral / np.pi)


def tail_probability_put(
    spot: float,
    strike: float,
    tau: float,
    params: HestonParams,
    n_points: int = 128,
) -> float:
    """Approx P(S_T < K) using put-call and digital approximation."""
    # P(S<K) ≈ 1 + dP/dK * K for OTM put density (rough via finite diff)
    eps = strike * 0.001
    p1 = heston_call_price(spot, strike - eps, tau, params, n_points)
    p2 = heston_call_price(spot, strike + eps, tau, params, n_points)
    # -dC/dK = N(d2) proxy
    density = (p1 - p2) / (2 * eps)
    return float(np.clip(density * strike, 0.0, 1.0))


def calibrate_wing_strike(
    spot: float,
    tau: float,
    params: HestonParams,
    target_touch_prob: float = 0.12,
    strike_min_pct: float = 0.03,
    strike_max_pct: float = 0.15,
    n_grid: int = 40,
) -> float:
    """Find OTM put strike with Heston touch probability near target."""
    strikes = spot * (1 - np.linspace(strike_min_pct, strike_max_pct, n_grid))
    probs = [tail_probability_put(spot, k, tau, params) for k in strikes]
    idx = int(np.argmin(np.abs(np.array(probs) - target_touch_prob)))
    return float(strikes[idx])
