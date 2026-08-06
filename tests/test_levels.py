from __future__ import annotations

import unittest

from adaptive_orb.levels import nearest_round_level, pad_stop, shave_target


class RoundLevelTests(unittest.TestCase):
    def test_nearest_level_uses_00_20_40_50_60_80_offsets(self) -> None:
        self.assertEqual(nearest_round_level(23415.0), 23420.0)
        self.assertEqual(nearest_round_level(23448.0), 23450.0)
        self.assertEqual(nearest_round_level(23492.0), 23500.0)
        self.assertEqual(nearest_round_level(23371.0), 23380.0)
        # 30 is not a round offset; 23430 sits between 23420 and 23440.
        self.assertIn(nearest_round_level(23429.0), (23420.0,))
        self.assertIn(nearest_round_level(23431.0), (23440.0,))

    def test_tie_resolves_to_lower_level(self) -> None:
        self.assertEqual(nearest_round_level(23410.0), 23400.0)

    def test_target_is_shaved_in_front_of_a_level(self) -> None:
        shaved = shave_target(23400.0, 23451.0, 1, 0.25, front_ticks=4, window_ticks=8)
        self.assertEqual(shaved, 23449.0)

    def test_target_is_never_extended(self) -> None:
        # Raw target already in front of the level; shaving must not push it out.
        self.assertEqual(
            shave_target(23400.0, 23448.5, 1, 0.25, front_ticks=4, window_ticks=8), 23448.5
        )

    def test_target_far_from_levels_is_unchanged(self) -> None:
        self.assertEqual(
            shave_target(23400.0, 23435.0, 1, 0.25, front_ticks=4, window_ticks=8), 23435.0
        )

    def test_short_target_is_shaved_above_the_level(self) -> None:
        shaved = shave_target(23470.0, 23449.0, -1, 0.25, front_ticks=4, window_ticks=8)
        self.assertEqual(shaved, 23451.0)

    def test_stop_near_a_level_is_padded_beyond_it(self) -> None:
        padded = pad_stop(
            23460.0, 23450.5, 1, 0.25, trigger_ticks=4, pad_ticks=6, max_stop_ticks=240
        )
        self.assertEqual(padded, 23448.5)

    def test_stop_pad_respects_maximum_stop_distance(self) -> None:
        unchanged = pad_stop(
            23460.0, 23450.5, 1, 0.25, trigger_ticks=4, pad_ticks=6, max_stop_ticks=40
        )
        self.assertEqual(unchanged, 23450.5)

    def test_stop_already_beyond_the_level_is_unchanged(self) -> None:
        self.assertEqual(
            pad_stop(23460.0, 23448.0, 1, 0.25, trigger_ticks=4, pad_ticks=6, max_stop_ticks=240),
            23448.0,
        )


if __name__ == "__main__":
    unittest.main()
