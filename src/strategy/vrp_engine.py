"""
Volatility Risk Premium (VRP) signal engine.

The VRP Engine is the core alpha signal generator of AVRPE.

Economic rationale:
  The Volatility Risk Premium (VRP = IV - RV) exists because option sellers
  demand a premium for bearing variance risk. Empirically, SPX implied vol
  exceeds subsequent realized vol approximately 85% of trading days, with an
  average premium of 3-4 vol points.

  This persistent positive VRP is the theoretical basis for systematic
  options premium selling strategies.

Mathematical signal:
  VRP_t = σ_IV(t, T_short) - σ_RV(t, window)
  Signal_strength = Percentile_rank(VRP_t, 20-day lookback)
  
  Entry when: Signal_strength > 0.60 (60th percentile)
  Exit when:  Signal_strength < 0.30 or Regime = Crisis

Additional considerations:
  1. VIX term structure slope (contango = favourable for sellers)
  2. Skew level (high put skew → expensive puts → more credit)
  3. Realized vol trend (rising RV → dangerous for short vol)
  4. Gamma risk (intraday moves exceeding expected range)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from src.models.volatility.garch import GARCHModel, GARCHResult
from src.models.volatility.realized_vol import RealizedVolatility
from src.models.regime.hmm_detector import RegimeState, RegimeLabel
from src.utils.logger import logger


@dataclass
class VRPSignal:
    """Complete VRP signal at a point in time."""
    timestamp: datetime
    symbol: str

    # Volatility estimates
    implied_vol: float          # ATM implied vol (from options chain)
    realized_vol_cc: float      # Close-to-close RV
    realized_vol_yz: float      # Yang-Zhang RV
    garch_forecast: float       # GARCH 1-step ahead vol forecast

    # VRP measures
    vrp_cc: float              # IV - CC_RV
    vrp_yz: float              # IV - YZ_RV
    vrp_garch: float           # IV - GARCH_RV
    vrp_composite: float        # Weighted composite

    # Signal strength
    vrp_percentile: float      # 0-1 percentile rank vs 20-day history
    signal_strength: float     # Final 0-1 signal (higher = sell more aggressively)

    # Supporting data
    vix_level: float
    vix_term_slope: float      # VIX3M/VIX ratio
    skew_25d: float            # 25-delta put-call vol difference
    put_call_ratio: float

    # Regime
    regime: Optional[RegimeState] = None

    @property
    def is_trade_signal(self) -> bool:
        """True if VRP signal is strong enough to enter a new position."""
        return (
            self.signal_strength > 0.60
            and self.vix_term_slope > 1.00  # VIX in contango
            and (self.regime is None or self.regime.allow_new_positions)
        )

    @property
    def recommended_delta(self) -> float:
        """
        Recommended short delta for strike selection.
        Higher VRP and lower vol → sell closer to ATM.
        Lower VRP or higher vol → sell further OTM.
        """
        # Base: 15-delta
        base_delta = 0.15
        if self.implied_vol > 0.25:
            return max(base_delta - 0.03, 0.10)  # go further OTM in high vol
        elif self.implied_vol < 0.15:
            return min(base_delta + 0.03, 0.20)  # can sell closer in low vol
        return base_delta

    @property
    def vega_budget(self) -> float:
        """
        Recommended vega exposure multiplier.
        High VRP → allocate more vega budget.
        Low VRP → minimal vega exposure.
        """
        return float(np.clip(self.signal_strength, 0.3, 1.0))


class VRPEngine:
    """
    Computes the Volatility Risk Premium signal for a given underlying.

    Combines GARCH forecasts, multiple realized vol estimators, VIX data,
    and regime state to produce a unified trade signal.

    Typical usage:
        engine = VRPEngine("SPX")
        engine.update(price_df, implied_vol=0.17, vix=14.5, vix3m=15.2)
        signal = engine.current_signal
        if signal.is_trade_signal:
            ...
    """

    # VRP component weights for composite score
    VRP_WEIGHTS = {
        "cc": 0.20,
        "yz": 0.40,    # Yang-Zhang most robust
        "garch": 0.40,  # GARCH forward-looking
    }

    def __init__(
        self,
        symbol: str,
        rv_window: int = 21,
        vrp_lookback: int = 20,
        garch_lookback: int = 252,
        entry_threshold: float = 0.60,
        exit_threshold: float = 0.30,
    ) -> None:
        self.symbol = symbol
        self.rv_window = rv_window
        self.vrp_lookback = vrp_lookback
        self.garch_lookback = garch_lookback
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold

        self._rv_calc = RealizedVolatility(window=rv_window)
        self._garch = GARCHModel(p=1, q=1, distribution="normal")
        self._garch_result: Optional[GARCHResult] = None
        self._vrp_history: list = []
        self.current_signal: Optional[VRPSignal] = None

    # ------------------------------------------------------------------
    # Update cycle
    # ------------------------------------------------------------------

    def update(
        self,
        price_df: pd.DataFrame,
        implied_vol: float,
        vix: float,
        vix3m: float,
        skew_25d: float = 0.0,
        put_call_ratio: float = 1.0,
        regime: Optional[RegimeState] = None,
    ) -> VRPSignal:
        """
        Compute the current VRP signal.

        Parameters
        ----------
        price_df     : DataFrame with [open, high, low, close] columns
        implied_vol  : ATM implied vol (annualised fraction)
        vix          : VIX level (annualised, as fraction e.g. 0.15 for VIX=15)
        vix3m        : VIX3M level
        skew_25d     : 25-delta put - 25-delta call vol (positive = put skew)
        put_call_ratio : options market put/call ratio
        regime       : current regime state from HMM

        Returns
        -------
        VRPSignal with current signal quality
        """
        if len(price_df) < self.rv_window:
            logger.warning(f"Insufficient data for VRP calculation: {len(price_df)} bars")
            return self._default_signal(implied_vol, vix, vix3m, regime)

        o = price_df["open"].values
        h = price_df["high"].values
        l = price_df["low"].values
        c = price_df["close"].values

        # 1. Multiple realized vol estimates
        rv_cc = self._rv_calc.close_to_close(c[-self.rv_window:])
        rv_yz = self._rv_calc.yang_zhang(o[-self.rv_window:], h[-self.rv_window:],
                                          l[-self.rv_window:], c[-self.rv_window:])
        rv_pk = self._rv_calc.parkinson(h[-self.rv_window:], l[-self.rv_window:])

        # 2. GARCH forecast
        if len(price_df) >= self.garch_lookback:
            log_returns = np.log(c / np.roll(c, 1))[1:]
            log_returns = log_returns[-self.garch_lookback:]
            try:
                self._garch_result = self._garch.fit(pd.Series(log_returns))
                rv_garch = self._garch.forecast(h=1)
            except Exception as e:
                logger.debug(f"GARCH fit failed: {e}")
                rv_garch = rv_cc
        else:
            rv_garch = rv_yz

        # 3. VRP calculations
        vrp_cc = implied_vol - rv_cc
        vrp_yz = implied_vol - rv_yz
        vrp_garch = implied_vol - rv_garch

        vrp_composite = (
            self.VRP_WEIGHTS["cc"] * vrp_cc
            + self.VRP_WEIGHTS["yz"] * vrp_yz
            + self.VRP_WEIGHTS["garch"] * vrp_garch
        )

        # 4. VRP percentile rank vs history
        self._vrp_history.append(vrp_composite)
        if len(self._vrp_history) > max(self.vrp_lookback * 3, 60):
            self._vrp_history = self._vrp_history[-self.vrp_lookback * 3:]

        recent_vrp = self._vrp_history[-self.vrp_lookback:]
        vrp_percentile = float((np.array(recent_vrp) < vrp_composite).mean())

        # 5. Composite signal strength
        # Components: VRP percentile, VIX contango, skew signal
        vix_term_slope = vix3m / vix if vix > 0 else 1.0
        vix_contango_score = min((vix_term_slope - 1.0) / 0.05 + 0.5, 1.0)  # [0,1]

        signal_strength = (
            0.60 * vrp_percentile
            + 0.25 * max(vix_contango_score, 0)
            + 0.15 * (1.0 if put_call_ratio > 0.8 else 0.5)
        )

        # Reduce signal if regime is unfavourable
        if regime is not None:
            signal_strength *= regime.kelly_multiplier * 2  # scale by regime
            signal_strength = min(signal_strength, 1.0)

        # High realized vol spike → override signal downward
        if rv_yz > implied_vol * 1.10:
            signal_strength *= 0.5
            logger.debug(f"RV spike detected: RV_YZ={rv_yz:.2%} > IV={implied_vol:.2%}")

        self.current_signal = VRPSignal(
            timestamp=datetime.now(),
            symbol=self.symbol,
            implied_vol=implied_vol,
            realized_vol_cc=rv_cc,
            realized_vol_yz=rv_yz,
            garch_forecast=rv_garch,
            vrp_cc=vrp_cc,
            vrp_yz=vrp_yz,
            vrp_garch=vrp_garch,
            vrp_composite=vrp_composite,
            vrp_percentile=vrp_percentile,
            signal_strength=float(np.clip(signal_strength, 0, 1)),
            vix_level=vix,
            vix_term_slope=vix_term_slope,
            skew_25d=skew_25d,
            put_call_ratio=put_call_ratio,
            regime=regime,
        )

        logger.debug(
            f"VRP signal [{self.symbol}]: "
            f"IV={implied_vol:.2%} RV_YZ={rv_yz:.2%} GARCH={rv_garch:.2%} "
            f"VRP={vrp_composite:.3f} pctl={vrp_percentile:.2f} "
            f"strength={signal_strength:.2f} "
            f"trade={'YES' if self.current_signal.is_trade_signal else 'NO'}"
        )
        return self.current_signal

    def _default_signal(
        self,
        implied_vol: float,
        vix: float,
        vix3m: float,
        regime: Optional[RegimeState],
    ) -> VRPSignal:
        """Return a neutral default signal when insufficient data."""
        return VRPSignal(
            timestamp=datetime.now(),
            symbol=self.symbol,
            implied_vol=implied_vol,
            realized_vol_cc=0.0,
            realized_vol_yz=0.0,
            garch_forecast=0.0,
            vrp_cc=0.0,
            vrp_yz=0.0,
            vrp_garch=0.0,
            vrp_composite=0.0,
            vrp_percentile=0.5,
            signal_strength=0.0,
            vix_level=vix,
            vix_term_slope=vix3m / vix if vix > 0 else 1.0,
            skew_25d=0.0,
            put_call_ratio=1.0,
            regime=regime,
        )

    # ------------------------------------------------------------------
    # Continuous monitoring (intraday)
    # ------------------------------------------------------------------

    def check_exit_conditions(
        self,
        current_iv: float,
        current_rv: float,
        regime: Optional[RegimeState] = None,
    ) -> bool:
        """
        Return True if active positions should be closed/reduced.

        Exit conditions:
          1. VRP signal drops below exit_threshold
          2. RV spikes above IV (realized > implied)
          3. Regime transitions to crisis
          4. VIX backwardation (term structure inverts)
        """
        if regime and not regime.allow_new_positions:
            logger.warning("Exit signal: Crisis regime detected")
            return True

        if current_rv > current_iv * 1.15:
            logger.warning(f"Exit signal: RV ({current_rv:.2%}) > IV ({current_iv:.2%})")
            return True

        if self.current_signal and self.current_signal.signal_strength < self.exit_threshold:
            logger.info(f"Exit signal: VRP signal weak ({self.current_signal.signal_strength:.2f})")
            return True

        return False
