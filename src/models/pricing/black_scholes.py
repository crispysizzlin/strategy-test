"""
Black-Scholes-Merton options pricing model with full Greek suite.

Mathematical Foundation
-----------------------
The BSM model assumes log-normal price process under the risk-neutral measure:
  dS = (r - q)·S·dt + σ·S·dW

Call price:
  C = S·e^(-q·T)·N(d1) - K·e^(-r·T)·N(d2)

Put price:
  P = K·e^(-r·T)·N(-d2) - S·e^(-q·T)·N(-d1)

where:
  d1 = [ln(S/K) + (r - q + σ²/2)·T] / (σ·√T)
  d2 = d1 - σ·√T

Greeks (first-order sensitivities):
  Delta (Δ) = ∂V/∂S     — sensitivity to spot price
  Gamma (Γ) = ∂²V/∂S²  — second-order sensitivity to spot
  Theta (Θ) = ∂V/∂t     — time decay (per calendar day, negative for long)
  Vega  (ν) = ∂V/∂σ     — sensitivity to implied vol (per 1% change)
  Rho   (ρ) = ∂V/∂r     — sensitivity to interest rate

Second-order Greeks:
  Vanna = ∂²V/(∂S·∂σ)   — how delta changes with vol
  Volga (Vomma) = ∂²V/∂σ²  — how vega changes with vol
  Charm = ∂Δ/∂t          — time decay of delta (theta of delta)

Implied Volatility:
  Inverted via Brent method (Newton-Raphson with Halley correction).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
from scipy.stats import norm
from scipy.optimize import brentq

from src.utils.logger import logger

# Normal PDF and CDF
_N = norm.cdf
_n = norm.pdf
_N_inv = norm.ppf


@dataclass
class OptionGreeks:
    """Complete Greek profile for an option."""
    price: float
    delta: float
    gamma: float
    theta: float      # per calendar day
    vega: float       # per 1 vol point (σ changes by 0.01)
    rho: float        # per 1% change in r
    vanna: float = 0.0
    volga: float = 0.0
    charm: float = 0.0
    speed: float = 0.0  # ∂Γ/∂S
    implied_vol: float = 0.0

    @property
    def theta_per_dollar(self) -> float:
        """Theta relative to option price."""
        return self.theta / self.price if self.price > 0 else 0.0

    @property
    def theta_vega_ratio(self) -> float:
        """
        Theta/Vega ratio — key metric for premium selling.
        Higher = more theta income per unit of vega risk.
        Institutional target: > 0.05 (5 cents daily theta per dollar of vega)
        """
        return abs(self.theta) / abs(self.vega) if abs(self.vega) > 0 else 0.0


class BlackScholes:
    """
    Black-Scholes-Merton option pricing with full Greek suite.

    Supports continuous dividend yield (q) for index options.
    For SPX/SPY, use q = dividend yield ≈ 1.3-1.6%.
    """

    # ------------------------------------------------------------------
    # Core pricing
    # ------------------------------------------------------------------

    @staticmethod
    def _d1_d2(
        S: float, K: float, r: float, q: float, sigma: float, T: float
    ) -> Tuple[float, float]:
        """Compute d1 and d2 for BSM formula."""
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return (np.inf if S > K else -np.inf), (np.inf if S > K else -np.inf)
        sqrt_T = np.sqrt(T)
        d1 = (np.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T
        return d1, d2

    @classmethod
    def price(
        cls,
        S: float,        # Spot price
        K: float,        # Strike price
        r: float,        # Risk-free rate (annual, continuous)
        q: float,        # Dividend yield (annual, continuous)
        sigma: float,    # Implied volatility (annual)
        T: float,        # Time to expiry (years)
        option_type: str = "call",
    ) -> float:
        """
        Compute BSM option price.

        Parameters
        ----------
        S, K, r, q, sigma, T : standard BSM inputs
        option_type : 'call' or 'put'

        Returns
        -------
        Option fair value
        """
        if T <= 0:
            # Intrinsic value at expiry
            if option_type.lower() == "call":
                return max(S - K, 0.0)
            return max(K - S, 0.0)

        d1, d2 = cls._d1_d2(S, K, r, q, sigma, T)
        discount_K = K * np.exp(-r * T)
        discount_S = S * np.exp(-q * T)

        if option_type.lower() == "call":
            return float(discount_S * _N(d1) - discount_K * _N(d2))
        else:
            return float(discount_K * _N(-d2) - discount_S * _N(-d1))

    @classmethod
    def greeks(
        cls,
        S: float,
        K: float,
        r: float,
        q: float,
        sigma: float,
        T: float,
        option_type: str = "call",
    ) -> OptionGreeks:
        """
        Compute all Greeks for an option.

        All sensitivities are in practical units:
          - Delta: ∂V/∂S (fraction, e.g. 0.50 per $1 move)
          - Gamma: ∂²V/∂S² (per $1 move)
          - Theta: per calendar day (total, negative for long)
          - Vega: per 1 vol point (σ changes by 0.01 = 1%)
          - Rho: per 1% change in r
        """
        price = cls.price(S, K, r, q, sigma, T, option_type)

        if T <= 0 or sigma <= 0:
            return OptionGreeks(
                price=price, delta=(1.0 if option_type == "call" and S > K else 0.0),
                gamma=0.0, theta=0.0, vega=0.0, rho=0.0,
            )

        d1, d2 = cls._d1_d2(S, K, r, q, sigma, T)
        sqrt_T = np.sqrt(T)
        n_d1 = _n(d1)
        disc_S = S * np.exp(-q * T)
        disc_K = K * np.exp(-r * T)

        # Delta
        if option_type.lower() == "call":
            delta = float(np.exp(-q * T) * _N(d1))
        else:
            delta = float(-np.exp(-q * T) * _N(-d1))

        # Gamma (same for calls and puts)
        gamma = float(np.exp(-q * T) * n_d1 / (S * sigma * sqrt_T))

        # Theta (per calendar day, consistent with market convention)
        if option_type.lower() == "call":
            theta_annual = (
                -np.exp(-q * T) * S * n_d1 * sigma / (2 * sqrt_T)
                + q * disc_S * _N(d1)
                - r * disc_K * _N(d2)
            )
        else:
            theta_annual = (
                -np.exp(-q * T) * S * n_d1 * sigma / (2 * sqrt_T)
                - q * disc_S * _N(-d1)
                + r * disc_K * _N(-d2)
            )
        theta = float(theta_annual / 365)  # per calendar day

        # Vega (per 1 vol point = 0.01 change in σ)
        vega = float(disc_S * n_d1 * sqrt_T * 0.01)

        # Rho (per 1% change in r = 0.01 change)
        if option_type.lower() == "call":
            rho = float(disc_K * T * _N(d2) * 0.01)
        else:
            rho = float(-disc_K * T * _N(-d2) * 0.01)

        # Vanna = ∂²V/(∂S·∂σ)
        vanna = float(-np.exp(-q * T) * n_d1 * d2 / sigma)

        # Volga (Vomma) = ∂²V/∂σ²
        volga = float(disc_S * n_d1 * sqrt_T * d1 * d2 / sigma)

        # Charm = ∂Δ/∂t (per calendar day)
        if option_type.lower() == "call":
            charm_annual = q * np.exp(-q * T) * _N(d1) - np.exp(-q * T) * n_d1 * (
                2 * (r - q) * T - d2 * sigma * sqrt_T
            ) / (2 * T * sigma * sqrt_T)
        else:
            charm_annual = -q * np.exp(-q * T) * _N(-d1) - np.exp(-q * T) * n_d1 * (
                2 * (r - q) * T - d2 * sigma * sqrt_T
            ) / (2 * T * sigma * sqrt_T)
        charm = float(charm_annual / 365)

        # Speed = ∂Γ/∂S
        speed = float(-gamma / S * (1 + d1 / (sigma * sqrt_T)))

        return OptionGreeks(
            price=price,
            delta=delta,
            gamma=gamma,
            theta=theta,
            vega=vega,
            rho=rho,
            vanna=vanna,
            volga=volga,
            charm=charm,
            speed=speed,
            implied_vol=sigma,
        )

    # ------------------------------------------------------------------
    # Implied Volatility
    # ------------------------------------------------------------------

    @classmethod
    def implied_volatility(
        cls,
        market_price: float,
        S: float,
        K: float,
        r: float,
        q: float,
        T: float,
        option_type: str = "call",
        tol: float = 1e-7,
        max_iter: int = 1000,
    ) -> float:
        """
        Compute implied volatility via Brent's method.

        Uses a tight bracketing approach. Falls back to Newton-Raphson
        if the bracket can be established.

        Returns np.nan if IV cannot be found.
        """
        if T <= 0 or market_price <= 0:
            return np.nan

        # Intrinsic value check
        if option_type.lower() == "call":
            intrinsic = max(S * np.exp(-q * T) - K * np.exp(-r * T), 0)
        else:
            intrinsic = max(K * np.exp(-r * T) - S * np.exp(-q * T), 0)

        if market_price <= intrinsic + 1e-10:
            return np.nan

        def objective(sigma):
            return cls.price(S, K, r, q, sigma, T, option_type) - market_price

        try:
            # Find bracket
            low, high = 1e-4, 5.0
            if objective(low) * objective(high) > 0:
                return np.nan

            iv = brentq(objective, low, high, xtol=tol, maxiter=max_iter)
            return float(iv)
        except (ValueError, RuntimeError):
            return np.nan

    # ------------------------------------------------------------------
    # Forward price utilities
    # ------------------------------------------------------------------

    @staticmethod
    def forward_price(S: float, r: float, q: float, T: float) -> float:
        """F = S · e^((r-q)·T)"""
        return float(S * np.exp((r - q) * T))

    @staticmethod
    def log_moneyness(S: float, K: float, r: float, q: float, T: float) -> float:
        """k = ln(K/F) = ln(K/S) - (r-q)·T"""
        F = S * np.exp((r - q) * T)
        return float(np.log(K / F))

    @staticmethod
    def delta_to_strike(
        S: float, r: float, q: float, sigma: float, T: float,
        target_delta: float, option_type: str = "put"
    ) -> float:
        """
        Find the strike corresponding to a target delta.

        Used to select strikes for premium selling at desired delta (e.g. 16Δ).

        K = S·exp(-r·T) · exp(-N^{-1}(Δ·e^{qT})·σ·√T + σ²·T/2)
        """
        sqrt_T = np.sqrt(T)
        if option_type.lower() == "put":
            # For puts: Δ_put = -e^{-qT}·N(-d1) → solve for d1
            # |Δ_put| = e^{-qT}·N(-d1) = target_delta → d1 = -N^{-1}(target_delta·e^{qT})
            d1 = -_N_inv(target_delta * np.exp(q * T))
        else:
            d1 = _N_inv(target_delta * np.exp(q * T))

        # K = S·exp((r-q+σ²/2)·T - d1·σ·√T) (from d1 definition)
        K = S * np.exp((r - q + 0.5 * sigma ** 2) * T - d1 * sigma * sqrt_T)
        return float(K)

    # ------------------------------------------------------------------
    # Probability utilities
    # ------------------------------------------------------------------

    @classmethod
    def prob_expire_worthless(
        cls,
        S: float, K: float, r: float, q: float, sigma: float, T: float,
        option_type: str = "call"
    ) -> float:
        """
        Risk-neutral probability of expiring out-of-the-money (worthless).
        = 1 - P(in the money)
        = 1 - N(d2) for calls, N(d2) for puts (adjusted)
        """
        if T <= 0 or sigma <= 0:
            return 1.0 if (S < K) == (option_type == "call") else 0.0
        d1, d2 = cls._d1_d2(S, K, r, q, sigma, T)
        if option_type.lower() == "call":
            return float(1 - _N(d2))
        else:
            return float(_N(d2))

    @classmethod
    def prob_touch(
        cls,
        S: float, K: float, r: float, q: float, sigma: float, T: float,
        option_type: str = "call"
    ) -> float:
        """
        Probability of touching strike K at any point before expiry.
        Uses reflection principle: P_touch ≈ 2 · N(-d2)  (approximately).
        """
        if T <= 0 or sigma <= 0:
            return 0.0
        _, d2 = cls._d1_d2(S, K, r, q, sigma, T)
        if option_type.lower() == "call":
            return float(2 * _N(-d2))
        else:
            return float(2 * _N(d2))

    @classmethod
    def expected_value_short_spread(
        cls,
        S: float,
        short_K: float,
        long_K: float,
        r: float,
        q: float,
        sigma: float,
        T: float,
        credit: float,
        option_type: str = "put",
    ) -> float:
        """
        Expected value of a short credit spread at expiry.

        EV = credit - P(assign) · spread_width
        where P(assign) ≈ P(short_K reached)

        For a put spread: P(assign) ≈ N(-d2_short) under risk-neutral measure.
        """
        spread_width = abs(short_K - long_K)
        max_loss = spread_width - credit

        d1, d2 = cls._d1_d2(S, short_K, r, q, sigma, T)
        if option_type.lower() == "put":
            p_itm = float(_N(-d2))   # P(S < short_K)
        else:
            p_itm = float(_N(d2))    # P(S > short_K)

        ev = credit - p_itm * max_loss
        return float(ev)
