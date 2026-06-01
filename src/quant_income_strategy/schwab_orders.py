"""Schwab order-payload helpers for selected candidate trades."""

from __future__ import annotations

from .models import CandidateTrade, SpreadLeg


def build_opening_order(candidate: CandidateTrade) -> dict:
    """Build a Schwab Trader API multi-leg NET_CREDIT order payload.

    The caller is responsible for submitting this body to
    `/trader/v1/accounts/{accountHash}/orders` after authentication and final
    human/broker compliance checks.
    """

    return {
        "orderType": "NET_CREDIT",
        "session": "NORMAL",
        "price": candidate.order_price,
        "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "complexOrderStrategyType": _complex_strategy(candidate),
        "orderLegCollection": [
            _order_leg(leg, candidate.contracts) for leg in candidate.legs
        ],
    }


def build_profit_taking_close_order(
    candidate: CandidateTrade,
    profit_capture: float = 0.50,
) -> dict:
    """Build a conservative GTC buy-to-close order after entry is filled."""

    if not 0.0 < profit_capture < 1.0:
        raise ValueError("profit_capture must be between 0 and 1")
    debit = max(0.01, candidate.net_credit * (1.0 - profit_capture))
    return {
        "orderType": "NET_DEBIT",
        "session": "NORMAL",
        "price": f"{debit:.2f}",
        "duration": "GOOD_TILL_CANCEL",
        "orderStrategyType": "SINGLE",
        "complexOrderStrategyType": _complex_strategy(candidate),
        "orderLegCollection": [
            _closing_order_leg(leg, candidate.contracts) for leg in candidate.legs
        ],
    }


def _complex_strategy(candidate: CandidateTrade) -> str:
    if candidate.strategy_type == "IRON_CONDOR":
        return "IRON_CONDOR"
    return "VERTICAL"


def _order_leg(leg: SpreadLeg, quantity: int) -> dict:
    return {
        "instruction": leg.instruction,
        "quantity": quantity,
        "instrument": {
            "symbol": leg.quote.option_symbol,
            "assetType": "OPTION",
        },
    }


def _closing_order_leg(leg: SpreadLeg, quantity: int) -> dict:
    instruction = "BUY_TO_CLOSE" if leg.instruction == "SELL_TO_OPEN" else "SELL_TO_CLOSE"
    return {
        "instruction": instruction,
        "quantity": quantity,
        "instrument": {
            "symbol": leg.quote.option_symbol,
            "assetType": "OPTION",
        },
    }
