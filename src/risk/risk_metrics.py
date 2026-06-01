"""
Portfolio risk metrics: VaR, CVaR, Greeks P&L attribution, scenario analysis.

CVaR (Conditional Value at Risk / Expected Shortfall):
  CVaR_α = E[L | L > VaR_α] = (1/(1-α)) · ∫_{VaR_α}^∞ l·f(l) dl

For options portfolios:
  Historical simulation: resample historical returns × portfolio Greeks
  Parametric: assume normal returns, VaR = μ + z_α·σ
  Full revaluation: re-price all legs under each scenario

Tail risk scenarios considered:
  1. +/- 2σ daily move (normal market)
  2. +/- 4σ daily move (fat-tail event)
  3. Vol spike: IV increases by 5 vol points overnight
  4. Vol crush: IV decreases by 5 vol points
  5. Flash crash: -5% intraday, then recovery
  6. Gap open: +/- 3% overnight gap
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.stats import norm

from src.models.pricing.black_scholes import BlackScholes
from src.utils.logger import logger


@dataclass
class RiskReport:
    """Portfolio risk report."""
    var_95: float          # 1-day 95% VaR ($)
    var_99: float          # 1-day 99% VaR ($)
    cvar_95: float         # 1-day 95% CVaR ($)
    cvar_99: float         # 1-day 99% CVaR ($)

    # Scenario P&L
    scenario_2sigma: float
    scenario_4sigma: float
    scenario_vol_spike: float
    scenario_vol_crush: float
    scenario_gap_up: float
    scenario_gap_down: float

    # Attribution
    delta_pnl_per_pct: float   # P&L from 1% spot move
    gamma_pnl_per_pct: float   # P&L from 1% spot move (gamma component)
    theta_daily: float          # Expected daily theta income
    vega_per_vol_point: float   # P&L from 1 vol-point move

    @property
    def worst_scenario(self) -> float:
        """Worst-case scenario P&L."""
        return min(
            self.scenario_2sigma, self.scenario_4sigma,
            self.scenario_vol_spike, self.scenario_gap_down,
        )

    @property
    def is_acceptable(self) -> bool:
        """Risk acceptable if CVaR95 < 3% of typical $20k account."""
        return self.cvar_95 > -600.0  # -$600 max expected shortfall


class RiskMetrics:
    """
    Computes risk metrics for an options portfolio.

    Supports historical simulation and parametric approaches.
    """

    def __init__(
        self,
        account_size: float = 20000.0,
        r: float = 0.05,
        q: float = 0.014,
    ) -> None:
        self.account_size = account_size
        self.r = r
        self.q = q

    # ------------------------------------------------------------------
    # Full revaluation P&L
    # ------------------------------------------------------------------

    def pnl_under_scenario(
        self,
        positions: list,   # list of SelectedStructure objects
        spot: float,
        sigma: float,
        spot_shock: float = 0.0,    # % change in spot (e.g. 0.02 = +2%)
        vol_shock: float = 0.0,     # absolute vol change (e.g. 0.05 = +5pp)
        dt: float = 1/252,          # time elapsed (1 trading day)
    ) -> float:
        """
        Compute portfolio P&L under a given spot and vol scenario.

        Parameters
        ----------
        positions   : list of SelectedStructure objects
        spot        : current spot price
        sigma       : current ATM vol
        spot_shock  : fractional spot move (e.g. -0.03 = -3%)
        vol_shock   : absolute vol change (e.g. 0.05 = +5%)
        dt          : time elapsed in years

        Returns
        -------
        Portfolio P&L in dollars
        """
        new_spot = spot * (1 + spot_shock)
        new_sigma = max(sigma + vol_shock, 0.01)
        total_pnl = 0.0

        for struct in positions:
            T_orig = struct.dte / 365.0
            T_new = max(T_orig - dt, 1e-6)

            # Price current and new values for each leg
            pnl = self._structure_pnl(struct, spot, sigma, T_orig,
                                       new_spot, new_sigma, T_new)
            total_pnl += pnl

        return total_pnl

    def _structure_pnl(
        self,
        struct,
        spot_old: float, sigma_old: float, T_old: float,
        spot_new: float, sigma_new: float, T_new: float,
    ) -> float:
        """P&L for a single structure between old and new states."""
        multiplier = 100  # options multiplier
        pnl = 0.0

        def price_leg(K, ot, spot, sigma, T):
            return BlackScholes.price(spot, K, self.r, self.q, sigma, T, ot)

        if struct.structure_type == "iron_condor":
            legs = [
                (-1, struct.put_short_strike, "put"),
                (+1, struct.put_long_strike, "put"),
                (-1, struct.call_short_strike, "call"),
                (+1, struct.call_long_strike, "call"),
            ]
        elif struct.structure_type == "broken_wing_butterfly":
            legs = [
                (+1, struct.put_long_strike, "put"),
                (-2, struct.put_short_strike, "put"),
                (+1, struct.middle_strike, "put"),
            ]
        else:
            return 0.0

        for sign, K, ot in legs:
            if K is None:
                continue
            old_price = price_leg(K, ot, spot_old, sigma_old, T_old)
            new_price = price_leg(K, ot, spot_new, sigma_new, T_new)
            pnl += sign * (old_price - new_price) * multiplier

        return pnl * (struct.contracts if hasattr(struct, "contracts") else 1)

    # ------------------------------------------------------------------
    # Scenario analysis
    # ------------------------------------------------------------------

    def run_scenarios(
        self,
        positions: list,
        spot: float,
        sigma: float,
        daily_sigma: Optional[float] = None,
    ) -> RiskReport:
        """
        Run standard scenario analysis.

        Parameters
        ----------
        positions    : list of SelectedStructure objects
        spot         : current spot price
        sigma        : ATM implied vol
        daily_sigma  : actual daily vol for stress scenarios (if None: use sigma/√252)
        """
        if daily_sigma is None:
            daily_sigma = sigma / np.sqrt(252)

        def scenario(spot_shock=0.0, vol_shock=0.0):
            return self.pnl_under_scenario(positions, spot, sigma, spot_shock, vol_shock)

        s2 = min(
            scenario(-2 * daily_sigma),
            scenario(+2 * daily_sigma),
        )
        s4 = min(
            scenario(-4 * daily_sigma),
            scenario(+4 * daily_sigma),
        )
        vol_spike = scenario(0, +0.05)       # +5 vol points
        vol_crush = scenario(0, -0.05)       # -5 vol points
        gap_up = scenario(+0.03)             # +3% gap
        gap_down = scenario(-0.03)           # -3% gap

        # Greeks-based attribution
        from src.strategy.portfolio_manager import PortfolioManager, PortfolioGreeks
        greeks = PortfolioGreeks()  # rough aggregate from positions
        for s in positions:
            greeks.delta += getattr(s, "delta", 0) * 100
            greeks.gamma += getattr(s, "gamma", 0) * 100
            greeks.theta += getattr(s, "theta", 0) * 100
            greeks.vega += getattr(s, "vega", 0) * 100

        delta_pnl = greeks.delta * spot * 0.01        # $-P&L per 1% spot move
        gamma_pnl = 0.5 * greeks.gamma * (spot * 0.01) ** 2
        theta_daily = greeks.theta                    # per calendar day
        vega_pnl = greeks.vega                       # per 1 vol point

        # Historical simulation for VaR/CVaR
        # Simulate 10,000 scenarios under normal distribution
        n_sim = 10000
        np.random.seed(42)
        daily_returns = np.random.normal(0, daily_sigma, n_sim)
        vol_shocks = np.random.normal(0, 0.01, n_sim)  # daily vol changes

        pnl_distribution = []
        for dr, dv in zip(daily_returns[:500], vol_shocks[:500]):  # subset for speed
            pnl_sim = self.pnl_under_scenario(positions, spot, sigma, dr, dv)
            pnl_distribution.append(pnl_sim)

        if pnl_distribution:
            pnl_arr = np.array(pnl_distribution)
            var_95 = float(np.percentile(pnl_arr, 5))
            var_99 = float(np.percentile(pnl_arr, 1))
            cvar_95 = float(pnl_arr[pnl_arr <= var_95].mean()) if (pnl_arr <= var_95).any() else var_95
            cvar_99 = float(pnl_arr[pnl_arr <= var_99].mean()) if (pnl_arr <= var_99).any() else var_99
        else:
            # Fallback: parametric VaR
            var_95 = delta_pnl * norm.ppf(0.05) - 0.5 * abs(gamma_pnl)
            var_99 = delta_pnl * norm.ppf(0.01) - 0.5 * abs(gamma_pnl)
            cvar_95 = var_95 * 1.2
            cvar_99 = var_99 * 1.5

        return RiskReport(
            var_95=var_95,
            var_99=var_99,
            cvar_95=cvar_95,
            cvar_99=cvar_99,
            scenario_2sigma=s2,
            scenario_4sigma=s4,
            scenario_vol_spike=vol_spike,
            scenario_vol_crush=vol_crush,
            scenario_gap_up=gap_up,
            scenario_gap_down=gap_down,
            delta_pnl_per_pct=delta_pnl,
            gamma_pnl_per_pct=gamma_pnl,
            theta_daily=theta_daily,
            vega_per_vol_point=vega_pnl,
        )

    # ------------------------------------------------------------------
    # Margin estimation
    # ------------------------------------------------------------------

    @staticmethod
    def required_margin(spread_width: float, contracts: int, multiplier: int = 100) -> float:
        """
        Margin requirement for a defined-risk spread.
        CBOE rules: max_loss = (spread_width - credit) × 100 × contracts
        Schwab typically holds this as margin for spreads.
        """
        return spread_width * contracts * multiplier
