"""
Fractional Kelly position sizing (Thorp, 2006; MacLean-Ziemba, 2010).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KellyResult:
    kelly_fraction: float
    contracts: int
    risk_dollars: float


def fractional_kelly_contracts(
    account_equity: float,
    win_prob: float,
    win_amount: float,
    loss_amount: float,
    max_risk_pct: float = 0.02,
    kelly_divisor: float = 4.0,
) -> KellyResult:
    """
    Contracts for defined-risk trade.

    win_amount / loss_amount are per-contract dollar amounts.
    Uses quarter-Kelly by default (kelly_divisor=4).
    """
    if loss_amount <= 0 or account_equity <= 0:
        return KellyResult(0.0, 0, 0.0)

    edge = win_prob * win_amount - (1 - win_prob) * loss_amount
    kelly = edge / (win_amount * loss_amount) if win_amount * loss_amount > 0 else 0.0
    f = max(0.0, kelly / kelly_divisor)
    f = min(f, max_risk_pct)

    risk_budget = account_equity * f
    contracts = int(risk_budget // loss_amount)
    return KellyResult(
        kelly_fraction=f,
        contracts=max(contracts, 0),
        risk_dollars=contracts * loss_amount,
    )
