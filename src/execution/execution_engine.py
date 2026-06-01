"""
Execution engine: order submission, fill tracking, and retry logic.

Implements smart order routing for options spreads:
  1. Check Level 2 quality before entering
  2. Submit as NET_CREDIT multi-leg order (reduces transaction costs)
  3. Start with aggressive limit (mid - small offset)
  4. Step down limit price if unfilled after timeout
  5. Cancel and retry if slippage too large
  6. Log all fills for Kelly empirical updating

Schwab API specifics for options spreads:
  - Multi-leg orders supported natively
  - NET_CREDIT order type for spread entries
  - NET_DEBIT for spread exits
  - complexOrderStrategyType must match the structure
  - Use LIMIT orders only (no MARKET orders for spreads)
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Dict, List, Optional

from src.broker.schwab_client import SchwabClient
from src.broker.order_manager import Order, OrderManager, OrderStatus
from src.execution.order_flow_analyzer import OrderFlowAnalyzer, OrderFlowSignal
from src.strategy.structure_selector import SelectedStructure
from src.utils.logger import logger
from src.utils.helpers import retry


class ExecutionEngine:
    """
    Manages the full lifecycle of order submission and fill tracking.

    Implements a tiered price-stepping algorithm:
      Attempt 1: mid − 1 tick (passive, best price)
      Attempt 2: mid − 0 ticks (mid price)
      Attempt 3: mid + 0.5 tick (slightly aggressive to ensure fill)
      Max 3 attempts per order, 30 seconds per attempt.
    """

    TICK_SIZE = 0.05  # Standard options tick

    def __init__(
        self,
        schwab_client: SchwabClient,
        order_manager: OrderManager,
        order_flow_analyzer: Optional[OrderFlowAnalyzer] = None,
        order_timeout_sec: int = 30,
        max_attempts: int = 3,
        max_slippage_pct: float = 0.02,
    ) -> None:
        self.client = schwab_client
        self.order_manager = order_manager
        self.flow_analyzer = order_flow_analyzer
        self.order_timeout_sec = order_timeout_sec
        self.max_attempts = max_attempts
        self.max_slippage_pct = max_slippage_pct

        self._pending_orders: Dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------

    def submit_spread_entry(
        self,
        structure: SelectedStructure,
        order: Order,
        flow_signal: Optional[OrderFlowSignal] = None,
    ) -> Dict:
        """
        Submit a spread entry order with smart limit pricing.

        Uses Level 2 data to adjust the initial limit price:
          - If OFI shows buying pressure (favourable): use slightly above mid
          - If OFI neutral or unfavourable: use slightly below mid

        Parameters
        ----------
        structure    : SelectedStructure from StructureSelector
        order        : Order built by OrderManager
        flow_signal  : Optional order flow signal for pricing

        Returns
        -------
        Dict with order_id, status, fill_price
        """
        # Determine initial limit price
        base_credit = order.limit_price or structure.net_credit
        if base_credit is None or base_credit <= 0:
            logger.warning("Cannot submit order: invalid credit")
            return {"status": "rejected", "reason": "invalid_credit"}

        # Adjust based on order flow
        if flow_signal and flow_signal.is_favourable_to_sell:
            initial_limit = base_credit + self.TICK_SIZE  # aggressive
        else:
            initial_limit = base_credit - self.TICK_SIZE  # conservative

        for attempt in range(self.max_attempts):
            limit_price = initial_limit - attempt * self.TICK_SIZE  # step down each attempt

            # Slippage check
            slippage_pct = abs(limit_price - base_credit) / max(base_credit, 1e-6)
            if slippage_pct > self.max_slippage_pct:
                logger.warning(
                    f"Max slippage exceeded ({slippage_pct:.1%}) — not submitting"
                )
                return {"status": "rejected", "reason": "max_slippage"}

            order.limit_price = round(max(limit_price, 0.01), 2)
            spec = self.order_manager.to_schwab_spec(order)

            logger.info(
                f"Submitting {structure.structure_type} order "
                f"attempt={attempt+1} limit={order.limit_price:.2f}"
            )

            try:
                result = self.client.place_order(spec)
                broker_order_id = result.get("order_id", "")

                if result.get("status") == "PAPER":
                    order.status = OrderStatus.PAPER
                    self.order_manager.register_order(order)
                    logger.info(f"PAPER: order {order.id} submitted at {order.limit_price:.2f}")
                    return {
                        "order_id": order.id,
                        "broker_order_id": broker_order_id,
                        "status": "PAPER",
                        "fill_price": order.limit_price,
                    }

                # Poll for fill
                fill_result = self._poll_for_fill(broker_order_id)

                if fill_result["filled"]:
                    self.order_manager.register_order(order)
                    self.order_manager.mark_filled(
                        order.id,
                        fill_result["fill_price"],
                        broker_order_id,
                    )
                    logger.info(
                        f"Spread filled: {structure.structure_type} "
                        f"@ {fill_result['fill_price']:.4f} "
                        f"(target was {base_credit:.4f})"
                    )
                    return {
                        "order_id": order.id,
                        "broker_order_id": broker_order_id,
                        "status": "FILLED",
                        "fill_price": fill_result["fill_price"],
                    }
                else:
                    # Cancel and retry at lower limit
                    self.client.cancel_order(broker_order_id)
                    logger.debug(
                        f"Order unfilled (attempt {attempt+1}), "
                        f"cancelling and retrying"
                    )
                    time.sleep(1)

            except Exception as e:
                logger.error(f"Order submission error (attempt {attempt+1}): {e}")
                time.sleep(2)

        logger.warning(f"Order not filled after {self.max_attempts} attempts")
        return {"status": "unfilled", "reason": "timeout_all_attempts"}

    # ------------------------------------------------------------------
    # Exit
    # ------------------------------------------------------------------

    def submit_spread_exit(
        self,
        original_order: Order,
        target_debit: float,
        reason: str = "management",
    ) -> Dict:
        """
        Close an existing spread position.

        Uses NET_DEBIT order type (buy to close).
        For profit-taking: target_debit = 50% of original credit
        For stop-loss: target_debit = 2x original credit

        Parameters
        ----------
        original_order : original filled order to reverse
        target_debit   : target price to pay to close (net debit per contract)
        reason         : why we are closing (for logging)
        """
        close_order = self.order_manager.build_close_order(
            original_order, target_debit, f"CLOSE_{reason}"
        )

        initial_debit = target_debit + self.TICK_SIZE  # start slightly above target (buy)

        for attempt in range(self.max_attempts):
            close_order.limit_price = round(initial_debit + attempt * self.TICK_SIZE, 2)
            spec = self.order_manager.to_schwab_spec(close_order)

            logger.info(
                f"Closing position ({reason}) "
                f"attempt={attempt+1} debit_limit={close_order.limit_price:.2f}"
            )

            try:
                result = self.client.place_order(spec)
                broker_order_id = result.get("order_id", "")

                if result.get("status") == "PAPER":
                    logger.info(f"PAPER close: {close_order.id}")
                    return {
                        "order_id": close_order.id,
                        "status": "PAPER",
                        "fill_price": close_order.limit_price,
                        "reason": reason,
                    }

                fill_result = self._poll_for_fill(broker_order_id)

                if fill_result["filled"]:
                    logger.info(
                        f"Position closed ({reason}) @ {fill_result['fill_price']:.4f}"
                    )
                    return {
                        "order_id": close_order.id,
                        "broker_order_id": broker_order_id,
                        "status": "FILLED",
                        "fill_price": fill_result["fill_price"],
                        "reason": reason,
                    }
                else:
                    self.client.cancel_order(broker_order_id)
                    time.sleep(1)

            except Exception as e:
                logger.error(f"Close order error (attempt {attempt+1}): {e}")
                time.sleep(2)

        logger.error(f"CRITICAL: Could not close position after {self.max_attempts} attempts!")
        return {"status": "failed", "reason": f"timeout_{reason}"}

    # ------------------------------------------------------------------
    # Fill polling
    # ------------------------------------------------------------------

    def _poll_for_fill(
        self, broker_order_id: str, timeout: Optional[int] = None
    ) -> Dict:
        """
        Poll Schwab API until order is filled or timeout reached.

        Returns {'filled': bool, 'fill_price': float}
        """
        if timeout is None:
            timeout = self.order_timeout_sec
        start = time.time()

        while time.time() - start < timeout:
            try:
                orders = self.client.get_orders()
                for ord_data in orders:
                    if str(ord_data.get("orderId", "")) == str(broker_order_id):
                        status = ord_data.get("status", "")
                        if status == "FILLED":
                            fill_price = float(
                                ord_data.get("price", 0) or
                                ord_data.get("filledPrice", 0)
                            )
                            return {"filled": True, "fill_price": fill_price}
                        elif status in ("CANCELLED", "REJECTED", "EXPIRED"):
                            return {"filled": False, "fill_price": 0}
            except Exception as e:
                logger.debug(f"Poll error: {e}")

            time.sleep(2)

        return {"filled": False, "fill_price": 0}

    # ------------------------------------------------------------------
    # Market hours check
    # ------------------------------------------------------------------

    @staticmethod
    def is_market_open() -> bool:
        """Basic market hours check (NY time)."""
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/New_York"))
        if now.weekday() >= 5:
            return False
        market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
        return market_open <= now <= market_close

    @staticmethod
    def minutes_to_close() -> int:
        """Minutes remaining until market close."""
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/New_York"))
        close = now.replace(hour=16, minute=0, second=0, microsecond=0)
        delta = close - now
        return max(0, int(delta.total_seconds() / 60))
