"""
Fractional Kelly position sizing for defined-risk options structures.

Reference: Kelly (1956); practitioners use 1/4 - 1/2 Kelly for estimation robustness.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PositionSize:
    contracts: int
    kelly_fraction_raw: float
    kelly_fraction_used: float
    max_loss_dollars: float
    rationale: str


class FractionalKellySizer:
    """
    f* = (p * (b + 1) - 1) / b  where b = max_profit / max_loss (credit trades).

    For credit spreads: b = credit / (width - credit).
    """

    def __init__(
        self,
        kelly_fraction: float = 0.25,
        max_allocation_pct: float = 0.12,
        default_win_prob: float = 0.68,
    ) -> None:
        self.kelly_fraction = kelly_fraction
        self.max_allocation_pct = max_allocation_pct
        self.default_win_prob = default_win_prob

    def kelly_optimal(self, win_prob: float, win_loss_ratio: float) -> float:
        """Raw Kelly fraction of capital."""
        if win_loss_ratio <= 0:
            return 0.0
        b = win_loss_ratio
        p = win_prob
        f_star = (p * (b + 1) - 1) / b
        return max(0.0, f_star)

    def size_iron_condor(
        self,
        account_equity: float,
        max_loss_per_contract: float,
        credit_per_contract: float,
        win_prob: float | None = None,
        regime_multiplier: float = 1.0,
        max_loss_pct_per_trade: float = 0.05,
    ) -> PositionSize:
        p = win_prob if win_prob is not None else self.default_win_prob
        if max_loss_per_contract <= 0:
            return PositionSize(0, 0.0, 0.0, 0.0, "invalid max loss")

        b = credit_per_contract / max_loss_per_contract
        f_star = self.kelly_optimal(p, b)
        f_used = f_star * self.kelly_fraction * regime_multiplier

        # Cap by per-trade max loss
        max_loss_budget = account_equity * max_loss_pct_per_trade
        kelly_capital = account_equity * min(f_used, self.max_allocation_pct)
        contracts_by_kelly = int(kelly_capital / max_loss_per_contract)
        contracts_by_risk = int(max_loss_budget / max_loss_per_contract)
        contracts = max(0, min(contracts_by_kelly, contracts_by_risk))

        rationale = (
            f"f*={f_star:.3f}, used={f_used:.3f}, "
            f"contracts={contracts} (max_loss/ct=${max_loss_per_contract:.0f})"
        )

        return PositionSize(
            contracts=contracts,
            kelly_fraction_raw=f_star,
            kelly_fraction_used=f_used,
            max_loss_dollars=contracts * max_loss_per_contract,
            rationale=rationale,
        )
