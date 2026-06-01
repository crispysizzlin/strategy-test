"""
Level 2 order book analysis for options entry timing.

This module provides one of AVRPE's key differentiators: using the Level 2
order book to time option entries for better fills and reduced slippage.

The key insight is that options market makers adjust their quotes dynamically
based on order flow. By analysing the bid/ask order book imbalance, we can:
  1. Detect institutional buying/selling pressure
  2. Time entries to sell into bid-side pressure (better fill prices)
  3. Avoid entering when the spread is artificially wide (illiquid)
  4. Identify unusual options activity that may signal market direction

Order Flow Imbalance (OFI):
  OFI = (B_depth - A_depth) / (B_depth + A_depth)
  Range: [-1, +1]
  OFI > 0 → buying pressure (bids > asks)
  OFI < 0 → selling pressure (asks > bids)

For PREMIUM SELLING (selling options):
  We WANT to sell when there is buying pressure on those options
  (i.e., market participants are bidding aggressively)
  This means: enter short positions when OFI > threshold

Spread Quality Score:
  Measures how much we are giving up to the market maker.
  SQ = (mid - bid) / mid  → lower is better (tighter spread)
  Target: < 2% of mid price for liquid options

Volume Momentum:
  Recent volume vs 20-period average.
  High volume → market is "active" → better fill quality expected
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Tuple

import numpy as np

from src.broker.streaming import Level2Snapshot
from src.utils.logger import logger


@dataclass
class OrderFlowSignal:
    """Order flow analysis output for an option symbol."""
    timestamp: datetime
    symbol: str
    ofi: float                    # Order Flow Imbalance [-1, +1]
    spread_quality: float         # [0, 1]: 1 = tight spread, 0 = wide
    volume_momentum: float        # recent_vol / avg_vol
    bid_pressure: float           # [0, 1]: 1 = aggressive buying
    ask_pressure: float           # [0, 1]: 1 = aggressive selling
    mid_price: float
    best_bid: float
    best_ask: float
    bid_depth: float              # Total bid-side size (5 levels)
    ask_depth: float              # Total ask-side size (5 levels)

    @property
    def is_favourable_to_sell(self) -> bool:
        """
        True when conditions are favourable for selling this option.
        
        Favourable when:
          1. OFI > 0.3 (buying pressure exists)
          2. Spread quality > 0.7 (tight spread, good fills expected)
          3. Volume is not extremely low
        """
        return (
            self.ofi > 0.2
            and self.spread_quality > 0.60
            and self.volume_momentum > 0.3
        )

    @property
    def limit_price_to_sell(self) -> float:
        """
        Suggested limit price for selling this option.
        
        When OFI > 0: bid up to mid − 10% of spread
        When OFI neutral: use mid
        Post-adjustment to improve fill while not leaving money on table.
        """
        spread = self.best_ask - self.best_bid
        if self.ofi > 0.3:
            # Buying pressure: we can get closer to mid or slightly above
            return self.mid_price + 0.05 * spread
        elif self.ofi < -0.2:
            # Selling pressure: accept below mid to ensure fill
            return self.mid_price - 0.10 * spread
        return self.mid_price

    @property
    def urgency_score(self) -> float:
        """
        How urgently to enter (0=wait, 1=enter immediately).
        High urgency when: bid pressure strong + spread tight + good volume.
        """
        return float(
            0.50 * max(self.ofi, 0)
            + 0.30 * self.spread_quality
            + 0.20 * min(self.volume_momentum, 2.0) / 2.0
        )


class OrderFlowAnalyzer:
    """
    Analyses Level 2 data to produce entry timing signals.

    Maintains rolling history of OFI and spread data for each symbol.
    Computes trend in order flow to detect institutional positioning.
    """

    def __init__(
        self,
        ofi_threshold: float = 0.20,
        spread_quality_threshold: float = 0.60,
        history_size: int = 50,
    ) -> None:
        self.ofi_threshold = ofi_threshold
        self.spread_quality_threshold = spread_quality_threshold
        self._history: Dict[str, deque] = {}
        self._volume_history: Dict[str, deque] = {}
        self._history_size = history_size

    def analyze(
        self,
        symbol: str,
        snapshot: Level2Snapshot,
        recent_volume: float = 0.0,
        avg_volume: float = 1.0,
    ) -> Optional[OrderFlowSignal]:
        """
        Produce an order flow signal from a Level 2 snapshot.

        Parameters
        ----------
        symbol         : options symbol (e.g. 'SPY   241220P00450000')
        snapshot       : Level2Snapshot from StreamingManager
        recent_volume  : volume in last N minutes
        avg_volume     : average volume for context (N-period average)

        Returns
        -------
        OrderFlowSignal or None if insufficient data
        """
        if snapshot is None:
            return None

        if not snapshot.bids or not snapshot.asks:
            return None

        best_bid = snapshot.best_bid
        best_ask = snapshot.best_ask
        mid = snapshot.mid
        spread = best_ask - best_bid

        if mid <= 0 or np.isnan(mid):
            return None

        # OFI
        ofi = snapshot.order_flow_imbalance

        # Spread quality: how tight is the spread relative to mid?
        spread_pct = spread / mid
        spread_quality = float(max(0, 1 - spread_pct / 0.05))  # 5% spread = quality=0

        # Volume momentum
        volume_momentum = float(recent_volume / max(avg_volume, 1e-6))

        # Bid / ask pressure from VWAP divergence
        vwap_bid = snapshot.vwap_bid
        vwap_ask = snapshot.vwap_ask
        if np.isfinite(vwap_bid) and np.isfinite(vwap_ask):
            bid_pressure = float(np.clip((vwap_bid - best_bid) / mid + 0.5, 0, 1))
            ask_pressure = float(np.clip((best_ask - vwap_ask) / mid + 0.5, 0, 1))
        else:
            bid_pressure = float(max(ofi, 0))
            ask_pressure = float(max(-ofi, 0))

        # Store OFI history for trend analysis
        if symbol not in self._history:
            self._history[symbol] = deque(maxlen=self._history_size)
        self._history[symbol].append((snapshot.timestamp, ofi))

        signal = OrderFlowSignal(
            timestamp=datetime.now(),
            symbol=symbol,
            ofi=ofi,
            spread_quality=spread_quality,
            volume_momentum=volume_momentum,
            bid_pressure=bid_pressure,
            ask_pressure=ask_pressure,
            mid_price=mid,
            best_bid=best_bid,
            best_ask=best_ask,
            bid_depth=snapshot.bid_depth,
            ask_depth=snapshot.ask_depth,
        )

        logger.debug(
            f"OFI [{symbol[:12]}]: ofi={ofi:.3f} spread_q={spread_quality:.2f} "
            f"vol_mom={volume_momentum:.2f} "
            f"sell_signal={'YES' if signal.is_favourable_to_sell else 'no'}"
        )
        return signal

    def ofi_trend(self, symbol: str, n: int = 10) -> float:
        """
        Linear trend in OFI over the last n observations.
        Positive trend → accelerating buying pressure.
        """
        if symbol not in self._history:
            return 0.0
        history = list(self._history[symbol])[-n:]
        if len(history) < 3:
            return 0.0
        ofi_vals = np.array([o for _, o in history])
        x = np.arange(len(ofi_vals))
        slope = np.polyfit(x, ofi_vals, 1)[0]
        return float(slope)

    def institutional_flow_score(self, symbol: str) -> float:
        """
        Estimate likelihood of institutional flow (0-1).
        Large block trades at mid or above suggest institutional accumulation.
        These typically presage directional moves.
        
        High score → be cautious selling options in that direction.
        """
        if symbol not in self._history:
            return 0.0
        history = list(self._history[symbol])
        if len(history) < 5:
            return 0.0
        recent_ofi = np.array([o for _, o in history[-10:]])
        # Institutional flow: sustained directional OFI with low variance
        mean_ofi = float(np.mean(recent_ofi))
        var_ofi = float(np.var(recent_ofi))
        if var_ofi < 0.01 and abs(mean_ofi) > 0.3:
            return float(abs(mean_ofi))
        return 0.0

    def combined_entry_signal(
        self,
        option_symbols: Dict[str, Level2Snapshot],
    ) -> Tuple[bool, float]:
        """
        Aggregate signal across multiple option legs of a spread.

        For an iron condor: analyse all 4 legs, compute average entry quality.
        Higher average quality → better time to enter.

        Returns (is_good_entry, quality_score)
        """
        signals = []
        for symbol, snapshot in option_symbols.items():
            sig = self.analyze(symbol, snapshot)
            if sig:
                signals.append(sig)

        if not signals:
            return False, 0.0

        avg_quality = float(np.mean([s.spread_quality for s in signals]))
        avg_ofi = float(np.mean([abs(s.ofi) for s in signals]))

        # Good entry: all legs have decent spread quality
        all_quality_ok = all(s.spread_quality > self.spread_quality_threshold for s in signals)
        quality_score = float(0.6 * avg_quality + 0.4 * avg_ofi)

        return all_quality_ok, quality_score
