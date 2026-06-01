"""Broker-agnostic data models used across the toolkit."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


class OptionType(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


class Instruction(str, Enum):
    BUY_TO_OPEN = "BUY_TO_OPEN"
    SELL_TO_OPEN = "SELL_TO_OPEN"
    BUY_TO_CLOSE = "BUY_TO_CLOSE"
    SELL_TO_CLOSE = "SELL_TO_CLOSE"


@dataclass(frozen=True)
class OptionContract:
    """An option contract identified by Schwab's OSI-style symbol.

    Schwab option symbol format (21 chars):
        RRRRRRYYMMDDCXXXXXXXX  (root padded to 6, yymmdd, C/P, strike*1000 padded 8)
    e.g. ``SPY   240920C00450000``
    """
    underlying: str
    expiry: date
    strike: float
    option_type: OptionType

    @property
    def osi_symbol(self) -> str:
        root = f"{self.underlying:<6}"
        ymd = self.expiry.strftime("%y%m%d")
        cp = "C" if self.option_type == OptionType.CALL else "P"
        strike_int = int(round(self.strike * 1000))
        return f"{root}{ymd}{cp}{strike_int:08d}"

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return self.osi_symbol


@dataclass
class Quote:
    symbol: str
    bid: float
    ask: float
    last: float = float("nan")
    bid_size: int = 0
    ask_size: int = 0
    volume: int = 0
    open_interest: int = 0
    implied_vol: float = float("nan")
    delta: float = float("nan")
    gamma: float = float("nan")
    theta: float = float("nan")
    vega: float = float("nan")
    underlying_price: float = float("nan")

    @property
    def mid(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return 0.5 * (self.bid + self.ask)
        return self.last

    @property
    def spread(self) -> float:
        return max(self.ask - self.bid, 0.0)


@dataclass
class OrderLeg:
    contract: OptionContract
    instruction: Instruction
    quantity: int


@dataclass
class MultiLegOrder:
    legs: list[OrderLeg]
    net_price: float                 # net credit (positive) or debit
    order_type: str = "NET_CREDIT"   # NET_CREDIT | NET_DEBIT | LIMIT | MARKET
    duration: str = "DAY"
    session: str = "NORMAL"
    tag: str = field(default="qist")


@dataclass
class BookLevel:
    price: float
    size: int
    num_orders: int = 0


@dataclass
class OrderBook:
    """Level 2 order book snapshot."""
    symbol: str
    bids: list[BookLevel]
    asks: list[BookLevel]
    timestamp_ms: int = 0

    @property
    def best_bid(self) -> Optional[BookLevel]:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> Optional[BookLevel]:
        return self.asks[0] if self.asks else None

    @property
    def microprice(self) -> float:
        """Size-weighted mid (Level 2 fair value)."""
        bb, ba = self.best_bid, self.best_ask
        if not bb or not ba:
            return float("nan")
        denom = bb.size + ba.size
        if denom == 0:
            return 0.5 * (bb.price + ba.price)
        return (bb.price * ba.size + ba.price * bb.size) / denom

    def imbalance(self, depth: int = 5) -> float:
        """Order-book imbalance in [-1, 1]; >0 means bid-heavy (upward pressure)."""
        b = sum(l.size for l in self.bids[:depth])
        a = sum(l.size for l in self.asks[:depth])
        denom = a + b
        return (b - a) / denom if denom else 0.0
