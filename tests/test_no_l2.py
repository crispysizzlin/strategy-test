"""Behavioral regressions; all prices below are synthetic, not performance data."""
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest

from adaptive_orb.backtest import _window_context, run_backtest
from adaptive_orb.cli import _scale_costs, main
from adaptive_orb.config import ResearchConfig, SessionWindowConfig, load_config
from adaptive_orb.data import write_rows
from test_backtest import _baseline_day, _long_setup, _config


ROOT = Path(__file__).resolve().parents[1]


class NoLevel2Tests(unittest.TestCase):
    def test_new_mnq_profile_defaults_to_no_depth(self):
        for config in (ResearchConfig(), load_config(ROOT / "config/mnq_lucid_pro_100k.json")):
            self.assertFalse(config.signal.require_l2)
            self.assertFalse(config.execution.use_wall_entries)

    def test_no_l2_ignores_conflicting_depth_and_wall_fields(self):
        base = _config(require_l2=False)
        config = replace(base, execution=replace(base.execution, use_wall_entries=True))
        bars = _baseline_day() + _long_setup(include_l2=False)
        expected = run_backtest(bars, config)
        # Saved wall=true must not quietly reactivate depth execution.
        enriched = [replace(bar, depth_imbalance=-1, ofi_norm=-1, trade_delta_norm=-1,
                            microprice_ticks=-1, l2_persistence=-1, spread_ticks=100,
                            l2_age_ms=99999, bid_wall_price=103, bid_wall_ratio=10)
                    for bar in bars]
        actual = run_backtest(enriched, config)
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual.trades), 1)
        self.assertEqual(actual.trades[0].entry_type, "market")
        self.assertIsNone(actual.trades[0].l2_composite)

    def test_all_three_windows_trade_without_any_depth(self):
        base = _config(require_l2=False)
        windows = (
            SessionWindowConfig("globex_reopen", "18:00", 3, "19:00", "20:00", risk_fraction=0.5),
            SessionWindowConfig("london_open", "03:00", 3, "04:00", "05:00", risk_fraction=0.5),
            SessionWindowConfig("ny_open", "09:30", 3, "10:00", "15:55"),
        )
        config = replace(base, signal=replace(base.signal, sessions=windows))
        bars = _baseline_day()
        template = _long_setup(include_l2=False)
        for window, offset in zip(windows, (-15.5, -6.5, 0)):
            part = [replace(bar, timestamp=bar.timestamp + timedelta(hours=offset)) for bar in template[:-1]]
            hour, minute = map(int, window.flatten.split(":"))
            part.append(replace(template[-1], timestamp=part[0].timestamp.replace(hour=hour, minute=minute)))
            bars.extend(part)
        result = run_backtest(sorted(bars, key=lambda bar: bar.timestamp), config)
        self.assertEqual([trade.window for trade in result.trades], [window.name for window in windows])
        self.assertEqual([trade.quantity for trade in result.trades], [1, 1, 2])
        self.assertEqual({trade.session for trade in result.trades}, {"2026-01-02"})
        self.assertTrue(all(trade.entry_type == "market" for trade in result.trades))

    def test_stress_doubles_every_effective_session_slippage(self):
        config = load_config(ROOT / "config/mnq_lucid_pro_100k.json")
        stressed = _scale_costs(config, 2)
        self.assertEqual(stressed.instrument.commission_per_side, 2 * config.instrument.commission_per_side)
        self.assertEqual([_window_context(window, stressed).slippage_ticks_per_side
                          for window in stressed.signal.resolved_sessions()], [4, 4, 2])
        legacy = _config()
        self.assertFalse(_scale_costs(legacy, 2).signal.sessions)

    def test_long_stop_fills_at_gap_open_with_adverse_slippage(self):
        bars = _baseline_day() + _long_setup(include_l2=False)
        bars[-2] = replace(bars[-2], open=105.5, high=106, low=105, close=105.5)
        bars[-1] = replace(bars[-1], timestamp=bars[-2].timestamp + timedelta(minutes=1),
                           open=100, high=101, low=99, close=100)
        config = _config(require_l2=False)
        config = replace(config, instrument=replace(config.instrument, slippage_ticks_per_side=0.25))
        trade = run_backtest(bars, config).trades[0]
        self.assertEqual(trade.exit_reason, "stop")
        self.assertEqual(trade.exit_price, 99.75)
        self.assertLess(trade.exit_price, trade.stop_price)

    def test_short_stop_fills_at_gap_open_with_adverse_slippage(self):
        bars = _baseline_day() + _long_setup(include_l2=False)
        bars[-2] = replace(bars[-2], open=105.5, high=106, low=105, close=105.5)
        bars[-1] = replace(bars[-1], timestamp=bars[-2].timestamp + timedelta(minutes=1),
                           open=100, high=101, low=99, close=100)
        mirrored = [replace(bar, open=220-bar.open, high=220-bar.low,
                            low=220-bar.high, close=220-bar.close) for bar in bars]
        config = _config(require_l2=False)
        config = replace(config, instrument=replace(config.instrument, slippage_ticks_per_side=0.25))
        trade = run_backtest(mirrored, config).trades[0]
        self.assertEqual(trade.exit_reason, "stop")
        self.assertEqual(trade.exit_price, 120.25)
        self.assertGreater(trade.exit_price, trade.stop_price)

    def test_cli_override_accepts_ohlcv_only_and_reports_effective_mode(self):
        from dataclasses import asdict
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text(json.dumps(asdict(_config(require_l2=True))))
            fields = ["timestamp", "open", "high", "low", "close", "volume"]
            write_rows(root / "bars.csv", fields,
                       ({key: getattr(bar, key).isoformat() if key == "timestamp" else getattr(bar, key)
                         for key in fields} for bar in _baseline_day() + _long_setup(include_l2=False)))
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["backtest", "--data", str(root / "bars.csv"),
                                       "--config", str(root / "config.json"), "--no-l2",
                                       "--output", str(root / "results"), "--bootstrap-simulations", "20"]), 0)
            report = json.loads((root / "results/summary.json").read_text())
            self.assertEqual(report["summary"]["trades"], 1)
            self.assertEqual(report["summary_by_window"]["primary"], report["summary"])
            self.assertEqual(report["assumptions"]["signal_mode"], "price_volume")
            self.assertFalse(report["effective_config"]["execution"]["use_wall_entries"])
            self.assertFalse(report["assumptions"]["live_l1_spread_guard_simulated"])


if __name__ == "__main__":
    unittest.main()
