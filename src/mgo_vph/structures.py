"""
Defined-risk structure selection for Level-3 Schwab automation.
"""

from __future__ import annotations

from dataclasses import dataclass

from mgo_vph.heston import HestonParams, calibrate_wing_strike


@dataclass(frozen=True)
class IronCondorSpec:
    symbol: str
    expiration: str  # ISO date
    short_put: float
    long_put: float
    short_call: float
    long_call: float
    target_credit: float
    max_loss: float
    contracts: int


def build_iron_condor(
    symbol: str,
    expiration: str,
    spot: float,
    tau_years: float,
    heston: HestonParams,
    wing_width: float,
    credit: float,
    contracts: int,
) -> IronCondorSpec:
    """Construct 4-leg iron condor from Heston-calibrated short put and symmetric call."""
    short_put = calibrate_wing_strike(spot, tau_years, heston, target_touch_prob=0.12)
    long_put = short_put - wing_width
    # Symmetric call side by distance
    dist = spot - short_put
    short_call = spot + dist
    long_call = short_call + wing_width
    max_loss = wing_width - credit
    return IronCondorSpec(
        symbol=symbol,
        expiration=expiration,
        short_put=round(short_put, 2),
        long_put=round(long_put, 2),
        short_call=round(short_call, 2),
        long_call=round(long_call, 2),
        target_credit=round(credit, 2),
        max_loss=round(max_loss, 2),
        contracts=contracts,
    )
