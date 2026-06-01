"""
Avellaneda-Stoikov reservation pricing adapted for strike selection.

Reference: Avellaneda & Stoikov (2008); Stoikov & Saglam (2009) for Greeks inventory.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PortfolioGreeks:
    """Normalized portfolio Greek inventory."""

    net_delta: float  # share-equivalent delta / account_notional
    net_vega: float  # dollars per 1% IV
    net_gamma: float


@dataclass
class StrikeAdjustment:
    delta_adjustment: float
    adjusted_short_delta: float
    reservation_offset: float


class InventorySkew:
    """
    Shift short strikes based on inventory (reservation price framework).

    r_t = S_t - q * gamma * sigma^2 * tau
    For strikes: positive q (short delta) -> push strikes further OTM.
    """

    def __init__(
        self,
        risk_aversion: float = 0.25,
        delta_skew_factor: float = 0.02,
        min_short_delta: float = 0.10,
        max_short_delta: float = 0.22,
    ) -> None:
        self.gamma = risk_aversion
        self.delta_skew_factor = delta_skew_factor
        self.min_delta = min_short_delta
        self.max_delta = max_short_delta

    def adjust_short_delta(
        self,
        base_delta: float,
        greeks: PortfolioGreeks,
        underlying_price: float,
        dte: int,
        realized_vol_pct: float,
    ) -> StrikeAdjustment:
        """Return adjusted target short delta for new structure."""
        tau = max(dte, 1) / 365.0
        sigma = realized_vol_pct / 100.0

        # q: normalized delta inventory in [-1, 1] roughly
        q = np_clip(greeks.net_delta, -1.0, 1.0)

        reservation_offset = q * self.gamma * (sigma**2) * tau * underlying_price

        # Short delta inventory -> move strikes OTM (lower delta magnitude for puts)
        delta_adj = -q * self.delta_skew_factor
        adjusted = base_delta + delta_adj
        adjusted = max(self.min_delta, min(self.max_delta, adjusted))

        return StrikeAdjustment(
            delta_adjustment=delta_adj,
            adjusted_short_delta=adjusted,
            reservation_offset=reservation_offset,
        )


def np_clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))
