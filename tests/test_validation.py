from dataclasses import replace

from adaptive_orb.config import ResearchConfig
from adaptive_orb.validation import moving_block_bootstrap, probabilistic_sharpe_ratio, production_gate
import unittest


class ValidationTests(unittest.TestCase):
    def test_research_engine_rejects_multiple_trades_per_session(self) -> None:
        config = ResearchConfig()
        with self.assertRaisesRegex(ValueError, "exactly one trade"):
            replace(config, risk=replace(config.risk, max_trades_per_session=2)).validate()

    def test_probabilistic_sharpe_orders_good_and_bad_series(self) -> None:
        good = [1.0, 0.5, 1.5, -0.2, 0.8, 1.2] * 20
        bad = [-value for value in good]
        self.assertGreater(probabilistic_sharpe_ratio(good), 0.95)
        self.assertLess(probabilistic_sharpe_ratio(bad), 0.05)

    def test_bootstrap_is_seeded_and_reports_loss_probability(self) -> None:
        values = [2.0, -1.0, 1.0, 0.5, -0.25] * 10
        first = moving_block_bootstrap(values, simulations=100, block_length=5, seed=11)
        second = moving_block_bootstrap(values, simulations=100, block_length=5, seed=11)
        self.assertEqual(first, second)
        self.assertIsNotNone(first)
        assert first is not None
        self.assertGreaterEqual(first.loss_probability, 0)
        self.assertLessEqual(first.loss_probability, 1)

    def test_production_gate_rejects_small_backtest(self) -> None:
        gate = production_gate(
            out_of_sample_trades=40,
            net_profit_factor=1.3,
            psr=0.97,
            stressed_net_pnl=10,
            profitable_walk_forward_windows=4,
            total_walk_forward_windows=5,
            live_sim_sessions=20,
        )
        self.assertFalse(gate.passed)
        self.assertTrue(any("150" in failure for failure in gate.failures))


if __name__ == "__main__":
    unittest.main()
