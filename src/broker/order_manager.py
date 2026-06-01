"""
Order construction and management for options strategies.

Provides builders for:
  - Single-leg options orders
  - Multi-leg spreads (Iron Condor, Butterfly, Calendar, Diagonal)
  - OCO (One-Cancels-Other) brackets
  - Order status tracking
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from src.utils.logger import logger


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    PLACED = "PLACED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    PAPER = "PAPER"


class OrderSide(str, Enum):
    BUY_TO_OPEN = "BUY_TO_OPEN"
    SELL_TO_OPEN = "SELL_TO_OPEN"
    BUY_TO_CLOSE = "BUY_TO_CLOSE"
    SELL_TO_CLOSE = "SELL_TO_CLOSE"


@dataclass
class Leg:
    symbol: str
    instruction: OrderSide
    quantity: int
    price: float = 0.0


@dataclass
class Order:
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    strategy_name: str = ""
    legs: List[Leg] = field(default_factory=list)
    order_type: str = "NET_CREDIT"        # NET_CREDIT | NET_DEBIT | LIMIT | MARKET
    limit_price: Optional[float] = None
    session: str = "NORMAL"
    duration: str = "DAY"
    status: OrderStatus = OrderStatus.PENDING
    broker_order_id: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)
    filled_at: Optional[datetime] = None
    fill_price: Optional[float] = None
    # Greeks at time of entry
    entry_delta: float = 0.0
    entry_theta: float = 0.0
    entry_vega: float = 0.0
    entry_gamma: float = 0.0
    # Risk parameters
    max_profit: float = 0.0
    max_loss: float = 0.0
    breakeven_lower: float = 0.0
    breakeven_upper: float = 0.0


class OrderManager:
    """
    Constructs Schwab API order specifications for options strategies.

    All multi-leg orders are submitted as single net-credit/net-debit orders
    to reduce transaction costs and avoid partial fills.
    """

    # ------------------------------------------------------------------
    # Iron Condor
    # ------------------------------------------------------------------

    @staticmethod
    def build_iron_condor(
        put_short_symbol: str,
        put_long_symbol: str,
        call_short_symbol: str,
        call_long_symbol: str,
        quantity: int,
        net_credit: float,
        strategy_name: str = "IronCondor",
    ) -> Order:
        """
        Build a short iron condor order (sell put spread + sell call spread).

        Net credit is the total credit for 4 legs (per contract).
        """
        order = Order(
            strategy_name=strategy_name,
            order_type="NET_CREDIT",
            limit_price=round(net_credit, 2),
            legs=[
                Leg(put_short_symbol, OrderSide.SELL_TO_OPEN, quantity),
                Leg(put_long_symbol, OrderSide.BUY_TO_OPEN, quantity),
                Leg(call_short_symbol, OrderSide.SELL_TO_OPEN, quantity),
                Leg(call_long_symbol, OrderSide.BUY_TO_OPEN, quantity),
            ],
        )
        return order

    @staticmethod
    def build_iron_condor_spec(order: Order) -> Dict[str, Any]:
        """Convert an iron condor Order to Schwab API JSON spec."""
        return {
            "orderType": "NET_CREDIT",
            "session": order.session,
            "price": str(order.limit_price),
            "duration": order.duration,
            "orderStrategyType": "SINGLE",
            "orderLegCollection": [
                {
                    "instruction": leg.instruction.value,
                    "quantity": leg.quantity,
                    "instrument": {
                        "symbol": leg.symbol,
                        "assetType": "OPTION",
                    },
                }
                for leg in order.legs
            ],
            "complexOrderStrategyType": "IRON_CONDOR",
        }

    # ------------------------------------------------------------------
    # Broken Wing Butterfly
    # ------------------------------------------------------------------

    @staticmethod
    def build_broken_wing_butterfly(
        lower_long_symbol: str,    # Far OTM (wing)
        middle_short_symbol: str,  # 2x short
        upper_long_symbol: str,    # Closer to ATM (shifted wing)
        quantity: int,
        net_credit: float,
        strategy_name: str = "BrokenWingButterfly",
    ) -> Order:
        """
        Broken Wing Butterfly (put-side skew): asymmetric risk profile.
        Long 1 far OTM put, short 2 OTM puts, long 1 closer OTM put.
        Width of upper spread < width of lower spread → receive net credit.
        """
        order = Order(
            strategy_name=strategy_name,
            order_type="NET_CREDIT",
            limit_price=round(net_credit, 2),
            legs=[
                Leg(lower_long_symbol, OrderSide.BUY_TO_OPEN, quantity),
                Leg(middle_short_symbol, OrderSide.SELL_TO_OPEN, quantity * 2),
                Leg(upper_long_symbol, OrderSide.BUY_TO_OPEN, quantity),
            ],
        )
        return order

    # ------------------------------------------------------------------
    # Calendar Spread
    # ------------------------------------------------------------------

    @staticmethod
    def build_calendar_spread(
        front_month_symbol: str,   # Sell near-term (sell theta)
        back_month_symbol: str,    # Buy far-term (long vega)
        quantity: int,
        net_debit: float,
        strategy_name: str = "CalendarSpread",
    ) -> Order:
        """
        Buy a calendar spread: buy back-month, sell front-month.
        Net debit trade; profits if realized vol < implied vol.
        In HIGH VOL regimes with backwardation, calendar spreads capture
        the spread between front-month and back-month IV.
        """
        order = Order(
            strategy_name=strategy_name,
            order_type="NET_DEBIT",
            limit_price=round(net_debit, 2),
            legs=[
                Leg(front_month_symbol, OrderSide.SELL_TO_OPEN, quantity),
                Leg(back_month_symbol, OrderSide.BUY_TO_OPEN, quantity),
            ],
        )
        return order

    # ------------------------------------------------------------------
    # Single Vertical Spread (Credit Spread)
    # ------------------------------------------------------------------

    @staticmethod
    def build_vertical_spread(
        short_symbol: str,
        long_symbol: str,
        quantity: int,
        net_credit: float,
        strategy_name: str = "VerticalSpread",
    ) -> Order:
        """Sell a vertical credit spread (bull put or bear call)."""
        order = Order(
            strategy_name=strategy_name,
            order_type="NET_CREDIT",
            limit_price=round(net_credit, 2),
            legs=[
                Leg(short_symbol, OrderSide.SELL_TO_OPEN, quantity),
                Leg(long_symbol, OrderSide.BUY_TO_OPEN, quantity),
            ],
        )
        return order

    # ------------------------------------------------------------------
    # Close / Exit Orders
    # ------------------------------------------------------------------

    @staticmethod
    def build_close_order(
        original_order: Order,
        limit_price: float,
        strategy_name: str = "",
    ) -> Order:
        """Build a closing order (reverse all legs) for an existing position."""
        reversed_legs = []
        for leg in original_order.legs:
            if leg.instruction == OrderSide.SELL_TO_OPEN:
                new_instruction = OrderSide.BUY_TO_CLOSE
            elif leg.instruction == OrderSide.BUY_TO_OPEN:
                new_instruction = OrderSide.SELL_TO_CLOSE
            else:
                new_instruction = leg.instruction
            reversed_legs.append(Leg(leg.symbol, new_instruction, leg.quantity))

        order_type = "NET_DEBIT" if original_order.order_type == "NET_CREDIT" else "NET_CREDIT"
        return Order(
            strategy_name=strategy_name or f"CLOSE_{original_order.strategy_name}",
            order_type=order_type,
            limit_price=round(limit_price, 2),
            legs=reversed_legs,
        )

    # ------------------------------------------------------------------
    # Schwab API Spec Builder
    # ------------------------------------------------------------------

    @staticmethod
    def to_schwab_spec(order: Order) -> Dict[str, Any]:
        """
        Convert any Order to a Schwab API order specification.

        Handles both single-leg and multi-leg (spread) orders.
        """
        n_legs = len(order.legs)

        if n_legs == 1:
            complex_type = "NONE"
        elif n_legs == 2:
            # Infer vertical/calendar
            instr_set = {leg.instruction for leg in order.legs}
            if OrderSide.BUY_TO_OPEN in instr_set and OrderSide.SELL_TO_OPEN in instr_set:
                complex_type = "VERTICAL"
            else:
                complex_type = "DIAGONAL"
        elif n_legs == 3:
            complex_type = "BUTTERFLY"
        elif n_legs == 4:
            complex_type = "IRON_CONDOR"
        else:
            complex_type = "CUSTOM"

        spec: Dict[str, Any] = {
            "orderType": order.order_type,
            "session": order.session,
            "duration": order.duration,
            "orderStrategyType": "SINGLE",
            "complexOrderStrategyType": complex_type,
            "orderLegCollection": [
                {
                    "instruction": leg.instruction.value,
                    "quantity": leg.quantity,
                    "instrument": {
                        "symbol": leg.symbol,
                        "assetType": "OPTION",
                    },
                }
                for leg in order.legs
            ],
        }

        if order.limit_price is not None:
            spec["price"] = str(round(order.limit_price, 2))

        return spec

    # ------------------------------------------------------------------
    # Portfolio tracking
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        self.open_orders: Dict[str, Order] = {}
        self.closed_orders: Dict[str, Order] = {}

    def register_order(self, order: Order) -> None:
        self.open_orders[order.id] = order
        logger.debug(f"Registered order {order.id} ({order.strategy_name})")

    def mark_filled(
        self,
        order_id: str,
        fill_price: float,
        broker_order_id: str = "",
    ) -> None:
        if order_id in self.open_orders:
            order = self.open_orders.pop(order_id)
            order.status = OrderStatus.FILLED
            order.fill_price = fill_price
            order.filled_at = datetime.now()
            order.broker_order_id = broker_order_id
            self.closed_orders[order_id] = order
            logger.info(
                f"Order {order_id} filled at {fill_price:.4f} "
                f"(strategy: {order.strategy_name})"
            )

    def mark_cancelled(self, order_id: str) -> None:
        if order_id in self.open_orders:
            order = self.open_orders.pop(order_id)
            order.status = OrderStatus.CANCELLED
            self.closed_orders[order_id] = order
            logger.info(f"Order {order_id} cancelled")

    def get_open_strategies(self) -> List[str]:
        return [o.strategy_name for o in self.open_orders.values()]

    def count_open_positions(self) -> int:
        return len(self.open_orders)
