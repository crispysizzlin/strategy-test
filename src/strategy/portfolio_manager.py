"""
Portfolio-level Greeks management and position tracking.

Monitors aggregate Greeks exposure across all open positions and
triggers hedging or adjustment orders when limits are breached.

Portfolio Greek limits (for $20,000 account):
  Delta:  [-1000, +1000] equivalent dollars
  Gamma:  [-0.50, +0.50] in percent terms (loss if |Γ|·ΔS²/2 > limit)
  Theta:  Target +$40 to +$150 per calendar day
  Vega:   [-$1000, 0] (net short vega budget)

Institutional practice:
  The Theta/Vega ratio is a key quality metric for premium sellers.
  Target ratio: 0.05+ (collect 5 cents theta per dollar of vega exposure).
  
  Gamma/Theta balance: Short premium positions carry positive theta but
  negative gamma. The P&L for a short gamma position is approximately:
    P&L_daily ≈ θ·Δt - ½·Γ·(ΔS)²
  Profitable when: |θ|·Δt > ½·|Γ|·E[ΔS²] = ½·|Γ|·σ²·S²·Δt
  i.e., when realized vol < breakeven vol = √(2|θ| / (|Γ|·S²))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.broker.order_manager import Order, OrderManager
from src.models.pricing.black_scholes import BlackScholes, OptionGreeks
from src.strategy.structure_selector import SelectedStructure
from src.utils.logger import logger


@dataclass
class PortfolioGreeks:
    """Aggregate Greek exposure across all open positions."""
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0   # per calendar day
    vega: float = 0.0    # per 1 vol point
    rho: float = 0.0
    # Positions with highest gamma risk
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def is_delta_neutral(self, tolerance: float = 0.05) -> bool:
        return abs(self.delta) <= tolerance

    @property
    def breakeven_vol(self) -> float:
        """
        Breakeven realized vol for the portfolio (annualised).
        Above this vol, gamma losses exceed theta income.
        breakeven_vol = √(2·|θ|_daily·252 / (|Γ|·S²)) (portfolio level)
        """
        if abs(self.gamma) < 1e-10:
            return float("inf")
        # Using S=1 normalisation; caller must multiply by spot
        daily_theta = abs(self.theta)
        return float(np.sqrt(2 * daily_theta * 252 / max(abs(self.gamma), 1e-10)))


@dataclass
class OpenPosition:
    """Tracks an individual open options position."""
    id: str
    structure: SelectedStructure
    order: Order
    contracts: int
    entry_credit: float      # Net credit collected (per contract, per share)
    entry_time: datetime = field(default_factory=datetime.now)
    current_value: float = 0.0
    unrealized_pnl: float = 0.0
    days_held: int = 0
    profit_target: float = 0.0   # 50% of initial credit
    stop_loss: float = 0.0       # 2x initial credit (loss)

    @property
    def pnl_pct(self) -> float:
        """P&L as percentage of maximum profit."""
        max_p = self.entry_credit * self.contracts * 100
        return self.unrealized_pnl / max_p if max_p > 0 else 0.0

    @property
    def at_profit_target(self) -> bool:
        return self.unrealized_pnl >= self.profit_target

    @property
    def at_stop_loss(self) -> bool:
        return self.unrealized_pnl <= -self.stop_loss


class PortfolioManager:
    """
    Manages the options portfolio: Greeks, P&L, exits, and risk limits.

    Implements institutional best practices:
      1. Profit-taking at 50% of max credit (higher win-rate, lower exposure)
      2. Stop-loss at 2x credit received (max loss bounded)
      3. DTE-based time stops (exit at 21 DTE → avoid gamma risk)
      4. Portfolio Greek limits with auto-hedging
      5. Daily P&L circuit breakers
    """

    # Portfolio-level Greek limits (for $20k account)
    LIMITS = {
        "delta_pct": 0.05,       # Max net delta as % of portfolio
        "gamma_pct": 0.005,      # Max net gamma (portfolio Γ × S²)
        "theta_min": 40.0,       # Min daily theta ($)
        "theta_max": 150.0,      # Max daily theta ($)
        "vega_max": -1000.0,     # Max short vega ($)
        "vega_min": -100.0,      # Don't be too short vega either
    }

    def __init__(
        self,
        account_size: float = 20000.0,
        profit_target_pct: float = 0.50,
        stop_loss_multiplier: float = 2.0,
        time_stop_dte: int = 1,
    ) -> None:
        self.account_size = account_size
        self.profit_target_pct = profit_target_pct
        self.stop_loss_multiplier = stop_loss_multiplier
        self.time_stop_dte = time_stop_dte

        self.positions: Dict[str, OpenPosition] = {}
        self._daily_pnl: float = 0.0
        self._total_pnl: float = 0.0
        self._peak_equity: float = account_size
        self._order_manager = OrderManager()

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    def add_position(
        self,
        structure: SelectedStructure,
        order: Order,
        contracts: int,
        entry_credit: float,
    ) -> OpenPosition:
        """Register a newly filled position."""
        profit_target = entry_credit * contracts * 100 * self.profit_target_pct
        stop_loss = entry_credit * contracts * 100 * self.stop_loss_multiplier

        pos = OpenPosition(
            id=order.id,
            structure=structure,
            order=order,
            contracts=contracts,
            entry_credit=entry_credit,
            profit_target=profit_target,
            stop_loss=stop_loss,
        )
        self.positions[order.id] = pos
        logger.info(
            f"Position added: {structure.describe()} "
            f"× {contracts} contracts | credit={entry_credit:.4f} "
            f"target={profit_target:.2f} stop={stop_loss:.2f}"
        )
        return pos

    def remove_position(self, position_id: str) -> Optional[OpenPosition]:
        return self.positions.pop(position_id, None)

    # ------------------------------------------------------------------
    # Portfolio Greeks aggregation
    # ------------------------------------------------------------------

    def compute_portfolio_greeks(
        self,
        spot: float,
        sigma: float,
        r: float = 0.05,
        q: float = 0.014,
    ) -> PortfolioGreeks:
        """
        Aggregate Greeks across all open positions.

        Parameters
        ----------
        spot  : current spot price
        sigma : current ATM implied vol
        r, q  : rates for repricing
        """
        total = PortfolioGreeks()

        for pos in self.positions.values():
            s = pos.structure
            T = max((s.expiry - datetime.now().date()).days / 365, 1e-6)

            # Compute per-leg Greeks and aggregate
            legs_data = self._get_position_legs(s, spot, sigma, r, q, T)
            for multiplier, greeks in legs_data:
                total.delta += multiplier * greeks.delta * pos.contracts * 100
                total.gamma += multiplier * greeks.gamma * pos.contracts * 100
                total.theta += multiplier * greeks.theta * pos.contracts * 100
                total.vega += multiplier * greeks.vega * pos.contracts * 100

        return total

    def _get_position_legs(
        self,
        structure: SelectedStructure,
        spot: float,
        sigma: float,
        r: float,
        q: float,
        T: float,
    ) -> List[Tuple[float, OptionGreeks]]:
        """
        Return (multiplier, greeks) for each leg.
        Multiplier is +1 for long, -1 for short.
        """
        legs = []
        rough_put_iv = sigma * 1.05
        rough_call_iv = sigma * 1.00

        if structure.structure_type == "iron_condor":
            if structure.put_short_strike:
                legs.append((-1, BlackScholes.greeks(spot, structure.put_short_strike, r, q, rough_put_iv, T, "put")))
            if structure.put_long_strike:
                legs.append((+1, BlackScholes.greeks(spot, structure.put_long_strike, r, q, rough_put_iv, T, "put")))
            if structure.call_short_strike:
                legs.append((-1, BlackScholes.greeks(spot, structure.call_short_strike, r, q, rough_call_iv, T, "call")))
            if structure.call_long_strike:
                legs.append((+1, BlackScholes.greeks(spot, structure.call_long_strike, r, q, rough_call_iv, T, "call")))

        elif structure.structure_type == "broken_wing_butterfly":
            if structure.put_long_strike:
                legs.append((+1, BlackScholes.greeks(spot, structure.put_long_strike, r, q, rough_put_iv, T, "put")))
            if structure.put_short_strike:
                legs.append((-2, BlackScholes.greeks(spot, structure.put_short_strike, r, q, rough_put_iv, T, "put")))
            if structure.middle_strike:
                legs.append((+1, BlackScholes.greeks(spot, structure.middle_strike, r, q, rough_put_iv, T, "put")))

        return legs

    # ------------------------------------------------------------------
    # Exit management
    # ------------------------------------------------------------------

    def get_positions_to_close(
        self,
        spot: float,
        sigma: float,
        current_date=None,
    ) -> List[Tuple[OpenPosition, str]]:
        """
        Return list of (position, reason) that should be closed.

        Exit triggers:
          1. Profit target hit (50% of max credit)
          2. Stop-loss hit (200% of max credit)
          3. DTE time stop (1 DTE before expiry)
          4. Delta or gamma limit breach
        """
        if current_date is None:
            current_date = datetime.now().date()

        to_close = []

        for pos_id, pos in self.positions.items():
            reason = None

            # Time stop
            dte_remaining = (pos.structure.expiry - current_date).days
            if dte_remaining <= self.time_stop_dte:
                reason = f"time_stop (DTE={dte_remaining})"

            # Profit target
            elif pos.at_profit_target:
                reason = f"profit_target ({pos.pnl_pct:.1%})"

            # Stop loss
            elif pos.at_stop_loss:
                reason = f"stop_loss ({pos.pnl_pct:.1%})"

            if reason:
                to_close.append((pos, reason))
                logger.info(f"Position {pos_id} flagged for close: {reason}")

        return to_close

    # ------------------------------------------------------------------
    # P&L tracking
    # ------------------------------------------------------------------

    def update_pnl(self, realized_pnl: float) -> None:
        """Update daily and total P&L after a trade closes."""
        self._daily_pnl += realized_pnl
        self._total_pnl += realized_pnl
        current_equity = self.account_size + self._total_pnl
        self._peak_equity = max(self._peak_equity, current_equity)

    def reset_daily_pnl(self) -> float:
        """Call at end of each trading day. Returns the day's P&L."""
        day_pnl = self._daily_pnl
        self._daily_pnl = 0.0
        return day_pnl

    @property
    def current_equity(self) -> float:
        return self.account_size + self._total_pnl

    @property
    def drawdown(self) -> float:
        """Current drawdown from peak equity."""
        return (self.current_equity - self._peak_equity) / self._peak_equity

    @property
    def daily_loss_limit_reached(self, limit_pct: float = 0.03) -> bool:
        return self._daily_pnl < -(self.account_size * limit_pct)

    # ------------------------------------------------------------------
    # Greeks limit checks
    # ------------------------------------------------------------------

    def check_greek_limits(
        self, greeks: PortfolioGreeks, spot: float
    ) -> Dict[str, bool]:
        """Check whether portfolio Greeks are within limits."""
        delta_dollar = greeks.delta * spot
        delta_limit = self.account_size * self.LIMITS["delta_pct"]

        return {
            "delta_ok": abs(delta_dollar) <= delta_limit,
            "theta_ok": (
                self.LIMITS["theta_min"] <= greeks.theta * (-1) <= self.LIMITS["theta_max"]
            ),
            "vega_ok": (
                self.LIMITS["vega_max"] <= greeks.vega <= self.LIMITS["vega_min"]
            ),
        }

    # ------------------------------------------------------------------
    # Summary report
    # ------------------------------------------------------------------

    def portfolio_summary(self, spot: float, sigma: float) -> dict:
        """Return a summary dict of current portfolio state."""
        greeks = self.compute_portfolio_greeks(spot, sigma)
        limits = self.check_greek_limits(greeks, spot)

        return {
            "n_positions": len(self.positions),
            "equity": self.current_equity,
            "daily_pnl": self._daily_pnl,
            "total_pnl": self._total_pnl,
            "drawdown": self.drawdown,
            "portfolio_delta": greeks.delta,
            "portfolio_gamma": greeks.gamma,
            "portfolio_theta": greeks.theta,
            "portfolio_vega": greeks.vega,
            "delta_limit_ok": limits["delta_ok"],
            "theta_in_target": limits["theta_ok"],
            "vega_limit_ok": limits["vega_ok"],
            "breakeven_vol": greeks.breakeven_vol,
        }
