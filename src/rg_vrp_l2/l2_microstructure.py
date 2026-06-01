"""
Level-2 order book microstructure signals for entry timing.

Uses bid/ask depth from Schwab NASDAQ_BOOK / OPTIONS_BOOK streams.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BookLevel:
    price: float
    aggregate_size: int


@dataclass
class OrderBookSnapshot:
    symbol: str
    bids: list[BookLevel] = field(default_factory=list)
    asks: list[BookLevel] = field(default_factory=list)
    timestamp_ms: int = 0


@dataclass
class L2EntrySignal:
    spread_pct: float
    obi: float
    bid_depth: int
    ask_depth: int
    allow_entry: bool
    reason: str


class L2Microstructure:
    """Order book imbalance and liquidity gates."""

    def __init__(
        self,
        max_spread_pct: float = 0.08,
        obi_min: float = -0.15,
        obi_max: float = 0.15,
        min_depth_contracts: int = 20,
        book_levels: int = 5,
    ) -> None:
        self.max_spread_pct = max_spread_pct
        self.obi_min = obi_min
        self.obi_max = obi_max
        self.min_depth = min_depth_contracts
        self.book_levels = book_levels

    def order_book_imbalance(self, book: OrderBookSnapshot) -> float:
        """
        OBI = (sum_bid - sum_ask) / (sum_bid + sum_ask) on top N levels.
        """
        bids = book.bids[: self.book_levels]
        asks = book.asks[: self.book_levels]
        bid_vol = sum(b.aggregate_size for b in bids)
        ask_vol = sum(a.aggregate_size for a in asks)
        total = bid_vol + ask_vol
        if total == 0:
            return 0.0
        return (bid_vol - ask_vol) / total

    def evaluate(
        self,
        book: OrderBookSnapshot,
        quote_bid: float,
        quote_ask: float,
    ) -> L2EntrySignal:
        mid = (quote_bid + quote_ask) / 2.0
        spread_pct = (quote_ask - quote_bid) / mid if mid > 0 else 1.0

        obi = self.order_book_imbalance(book)
        bid_depth = sum(b.aggregate_size for b in book.bids[: self.book_levels])
        ask_depth = sum(a.aggregate_size for a in book.asks[: self.book_levels])
        min_side_depth = min(bid_depth, ask_depth)

        allow = True
        reasons = []

        if spread_pct > self.max_spread_pct:
            allow = False
            reasons.append(f"spread {spread_pct:.1%} > {self.max_spread_pct:.1%}")
        if obi < self.obi_min or obi > self.obi_max:
            allow = False
            reasons.append(f"OBI {obi:.3f} outside [{self.obi_min}, {self.obi_max}]")
        if min_side_depth < self.min_depth:
            allow = False
            reasons.append(f"depth {min_side_depth} < {self.min_depth}")

        if not reasons:
            reasons.append("L2 gates pass")

        return L2EntrySignal(
            spread_pct=spread_pct,
            obi=obi,
            bid_depth=bid_depth,
            ask_depth=ask_depth,
            allow_entry=allow,
            reason="; ".join(reasons),
        )
