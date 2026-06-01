"""
Schwab API client wrapper built on top of schwab-py.

Provides a clean interface for:
  - Authentication and token management
  - Real-time and historical market data
  - Options chain fetching with full Greeks
  - Order placement and management
  - Account information
"""

from __future__ import annotations

import os
import json
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any

import pandas as pd
import numpy as np

from src.utils.logger import logger
from src.utils.helpers import retry


class SchwabClient:
    """
    Thin wrapper around schwab-py providing options-centric methods.

    Authentication
    --------------
    Uses OAuth2 via schwab-py. On first run, a browser window opens for
    Schwab login. Tokens are cached at `token_path` and auto-refreshed.

    Set the following environment variables (or Cursor Secrets):
      SCHWAB_API_KEY       - Developer app key
      SCHWAB_APP_SECRET    - Developer app secret
      SCHWAB_CALLBACK_URL  - Must match app registration (e.g. https://127.0.0.1:8182/)
      SCHWAB_TOKEN_PATH    - Path to store/load token JSON
      SCHWAB_ACCOUNT_NUMBER - Full account number
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        callback_url: Optional[str] = None,
        token_path: Optional[str] = None,
        account_number: Optional[str] = None,
        paper_mode: bool = False,
    ) -> None:
        self.api_key = api_key or os.environ.get("SCHWAB_API_KEY", "")
        self.app_secret = app_secret or os.environ.get("SCHWAB_APP_SECRET", "")
        self.callback_url = callback_url or os.environ.get(
            "SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182/"
        )
        self.token_path = token_path or os.environ.get(
            "SCHWAB_TOKEN_PATH", "./schwab_token.json"
        )
        self.account_number = account_number or os.environ.get(
            "SCHWAB_ACCOUNT_NUMBER", ""
        )
        self.paper_mode = paper_mode
        self._client = None
        self._account_hash: Optional[str] = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self) -> "SchwabClient":
        """
        Authenticate with Schwab and return self.

        On first call: opens browser for OAuth login.
        Subsequent calls: loads cached token and refreshes if needed.
        """
        try:
            import schwab
            from schwab import auth
        except ImportError:
            raise ImportError(
                "schwab-py not installed. Run: pip install schwab-py"
            )

        if not self.api_key or not self.app_secret:
            raise ValueError(
                "SCHWAB_API_KEY and SCHWAB_APP_SECRET must be set. "
                "Add them to your Cursor Secrets (cursor.com/dashboard)."
            )

        try:
            self._client = auth.client_from_token_file(
                token_path=self.token_path,
                api_key=self.api_key,
                app_secret=self.app_secret,
            )
            logger.info("Schwab: loaded cached token")
        except Exception:
            logger.info("Schwab: no cached token, starting OAuth flow")
            self._client = auth.client_from_login_flow(
                api_key=self.api_key,
                app_secret=self.app_secret,
                callback_url=self.callback_url,
                token_path=self.token_path,
            )
            logger.info("Schwab: OAuth login successful")

        self._account_hash = self._resolve_account_hash()
        logger.info(f"Schwab connected | account hash: {self._account_hash[:8]}...")
        return self

    def _resolve_account_hash(self) -> str:
        """Resolve account number to account hash required by newer Schwab API."""
        resp = self._client.get_account_numbers()
        if resp.status_code != 200:
            raise ConnectionError(f"Could not fetch account numbers: {resp.text}")
        accounts = resp.json()
        for acct in accounts:
            if self.account_number and acct.get("accountNumber") == self.account_number:
                return acct["hashValue"]
        if accounts:
            return accounts[0]["hashValue"]
        raise ValueError("No account found in Schwab response")

    @property
    def connected(self) -> bool:
        return self._client is not None

    def _ensure_connected(self) -> None:
        if not self.connected:
            raise RuntimeError("SchwabClient not connected. Call .connect() first.")

    # ------------------------------------------------------------------
    # Market Data
    # ------------------------------------------------------------------

    @retry(max_attempts=3, delay=1.0)
    def get_quote(self, symbol: str) -> Dict[str, Any]:
        """Fetch a real-time Level 1 quote for a symbol."""
        self._ensure_connected()
        resp = self._client.get_quote(symbol)
        if resp.status_code != 200:
            raise RuntimeError(f"Quote fetch failed for {symbol}: {resp.text}")
        data = resp.json()
        return data.get(symbol, {})

    @retry(max_attempts=3, delay=1.0)
    def get_quotes(self, symbols: List[str]) -> Dict[str, Any]:
        """Fetch real-time Level 1 quotes for multiple symbols."""
        self._ensure_connected()
        resp = self._client.get_quotes(symbols)
        if resp.status_code != 200:
            raise RuntimeError(f"Quotes fetch failed: {resp.text}")
        return resp.json()

    @retry(max_attempts=3, delay=1.0)
    def get_price_history(
        self,
        symbol: str,
        period_type: str = "year",
        period: int = 2,
        frequency_type: str = "daily",
        frequency: int = 1,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV price history.

        Returns a DataFrame with columns: open, high, low, close, volume, datetime
        """
        self._ensure_connected()
        import schwab
        resp = self._client.get_price_history(
            symbol=symbol,
            period_type=getattr(self._client.PriceHistory.PeriodType, period_type.upper()),
            period=getattr(self._client.PriceHistory.Period, f"_{period}"),
            frequency_type=getattr(
                self._client.PriceHistory.FrequencyType, frequency_type.upper()
            ),
            frequency=getattr(self._client.PriceHistory.Frequency, f"_{frequency}"),
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Price history fetch failed for {symbol}: {resp.text}")

        data = resp.json()
        candles = data.get("candles", [])
        if not candles:
            return pd.DataFrame()

        df = pd.DataFrame(candles)
        df["datetime"] = pd.to_datetime(df["datetime"], unit="ms")
        df.set_index("datetime", inplace=True)
        df = df[["open", "high", "low", "close", "volume"]]
        return df.sort_index()

    # ------------------------------------------------------------------
    # Options Chain
    # ------------------------------------------------------------------

    @retry(max_attempts=3, delay=2.0)
    def get_options_chain(
        self,
        symbol: str,
        contract_type: str = "ALL",
        strike_count: int = 20,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        include_quotes: bool = True,
    ) -> Dict[str, Any]:
        """
        Fetch a complete options chain with Greeks and IV.

        Returns the raw Schwab API JSON structure keyed by expiry → strike.
        """
        self._ensure_connected()
        import schwab

        kwargs: Dict[str, Any] = {
            "symbol": symbol,
            "contract_type": getattr(
                self._client.Options.ContractType, contract_type
            ),
            "strike_count": strike_count,
            "include_underlying_quote": include_quotes,
        }
        if from_date:
            kwargs["from_date"] = from_date
        if to_date:
            kwargs["to_date"] = to_date

        resp = self._client.get_option_chain(**kwargs)
        if resp.status_code != 200:
            raise RuntimeError(f"Options chain fetch failed for {symbol}: {resp.text}")
        return resp.json()

    def parse_options_chain_to_df(self, chain_json: Dict[str, Any]) -> pd.DataFrame:
        """
        Convert raw options chain JSON to a flat DataFrame.

        Columns include: symbol, expiry, dte, option_type, strike,
                         bid, ask, mid, last, volume, open_interest,
                         iv (impliedVolatility), delta, gamma, theta, vega, rho
        """
        records = []
        underlying_price = chain_json.get("underlyingPrice", np.nan)

        for exp_map_key in ("callExpDateMap", "putExpDateMap"):
            opt_type = "call" if "call" in exp_map_key else "put"
            exp_map = chain_json.get(exp_map_key, {})

            for exp_key, strikes in exp_map.items():
                # exp_key format: "2024-01-19:7" (date:DTE)
                parts = exp_key.split(":")
                try:
                    expiry = datetime.strptime(parts[0], "%Y-%m-%d").date()
                    dte = int(parts[1]) if len(parts) > 1 else (expiry - date.today()).days
                except ValueError:
                    continue

                for strike_str, opts in strikes.items():
                    for opt in opts:
                        records.append({
                            "symbol": opt.get("symbol", ""),
                            "underlying": chain_json.get("symbol", ""),
                            "underlying_price": underlying_price,
                            "expiry": expiry,
                            "dte": dte,
                            "option_type": opt_type,
                            "strike": float(strike_str),
                            "bid": opt.get("bid", np.nan),
                            "ask": opt.get("ask", np.nan),
                            "mid": (opt.get("bid", 0) + opt.get("ask", 0)) / 2,
                            "last": opt.get("last", np.nan),
                            "volume": opt.get("totalVolume", 0),
                            "open_interest": opt.get("openInterest", 0),
                            "iv": opt.get("volatility", np.nan) / 100
                            if opt.get("volatility") is not None else np.nan,
                            "delta": opt.get("delta", np.nan),
                            "gamma": opt.get("gamma", np.nan),
                            "theta": opt.get("theta", np.nan),
                            "vega": opt.get("vega", np.nan),
                            "rho": opt.get("rho", np.nan),
                            "theoretical_value": opt.get("theoreticalOptionValue", np.nan),
                            "intrinsic_value": opt.get("intrinsicValue", np.nan),
                            "time_value": opt.get("timeValue", np.nan),
                            "in_the_money": opt.get("inTheMoney", False),
                            "multiplier": opt.get("multiplier", 100),
                        })

        return pd.DataFrame(records)

    # ------------------------------------------------------------------
    # Account Information
    # ------------------------------------------------------------------

    @retry(max_attempts=3, delay=1.0)
    def get_account(self) -> Dict[str, Any]:
        """Fetch account details including balances and positions."""
        self._ensure_connected()
        resp = self._client.get_account(
            account_hash=self._account_hash,
            fields=[self._client.Account.Fields.POSITIONS],
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Account fetch failed: {resp.text}")
        return resp.json()

    def get_account_balance(self) -> float:
        """Return net liquidation value of the account."""
        acct = self.get_account()
        return float(
            acct.get("securitiesAccount", {})
            .get("currentBalances", {})
            .get("liquidationValue", 0)
        )

    def get_buying_power(self) -> float:
        """Return options buying power."""
        acct = self.get_account()
        balances = acct.get("securitiesAccount", {}).get("currentBalances", {})
        return float(balances.get("optionBuyingPower", balances.get("buyingPower", 0)))

    def get_positions(self) -> pd.DataFrame:
        """Return open positions as a DataFrame."""
        acct = self.get_account()
        positions = (
            acct.get("securitiesAccount", {}).get("positions", [])
        )
        if not positions:
            return pd.DataFrame()
        records = []
        for pos in positions:
            instrument = pos.get("instrument", {})
            records.append({
                "symbol": instrument.get("symbol", ""),
                "asset_type": instrument.get("assetType", ""),
                "quantity": pos.get("longQuantity", 0) - pos.get("shortQuantity", 0),
                "average_price": pos.get("averagePrice", np.nan),
                "market_value": pos.get("marketValue", np.nan),
                "unrealized_pnl": pos.get("currentDayProfitLoss", np.nan),
                "cost_basis": pos.get("averagePrice", 0)
                * abs(pos.get("longQuantity", 0) - pos.get("shortQuantity", 0))
                * 100,
            })
        return pd.DataFrame(records)

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    @retry(max_attempts=3, delay=2.0)
    def place_order(self, order_spec: Dict[str, Any]) -> Dict[str, Any]:
        """
        Place an order via the Schwab API.

        Parameters
        ----------
        order_spec : Schwab order specification dict (built by OrderManager)

        Returns
        -------
        Response dict with order_id and status
        """
        self._ensure_connected()
        if self.paper_mode:
            logger.warning(f"PAPER MODE: would place order: {json.dumps(order_spec, default=str)}")
            return {"order_id": "PAPER_ORDER", "status": "PAPER"}

        resp = self._client.place_order(
            account_hash=self._account_hash,
            order_spec=order_spec,
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Order placement failed: {resp.text}")

        location = resp.headers.get("Location", "")
        order_id = location.split("/")[-1] if location else "unknown"
        logger.info(f"Order placed: {order_id}")
        return {"order_id": order_id, "status": "PLACED"}

    @retry(max_attempts=3, delay=1.0)
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        self._ensure_connected()
        resp = self._client.cancel_order(
            account_hash=self._account_hash,
            order_id=order_id,
        )
        return resp.status_code in (200, 201)

    @retry(max_attempts=3, delay=1.0)
    def get_orders(
        self,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return list of orders."""
        self._ensure_connected()
        kwargs: Dict[str, Any] = {"account_hash": self._account_hash}
        if from_date:
            kwargs["from_entered_datetime"] = from_date
        if to_date:
            kwargs["to_entered_datetime"] = to_date
        if status:
            kwargs["status"] = status

        resp = self._client.get_orders_for_account(**kwargs)
        if resp.status_code != 200:
            raise RuntimeError(f"Orders fetch failed: {resp.text}")
        return resp.json() or []

    # ------------------------------------------------------------------
    # Streaming Factory
    # ------------------------------------------------------------------

    def create_stream_client(self):
        """Return a schwab StreamClient for real-time data."""
        self._ensure_connected()
        import schwab
        return schwab.streaming.StreamClient(
            client=self._client,
            account_id=int(self.account_number) if self.account_number.isdigit() else 0,
        )
