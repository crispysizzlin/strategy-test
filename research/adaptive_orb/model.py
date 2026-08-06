from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Direction(Enum):
    LONG = 1
    SHORT = -1


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_value: float | None = None
    depth_imbalance: float | None = None
    ofi_norm: float | None = None
    trade_delta_norm: float | None = None
    microprice_ticks: float | None = None
    l2_persistence: float | None = None
    spread_ticks: float | None = None
    l2_age_ms: float | None = None
    # Persistent resting-liquidity "walls" observed at the close of the minute.
    # Prices are absolute; ratios are wall size divided by the median displayed
    # level size on the same side of the book (adaptive, not a fixed contract count).
    bid_wall_price: float | None = None
    bid_wall_ratio: float | None = None
    ask_wall_price: float | None = None
    ask_wall_ratio: float | None = None

    @property
    def has_l2(self) -> bool:
        return all(
            value is not None
            for value in (
                self.depth_imbalance,
                self.ofi_norm,
                self.trade_delta_norm,
                self.microprice_ticks,
                self.l2_persistence,
                self.spread_ticks,
                self.l2_age_ms,
            )
        )

    @property
    def l2_composite(self) -> float | None:
        if not self.has_l2:
            return None
        assert self.depth_imbalance is not None
        assert self.ofi_norm is not None
        assert self.trade_delta_norm is not None
        assert self.microprice_ticks is not None
        # Bounded, intentionally simple weights. They are fixed before validation rather
        # than selected from a large optimization grid.
        micro = max(-1.0, min(1.0, self.microprice_ticks))
        return (
            0.40 * max(-1.0, min(1.0, self.depth_imbalance))
            + 0.30 * max(-1.0, min(1.0, self.ofi_norm))
            + 0.20 * max(-1.0, min(1.0, self.trade_delta_norm))
            + 0.10 * micro
        )


@dataclass(frozen=True)
class Trade:
    session: str
    direction: Direction
    signal_time: datetime
    entry_time: datetime
    exit_time: datetime
    decision_price: float
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    quantity: int
    exit_reason: str
    gross_pnl: float
    commission: float
    net_pnl: float
    risk_dollars: float
    hold_bars: int
    opening_range: float
    atr: float | None
    l2_composite: float | None
    window: str = "primary"
    entry_type: str = "market"


@dataclass(frozen=True)
class DailyResult:
    session: str
    net_pnl: float
    gross_pnl: float
    commission: float
    trades: int
