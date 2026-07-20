from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from adaptive_orb.l2 import aggregate_l2_csv


class Level2AggregationTests(unittest.TestCase):
    def test_uses_tail_samples_without_future_minutes(self) -> None:
        rows = [
            ["2026-01-02T14:30:01+00:00", 0.1],
            ["2026-01-02T14:30:30+00:00", 0.3],
            ["2026-01-02T14:30:59+00:00", 0.5],
            ["2026-01-02T14:31:01+00:00", -0.4],
        ]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "raw.csv"
            output = Path(directory) / "features.csv"
            with source.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    [
                        "received_utc",
                        "depth_imbalance",
                        "ofi_norm",
                        "trade_delta_norm",
                        "microprice_ticks",
                        "signed_persistence",
                        "spread_ticks",
                    ]
                )
                for timestamp, value in rows:
                    writer.writerow([timestamp, value, value, value, value, value, 1.0])
            self.assertEqual(aggregate_l2_csv(source, output, tail_samples=2), 2)
            with output.open("r", encoding="utf-8", newline="") as handle:
                result = list(csv.DictReader(handle))
            self.assertEqual(len(result), 2)
            self.assertAlmostEqual(float(result[0]["depth_imbalance"]), 0.4)
            self.assertAlmostEqual(float(result[0]["l2_age_ms"]), 1_000.0)
            self.assertAlmostEqual(float(result[1]["depth_imbalance"]), -0.4)


if __name__ == "__main__":
    unittest.main()
