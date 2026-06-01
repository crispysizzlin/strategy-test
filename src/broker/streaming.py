"""
Real-time streaming data manager using Schwab's WebSocket API (via schwab-py).

Provides:
  - Level 1 equity/options quotes
  - Level 2 options order book (bid/ask depth)
  - Account activity streaming
  - Thread-safe data store for the main engine to read
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from src.utils.logger import logger


class Level2Snapshot:
    """In-memory snapshot of the Level 2 order book for a single symbol."""

    def __init__(self, symbol: str, max_history: int = 100) -> None:
        self.symbol = symbol
        self.bids: List[tuple] = []    # [(price, size), ...]
        self.asks: List[tuple] = []    # [(price, size), ...]
        self.timestamp: float = 0.0
        self._bid_history: deque = deque(maxlen=max_history)
        self._ask_history: deque = deque(maxlen=max_history)

    def update(self, bids: List[tuple], asks: List[tuple], timestamp: float) -> None:
        self.bids = sorted(bids, key=lambda x: -x[0])  # descending by price
        self.asks = sorted(asks, key=lambda x: x[0])   # ascending by price
        self.timestamp = timestamp
        if bids:
            self._bid_history.append((timestamp, sum(s for _, s in bids)))
        if asks:
            self._ask_history.append((timestamp, sum(s for _, s in asks)))

    @property
    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else np.nan

    @property
    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else np.nan

    @property
    def spread(self) -> float:
        if self.bids and self.asks:
            return self.best_ask - self.best_bid
        return np.nan

    @property
    def mid(self) -> float:
        if self.bids and self.asks:
            return (self.best_bid + self.best_ask) / 2
        return np.nan

    @property
    def bid_depth(self) -> float:
        """Total bid-side size (first 5 levels)."""
        return sum(s for _, s in self.bids[:5])

    @property
    def ask_depth(self) -> float:
        """Total ask-side size (first 5 levels)."""
        return sum(s for _, s in self.asks[:5])

    @property
    def order_flow_imbalance(self) -> float:
        """
        Order Flow Imbalance (OFI):
          OFI = (bid_depth - ask_depth) / (bid_depth + ask_depth)
          Range: [-1, +1]
          Positive → buying pressure, Negative → selling pressure
        """
        total = self.bid_depth + self.ask_depth
        if total == 0:
            return 0.0
        return (self.bid_depth - self.ask_depth) / total

    @property
    def vwap_bid(self) -> float:
        """Volume-weighted average bid price (first 5 levels)."""
        bids = self.bids[:5]
        total_size = sum(s for _, s in bids)
        if total_size == 0:
            return np.nan
        return sum(p * s for p, s in bids) / total_size

    @property
    def vwap_ask(self) -> float:
        """Volume-weighted average ask price (first 5 levels)."""
        asks = self.asks[:5]
        total_size = sum(s for _, s in asks)
        if total_size == 0:
            return np.nan
        return sum(p * s for p, s in asks) / total_size


class StreamingManager:
    """
    Manages real-time data streaming from Schwab.

    Usage (async context):
        manager = StreamingManager(schwab_client)
        await manager.start(symbols=["SPY", "SPX"])
        snapshot = manager.get_level2("SPY")
    """

    def __init__(self, schwab_client, callback: Optional[Callable] = None) -> None:
        self._client = schwab_client
        self._external_callback = callback
        self._stream_client = None
        self._level2: Dict[str, Level2Snapshot] = {}
        self._quotes: Dict[str, Dict] = {}
        self._running = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_options_book(self, msg: Dict[str, Any]) -> None:
        """Parse a Level 2 options book message."""
        content = msg.get("content", [])
        for item in content:
            symbol = item.get("key", "")
            if not symbol:
                continue
            bids = [
                (float(entry.get("0", 0)), float(entry.get("1", 0)))
                for entry in item.get("1", [])
            ]
            asks = [
                (float(entry.get("0", 0)), float(entry.get("1", 0)))
                for entry in item.get("2", [])
            ]
            ts = float(item.get("3", 0))

            with self._lock:
                if symbol not in self._level2:
                    self._level2[symbol] = Level2Snapshot(symbol)
                self._level2[symbol].update(bids, asks, ts)

        if self._external_callback:
            self._external_callback("options_book", msg)

    def _handle_level1_option(self, msg: Dict[str, Any]) -> None:
        """Parse Level 1 options quotes."""
        content = msg.get("content", [])
        for item in content:
            symbol = item.get("key", "")
            if symbol:
                with self._lock:
                    self._quotes[symbol] = item

        if self._external_callback:
            self._external_callback("level1_option", msg)

    def _handle_level1_equity(self, msg: Dict[str, Any]) -> None:
        """Parse Level 1 equity quotes."""
        content = msg.get("content", [])
        for item in content:
            symbol = item.get("key", "")
            if symbol:
                with self._lock:
                    self._quotes[symbol] = item

        if self._external_callback:
            self._external_callback("level1_equity", msg)

    def _handle_account_activity(self, msg: Dict[str, Any]) -> None:
        """Handle account activity (fills, cancels)."""
        logger.info(f"Account activity: {msg}")
        if self._external_callback:
            self._external_callback("account_activity", msg)

    # ------------------------------------------------------------------
    # Async streaming loop
    # ------------------------------------------------------------------

    async def start(
        self,
        equity_symbols: List[str] | None = None,
        option_symbols: List[str] | None = None,
    ) -> None:
        """
        Start all streaming subscriptions.

        Parameters
        ----------
        equity_symbols  : list of equity/ETF symbols (e.g. ["SPY", "QQQ"])
        option_symbols  : list of OCC option symbols for Level 2 order book
        """
        self._stream_client = self._client.create_stream_client()
        await self._stream_client.login()
        self._running = True

        # Account activity
        self._stream_client.add_account_activity_handler(self._handle_account_activity)
        await self._stream_client.account_activity_sub()

        # Level 1 equity quotes
        if equity_symbols:
            self._stream_client.add_level_one_equity_handler(self._handle_level1_equity)
            await self._stream_client.level_one_equity_subs(equity_symbols)
            logger.info(f"Streaming L1 equity: {equity_symbols}")

        # Level 1 options quotes (Greeks, IV)
        if option_symbols:
            self._stream_client.add_level_one_option_handler(self._handle_level1_option)
            await self._stream_client.level_one_option_subs(option_symbols)

            # Level 2 options order book
            self._stream_client.add_options_book_handler(self._handle_options_book)
            await self._stream_client.options_book_subs(option_symbols)
            logger.info(f"Streaming L2 options book: {len(option_symbols)} symbols")

        logger.info("Streaming started")

        while self._running:
            await self._stream_client.handle_message()

    async def stop(self) -> None:
        """Gracefully stop streaming."""
        self._running = False
        if self._stream_client:
            await self._stream_client.logout()
        logger.info("Streaming stopped")

    def start_in_thread(
        self,
        equity_symbols: List[str] | None = None,
        option_symbols: List[str] | None = None,
    ) -> threading.Thread:
        """Launch streaming in a background thread."""
        loop = asyncio.new_event_loop()

        def _run():
            loop.run_until_complete(
                self.start(equity_symbols=equity_symbols, option_symbols=option_symbols)
            )

        thread = threading.Thread(target=_run, daemon=True, name="avrpe-stream")
        thread.start()
        return thread

    # ------------------------------------------------------------------
    # Data access (thread-safe)
    # ------------------------------------------------------------------

    def get_level2(self, symbol: str) -> Optional[Level2Snapshot]:
        with self._lock:
            return self._level2.get(symbol)

    def get_quote(self, symbol: str) -> Optional[Dict]:
        with self._lock:
            return self._quotes.get(symbol)

    def get_all_quotes(self) -> Dict[str, Dict]:
        with self._lock:
            return dict(self._quotes)

    def subscribe_options(self, symbols: List[str]) -> None:
        """Dynamically add option symbols to the Level 2 feed."""
        if self._stream_client and self._running:
            asyncio.run_coroutine_threadsafe(
                self._stream_client.options_book_add(symbols),
                asyncio.get_event_loop(),
            )
            logger.debug(f"Added {len(symbols)} options to L2 stream")

    def unsubscribe_options(self, symbols: List[str]) -> None:
        """Remove option symbols from the Level 2 feed."""
        if self._stream_client and self._running:
            asyncio.run_coroutine_threadsafe(
                self._stream_client.options_book_unsubs(symbols),
                asyncio.get_event_loop(),
            )
