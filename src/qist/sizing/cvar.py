"""Conditional Value-at-Risk (CVaR / Expected Shortfall) utilities.

CVaR_alpha is the expected loss conditional on being in the worst (1-alpha)
tail.  Unlike VaR it is coherent (sub-additive) and is the right risk measure
for a negatively skewed short-vol book.  We use it two ways:

1. As a *hard budget*: number of contracts is capped so the portfolio CVaR at
   99% stays within a fraction of equity.
2. Rockafellar-Uryasev (2000) linear-programming representation, which lets CVaR
   be minimized/constrained with convex methods:

       CVaR_alpha = min_eta  eta + 1/(1-alpha) * E[(L - eta)+]

References
----------
Rockafellar, R.T. & Uryasev, S. (2000). Optimization of Conditional
Value-at-Risk. *Journal of Risk*, 2, 21-42.
"""

from __future__ import annotations

import numpy as np


def var_cvar(losses: np.ndarray, alpha: float = 0.99) -> tuple[float, float]:
    """Return (VaR, CVaR) at confidence ``alpha`` from a sample of *losses*.

    Losses are positive numbers for adverse outcomes (loss = -pnl).
    """
    losses = np.asarray(losses, dtype=float)
    losses = losses[np.isfinite(losses)]
    if len(losses) == 0:
        return float("nan"), float("nan")
    var = float(np.quantile(losses, alpha))
    tail = losses[losses >= var]
    cvar = float(tail.mean()) if len(tail) else var
    return var, cvar


def rockafellar_uryasev_cvar(losses: np.ndarray, alpha: float = 0.99) -> float:
    """CVaR via the Rockafellar-Uryasev formula (eta = empirical VaR)."""
    losses = np.asarray(losses, dtype=float)
    losses = losses[np.isfinite(losses)]
    if len(losses) == 0:
        return float("nan")
    eta = float(np.quantile(losses, alpha))
    excess = np.maximum(losses - eta, 0.0)
    return eta + excess.mean() / (1.0 - alpha)


def max_contracts_by_cvar(per_contract_losses: np.ndarray, equity: float,
                          cvar_budget_frac: float = 0.05,
                          alpha: float = 0.99) -> int:
    """Largest integer contract count whose portfolio CVaR <= budget.

    Assumes losses scale linearly with contract count (true for identical,
    co-moving structures - a conservative simplification because it ignores any
    diversification benefit).
    """
    _, cvar_1 = var_cvar(per_contract_losses, alpha)
    if not np.isfinite(cvar_1) or cvar_1 <= 0:
        return 0
    budget = cvar_budget_frac * equity
    return int(max(np.floor(budget / cvar_1), 0))
