"""Kelly criterion sizing for defined-risk premium trades.

The Kelly fraction maximizes the expected log growth rate of capital.  For a
binary defined-risk trade that wins ``b`` units with probability ``p`` and loses
1 unit (the max loss) with probability ``q = 1-p``:

    f* = (b*p - q) / b = p - q/b

Short-vol payoff distributions are negatively skewed and *fat-tailed*, so full
Kelly is reckless.  We therefore (a) always apply a fractional multiplier
(default 0.25, "quarter-Kelly"), and (b) expose a general numeric Kelly that
maximizes E[log(1 + f * R)] over an empirical/Monte-Carlo payoff sample, which
honours the true skew instead of the binary approximation.

References
----------
Kelly, J. (1956). A New Interpretation of Information Rate. *Bell System TJ*.
Thorp, E. (2006). The Kelly Criterion in Blackjack, Sports Betting and the
Stock Market.
"""

from __future__ import annotations

import numpy as np


def kelly_binary(win_prob: float, payoff_ratio: float) -> float:
    """Classic binary Kelly fraction.  ``payoff_ratio`` = win amount / max loss."""
    if payoff_ratio <= 0:
        return 0.0
    q = 1.0 - win_prob
    f = win_prob - q / payoff_ratio
    return max(f, 0.0)


def kelly_from_samples(returns_per_unit_risk: np.ndarray,
                       f_grid: np.ndarray | None = None) -> float:
    """Growth-optimal fraction from an empirical payoff sample.

    ``returns_per_unit_risk`` are P&L outcomes expressed as a multiple of the
    capital put at risk (so -1.0 == lose the full max loss).  Maximizes the mean
    of log(1 + f * R) on a grid, clipped to keep 1 + f*R > 0 for all samples.
    """
    r = np.asarray(returns_per_unit_risk, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) == 0:
        return 0.0
    worst = r.min()
    f_max = 0.999 / abs(worst) if worst < 0 else 1.0
    if f_grid is None:
        f_grid = np.linspace(0.0, min(f_max, 1.0), 200)
    best_f, best_g = 0.0, -np.inf
    for f in f_grid:
        vals = 1.0 + f * r
        if np.any(vals <= 0):
            continue
        g = float(np.mean(np.log(vals)))
        if g > best_g:
            best_g, best_f = g, f
    return best_f


def fractional_kelly(full_kelly: float, fraction: float = 0.25,
                     cap: float = 0.5) -> float:
    """Down-scale a full-Kelly fraction and cap total risk exposure."""
    return float(min(max(full_kelly * fraction, 0.0), cap))
