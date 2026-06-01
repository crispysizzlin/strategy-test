"""Charles Schwab streaming API (WebSocket) - Level 1/2 market data.

The streamer delivers push-based quotes with no hard line limit for options.
We subscribe to:

* ``LEVELONE_OPTIONS``  - real-time option bid/ask/greeks/IV
* ``LEVELONE_EQUITIES`` - underlying quotes
* ``NASDAQ_BOOK`` / ``NYSE_BOOK`` - full Level 2 depth (the order book)
* ``CHART_EQUITY``      - minute OHLCV

The streamer requires the streamer credentials returned by
``GET /trader/v1/userPreference``.  This module focuses on building the correct
subscription/login JSON envelopes and parsing book messages into our
``OrderBook`` model; the transport (websocket-client) is injected so it can be
unit-tested without a live socket.

Reference: developer.schwab.com streaming docs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .models import BookLevel, OrderBook


@dataclass
class StreamerInfo:
    """From /trader/v1/userPreference -> streamerInfo[0]."""
    socket_url: str
    customer_id: str          # schwabClientCustomerId
    correl_id: str            # schwabClientCorrelId
    channel: str
    function_id: str


@dataclass
class SchwabStreamer:
    streamer: StreamerInfo
    access_token: str
    send: Callable[[str], None]                # injected websocket send
    on_book: Optional[Callable[[OrderBook], None]] = None
    on_option: Optional[Callable[[dict], None]] = None
    _request_id: int = field(default=0, init=False)

    def _next_id(self) -> str:
        self._request_id += 1
        return str(self._request_id)

    def _envelope(self, service: str, command: str, params: dict[str, Any]) -> str:
        return json.dumps({
            "requests": [{
                "service": service,
                "command": command,
                "requestid": self._next_id(),
                "SchwabClientCustomerId": self.streamer.customer_id,
                "SchwabClientCorrelId": self.streamer.correl_id,
                "parameters": params,
            }]
        })

    def login(self) -> None:
        self.send(self._envelope("ADMIN", "LOGIN", {
            "Authorization": self.access_token,
            "SchwabClientChannel": self.streamer.channel,
            "SchwabClientFunctionId": self.streamer.function_id,
        }))

    def subscribe_options(self, option_symbols: list[str]) -> None:
        self.send(self._envelope("LEVELONE_OPTIONS", "SUBS", {
            "keys": ",".join(option_symbols),
            # fields: symbol, bid, ask, last, delta, gamma, theta, vega, IV, OI...
            "fields": "0,2,3,4,8,10,20,21,28,29,30,31,32",
        }))

    def subscribe_book(self, symbols: list[str], venue: str = "NASDAQ_BOOK") -> None:
        self.send(self._envelope(venue, "SUBS", {
            "keys": ",".join(symbols),
            "fields": "0,1,2,3",   # symbol, book time, bid side, ask side
        }))

    def subscribe_equity_quotes(self, symbols: list[str]) -> None:
        self.send(self._envelope("LEVELONE_EQUITIES", "SUBS", {
            "keys": ",".join(symbols),
            "fields": "0,1,2,3,4,5,8",
        }))

    # ---- message handling ----------------------------------------------
    def handle_message(self, raw: str) -> None:
        msg = json.loads(raw)
        for item in msg.get("data", []):
            service = item.get("service")
            for content in item.get("content", []):
                if service in ("NASDAQ_BOOK", "NYSE_BOOK", "OPTIONS_BOOK"):
                    book = parse_book_content(content, item.get("timestamp", 0))
                    if self.on_book:
                        self.on_book(book)
                elif service == "LEVELONE_OPTIONS" and self.on_option:
                    self.on_option(content)


def parse_book_content(content: dict[str, Any], ts: int = 0) -> OrderBook:
    """Parse a Schwab book content node into an OrderBook.

    Book messages use field "1" = book time, "2" = bid levels, "3" = ask levels.
    Each level: {"0": price, "1": total size, "2": num market makers, "3":[...]}.
    """
    symbol = content.get("key", "")

    def _levels(node: Any) -> list[BookLevel]:
        out: list[BookLevel] = []
        for lvl in node or []:
            out.append(BookLevel(
                price=float(lvl.get("0", 0.0)),
                size=int(lvl.get("1", 0)),
                num_orders=int(lvl.get("2", 0)),
            ))
        return out

    bids = _levels(content.get("2"))
    asks = _levels(content.get("3"))
    bids.sort(key=lambda x: x.price, reverse=True)
    asks.sort(key=lambda x: x.price)
    return OrderBook(symbol=symbol, bids=bids, asks=asks,
                     timestamp_ms=int(content.get("1", ts) or ts))
