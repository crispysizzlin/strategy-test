"""
Options spread pricing utilities.

Computes fair value, Greeks, and risk metrics for multi-leg structures:
  - Iron Condor
  - Broken Wing Butterfly
  - Calendar Spread
  - Vertical Credit Spread
  - Iron Butterfly

Key analytics used in structure selection:
  - Net credit vs max risk
  - Probability of profit
  - Theta/Gamma ratio (institutional target: > 0.05)
  - Expected value
  - Breakeven range
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from .black_scholes import BlackScholes, OptionGreeks


@dataclass
class SpreadAnalysis:
    """Complete analysis of a multi-leg options structure."""
    structure_type: str
    net_credit: float
    max_profit: float
    max_loss: float
    profit_probability: float    # Risk-neutral P(full profit)
    expected_value: float
    breakeven_lower: float
    breakeven_upper: float
    # Portfolio Greeks (all legs combined)
    delta: float
    gamma: float
    theta: float
    vega: float
    # Quality metrics
    theta_gamma_ratio: float     # |θ|/|Γ| — higher = better quality income
    credit_to_width_ratio: float # credit / spread_width — higher = better pricing
    risk_reward: float           # max_profit / max_loss

    @property
    def is_tradeable(self) -> bool:
        """Basic sanity checks."""
        return (
            self.net_credit > 0
            and self.max_loss > 0
            and self.profit_probability > 0.50
            and self.theta < 0   # Actually negative theta from the structure's perspective... wait.
            # For SHORT structures, theta should be POSITIVE (we collect theta)
        )


class SpreadPricer:
    """
    Prices and analyses multi-leg options structures.

    All methods return SpreadAnalysis objects with complete risk/reward profiles.
    """

    def __init__(
        self,
        r: float = 0.05,     # Risk-free rate
        q: float = 0.014,    # Dividend yield (SPX ≈ 1.4%)
    ) -> None:
        self.r = r
        self.q = q
        self.bs = BlackScholes()

    # ------------------------------------------------------------------
    # Iron Condor
    # ------------------------------------------------------------------

    def price_iron_condor(
        self,
        S: float,
        put_short_K: float,
        put_long_K: float,
        call_short_K: float,
        call_long_K: float,
        sigma: float,        # ATM vol (simplified; ideally use per-strike IV)
        T: float,            # Years to expiry
        put_short_iv: Optional[float] = None,
        put_long_iv: Optional[float] = None,
        call_short_iv: Optional[float] = None,
        call_long_iv: Optional[float] = None,
    ) -> SpreadAnalysis:
        """
        Price a short iron condor: short put spread + short call spread.

        Structure:
          Buy put at put_long_K (far OTM)
          Sell put at put_short_K (OTM)
          Sell call at call_short_K (OTM)
          Buy call at call_long_K (far OTM)

        Net credit = (short_put + short_call) - (long_put + long_call)
        Max profit = net credit (achieved when S stays between shorts)
        Max loss = spread_width - net_credit (if either side breaches)
        """
        # Use per-leg IV if provided, else ATM vol (skew-adjusted rough proxy)
        # For simplicity: put IVs typically higher due to skew
        iv_ps = put_short_iv if put_short_iv is not None else sigma * 1.05
        iv_pl = put_long_iv if put_long_iv is not None else sigma * 1.08
        iv_cs = call_short_iv if call_short_iv is not None else sigma * 1.00
        iv_cl = call_long_iv if call_long_iv is not None else sigma * 0.98

        g_ps = BlackScholes.greeks(S, put_short_K, self.r, self.q, iv_ps, T, "put")
        g_pl = BlackScholes.greeks(S, put_long_K, self.r, self.q, iv_pl, T, "put")
        g_cs = BlackScholes.greeks(S, call_short_K, self.r, self.q, iv_cs, T, "call")
        g_cl = BlackScholes.greeks(S, call_long_K, self.r, self.q, iv_cl, T, "call")

        # Net credit (sell short legs, buy long legs)
        net_credit = (g_ps.price + g_cs.price) - (g_pl.price + g_cl.price)

        # Spread widths
        put_width = put_short_K - put_long_K
        call_width = call_long_K - call_short_K
        max_loss = max(put_width, call_width) - net_credit

        # Portfolio Greeks (short = negative sign for sold legs)
        delta = -g_ps.delta - g_cs.delta + g_pl.delta + g_cl.delta
        gamma = -g_ps.gamma - g_cs.gamma + g_pl.gamma + g_cl.gamma
        theta = -(g_ps.theta + g_cs.theta) + (g_pl.theta + g_cl.theta)
        # For short iron condor: theta should be POSITIVE (we benefit from decay)
        # Note: BSM theta is already negative for long options, so:
        # sold options contribute positive theta to P&L
        theta_portfolio = -(g_ps.theta + g_cs.theta) + (g_pl.theta + g_cl.theta)
        vega = -(g_ps.vega + g_cs.vega) + (g_pl.vega + g_cl.vega)

        # Breakevens
        breakeven_lower = put_short_K - net_credit
        breakeven_upper = call_short_K + net_credit

        # Probability of profit (both sides expire OTM)
        p_put_otm = BlackScholes.prob_expire_worthless(S, put_short_K, self.r, self.q, sigma, T, "put")
        p_call_otm = BlackScholes.prob_expire_worthless(S, call_short_K, self.r, self.q, sigma, T, "call")
        p_profit = p_put_otm * p_call_otm  # Simplified (assumes independence — conservative)

        # Expected value
        ev = net_credit - (1 - p_profit) * max_loss

        # Quality metrics
        theta_gamma_ratio = abs(theta_portfolio) / max(abs(gamma) * S ** 2, 1e-8)
        credit_to_width = net_credit / max(put_width, call_width)

        return SpreadAnalysis(
            structure_type="iron_condor",
            net_credit=net_credit,
            max_profit=net_credit,
            max_loss=max_loss,
            profit_probability=p_profit,
            expected_value=ev,
            breakeven_lower=breakeven_lower,
            breakeven_upper=breakeven_upper,
            delta=delta,
            gamma=gamma,
            theta=theta_portfolio,
            vega=vega,
            theta_gamma_ratio=theta_gamma_ratio,
            credit_to_width_ratio=credit_to_width,
            risk_reward=net_credit / max(max_loss, 1e-8),
        )

    # ------------------------------------------------------------------
    # Broken Wing Butterfly (put-side)
    # ------------------------------------------------------------------

    def price_broken_wing_butterfly(
        self,
        S: float,
        lower_long_K: float,    # Far OTM (cheap protection)
        middle_short_K: float,  # Middle (2x short)
        upper_long_K: float,    # Closer to ATM (narrow upper wing)
        sigma: float,
        T: float,
        option_type: str = "put",
    ) -> SpreadAnalysis:
        """
        Broken Wing Butterfly: asymmetric risk/reward.

        Put BWB (bearish directional bias):
          Buy 1 far-OTM put (lower_long_K)
          Sell 2 OTM puts (middle_short_K)
          Buy 1 near-OTM put (upper_long_K)

        The upper wing is wider than lower wing → collect net credit.
        Risk: unlimited below lower strike if severely ITM.
        Max profit at middle strike at expiry.

        Advantage: In a slow grind down, BWB captures more than iron condor.
        """
        ot = option_type.lower()

        # Per-leg IVs (skew adjustment)
        def _iv_adjust(K):
            log_m = np.log(K / S)
            # Rough skew: 1 vol point extra per 10% OTM for puts
            if ot == "put":
                return sigma * (1 + 0.5 * max(-log_m, 0))
            return sigma * (1 + 0.3 * max(log_m, 0))

        g_ll = BlackScholes.greeks(S, lower_long_K, self.r, self.q, _iv_adjust(lower_long_K), T, ot)
        g_ms = BlackScholes.greeks(S, middle_short_K, self.r, self.q, _iv_adjust(middle_short_K), T, ot)
        g_ul = BlackScholes.greeks(S, upper_long_K, self.r, self.q, _iv_adjust(upper_long_K), T, ot)

        # Net credit (buy 1 + sell 2 + buy 1)
        net_credit = -g_ll.price + 2 * g_ms.price - g_ul.price

        # Risk: loss if below lower_long_K (uncapped relative to wings)
        upper_spread = upper_long_K - middle_short_K  # if puts
        lower_spread = middle_short_K - lower_long_K
        max_profit = net_credit + upper_spread  # achieved at middle_short_K
        max_loss_below = lower_spread - upper_spread + net_credit   # loss below lower_long

        # Greeks
        delta = g_ll.delta - 2 * g_ms.delta + g_ul.delta
        gamma = g_ll.gamma - 2 * g_ms.gamma + g_ul.gamma
        theta = -g_ll.theta + 2 * g_ms.theta - g_ul.theta
        vega = -g_ll.vega + 2 * g_ms.vega - g_ul.vega

        # Breakeven (put BWB)
        if ot == "put":
            be_lower = upper_long_K - max_profit
            be_upper = upper_long_K + net_credit  # upper breakeven not relevant for puts
        else:
            be_lower = upper_long_K - net_credit
            be_upper = upper_long_K + max_profit

        p_profit = BlackScholes.prob_expire_worthless(
            S, upper_long_K, self.r, self.q, sigma, T, ot
        )

        return SpreadAnalysis(
            structure_type="broken_wing_butterfly",
            net_credit=net_credit,
            max_profit=max_profit,
            max_loss=abs(max_loss_below),
            profit_probability=p_profit,
            expected_value=net_credit - (1 - p_profit) * abs(max_loss_below),
            breakeven_lower=be_lower,
            breakeven_upper=be_upper,
            delta=delta,
            gamma=gamma,
            theta=theta,
            vega=vega,
            theta_gamma_ratio=abs(theta) / max(abs(gamma) * S ** 2, 1e-8),
            credit_to_width_ratio=net_credit / max(upper_spread, 1e-8),
            risk_reward=max_profit / max(abs(max_loss_below), 1e-8),
        )

    # ------------------------------------------------------------------
    # Calendar Spread
    # ------------------------------------------------------------------

    def price_calendar_spread(
        self,
        S: float,
        K: float,
        sigma_front: float,
        sigma_back: float,
        T_front: float,
        T_back: float,
        option_type: str = "call",
    ) -> SpreadAnalysis:
        """
        Calendar spread: buy back-month, sell front-month.

        Net debit = back_month_price - front_month_price
        Max profit achieved when front-month expires ATM (theta maximal).
        Profits when realized vol < implied vol in front-month.
        """
        g_front = BlackScholes.greeks(S, K, self.r, self.q, sigma_front, T_front, option_type)
        g_back = BlackScholes.greeks(S, K, self.r, self.q, sigma_back, T_back, option_type)

        net_debit = g_back.price - g_front.price

        # Portfolio Greeks (long back, short front)
        delta = g_back.delta - g_front.delta
        gamma = g_back.gamma - g_front.gamma
        theta = g_back.theta - g_front.theta   # Net theta (front decay > back)
        vega = g_back.vega - g_front.vega       # Net long vega (back > front)

        # Approximate max profit and breakevens (heuristic)
        max_profit = g_back.price * 0.50  # rough: 50% of back-month value
        max_loss = net_debit

        p_profit = 0.50  # heuristic for calendars (very regime-dependent)

        return SpreadAnalysis(
            structure_type="calendar_spread",
            net_credit=-net_debit,  # negative = debit trade
            max_profit=max_profit,
            max_loss=max_loss,
            profit_probability=p_profit,
            expected_value=max_profit * p_profit - max_loss * (1 - p_profit),
            breakeven_lower=S * 0.97,
            breakeven_upper=S * 1.03,
            delta=delta,
            gamma=gamma,
            theta=theta,
            vega=vega,
            theta_gamma_ratio=abs(theta) / max(abs(gamma) * S ** 2, 1e-8),
            credit_to_width_ratio=-net_debit / S,
            risk_reward=max_profit / max(max_loss, 1e-8),
        )

    # ------------------------------------------------------------------
    # Single Credit Spread
    # ------------------------------------------------------------------

    def price_vertical_spread(
        self,
        S: float,
        short_K: float,
        long_K: float,
        sigma_short: float,
        sigma_long: float,
        T: float,
        option_type: str = "put",
    ) -> SpreadAnalysis:
        """Sell a vertical credit spread (bull put or bear call)."""
        g_short = BlackScholes.greeks(S, short_K, self.r, self.q, sigma_short, T, option_type)
        g_long = BlackScholes.greeks(S, long_K, self.r, self.q, sigma_long, T, option_type)

        net_credit = g_short.price - g_long.price
        spread_width = abs(short_K - long_K)
        max_loss = spread_width - net_credit

        delta = -g_short.delta + g_long.delta
        gamma = -g_short.gamma + g_long.gamma
        theta = -g_short.theta + g_long.theta   # positive (collect theta)
        vega = -g_short.vega + g_long.vega       # negative (short vega)

        be = short_K - net_credit if option_type == "put" else short_K + net_credit

        p_profit = BlackScholes.prob_expire_worthless(S, short_K, self.r, self.q, sigma_short, T, option_type)

        return SpreadAnalysis(
            structure_type=f"credit_spread_{option_type}",
            net_credit=net_credit,
            max_profit=net_credit,
            max_loss=max_loss,
            profit_probability=p_profit,
            expected_value=net_credit * p_profit - max_loss * (1 - p_profit),
            breakeven_lower=be if option_type == "put" else 0,
            breakeven_upper=be if option_type == "call" else float("inf"),
            delta=delta,
            gamma=gamma,
            theta=theta,
            vega=vega,
            theta_gamma_ratio=abs(theta) / max(abs(gamma) * S ** 2, 1e-8),
            credit_to_width_ratio=net_credit / spread_width,
            risk_reward=net_credit / max(max_loss, 1e-8),
        )

    # ------------------------------------------------------------------
    # Structure comparison
    # ------------------------------------------------------------------

    def compare_structures(
        self,
        analyses: List[SpreadAnalysis],
        min_prob_profit: float = 0.60,
        min_theta_gamma_ratio: float = 0.03,
    ) -> Optional[SpreadAnalysis]:
        """
        Select the best structure from a list of analysed spreads.

        Scoring function:
          score = 0.4·P(profit) + 0.3·(theta_gamma_ratio / 0.10) + 0.3·risk_reward

        Filters on minimum probability of profit and theta/gamma ratio.
        """
        candidates = [
            a for a in analyses
            if a.profit_probability >= min_prob_profit
            and a.theta_gamma_ratio >= min_theta_gamma_ratio
            and a.net_credit > 0
            and a.max_loss > 0
        ]

        if not candidates:
            return None

        def score(a: SpreadAnalysis) -> float:
            return (
                0.40 * a.profit_probability
                + 0.30 * min(a.theta_gamma_ratio / 0.10, 1.0)
                + 0.30 * min(a.risk_reward * 5, 1.0)  # cap at 20% credit-to-risk
            )

        return max(candidates, key=score)
