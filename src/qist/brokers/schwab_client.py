"""Charles Schwab Trader API client (REST).

Implements the OAuth2 (authorization-code + refresh) flow and the market-data /
trading endpoints we need:

* market data:  ``/marketdata/v1/quotes``, ``/marketdata/v1/{symbol}/pricehistory``,
                ``/marketdata/v1/chains``, ``/marketdata/v1/markets``
* trading:      ``/trader/v1/accounts``, ``.../orders`` (place / preview / cancel)

Auth model (per developer.schwab.com):
  1. Redirect a user to the consent URL with your app key; receive a ``code``.
  2. Exchange ``code`` (+ Basic app-key:secret) for access + refresh tokens.
  3. Access token lives ~30 min; refresh token ~7 days.  We auto-refresh.

This client never hard-codes secrets - they come from config / environment.  It
is written to be testable: every HTTP call goes through ``_request`` which can be
mocked, and parsing is isolated in pure functions.

NOTE: Live trading requires a funded, options-approved Schwab account and an
approved developer app.  Run in paper / backtest mode until you have verified
behaviour end-to-end.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

import requests

from .models import (Instruction, MultiLegOrder, OptionContract, OptionType,
                     Quote)

API_BASE = "https://api.schwabapi.com"
TOKEN_URL = f"{API_BASE}/v1/oauth/token"
AUTH_URL = f"{API_BASE}/v1/oauth/authorize"
MARKETDATA = f"{API_BASE}/marketdata/v1"
TRADER = f"{API_BASE}/trader/v1"


@dataclass
class SchwabTokens:
    access_token: str
    refresh_token: str
    expires_at: float        # epoch seconds

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at - 60  # refresh 60s early


class SchwabClient:
    def __init__(self, app_key: str, app_secret: str, redirect_uri: str,
                 tokens: Optional[SchwabTokens] = None,
                 session: Optional[requests.Session] = None,
                 timeout: float = 15.0) -> None:
        self.app_key = app_key
        self.app_secret = app_secret
        self.redirect_uri = redirect_uri
        self.tokens = tokens
        self._session = session or requests.Session()
        self.timeout = timeout

    # ---- OAuth ----------------------------------------------------------
    def authorization_url(self, scope: str = "readonly") -> str:
        return (f"{AUTH_URL}?client_id={self.app_key}"
                f"&redirect_uri={self.redirect_uri}&response_type=code&scope={scope}")

    def _basic_auth_header(self) -> dict[str, str]:
        raw = f"{self.app_key}:{self.app_secret}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode(),
                "Content-Type": "application/x-www-form-urlencoded"}

    def exchange_code(self, code: str) -> SchwabTokens:
        resp = self._session.post(TOKEN_URL, headers=self._basic_auth_header(),
                                  data={"grant_type": "authorization_code",
                                        "code": code,
                                        "redirect_uri": self.redirect_uri},
                                  timeout=self.timeout)
        resp.raise_for_status()
        self.tokens = self._parse_token_payload(resp.json())
        return self.tokens

    def refresh(self) -> SchwabTokens:
        if not self.tokens:
            raise RuntimeError("No tokens to refresh; run the auth flow first")
        resp = self._session.post(TOKEN_URL, headers=self._basic_auth_header(),
                                  data={"grant_type": "refresh_token",
                                        "refresh_token": self.tokens.refresh_token},
                                  timeout=self.timeout)
        resp.raise_for_status()
        payload = resp.json()
        payload.setdefault("refresh_token", self.tokens.refresh_token)
        self.tokens = self._parse_token_payload(payload)
        return self.tokens

    @staticmethod
    def _parse_token_payload(payload: dict[str, Any]) -> SchwabTokens:
        return SchwabTokens(
            access_token=payload["access_token"],
            refresh_token=payload["refresh_token"],
            expires_at=time.time() + float(payload.get("expires_in", 1800)),
        )

    # ---- HTTP core ------------------------------------------------------
    def _auth_header(self) -> dict[str, str]:
        if not self.tokens:
            raise RuntimeError("Not authenticated")
        if self.tokens.expired:
            self.refresh()
        return {"Authorization": f"Bearer {self.tokens.access_token}",
                "Accept": "application/json"}

    def _request(self, method: str, url: str, **kwargs) -> Any:
        headers = kwargs.pop("headers", {})
        headers.update(self._auth_header())
        resp = self._session.request(method, url, headers=headers,
                                     timeout=self.timeout, **kwargs)
        resp.raise_for_status()
        if resp.content and resp.headers.get("content-type", "").startswith("application/json"):
            return resp.json()
        return resp.text

    # ---- Market data ----------------------------------------------------
    def get_quotes(self, symbols: list[str]) -> dict[str, Quote]:
        data = self._request("GET", f"{MARKETDATA}/quotes",
                             params={"symbols": ",".join(symbols)})
        return parse_quotes(data)

    def get_price_history(self, symbol: str, period_type: str = "year",
                          period: int = 2, frequency_type: str = "daily",
                          frequency: int = 1) -> list[dict[str, Any]]:
        data = self._request("GET", f"{MARKETDATA}/pricehistory",
                             params={"symbol": symbol, "periodType": period_type,
                                     "period": period, "frequencyType": frequency_type,
                                     "frequency": frequency})
        return data.get("candles", [])

    def get_option_chain(self, symbol: str, contract_type: str = "ALL",
                         strike_count: int = 30, from_date: Optional[date] = None,
                         to_date: Optional[date] = None,
                         strategy: str = "SINGLE") -> dict[str, Any]:
        params: dict[str, Any] = {"symbol": symbol, "contractType": contract_type,
                                  "strikeCount": strike_count, "strategy": strategy}
        if from_date:
            params["fromDate"] = from_date.isoformat()
        if to_date:
            params["toDate"] = to_date.isoformat()
        return self._request("GET", f"{MARKETDATA}/chains", params=params)

    # ---- Trading --------------------------------------------------------
    def get_account_numbers(self) -> list[dict[str, str]]:
        return self._request("GET", f"{TRADER}/accounts/accountNumbers")

    def get_account(self, account_hash: str, fields: str = "positions") -> dict[str, Any]:
        return self._request("GET", f"{TRADER}/accounts/{account_hash}",
                             params={"fields": fields})

    def place_order(self, account_hash: str, order: MultiLegOrder) -> dict[str, Any]:
        payload = build_schwab_order_payload(order)
        return self._request("POST", f"{TRADER}/accounts/{account_hash}/orders",
                             json=payload)

    def preview_order(self, account_hash: str, order: MultiLegOrder) -> dict[str, Any]:
        payload = build_schwab_order_payload(order)
        return self._request("POST", f"{TRADER}/accounts/{account_hash}/previewOrder",
                             json=payload)

    def cancel_order(self, account_hash: str, order_id: str) -> Any:
        return self._request("DELETE", f"{TRADER}/accounts/{account_hash}/orders/{order_id}")

    def get_orders(self, account_hash: str, **params) -> list[dict[str, Any]]:
        return self._request("GET", f"{TRADER}/accounts/{account_hash}/orders",
                             params=params)


# ---- pure parsing / building helpers (unit-tested) ----------------------
def parse_quotes(data: dict[str, Any]) -> dict[str, Quote]:
    """Parse Schwab ``/quotes`` payload into our Quote model.

    Schwab nests data under ``{symbol: {quote: {...}, ...}}`` with equities and
    options exposing slightly different field sets; we read defensively.
    """
    out: dict[str, Quote] = {}
    for symbol, blob in data.items():
        if not isinstance(blob, dict):
            continue
        q = blob.get("quote", blob)
        out[symbol] = Quote(
            symbol=symbol,
            bid=float(q.get("bidPrice", q.get("bid", 0.0)) or 0.0),
            ask=float(q.get("askPrice", q.get("ask", 0.0)) or 0.0),
            last=float(q.get("lastPrice", q.get("last", float("nan"))) or float("nan")),
            bid_size=int(q.get("bidSize", 0) or 0),
            ask_size=int(q.get("askSize", 0) or 0),
            volume=int(q.get("totalVolume", 0) or 0),
            open_interest=int(q.get("openInterest", 0) or 0),
            implied_vol=float(q.get("volatility", float("nan")) or float("nan")),
            delta=float(q.get("delta", float("nan")) or float("nan")),
            gamma=float(q.get("gamma", float("nan")) or float("nan")),
            theta=float(q.get("theta", float("nan")) or float("nan")),
            vega=float(q.get("vega", float("nan")) or float("nan")),
            underlying_price=float(q.get("underlyingPrice", float("nan")) or float("nan")),
        )
    return out


_INSTRUCTION_MAP = {
    Instruction.BUY_TO_OPEN: "BUY_TO_OPEN",
    Instruction.SELL_TO_OPEN: "SELL_TO_OPEN",
    Instruction.BUY_TO_CLOSE: "BUY_TO_CLOSE",
    Instruction.SELL_TO_CLOSE: "SELL_TO_CLOSE",
}


def build_schwab_order_payload(order: MultiLegOrder) -> dict[str, Any]:
    """Build the Schwab JSON for a (possibly multi-leg) option order.

    Uses ``orderStrategyType=SINGLE`` with a ``complexOrderStrategyType`` and one
    leg per option, which is how Schwab represents verticals / condors / strangles.
    """
    legs = []
    for leg in order.legs:
        legs.append({
            "instruction": _INSTRUCTION_MAP[leg.instruction],
            "quantity": leg.quantity,
            "instrument": {
                "symbol": leg.contract.osi_symbol,
                "assetType": "OPTION",
            },
        })
    complex_type = _infer_complex_strategy(order)
    payload: dict[str, Any] = {
        "orderType": order.order_type,
        "session": order.session,
        "duration": order.duration,
        "orderStrategyType": "SINGLE",
        "complexOrderStrategyType": complex_type,
        "orderLegCollection": legs,
    }
    if order.order_type in ("NET_CREDIT", "NET_DEBIT", "LIMIT"):
        payload["price"] = round(abs(order.net_price), 2)
    return payload


def _infer_complex_strategy(order: MultiLegOrder) -> str:
    n = len(order.legs)
    if n == 1:
        return "NONE"
    if n == 2:
        types = {leg.contract.option_type for leg in order.legs}
        return "VERTICAL" if len(types) == 1 else "STRANGLE"
    if n == 4:
        return "IRON_CONDOR"
    return "CUSTOM"


def parse_iso_expiry(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()
