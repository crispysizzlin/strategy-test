from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from adaptive_orb.data import load_bars


class DataLoadingTests(unittest.TestCase):
    def test_loads_exact_trade_value_and_l2_age(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bars.csv"
            with source.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "timestamp",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "trade_value",
                        "l2_age_ms",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "timestamp": "2026-01-02T14:30:00Z",
                        "open": 6000,
                        "high": 6001,
                        "low": 5999,
                        "close": 6000.5,
                        "volume": 2,
                        "trade_value": 12000.5,
                        "l2_age_ms": 200,
                    }
                )
            bars = load_bars(source)
            self.assertEqual(bars[0].trade_value, 12000.5)
            self.assertEqual(bars[0].l2_age_ms, 200.0)

    def test_rejects_negative_l2_age(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bars.csv"
            source.write_text(
                "timestamp,open,high,low,close,volume,l2_age_ms\n"
                "2026-01-02T14:30:00Z,1,1,1,1,1,-1\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Negative l2_age_ms"):
                load_bars(source)


if __name__ == "__main__":
    unittest.main()
