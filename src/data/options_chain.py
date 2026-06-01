"""
Options chain processing: IV extraction, surface construction, liquidity filtering.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.models.pricing.black_scholes import BlackScholes
from src.utils.logger import logger


class OptionsChainProcessor:
    """
    Processes a raw options chain DataFrame into analytics for strategy use.

    Provides:
      - ATM implied vol
      - Skew metrics (25-delta, 10-delta)
      - Liquidity filters (volume, OI, bid-ask spread)
      - Strike selection for given delta targets
      - Put/call ratio
    """

    MIN_VOLUME = 10
    MIN_OI = 50
    MAX_SPREAD_PCT = 0.10  # Max 10% bid-ask spread

    def __init__(self) -> None:
        self._chain: pd.DataFrame = pd.DataFrame()
        self._spot: float = 0.0
        self._r: float = 0.05
        self._q: float = 0.014

    def update(
        self,
        chain_df: pd.DataFrame,
        spot: Optional[float] = None,
        r: float = 0.05,
        q: float = 0.014,
    ) -> "OptionsChainProcessor":
        """
        Update with a new options chain.

        Parameters
        ----------
        chain_df : DataFrame from SchwabClient.parse_options_chain_to_df()
        spot     : current underlying price (if None, inferred from chain)
        """
        self._chain = chain_df.copy()
        self._r = r
        self._q = q

        if spot is not None:
            self._spot = spot
        elif "underlying_price" in chain_df.columns and not chain_df.empty:
            self._spot = float(chain_df["underlying_price"].iloc[0])

        # Quality filters
        self._chain = self._filter_chain(self._chain)
        return self

    def _filter_chain(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply liquidity filters."""
        if df.empty:
            return df
        filtered = df.copy()
        # Remove options with very wide spreads (illiquid)
        if "bid" in df.columns and "ask" in df.columns and "mid" in df.columns:
            spread = (df["ask"] - df["bid"]).abs()
            mid = df["mid"].abs()
            spread_pct = spread / mid.clip(lower=1e-6)
            filtered = filtered[spread_pct < self.MAX_SPREAD_PCT]
        return filtered

    def atm_iv(self, dte_target: int = 7) -> float:
        """
        Return ATM implied vol for the nearest expiry to dte_target.

        Falls back to overall ATM average if specific DTE not available.
        """
        if self._chain.empty:
            return 0.15

        # Find best expiry
        available_dtes = self._chain["dte"].unique()
        if len(available_dtes) == 0:
            return 0.15

        closest_dte = int(min(available_dtes, key=lambda d: abs(d - dte_target)))
        slice_df = self._chain[self._chain["dte"] == closest_dte]

        if slice_df.empty:
            return 0.15

        # Find ATM: closest strike to spot
        spot = self._spot
        if spot == 0 and "underlying_price" in slice_df.columns:
            spot = float(slice_df["underlying_price"].iloc[0])

        if spot == 0:
            return float(slice_df["iv"].median())

        # ATM = strike closest to spot
        call_slice = slice_df[slice_df["option_type"] == "call"]
        put_slice = slice_df[slice_df["option_type"] == "put"]

        ivs = []
        for df_sub in [call_slice, put_slice]:
            if not df_sub.empty:
                idx = (df_sub["strike"] - spot).abs().idxmin()
                iv = df_sub.loc[idx, "iv"]
                if np.isfinite(iv) and 0.01 < iv < 3.0:
                    ivs.append(iv)

        return float(np.mean(ivs)) if ivs else 0.15

    def skew_25d(self, dte_target: int = 7) -> float:
        """
        25-delta skew: IV(25Δ put) - IV(25Δ call).
        Positive value means put vol > call vol (normal for equity).
        """
        if self._chain.empty or self._spot == 0:
            return 0.0

        available_dtes = self._chain["dte"].unique()
        if len(available_dtes) == 0:
            return 0.0

        closest_dte = int(min(available_dtes, key=lambda d: abs(d - dte_target)))
        slice_df = self._chain[self._chain["dte"] == closest_dte]

        puts = slice_df[slice_df["option_type"] == "put"]
        calls = slice_df[slice_df["option_type"] == "call"]

        # Find 25-delta options
        put_25 = self._find_delta_strike(puts, 0.25)
        call_25 = self._find_delta_strike(calls, 0.25)

        if put_25 is None or call_25 is None:
            return 0.0

        return float(put_25 - call_25)

    def _find_delta_strike(
        self, df: pd.DataFrame, target_delta: float
    ) -> Optional[float]:
        """Find the IV of the option closest to target_delta."""
        if df.empty or "delta" not in df.columns:
            return None
        df = df.dropna(subset=["delta", "iv"])
        if df.empty:
            return None
        idx = (df["delta"].abs() - target_delta).abs().idxmin()
        iv = df.loc[idx, "iv"]
        return float(iv) if np.isfinite(iv) else None

    def put_call_ratio(self, dte_max: int = 14) -> float:
        """Put/call volume ratio (sentiment indicator)."""
        if self._chain.empty:
            return 1.0
        df = self._chain[self._chain["dte"] <= dte_max]
        if df.empty:
            return 1.0
        put_vol = df[df["option_type"] == "put"]["volume"].sum()
        call_vol = df[df["option_type"] == "call"]["volume"].sum()
        return float(put_vol / max(call_vol, 1))

    def find_strikes_for_condor(
        self,
        put_delta: float = 0.15,
        call_delta: float = 0.15,
        dte_target: int = 7,
        wing_width: Optional[float] = None,
    ) -> Dict[str, Optional[float]]:
        """
        Find actual traded strikes closest to the target deltas.

        Returns dict with: put_short, put_long, call_short, call_long
        (all strikes from the actual options chain)
        """
        available_dtes = self._chain["dte"].unique()
        if len(available_dtes) == 0:
            return {}

        closest_dte = int(min(available_dtes, key=lambda d: abs(d - dte_target)))
        slice_df = self._chain[self._chain["dte"] == closest_dte]

        puts = slice_df[slice_df["option_type"] == "put"].dropna(subset=["delta"])
        calls = slice_df[slice_df["option_type"] == "call"].dropna(subset=["delta"])

        def find_k(df: pd.DataFrame, target: float) -> Optional[float]:
            if df.empty:
                return None
            idx = (df["delta"].abs() - target).abs().idxmin()
            return float(df.loc[idx, "strike"])

        put_short_K = find_k(puts, put_delta)
        call_short_K = find_k(calls, call_delta)

        if put_short_K is None or call_short_K is None:
            return {}

        # Find wing strikes (next available strike below/above)
        if wing_width is not None:
            put_long_K = put_short_K - wing_width
            call_long_K = call_short_K + wing_width
        else:
            # Use next available strike
            put_strikes = sorted(puts["strike"].unique())
            call_strikes = sorted(calls["strike"].unique())

            ps_idx = put_strikes.index(put_short_K) if put_short_K in put_strikes else -1
            put_long_K = put_strikes[max(0, ps_idx - 1)] if ps_idx > 0 else put_short_K - 10

            cs_idx = call_strikes.index(call_short_K) if call_short_K in call_strikes else -1
            call_long_K = call_strikes[min(len(call_strikes)-1, cs_idx+1)] if cs_idx < len(call_strikes)-1 else call_short_K + 10

        return {
            "put_short": put_short_K,
            "put_long": put_long_K,
            "call_short": call_short_K,
            "call_long": call_long_K,
            "expiry_dte": closest_dte,
        }
