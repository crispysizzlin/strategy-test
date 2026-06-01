"""
Composite entry signal: VRP + regime + OFI + skew (MGO-VPH).
"""

from __future__ import annotations

from dataclasses import dataclass

from mgo_vph.ofi import OFIState
from mgo_vph.regime import RegimeState
from mgo_vph.vrp import VRPResult


@dataclass(frozen=True)
class SignalConfig:
    vrp_z_min: float = 1.0
    p_low_vol_min: float = 0.65
    ofi_norm_min: float = -0.3
    ofi_norm_max: float = 0.5
    skew_premium_min: float = 0.0
    iv_must_exceed_rv: bool = True


@dataclass(frozen=True)
class CompositeSignal:
    allow_entry: bool
    reasons: tuple[str, ...]


def evaluate_entry(
    vrp: VRPResult,
    regime: RegimeState,
    ofi: OFIState,
    skew_premium: float,
    iv_30d: float,
    rv_forecast_5d: float,
    config: SignalConfig | None = None,
) -> CompositeSignal:
    cfg = config or SignalConfig()
    reasons: list[str] = []

    if vrp.vrp_z <= cfg.vrp_z_min:
        reasons.append(f"VRP z-score {vrp.vrp_z:.2f} <= {cfg.vrp_z_min}")
    if regime.p_low_vol < cfg.p_low_vol_min:
        reasons.append(f"P(low vol) {regime.p_low_vol:.2f} < {cfg.p_low_vol_min}")
    if not (cfg.ofi_norm_min <= ofi.normalized <= cfg.ofi_norm_max):
        reasons.append(f"OFI/depth {ofi.normalized:.2f} outside [{cfg.ofi_norm_min}, {cfg.ofi_norm_max}]")
    if skew_premium < cfg.skew_premium_min:
        reasons.append(f"skew premium {skew_premium:.4f} < {cfg.skew_premium_min}")
    if cfg.iv_must_exceed_rv and iv_30d <= rv_forecast_5d:
        reasons.append(f"IV {iv_30d:.4f} <= RV forecast {rv_forecast_5d:.4f}")

    return CompositeSignal(allow_entry=len(reasons) == 0, reasons=tuple(reasons))
