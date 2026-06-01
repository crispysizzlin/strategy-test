"""
Options structure selector — chooses the optimal spread structure for each regime.

Decision framework:
  Input: VRP signal strength + Regime state + Vol surface analytics
  Output: Selected structure type, strikes, expiry, and expected analytics

Structure selection matrix:
  ┌─────────────────┬─────────────────────┬────────────────────────────┐
  │ Regime          │ Primary Structure   │ Secondary Structure         │
  ├─────────────────┼─────────────────────┼────────────────────────────┤
  │ Low Vol         │ Iron Condor (1-3DTE)│ Short Strangle (closer)    │
  │ Normal Vol      │ Iron Condor (2-5DTE)│ Broken Wing Butterfly      │
  │ High Vol        │ Calendar Spread     │ Far-OTM Iron Condor        │
  │ Crisis          │ FLAT / Exit only    │ Protective Put Spread      │
  └─────────────────┴─────────────────────┴────────────────────────────┘

Strike selection:
  - Iron Condors: 15-delta strikes (85% probability of profit per side)
  - Broken Wing Butterfly: 20-delta upper, 10-delta lower
  - SPX: $10 wide wings; SPY: $2-$3 wide wings

Novelty: The "Skew-Adjusted Iron Condor" — adjusting call/put side widths
based on the vol surface skew:
  - High put skew → wider put wing (collect more premium)
  - Symmetric skew → equal wings
  - Backwardation in term structure → shift to calendars
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from src.models.pricing.black_scholes import BlackScholes
from src.models.pricing.spreads import SpreadPricer, SpreadAnalysis
from src.models.regime.hmm_detector import RegimeLabel, RegimeState
from src.strategy.vrp_engine import VRPSignal
from src.utils.helpers import next_expiry_date
from src.utils.logger import logger


@dataclass
class SelectedStructure:
    """Output of the structure selector."""
    structure_type: str
    underlying: str
    expiry: date
    dte: int

    # Strike configuration
    put_long_strike: Optional[float] = None
    put_short_strike: Optional[float] = None
    call_short_strike: Optional[float] = None
    call_long_strike: Optional[float] = None
    middle_strike: Optional[float] = None    # For butterflies

    # Pricing
    net_credit: float = 0.0
    max_loss: float = 0.0
    max_profit: float = 0.0
    profit_probability: float = 0.0
    expected_value: float = 0.0

    # Greeks
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    theta_gamma_ratio: float = 0.0

    # Execution
    contracts: int = 1
    analysis: Optional[SpreadAnalysis] = None

    @property
    def is_valid(self) -> bool:
        return (
            self.net_credit > 0
            and self.max_loss > 0
            and self.profit_probability > 0.55
        )

    def describe(self) -> str:
        return (
            f"{self.structure_type} {self.underlying} "
            f"exp={self.expiry} DTE={self.dte} "
            f"credit={self.net_credit:.4f} P(P)={self.profit_probability:.1%} "
            f"θ={self.theta:.4f} Γ={self.gamma:.6f} "
        )


class StructureSelector:
    """
    Selects the optimal options structure for the current market regime and signal.

    Core logic:
      1. Determine target DTE based on regime
      2. Compute target strikes using delta-to-strike mapping
      3. Price all candidate structures using SpreadPricer
      4. Apply skew adjustment for asymmetric wings
      5. Select best structure by risk-adjusted score
    """

    # Default parameters per regime
    REGIME_PARAMS = {
        RegimeLabel.LOW_VOL: {
            "dte_range": (1, 3),
            "delta_target": 0.14,
            "wing_multiplier": 1.0,
            "structures": ["iron_condor", "broken_wing_butterfly"],
        },
        RegimeLabel.NORMAL_VOL: {
            "dte_range": (2, 5),
            "delta_target": 0.16,
            "wing_multiplier": 1.2,
            "structures": ["iron_condor", "broken_wing_butterfly", "vertical_spread"],
        },
        RegimeLabel.HIGH_VOL: {
            "dte_range": (5, 14),
            "delta_target": 0.12,
            "wing_multiplier": 1.8,
            "structures": ["iron_condor", "calendar_spread"],
        },
        RegimeLabel.CRISIS: {
            "dte_range": (14, 45),
            "delta_target": 0.08,
            "wing_multiplier": 2.5,
            "structures": [],  # No new premium selling in crisis
        },
    }

    def __init__(
        self,
        r: float = 0.05,
        q: float = 0.014,
        spx_wing_width: float = 10.0,   # SPX points per wing
        spy_wing_width: float = 2.0,    # SPY points per wing
    ) -> None:
        self.r = r
        self.q = q
        self.spx_wing_width = spx_wing_width
        self.spy_wing_width = spy_wing_width
        self._pricer = SpreadPricer(r=r, q=q)

    def select(
        self,
        symbol: str,
        spot: float,
        sigma: float,
        signal: VRPSignal,
        regime: RegimeState,
        options_df: Optional[pd.DataFrame] = None,
    ) -> Optional[SelectedStructure]:
        """
        Main entry point: select the best structure given current conditions.

        Parameters
        ----------
        symbol    : underlying symbol (SPX / SPY / QQQ)
        spot      : current spot price
        sigma     : ATM implied vol (annualised)
        signal    : VRP signal from VRPEngine
        regime    : current regime from HMMRegimeDetector
        options_df: live options chain (for per-strike IV); if None, uses flat sigma

        Returns
        -------
        SelectedStructure or None (if no suitable trade found)
        """
        if not regime.allow_new_positions:
            logger.info(f"No new positions: regime={regime.label}")
            return None

        if signal.signal_strength < 0.40:
            logger.debug(f"VRP signal too weak ({signal.signal_strength:.2f})")
            return None

        params = self.REGIME_PARAMS.get(regime.label, self.REGIME_PARAMS[RegimeLabel.NORMAL_VOL])

        if not params["structures"]:
            return None

        dte_min, dte_max = params["dte_range"]
        dte = self._select_dte(dte_min, dte_max, signal)
        expiry = next_expiry_date(dte)
        T = dte / 365.0

        wing_width = (self.spx_wing_width if "SPX" in symbol.upper()
                      else self.spy_wing_width)

        # Apply regime wing multiplier
        wing_width *= params["wing_multiplier"]

        # Compute target strikes
        delta_target = params["delta_target"]

        # Adjust delta for signal strength (stronger signal → closer to ATM)
        delta_adj = delta_target * (1 + 0.2 * (0.7 - signal.signal_strength))
        delta_adj = float(np.clip(delta_adj, 0.08, 0.22))

        candidates: List[SpreadAnalysis] = []
        structures: List[dict] = []

        for structure_type in params["structures"]:
            try:
                result = self._price_structure(
                    structure_type, symbol, spot, sigma, T, delta_adj, wing_width,
                    signal.skew_25d, options_df
                )
                if result is not None:
                    candidates.append(result[0])
                    structures.append({**result[1], "type": structure_type, "expiry": expiry, "dte": dte})
            except Exception as e:
                logger.debug(f"Structure pricing failed ({structure_type}): {e}")
                continue

        if not candidates:
            logger.debug(f"No viable structures for {symbol} (DTE={dte})")
            return None

        best = self._pricer.compare_structures(
            candidates,
            min_prob_profit=0.60,
            min_theta_gamma_ratio=0.0,
        )

        if best is None:
            return None

        best_idx = candidates.index(best)
        best_meta = structures[best_idx]

        selected = SelectedStructure(
            structure_type=best.structure_type,
            underlying=symbol,
            expiry=expiry,
            dte=dte,
            net_credit=best.net_credit,
            max_loss=best.max_loss,
            max_profit=best.max_profit,
            profit_probability=best.profit_probability,
            expected_value=best.expected_value,
            delta=best.delta,
            gamma=best.gamma,
            theta=best.theta,
            vega=best.vega,
            theta_gamma_ratio=best.theta_gamma_ratio,
            analysis=best,
            **{k: v for k, v in best_meta.items()
               if k not in ("type", "expiry", "dte")},
        )

        logger.info(
            f"Structure selected: {selected.describe()} "
            f"regime={regime.label} signal={signal.signal_strength:.2f}"
        )
        return selected

    def _select_dte(self, dte_min: int, dte_max: int, signal: VRPSignal) -> int:
        """
        Choose DTE within the allowed range.

        Strong VRP signal → prefer shorter DTE (higher theta/gamma in short-dated)
        Weak signal → prefer longer DTE (slower gamma, more time to adjust)
        """
        if signal.signal_strength > 0.75:
            return max(dte_min, 1)  # Short-dated when signal is strong
        elif signal.signal_strength > 0.55:
            return (dte_min + dte_max) // 2
        else:
            return dte_max

    def _price_structure(
        self,
        structure_type: str,
        symbol: str,
        spot: float,
        sigma: float,
        T: float,
        delta_target: float,
        wing_width: float,
        skew_25d: float,
        options_df: Optional[pd.DataFrame],
    ) -> Optional[Tuple[SpreadAnalysis, dict]]:
        """Price a specific structure type. Returns (analysis, strike_metadata)."""

        def get_iv(K: float, option_type: str) -> float:
            """Get per-strike IV from chain or use flat sigma with skew adjustment."""
            if options_df is not None:
                row = options_df[
                    (options_df["option_type"] == option_type)
                    & (np.abs(options_df["strike"] - K) < wing_width / 2)
                ]
                if not row.empty:
                    iv = row.iloc[0]["iv"]
                    if np.isfinite(iv) and iv > 0:
                        return float(iv)
            # Rough skew model: puts more expensive by skew_25d
            log_m = np.log(K / spot)
            if option_type == "put":
                return sigma * (1 + max(-log_m, 0) * abs(skew_25d) * 2)
            return sigma * (1 + max(log_m, 0) * 0.3)

        if structure_type == "iron_condor":
            # Compute strikes via delta targeting
            put_short_K = BlackScholes.delta_to_strike(
                spot, self.r, self.q, sigma, T, delta_target, "put"
            )
            put_long_K = put_short_K - wing_width
            call_short_K = BlackScholes.delta_to_strike(
                spot, self.r, self.q, sigma, T, delta_target, "call"
            )
            call_long_K = call_short_K + wing_width

            # Skew adjustment: widen put wing if skew is steep
            if skew_25d > 0.03:
                put_long_K -= wing_width * 0.5  # wider protection on put side

            analysis = self._pricer.price_iron_condor(
                S=spot,
                put_short_K=put_short_K, put_long_K=put_long_K,
                call_short_K=call_short_K, call_long_K=call_long_K,
                sigma=sigma, T=T,
                put_short_iv=get_iv(put_short_K, "put"),
                put_long_iv=get_iv(put_long_K, "put"),
                call_short_iv=get_iv(call_short_K, "call"),
                call_long_iv=get_iv(call_long_K, "call"),
            )
            meta = {
                "put_short_strike": put_short_K, "put_long_strike": put_long_K,
                "call_short_strike": call_short_K, "call_long_strike": call_long_K,
            }
            return analysis, meta

        elif structure_type == "broken_wing_butterfly":
            # BWB: Asymmetric put butterfly
            upper_long_K = BlackScholes.delta_to_strike(
                spot, self.r, self.q, sigma, T, delta_target * 1.5, "put"
            )
            middle_short_K = upper_long_K - wing_width
            lower_long_K = middle_short_K - wing_width * 0.5  # narrow lower wing

            analysis = self._pricer.price_broken_wing_butterfly(
                S=spot,
                lower_long_K=lower_long_K,
                middle_short_K=middle_short_K,
                upper_long_K=upper_long_K,
                sigma=sigma, T=T,
                option_type="put",
            )
            meta = {
                "put_long_strike": lower_long_K,
                "put_short_strike": middle_short_K,
                "middle_strike": upper_long_K,
            }
            return analysis, meta

        elif structure_type == "vertical_spread":
            # Simple put credit spread (when only one side makes sense)
            short_K = BlackScholes.delta_to_strike(
                spot, self.r, self.q, sigma, T, delta_target, "put"
            )
            long_K = short_K - wing_width

            analysis = self._pricer.price_vertical_spread(
                S=spot, short_K=short_K, long_K=long_K,
                sigma_short=get_iv(short_K, "put"),
                sigma_long=get_iv(long_K, "put"),
                T=T, option_type="put",
            )
            meta = {
                "put_short_strike": short_K,
                "put_long_strike": long_K,
            }
            return analysis, meta

        elif structure_type == "calendar_spread":
            # ATM calendar spread for high-vol regime
            K = spot  # ATM
            T_front = min(T, 7 / 365)
            T_back = T

            analysis = self._pricer.price_calendar_spread(
                S=spot, K=K,
                sigma_front=get_iv(K, "call"),
                sigma_back=get_iv(K, "call") * 0.97,  # back-month slightly lower
                T_front=T_front, T_back=T_back,
                option_type="call",
            )
            meta = {"middle_strike": K}
            return analysis, meta

        return None
