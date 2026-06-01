"""
Charles Schwab API orchestration layer for RG-VRP-L2.

Requires schwab-py when live trading is enabled:
  pip install schwab-py

Environment:
  SCHWAB_APP_KEY, SCHWAB_APP_SECRET, SCHWAB_CALLBACK_URL, SCHWAB_ACCOUNT_HASH
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from rg_vrp_l2.strategy_engine import StrategyEngine, TradeDecision

logger = logging.getLogger(__name__)


@dataclass
class MarketSnapshot:
    underlying: str
    last_price: float
    bid: float
    ask: float
    implied_vol_30d: float
    implied_vol_7d: float | None
    chain: list[dict[str, Any]]


class SchwabOrchestrator:
    """
    Live orchestration wrapper. Separates broker I/O from strategy logic.

    In dry-run mode (default), logs decisions without placing orders.
    """

    def __init__(
        self,
        engine: StrategyEngine,
        dry_run: bool = True,
        token_path: str = ".schwab_token.json",
    ) -> None:
        self.engine = engine
        self.dry_run = dry_run
        self.token_path = token_path
        self._client = None

    def connect(self) -> bool:
        """Initialize Schwab client if credentials present."""
        if not os.environ.get("SCHWAB_APP_KEY"):
            logger.warning("Schwab credentials not set; running in signal-only mode")
            return False
        try:
            from schwab.auth import easy_client  # type: ignore

            self._client = easy_client(
                api_key=os.environ["SCHWAB_APP_KEY"],
                app_secret=os.environ["SCHWAB_APP_SECRET"],
                callback_url=os.environ.get(
                    "SCHWAB_CALLBACK_URL", "https://127.0.0.1:8182"
                ),
                token_path=self.token_path,
            )
            return True
        except ImportError:
            logger.warning("schwab-py not installed; pip install schwab-py for live")
            return False
        except Exception as e:
            logger.error("Schwab connect failed: %s", e)
            return False

    def get_account_equity(self) -> float:
        if self._client is None:
            return float(self.engine.equity)
        # Placeholder: implement account hash lookup from user preferences
        return float(self.engine.equity)

    def build_iron_condor_order(
        self,
        decision: TradeDecision,
        put_short_strike: float,
        put_long_strike: float,
        call_short_strike: float,
        call_long_strike: float,
        expiration: str,
    ) -> dict[str, Any]:
        """Build Schwab multi-leg NET_CREDIT order payload."""
        qty = decision.contracts
        legs = [
            {
                "instruction": "SELL_TO_OPEN",
                "quantity": qty,
                "instrument": {
                    "symbol": self._occ_symbol(
                        decision.underlying, expiration, "P", put_short_strike
                    ),
                    "assetType": "OPTION",
                },
            },
            {
                "instruction": "BUY_TO_OPEN",
                "quantity": qty,
                "instrument": {
                    "symbol": self._occ_symbol(
                        decision.underlying, expiration, "P", put_long_strike
                    ),
                    "assetType": "OPTION",
                },
            },
            {
                "instruction": "SELL_TO_OPEN",
                "quantity": qty,
                "instrument": {
                    "symbol": self._occ_symbol(
                        decision.underlying, expiration, "C", call_short_strike
                    ),
                    "assetType": "OPTION",
                },
            },
            {
                "instruction": "BUY_TO_OPEN",
                "quantity": qty,
                "instrument": {
                    "symbol": self._occ_symbol(
                        decision.underlying, expiration, "C", call_long_strike
                    ),
                    "assetType": "OPTION",
                },
            },
        ]
        return {
            "orderType": "NET_CREDIT",
            "session": "NORMAL",
            "duration": "DAY",
            "orderStrategyType": "SINGLE",
            "complexOrderStrategyType": "IRON_CONDOR",
            "orderLegCollection": legs,
        }

    @staticmethod
    def _occ_symbol(
        underlying: str, expiration: str, right: str, strike: float
    ) -> str:
        """Simplified OCC placeholder — production uses chain strike symbols."""
        exp = expiration.replace("-", "")[2:8]
        strike_int = int(strike * 1000)
        return f"{underlying:6s}{exp}{right}{strike_int:08d}".replace(" ", "")

    def execute_decision(self, decision: TradeDecision, order: dict | None = None) -> None:
        if decision.action in ("SKIP", "HOLD"):
            logger.info("Decision %s: %s", decision.action, decision.rationale)
            return
        if decision.action == "CLOSE_ALL":
            logger.warning("CLOSE_ALL: %s", decision.rationale)
            if not self.dry_run and self._client:
                self._close_short_vol_positions()
            return
        if decision.action == "ENTER":
            logger.info(
                "ENTER %s %s x%d @ delta %.2f — %s",
                decision.structure.value,
                decision.underlying,
                decision.contracts,
                decision.short_delta,
                decision.rationale,
            )
            if self.dry_run or self._client is None:
                return
            account = os.environ.get("SCHWAB_ACCOUNT_HASH")
            if account and order:
                self._client.place_order(account, order)

    def _close_short_vol_positions(self) -> None:
        """Close all short-vol structures — implement via get_orders + close legs."""
        logger.info("Would close short-vol positions via opposing orders")

    def run_cycle(self, snapshot: MarketSnapshot, **kwargs) -> TradeDecision:
        """Single evaluation cycle from market snapshot."""
        import pandas as pd
        import yfinance as yf

        hist = yf.download(
            snapshot.underlying,
            period="1y",
            progress=False,
            auto_adjust=True,
        )
        close = hist["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]

        from rg_vrp_l2.inventory_skew import PortfolioGreeks

        decision = self.engine.evaluate_entry(
            underlying=snapshot.underlying,
            close_prices=close,
            implied_vol_30d=snapshot.implied_vol_30d,
            implied_vol_7d=snapshot.implied_vol_7d,
            iv_history=None,
            portfolio_greeks=kwargs.get(
                "portfolio_greeks",
                PortfolioGreeks(net_delta=0, net_vega=0, net_gamma=0),
            ),
            open_position_count=kwargs.get("open_position_count", 0),
            book=kwargs.get("book"),
            quote_bid=snapshot.bid,
            quote_ask=snapshot.ask,
            credit_per_contract=kwargs.get("credit_per_contract", 100),
            max_loss_per_contract=kwargs.get("max_loss_per_contract", 400),
            index_iv=kwargs.get("index_iv"),
            component_ivs=kwargs.get("component_ivs"),
        )
        self.execute_decision(decision)
        return decision
