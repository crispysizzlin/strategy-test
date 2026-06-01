"""
Position sizing using regime-conditioned fractional Kelly criterion
with CVaR constraint.

Mathematical Foundation
-----------------------
Kelly Criterion (continuous):
  f* = (μ - r_f) / σ²
  For binary bets: f* = (p·b - q) / b
  where b = win/loss ratio, p = win probability, q = 1-p

Institutional modifications:
  1. Fractional Kelly: f = κ·f* where κ ∈ [0,1] (reduces variance)
  2. Regime multiplier: f_regime = κ·f* × m_k (regime k)
  3. CVaR constraint: f ≤ CVaR_budget / CVaR_per_unit
  4. Drawdown control: f → 0 as equity approaches max drawdown limit
  5. Volatility scaling: f ∝ 1/σ (less size in high vol)

For options credit spreads:
  p = P(profit) ≈ N(-d2) or empirical win rate
  b = credit / max_loss (credit-to-risk ratio)
  loss = max_loss of the spread structure

Kelly for spreads:
  f* = (p·b - (1-p)) / b = p - (1-p)/b

The Kelly allocation gives the FRACTION OF BANKROLL to risk.
We then translate this to a number of contracts:
  contracts = floor(f × account / (max_loss_per_contract))

CVaR Constraint (Expected Shortfall at 95%):
  CVaR_95 = E[L | L > VaR_95] ≤ CVaR_limit (3% of account)
  CVaR per contract ≈ max_loss × P(max_loss scenario)
  Max contracts = CVaR_limit_dollars / CVaR_per_contract

The binding constraint (Kelly vs CVaR) determines final position size.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from src.models.regime.hmm_detector import RegimeState, RegimeLabel
from src.strategy.vrp_engine import VRPSignal
from src.utils.logger import logger


@dataclass
class SizingResult:
    """Output of the position sizing calculation."""
    contracts: int
    max_risk_dollars: float
    kelly_fraction: float
    kelly_contracts: float
    cvar_contracts: float
    binding_constraint: str     # "kelly" | "cvar" | "max_risk" | "regime"
    rationale: str

    @property
    def is_nonzero(self) -> bool:
        return self.contracts > 0


class PositionSizer:
    """
    Determines the optimal number of contracts for a given trade.

    Uses regime-conditioned fractional Kelly with CVaR safety constraint.
    """

    def __init__(
        self,
        account_size: float = 20000.0,
        kelly_fraction: float = 0.25,       # Full Kelly × 0.25 (very conservative)
        max_risk_pct: float = 0.02,          # Max 2% of account per trade
        cvar_limit_pct: float = 0.03,        # CVaR95 budget: 3% of account
        max_vega_per_trade: float = -200.0,  # Max vega per trade
        max_positions: int = 6,
        win_rate_lookback: int = 60,
    ) -> None:
        self.account_size = account_size
        self.kelly_fraction = kelly_fraction
        self.max_risk_pct = max_risk_pct
        self.cvar_limit_pct = cvar_limit_pct
        self.max_vega_per_trade = max_vega_per_trade
        self.max_positions = max_positions
        self.win_rate_lookback = win_rate_lookback

        # Rolling win/loss history for empirical Kelly
        self._outcomes: list = []  # +1 for win, -1 for loss
        self._win_amounts: list = []
        self._loss_amounts: list = []

    # ------------------------------------------------------------------
    # Main sizing method
    # ------------------------------------------------------------------

    def size_position(
        self,
        prob_profit: float,
        max_loss_per_contract: float,   # Maximum loss per contract (in $)
        credit_per_contract: float,     # Net credit per contract (in $)
        signal: VRPSignal,
        regime: RegimeState,
        current_equity: float,
        open_positions: int = 0,
        contracts_per_side: int = 100,  # Multiplier (SPX = 100)
    ) -> SizingResult:
        """
        Compute optimal number of contracts to trade.

        Parameters
        ----------
        prob_profit            : P&L probability of the spread structure
        max_loss_per_contract  : maximum loss in $ per contract (spread width - credit)
        credit_per_contract    : net credit collected in $ per contract
        signal                 : current VRP signal
        regime                 : current market regime
        current_equity         : current portfolio value
        open_positions         : number of currently open positions
        contracts_per_side     : 100 for equity options

        Returns
        -------
        SizingResult with final contract count and rationale
        """
        # Safety checks
        if not regime.allow_new_positions:
            return SizingResult(0, 0, 0, 0, 0, "regime", "Crisis regime: no new positions")

        if open_positions >= self.max_positions:
            return SizingResult(
                0, 0, 0, 0, 0, "max_positions",
                f"Max positions reached ({open_positions}/{self.max_positions})"
            )

        if max_loss_per_contract <= 0 or credit_per_contract <= 0:
            return SizingResult(0, 0, 0, 0, 0, "invalid", "Invalid trade parameters")

        # 1. Kelly Criterion
        b = credit_per_contract / max_loss_per_contract   # credit-to-risk ratio
        p = prob_profit
        q = 1 - p

        raw_kelly = max(0, (p * b - q) / b)  # Kelly fraction of bankroll to RISK

        # Apply fractional Kelly
        fractional_kelly = raw_kelly * self.kelly_fraction

        # Apply regime multiplier
        regime_kelly = fractional_kelly * regime.kelly_multiplier

        # Apply signal strength scaling (stronger signal → closer to Kelly)
        signal_scale = 0.30 + 0.70 * signal.signal_strength  # [0.30, 1.00]
        final_kelly = regime_kelly * signal_scale

        # Kelly allocation in dollars
        kelly_dollars = final_kelly * current_equity
        kelly_contracts = kelly_dollars / max_loss_per_contract
        kelly_contracts = max(0, kelly_contracts)

        # 2. CVaR constraint
        # CVaR per contract ≈ max_loss × P(max_loss event)
        # P(max_loss) ≈ P(breach short strike) ≈ (1 - prob_profit)
        p_max_loss = 1 - prob_profit
        cvar_per_contract = max_loss_per_contract * p_max_loss
        cvar_budget = current_equity * self.cvar_limit_pct
        cvar_contracts = cvar_budget / max(cvar_per_contract, 1)

        # 3. Max risk per trade constraint
        max_risk_dollars = current_equity * self.max_risk_pct
        max_risk_contracts = max_risk_dollars / max_loss_per_contract

        # 4. Drawdown-based scaling
        # As equity falls, reduce size proportionally
        equity_ratio = current_equity / self.account_size
        if equity_ratio < 0.92:     # Down more than 8%
            drawdown_scale = max(0.25, equity_ratio)
        elif equity_ratio < 0.97:
            drawdown_scale = 0.75
        else:
            drawdown_scale = 1.0

        # 5. Take the minimum (most conservative)
        raw_contracts = min(
            kelly_contracts,
            cvar_contracts,
            max_risk_contracts,
        ) * drawdown_scale

        final_contracts = max(1, int(raw_contracts))

        # Determine binding constraint
        if final_contracts >= kelly_contracts - 0.5:
            binding = "cvar" if cvar_contracts < kelly_contracts else "max_risk"
        else:
            binding = "kelly"

        if drawdown_scale < 1.0:
            binding = "drawdown_scaling"

        max_risk_dollars = final_contracts * max_loss_per_contract

        rationale = (
            f"Kelly={kelly_contracts:.2f} CVaR={cvar_contracts:.2f} "
            f"MaxRisk={max_risk_contracts:.2f} "
            f"RegimeMult={regime.kelly_multiplier:.2f} "
            f"SignalScale={signal_scale:.2f} "
            f"DrawdownScale={drawdown_scale:.2f} "
            f"→ {final_contracts} contracts (${max_risk_dollars:.2f} at risk)"
        )

        logger.info(f"Position sizing: {rationale}")

        return SizingResult(
            contracts=final_contracts,
            max_risk_dollars=max_risk_dollars,
            kelly_fraction=final_kelly,
            kelly_contracts=kelly_contracts,
            cvar_contracts=cvar_contracts,
            binding_constraint=binding,
            rationale=rationale,
        )

    # ------------------------------------------------------------------
    # Win/loss tracking for empirical Kelly
    # ------------------------------------------------------------------

    def record_outcome(self, pnl: float, max_profit: float, max_loss: float) -> None:
        """
        Record a trade outcome for empirical win-rate estimation.

        Over time, this replaces theoretical probabilities with actual
        historical performance, improving Kelly accuracy.
        """
        win = pnl > 0
        self._outcomes.append(1 if win else 0)
        if win:
            self._win_amounts.append(pnl / max_profit if max_profit > 0 else 0)
        else:
            self._loss_amounts.append(abs(pnl) / max_loss if max_loss > 0 else 0)

        # Keep only recent history
        if len(self._outcomes) > self.win_rate_lookback:
            self._outcomes = self._outcomes[-self.win_rate_lookback:]
            self._win_amounts = self._win_amounts[-self.win_rate_lookback // 2:]
            self._loss_amounts = self._loss_amounts[-self.win_rate_lookback // 2:]

    @property
    def empirical_win_rate(self) -> Optional[float]:
        """Historical win rate (None if insufficient data)."""
        if len(self._outcomes) < 10:
            return None
        return float(np.mean(self._outcomes))

    @property
    def empirical_kelly(self) -> Optional[float]:
        """
        Empirical Kelly fraction based on actual win/loss data.
        Uses actual average win and loss amounts (not theoretical).
        """
        if len(self._outcomes) < 10:
            return None
        p = self.empirical_win_rate
        q = 1 - p
        if not self._win_amounts or not self._loss_amounts:
            return None
        avg_win = float(np.mean(self._win_amounts))
        avg_loss = float(np.mean(self._loss_amounts))
        if avg_loss == 0:
            return None
        b = avg_win / avg_loss
        kelly = max(0, (p * b - q) / b)
        return float(kelly)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def max_contracts_for_margin(
        self,
        max_loss_per_contract: float,
        buying_power: float,
        margin_factor: float = 1.0,  # Spreads require: spread_width × 100
    ) -> int:
        """Maximum contracts based on available margin/buying power."""
        required_per_contract = max_loss_per_contract * margin_factor
        return max(1, int(buying_power / required_per_contract))
