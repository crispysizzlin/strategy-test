"""Black-Scholes-Merton pricing, Greeks and implied volatility.

The Black-Scholes-Merton (BSM) PDE underpins every option Greek we use to
manage the short-premium book.  For a non-dividend-paying forward we price a
European option and expose the full first/second-order Greek set:

    delta = dV/dS,  gamma = d2V/dS2,  vega = dV/dsigma,
    theta = dV/dt,  rho = dV/dr,      vanna = d2V/dS dsigma,
    vomma = d2V/dsigma2

References
----------
Black, F. & Scholes, M. (1973). The Pricing of Options and Corporate
Liabilities. *Journal of Political Economy*.
Merton, R. (1973). Theory of Rational Option Pricing. *Bell Journal*.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.optimize import brentq
from scipy.stats import norm

SQRT_252 = math.sqrt(252.0)


def _d1_d2(S: float, K: float, t: float, r: float, sigma: float, q: float = 0.0):
    if t <= 0 or sigma <= 0:
        raise ValueError("t and sigma must be positive for d1/d2")
    vol_sqrt_t = sigma * math.sqrt(t)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * t) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    return d1, d2


def bs_price(S: float, K: float, t: float, r: float, sigma: float,
             is_call: bool, q: float = 0.0) -> float:
    """European option price under BSM.

    Handles the degenerate ``t<=0`` (intrinsic) and ``sigma<=0`` cases gracefully
    so the function is safe to call near expiry inside the engine.
    """
    if t <= 0:
        intrinsic = (S - K) if is_call else (K - S)
        return max(intrinsic, 0.0)
    if sigma <= 0:
        fwd = S * math.exp(-q * t) - K * math.exp(-r * t)
        intrinsic = fwd if is_call else -fwd
        return max(intrinsic, 0.0)
    d1, d2 = _d1_d2(S, K, t, r, sigma, q)
    df_r = math.exp(-r * t)
    df_q = math.exp(-q * t)
    if is_call:
        return S * df_q * norm.cdf(d1) - K * df_r * norm.cdf(d2)
    return K * df_r * norm.cdf(-d2) - S * df_q * norm.cdf(-d1)


@dataclass
class Greeks:
    price: float
    delta: float
    gamma: float
    vega: float      # per 1.00 (100%) change in vol; divide by 100 for per-vol-point
    theta: float     # per calendar day
    rho: float       # per 1.00 change in rate
    vanna: float
    vomma: float


def bs_greeks(S: float, K: float, t: float, r: float, sigma: float,
              is_call: bool, q: float = 0.0) -> Greeks:
    """Full Greek set.  ``theta`` is reported per calendar day (annual/365)."""
    price = bs_price(S, K, t, r, sigma, is_call, q)
    if t <= 0 or sigma <= 0:
        # Degenerate: deltas collapse to step functions, higher Greeks ~0.
        intrinsic_call = S > K
        delta = (1.0 if intrinsic_call else 0.0) if is_call else (
            -1.0 if not intrinsic_call else 0.0)
        return Greeks(price, delta, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    d1, d2 = _d1_d2(S, K, t, r, sigma, q)
    df_r = math.exp(-r * t)
    df_q = math.exp(-q * t)
    pdf_d1 = norm.pdf(d1)
    sqrt_t = math.sqrt(t)

    if is_call:
        delta = df_q * norm.cdf(d1)
        rho = K * t * df_r * norm.cdf(d2)
        theta_annual = (
            -S * df_q * pdf_d1 * sigma / (2 * sqrt_t)
            - r * K * df_r * norm.cdf(d2)
            + q * S * df_q * norm.cdf(d1)
        )
    else:
        delta = -df_q * norm.cdf(-d1)
        rho = -K * t * df_r * norm.cdf(-d2)
        theta_annual = (
            -S * df_q * pdf_d1 * sigma / (2 * sqrt_t)
            + r * K * df_r * norm.cdf(-d2)
            - q * S * df_q * norm.cdf(-d1)
        )

    gamma = df_q * pdf_d1 / (S * sigma * sqrt_t)
    vega = S * df_q * pdf_d1 * sqrt_t
    vanna = -df_q * pdf_d1 * d2 / sigma
    vomma = vega * d1 * d2 / sigma

    return Greeks(
        price=price,
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta_annual / 365.0,
        rho=rho,
        vanna=vanna,
        vomma=vomma,
    )


def implied_vol(price: float, S: float, K: float, t: float, r: float,
                is_call: bool, q: float = 0.0,
                lo: float = 1e-4, hi: float = 5.0) -> float:
    """Invert BSM for implied volatility via Brent's method.

    Returns ``nan`` when the quoted price violates no-arbitrage bounds (the
    solver has no root), which the caller should treat as an unusable quote.
    """
    if t <= 0 or price <= 0:
        return float("nan")
    intrinsic = max((S - K) if is_call else (K - S), 0.0) * math.exp(-r * t)
    upper = (S * math.exp(-q * t)) if is_call else (K * math.exp(-r * t))
    if price < intrinsic - 1e-8 or price > upper + 1e-8:
        return float("nan")

    def objective(sig: float) -> float:
        return bs_price(S, K, t, r, sig, is_call, q) - price

    try:
        flo, fhi = objective(lo), objective(hi)
        if flo * fhi > 0:
            return float("nan")
        return brentq(objective, lo, hi, xtol=1e-8, maxiter=200)
    except (ValueError, RuntimeError):
        return float("nan")


def forward_price(S: float, t: float, r: float, q: float = 0.0) -> float:
    return S * math.exp((r - q) * t)
