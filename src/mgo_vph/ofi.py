"""
Order Flow Imbalance (Cont, Kukanov & Stoikov, 2014).

Level-2 best bid/ask event aggregation for short-horizon directional filter.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BookSnapshot:
    bid_price: float
    bid_size: float
    ask_price: float
    ask_size: float


@dataclass(frozen=True)
class OFIState:
    ofi: float
    depth: float
    normalized: float


class OFIAccumulator:
    """Cumulative OFI over a rolling window of book updates."""

    def __init__(self) -> None:
        self._prev: BookSnapshot | None = None
        self.ofi: float = 0.0

    def _event_contribution(self, prev: BookSnapshot, curr: BookSnapshot) -> float:
        e = 0.0
        # Bid side
        if curr.bid_price > prev.bid_price:
            e += curr.bid_size
        elif curr.bid_price == prev.bid_price:
            e += curr.bid_size - prev.bid_size
        else:
            e -= prev.bid_size
        # Ask side (inverse sign convention per Cont et al.)
        if curr.ask_price < prev.ask_price:
            e -= curr.ask_size
        elif curr.ask_price == prev.ask_price:
            e -= curr.ask_size - prev.ask_size
        else:
            e += prev.ask_size
        return e

    def update(self, snap: BookSnapshot) -> float:
        if self._prev is not None:
            self.ofi += self._event_contribution(self._prev, snap)
        self._prev = snap
        return self.ofi

    def reset(self) -> None:
        self._prev = None
        self.ofi = 0.0

    def state(self, snap: BookSnapshot) -> OFIState:
        depth = max(snap.bid_size + snap.ask_size, 1.0)
        return OFIState(ofi=self.ofi, depth=depth, normalized=self.ofi / depth)
