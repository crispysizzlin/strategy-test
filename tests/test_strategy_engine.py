import unittest

from quant_income_strategy import OptionQuote, StrategyConfig, StrategyEngine, UnderlyingSnapshot
from quant_income_strategy.schwab_orders import (
    build_opening_order,
    build_profit_taking_close_order,
)


class StrategyEngineTest(unittest.TestCase):
    def test_engine_selects_defined_risk_candidates(self) -> None:
        engine = StrategyEngine(StrategyConfig(account_equity=20_000.0))

        trades = engine.find_trades([_sample_snapshot()])

        self.assertTrue(trades)
        self.assertGreaterEqual(trades[0].contracts, 1)
        self.assertLessEqual(trades[0].risk_dollars, 20_000.0 * 0.02)
        self.assertGreater(trades[0].max_loss, 0)
        self.assertGreater(trades[0].expected_value, 0)

    def test_schwab_order_payload_uses_multi_leg_net_credit(self) -> None:
        engine = StrategyEngine(StrategyConfig(account_equity=20_000.0))
        trade = engine.find_trades([_sample_snapshot()])[0]

        order = build_opening_order(trade)

        self.assertEqual(order["orderType"], "NET_CREDIT")
        self.assertEqual(order["duration"], "DAY")
        self.assertIn(order["complexOrderStrategyType"], {"VERTICAL", "IRON_CONDOR"})
        self.assertEqual(len(order["orderLegCollection"]), len(trade.legs))
        self.assertTrue(
            all(leg["quantity"] == trade.contracts for leg in order["orderLegCollection"])
        )

    def test_profit_taking_order_closes_opening_instructions(self) -> None:
        engine = StrategyEngine(StrategyConfig(account_equity=20_000.0))
        trade = engine.find_trades([_sample_snapshot()])[0]

        close_order = build_profit_taking_close_order(trade, profit_capture=0.50)
        instructions = {leg["instruction"] for leg in close_order["orderLegCollection"]}

        self.assertEqual(close_order["orderType"], "NET_DEBIT")
        self.assertIn("BUY_TO_CLOSE", instructions)
        self.assertIn("SELL_TO_CLOSE", instructions)


def _sample_snapshot() -> UnderlyingSnapshot:
    options = (
        _quote("SPY   260717P00430000", "PUT", 430, 0.95, 1.05, -0.16),
        _quote("SPY   260717P00428000", "PUT", 428, 0.50, 0.60, -0.09),
        _quote("SPY   260717P00426000", "PUT", 426, 0.30, 0.38, -0.05),
        _quote("SPY   260717C00470000", "CALL", 470, 0.95, 1.05, 0.16),
        _quote("SPY   260717C00472000", "CALL", 472, 0.50, 0.60, 0.09),
        _quote("SPY   260717C00474000", "CALL", 474, 0.30, 0.38, 0.05),
    )
    return UnderlyingSnapshot(
        symbol="SPY",
        price=450.0,
        realized_vol_20d=0.16,
        realized_vol_5d=0.14,
        iv_rank=0.55,
        vix=18.0,
        vix3m=21.0,
        event_risk=False,
        options=options,
    )


def _quote(
    symbol: str,
    option_type: str,
    strike: float,
    bid: float,
    ask: float,
    delta: float,
) -> OptionQuote:
    return OptionQuote(
        underlying="SPY",
        option_symbol=symbol,
        expiration="2026-07-17",
        dte=46,
        option_type=option_type,
        strike=strike,
        bid=bid,
        ask=ask,
        delta=delta,
        implied_volatility=0.24,
        bid_size=80,
        ask_size=65,
        open_interest=3_000,
        volume=700,
    )


if __name__ == "__main__":
    unittest.main()
