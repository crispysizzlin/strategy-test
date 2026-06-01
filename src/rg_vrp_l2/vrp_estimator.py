"""
Variance risk premium estimation (Carr & Wu, 2009 proxy).

VRP_t = IV_30 - RV_20 (annualized vol points).
Full replication uses OTM option integral; proxy is standard in production.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class VRPSignal:
    implied_vol_30d: float
    realized_vol_20d: float
    vrp: float
    iv_rank: float
    term_structure_slope: float | None
    should_sell_vol: bool
    reason: str


def realized_volatility(
    close: pd.Series,
    lookback: int = 20,
    annualization: int = 252,
) -> float:
    """Close-to-close realized vol, annualized."""
    rets = np.log(close / close.shift(1)).dropna()
    if len(rets) < lookback:
        lookback = len(rets)
    if lookback < 2:
        return float("nan")
    window = rets.iloc[-lookback:]
    return float(window.std() * np.sqrt(annualization) * 100)


def iv_rank(current_iv: float, iv_history: pd.Series) -> float:
    """Percentile of current IV in trailing history [0, 1]."""
    if iv_history.empty or np.isnan(current_iv):
        return 0.0
    return float((iv_history < current_iv).mean())


class VRPEstimator:
    """Evaluate whether variance risk premium favors short-vol entry."""

    def __init__(
        self,
        threshold_vol_points: float = 2.0,
        iv_rank_min: float = 0.30,
        rv_lookback_days: int = 20,
        iv_tenor_days: int = 30,
    ) -> None:
        self.threshold = threshold_vol_points
        self.iv_rank_min = iv_rank_min
        self.rv_lookback = rv_lookback_days
        self.iv_tenor = iv_tenor_days

    def evaluate(
        self,
        close_prices: pd.Series,
        implied_vol_30d: float,
        implied_vol_7d: float | None = None,
        iv_history: pd.Series | None = None,
    ) -> VRPSignal:
        rv = realized_volatility(close_prices, self.rv_lookback)
        vrp = implied_vol_30d - rv

        rank = 0.0
        if iv_history is not None and len(iv_history) > 10:
            rank = iv_rank(implied_vol_30d, iv_history)

        ts_slope = None
        if implied_vol_7d is not None:
            ts_slope = implied_vol_7d - implied_vol_30d

        sell = True
        reasons = []

        if vrp < self.threshold:
            sell = False
            reasons.append(f"VRP {vrp:.2f} < threshold {self.threshold}")
        if rank < self.iv_rank_min:
            sell = False
            reasons.append(f"IV rank {rank:.2f} < {self.iv_rank_min}")

        if not reasons:
            reasons.append("VRP and IV rank pass")

        return VRPSignal(
            implied_vol_30d=implied_vol_30d,
            realized_vol_20d=rv,
            vrp=vrp,
            iv_rank=rank,
            term_structure_slope=ts_slope,
            should_sell_vol=sell,
            reason="; ".join(reasons),
        )
