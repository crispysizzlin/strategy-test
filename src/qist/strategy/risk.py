"""Portfolio risk management and position sizing orchestration.

Combines the sizing primitives (Kelly, CVaR) with hard guardrails specific to a
small Level-3 account:

* per-trade max-loss budget,
* portfolio CVaR(99%) budget,
* net delta / vega limits scaled to equity,
* PDT (pattern-day-trader) guard: while equity < $25k FINRA caps day trades at 3
  per rolling 5 business days, so the engine must avoid strategies that *require*
  same-day round trips, and block new same-day exits once the budget is spent,
* drawdown circuit breaker that halts new risk after a peak-to-trough threshold.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..config import Config
from ..sizing.cvar import max_contracts_by_cvar, var_cvar
from ..sizing.kelly import fractional_kelly, kelly_binary, kelly_from_samples
from .structures import Structure


@dataclass
class PortfolioGreeks:
    delta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0
    theta: float = 0.0


@dataclass
class RiskState:
    equity: float
    high_water: float
    day_trades_used: int = 0
    open_structures: int = 0
    greeks: PortfolioGreeks = field(default_factory=PortfolioGreeks)

    @property
    def drawdown(self) -> float:
        if self.high_water <= 0:
            return 0.0
        return max(0.0, 1.0 - self.equity / self.high_water)


class RiskManager:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    # ---- gates ----------------------------------------------------------
    def trading_halted(self, state: RiskState) -> bool:
        return state.drawdown >= self.cfg.risk.max_drawdown_halt_frac

    def can_day_trade(self, state: RiskState) -> bool:
        if state.equity >= self.cfg.account.pdt_threshold:
            return True
        return state.day_trades_used < self.cfg.account.max_day_trades_per_5d

    # ---- sizing ---------------------------------------------------------
    def size_structure(self, structure: Structure, state: RiskState,
                       win_prob: float,
                       loss_samples: np.ndarray | None = None) -> int:
        """Return the number of contracts to trade for ``structure``.

        Sizing is the *minimum* of three independent caps:
          1. fractional-Kelly fraction of equity / max-loss-per-contract,
          2. per-trade max-loss budget,
          3. portfolio CVaR(99%) budget.
        """
        if self.trading_halted(state):
            return 0
        max_loss = structure.max_loss
        if max_loss <= 0:
            return 0

        # (1) Kelly on the trade's own payoff geometry.
        if loss_samples is not None and len(loss_samples) > 0:
            returns = -loss_samples / max_loss   # in units of max-loss risk
            full_kelly = kelly_from_samples(returns)
        else:
            full_kelly = kelly_binary(win_prob, structure.payoff_ratio)
        f = fractional_kelly(full_kelly, self.cfg.risk.kelly_fraction,
                             self.cfg.risk.kelly_cap)
        kelly_dollars = f * state.equity
        n_kelly = int(kelly_dollars // max_loss)

        # (2) Per-trade absolute max-loss budget.
        budget = self.cfg.risk.max_loss_per_trade_frac * state.equity
        n_budget = int(budget // max_loss)

        # (3) Portfolio CVaR budget.
        if loss_samples is not None and len(loss_samples) > 0:
            n_cvar = max_contracts_by_cvar(
                loss_samples, state.equity,
                self.cfg.risk.portfolio_cvar_budget_frac)
        else:
            # Without a sample, treat max_loss as the tail loss (conservative).
            n_cvar = int((self.cfg.risk.portfolio_cvar_budget_frac
                          * state.equity) // max_loss)

        n = max(min(n_kelly, n_budget, n_cvar), 0)
        return n

    # ---- greek checks ---------------------------------------------------
    def within_greek_limits(self, state: RiskState, add: PortfolioGreeks) -> bool:
        eq_k = state.equity / 1000.0
        new_delta = abs(state.greeks.delta + add.delta)
        new_vega = abs(state.greeks.vega + add.vega)
        if new_delta > self.cfg.risk.max_net_delta_per_1k * eq_k:
            return False
        if new_vega > self.cfg.risk.max_net_vega_per_1k * eq_k:
            return False
        return True

    # ---- management thresholds -----------------------------------------
    def profit_target_price(self, entry_credit: float) -> float:
        """Buy-to-close debit at which to take profit (50% of credit by default)."""
        return entry_credit * (1.0 - self.cfg.risk.take_profit_frac)

    def stop_loss_price(self, entry_credit: float) -> float:
        """Buy-to-close debit at which to stop out (credit * stop multiplier)."""
        return entry_credit * (1.0 + self.cfg.risk.stop_loss_mult)


def estimate_loss_samples(structure: Structure, rng_samples: np.ndarray) -> np.ndarray:
    """Map simulated underlying returns to structure P&L losses (positive=loss).

    A light, generic approximation: defined-risk structures lose linearly toward
    ``max_loss`` as the move exceeds the breakeven, capped at ``max_loss``.  Used
    by the CVaR sizer when a richer simulator is not supplied.
    """
    losses = np.where(rng_samples < 0,
                      np.minimum(-rng_samples * structure.max_loss * 5.0,
                                 structure.max_loss),
                      -structure.max_profit)
    return losses
