"""Signal generation: the conditional gate for premium harvesting.

This is where the academic pieces combine into a single, auditable decision.
Premium is harvested **only** when *all* conditions agree, which is precisely the
conditioning the literature shows is required to turn a weak unconditional VRP
into a Sharpe ~1 strategy and to flatten the catastrophic short-vol tail:

1. Variance Risk Premium is positive and statistically rich
   (forecast IV^2 - E[RV^2] > 0 with z-score above threshold).
   -> Carr-Wu (2009); Bollerslev-Tauchen-Zhou (2009).
2. Volatility regime is calm/low (HMM rank <= threshold).
   -> Hamilton (1989); Vilkov (2026) regime conditioning.
3. IV term structure is *not* in steep backwardation (no acute stress).
4. Optional mean-reversion tilt biases which side carries more risk.
   -> Ornstein-Uhlenbeck s-score (Avellaneda-Lee 2010).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from ..config import SignalConfig
from ..models.garch import GARCH11
from ..models.har_rv import HARRV
from ..models.ou_meanrev import fit_ou
from ..models.realized_vol import realized_variance_series, yang_zhang_vol
from ..models.regime import GaussianHMM
from ..models.vrp import forecast_vrp


class Decision(str, Enum):
    HARVEST = "HARVEST"          # sell premium (neutral / balanced)
    HARVEST_PUT_SIDE = "HARVEST_PUT_SIDE"   # bullish skew - favor put credit
    HARVEST_CALL_SIDE = "HARVEST_CALL_SIDE"  # bearish skew - favor call credit
    STAND_DOWN = "STAND_DOWN"    # no edge / too risky
    DEFEND = "DEFEND"            # stress regime - hold/cut, lift tail hedge


@dataclass
class SignalState:
    decision: Decision
    iv: float
    rv_forecast_har: float
    rv_forecast_garch: float
    rv_forecast: float
    vrp_var: float
    vrp_zscore: float
    regime_rank: int
    term_structure_slope: float
    ou_s_score: float
    reasons: list[str]

    @property
    def harvest(self) -> bool:
        return self.decision in (Decision.HARVEST, Decision.HARVEST_PUT_SIDE,
                                 Decision.HARVEST_CALL_SIDE)


def _term_structure_slope(iv_front: float, iv_back: float) -> float:
    """Positive => contango (back > front, calm); negative => backwardation."""
    if not (np.isfinite(iv_front) and np.isfinite(iv_back)) or iv_front <= 0:
        return 0.0
    return (iv_back - iv_front) / iv_front


class SignalEngine:
    """Stateful signal engine: fit models on history, evaluate on the latest bar."""

    def __init__(self, cfg: SignalConfig, n_regimes: int = 3) -> None:
        self.cfg = cfg
        self.n_regimes = n_regimes
        self._vrp_history: list[float] = []

    def evaluate(self, ohlc: pd.DataFrame, iv_front: float,
                 iv_back: float, horizon_days: int) -> SignalState:
        """Produce a decision from price history + current implied vols.

        ``ohlc`` must have columns open/high/low/close indexed by date.
        ``iv_front`` is the implied vol at the trade's expiry; ``iv_back`` a
        longer expiry for term-structure slope.
        """
        close = ohlc["close"]
        reasons: list[str] = []

        # --- realized vol forecast (HAR-RV primary, GARCH ensemble) ---------
        rv_daily = realized_variance_series(close, window=21).pow(0.5).dropna()
        har_fc = garch_fc = float("nan")
        try:
            har = HARRV().fit(rv_daily)
            har_fc = har.forecast(rv_daily, horizon_days=max(horizon_days, 1))
        except Exception:  # pragma: no cover - degenerate history
            reasons.append("har_fit_failed")
        try:
            log_ret = np.log(close / close.shift(1)).dropna().to_numpy()
            g = GARCH11().fit(log_ret)
            garch_fc = g.forecast_vol(horizon_days=max(horizon_days, 1))
        except Exception:  # pragma: no cover
            reasons.append("garch_fit_failed")

        fcs = [x for x in (har_fc, garch_fc) if np.isfinite(x)]
        if not fcs:
            rv_fc = yang_zhang_vol(ohlc["open"], ohlc["high"], ohlc["low"], close)
            reasons.append("fallback_yang_zhang")
        else:
            # Ensemble: lean on HAR-RV (0.7) but blend GARCH (0.3) when present.
            if np.isfinite(har_fc) and np.isfinite(garch_fc):
                rv_fc = 0.7 * har_fc + 0.3 * garch_fc
            else:
                rv_fc = fcs[0]

        # --- VRP signal -----------------------------------------------------
        vrp = forecast_vrp(iv_front, rv_fc, self._vrp_history)
        self._vrp_history.append(vrp.vrp_var)
        if len(self._vrp_history) > 252:
            self._vrp_history.pop(0)

        # --- regime ---------------------------------------------------------
        regime_rank = 0
        try:
            log_ret = np.log(close / close.shift(1)).dropna().to_numpy()
            std = log_ret.std()
            x = log_ret / std if std > 0 else log_ret
            hmm = GaussianHMM(n_states=self.n_regimes).fit(x)
            regime_rank = hmm.current_vol_rank(x)
        except Exception:  # pragma: no cover
            reasons.append("hmm_fit_failed")

        # --- term structure -------------------------------------------------
        slope = _term_structure_slope(iv_front, iv_back)

        # --- mean reversion tilt -------------------------------------------
        ou_s = float("nan")
        try:
            logp = np.log(close.tail(120).to_numpy())
            ou_s = fit_ou(logp).s_score
        except Exception:  # pragma: no cover
            pass

        # --- combine into decision -----------------------------------------
        decision = self._combine(vrp.vrp_var, vrp.z_score, regime_rank, slope,
                                 ou_s, reasons)

        return SignalState(
            decision=decision, iv=iv_front, rv_forecast_har=har_fc,
            rv_forecast_garch=garch_fc, rv_forecast=rv_fc, vrp_var=vrp.vrp_var,
            vrp_zscore=vrp.z_score, regime_rank=regime_rank,
            term_structure_slope=slope, ou_s_score=ou_s, reasons=reasons)

    def _combine(self, vrp_var: float, vrp_z: float, regime_rank: int,
                 slope: float, ou_s: float, reasons: list[str]) -> Decision:
        c = self.cfg
        if regime_rank > c.max_regime_rank_to_harvest:
            reasons.append(f"regime_rank={regime_rank}>limit")
            return Decision.DEFEND if regime_rank >= self.n_regimes - 1 else Decision.STAND_DOWN
        if slope < c.backwardation_block:
            reasons.append(f"backwardation slope={slope:.3f}")
            return Decision.STAND_DOWN
        if vrp_var <= c.min_vrp_var:
            reasons.append(f"vrp_var={vrp_var:.4f}<=min")
            return Decision.STAND_DOWN
        if np.isfinite(vrp_z) and vrp_z < c.min_vrp_zscore:
            reasons.append(f"vrp_z={vrp_z:.2f}<min")
            return Decision.STAND_DOWN

        reasons.append("all_conditions_met")
        # Mean-reversion tilt: if price is stretched low (s<<0) favor the put side
        # (expect bounce); if stretched high favor the call side.
        if np.isfinite(ou_s):
            if ou_s <= -1.0:
                return Decision.HARVEST_PUT_SIDE
            if ou_s >= 1.0:
                return Decision.HARVEST_CALL_SIDE
        return Decision.HARVEST
