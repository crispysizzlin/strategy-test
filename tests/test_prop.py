import unittest

from adaptive_orb.config import PropConfig
from adaptive_orb.model import DailyResult
from adaptive_orb.prop import simulate_eod_trailing_account


class PropSimulationTests(unittest.TestCase):
    def test_eod_floor_trails_and_locks(self) -> None:
        profile = PropConfig(
            enabled=True,
            initial_balance=100_000,
            profit_target=6_000,
            max_loss=3_000,
            daily_loss=1_800,
            locked_floor=100_100,
        )
        daily = [DailyResult(f"d{index}", 1_000, 1_000, 0, 1) for index in range(1, 7)]
        result = simulate_eod_trailing_account(daily, [], profile)
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.days_to_target, 6)
        self.assertEqual(result.max_loss_floor, 100_100)

    def test_daily_close_at_floor_breaches(self) -> None:
        profile = PropConfig(
            enabled=True,
            initial_balance=25_000,
            profit_target=1_250,
            max_loss=1_000,
            daily_loss=None,
            locked_floor=25_100,
        )
        daily = [DailyResult("d1", -1_000, -999, 1, 1)]
        result = simulate_eod_trailing_account(daily, [], profile)
        self.assertEqual(result.status, "breached")

    def test_best_day_fraction_uses_net_profit_after_losing_days(self) -> None:
        profile = PropConfig(
            enabled=True,
            initial_balance=100_000,
            profit_target=10_000,
            max_loss=3_000,
            daily_loss=None,
            locked_floor=100_100,
        )
        daily = [
            DailyResult("d1", 1_000, 1_000, 0, 1),
            DailyResult("d2", -500, -500, 0, 1),
            DailyResult("d3", 500, 500, 0, 1),
        ]
        result = simulate_eod_trailing_account(daily, [], profile)
        self.assertEqual(result.largest_profit_day_fraction, 1.0)


if __name__ == "__main__":
    unittest.main()
