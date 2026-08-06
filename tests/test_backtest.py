from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from adaptive_orb.backtest import _vwap_path, run_backtest
from adaptive_orb.config import (
    ExecutionConfig,
    InstrumentConfig,
    ResearchConfig,
    RiskConfig,
    SessionWindowConfig,
    SignalConfig,
)
from adaptive_orb.model import Bar


UTC = ZoneInfo("UTC")


def _bar(day: int, hour: int, minute: int, o: float, h: float, l: float, c: float, volume: float, *, l2: bool = False) -> Bar:
    kwargs = {}
    if l2:
        kwargs = {
            "depth_imbalance": 0.8,
            "ofi_norm": 0.8,
            "trade_delta_norm": 0.8,
            "microprice_ticks": 0.8,
            "l2_persistence": 0.8,
            "spread_ticks": 1.0,
            "l2_age_ms": 100.0,
        }
    return Bar(datetime(2026, 1, day, hour, minute, tzinfo=UTC), o, h, l, c, volume, **kwargs)


def _config(require_l2: bool = True) -> ResearchConfig:
    return ResearchConfig(
        instrument=InstrumentConfig(
            symbol="TEST",
            tick_size=1.0,
            tick_value=1.0,
            commission_per_side=0.5,
            slippage_ticks_per_side=0.0,
        ),
        signal=SignalConfig(
            timezone="UTC",
            session_open="09:30",
            opening_range_minutes=3,
            signal_end="10:00",
            flatten_time="15:55",
            atr_lookback_sessions=1,
            min_or_atr=0.10,
            max_or_atr=0.30,
            min_or_ticks=2,
            max_or_ticks=10,
            breakout_buffer_fraction=0.0,
            min_breakout_buffer_ticks=1,
            retest_tolerance_fraction=0.25,
            max_retest_bars=5,
            volume_ema_alpha=0.5,
            min_breakout_relative_volume=1.10,
            vwap_slope_bars=1,
            min_vwap_slope_ticks=0.0,
            require_l2=require_l2,
            min_l2_composite=0.15,
            min_l2_persistence=0.60,
            max_spread_ticks=1.0,
            max_l2_age_ms=750.0,
        ),
        risk=RiskConfig(
            max_risk_per_trade=10.0,
            max_contracts=2,
            min_stop_ticks=2,
            max_stop_ticks=8,
            structural_stop_fraction=0.25,
            reward_to_risk=1.5,
            time_stop_bars=10,
            failure_close_fraction=0.25,
            max_trades_per_session=1,
            internal_daily_loss_limit=20.0,
        ),
    )


def _baseline_day() -> list[Bar]:
    return [
        _bar(1, 9, 30, 95, 100, 90, 96, 100),
        _bar(1, 9, 31, 96, 105, 95, 104, 100),
        _bar(1, 9, 32, 104, 110, 100, 108, 100),
        _bar(1, 15, 55, 108, 109, 107, 108, 100),
    ]


def _long_setup(*, include_l2: bool = True, ambiguous_entry_bar: bool = False) -> list[Bar]:
    entry_high = 111 if not ambiguous_entry_bar else 111
    entry_low = 105 if not ambiguous_entry_bar else 102
    return [
        _bar(2, 9, 30, 100, 102, 100, 101, 100),
        _bar(2, 9, 31, 101, 103, 101, 102, 100),
        _bar(2, 9, 32, 102, 104, 102, 103, 100),
        _bar(2, 9, 33, 103, 106, 103, 106, 240),
        _bar(2, 9, 34, 106, 106, 104, 105, 150, l2=include_l2),
        _bar(2, 9, 35, 105.5, entry_high, entry_low, 110, 150),
        _bar(2, 15, 55, 110, 110, 109, 109, 100),
    ]


class BacktestTests(unittest.TestCase):
    def test_exact_trade_value_drives_research_vwap(self) -> None:
        bars = [
            _bar(2, 9, 30, 100, 110, 90, 100, 2),
            _bar(2, 9, 31, 100, 110, 90, 100, 2),
        ]
        bars = [replace(bars[0], trade_value=202.0), replace(bars[1], trade_value=206.0)]
        self.assertEqual(_vwap_path(bars), [101.0, 102.0])

    def test_mixed_exact_and_approximate_vwap_is_rejected(self) -> None:
        bars = [
            replace(_bar(2, 9, 30, 100, 101, 99, 100, 1), trade_value=100.0),
            _bar(2, 9, 31, 100, 101, 99, 100, 1),
        ]
        with self.assertRaisesRegex(ValueError, "every bar"):
            _vwap_path(bars)

    def test_cost_aware_long_target(self) -> None:
        result = run_backtest(_baseline_day() + _long_setup(), _config())
        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.exit_reason, "target")
        self.assertEqual(trade.decision_price, 105.5)
        self.assertEqual(trade.quantity, 2)
        self.assertEqual(trade.gross_pnl, 10.0)
        self.assertEqual(trade.commission, 2.0)
        self.assertEqual(trade.net_pnl, 8.0)

    def test_missing_l2_blocks_entry_when_required(self) -> None:
        result = run_backtest(_baseline_day() + _long_setup(include_l2=False), _config(require_l2=True))
        self.assertEqual(result.trades, ())

    def test_stale_l2_blocks_entry_when_required(self) -> None:
        bars = _baseline_day() + _long_setup()
        bars[-3] = replace(bars[-3], l2_age_ms=751.0)
        result = run_backtest(bars, _config(require_l2=True))
        self.assertEqual(result.trades, ())

    def test_incomplete_opening_range_blocks_entry(self) -> None:
        incomplete = _long_setup()
        del incomplete[1]
        result = run_backtest(_baseline_day() + incomplete, _config())
        self.assertEqual(result.trades, ())

    def test_bar_only_research_can_disable_l2_explicitly(self) -> None:
        result = run_backtest(_baseline_day() + _long_setup(include_l2=False), _config(require_l2=False))
        self.assertEqual(len(result.trades), 1)

    def test_same_bar_stop_and_target_uses_pessimistic_stop_first(self) -> None:
        result = run_backtest(_baseline_day() + _long_setup(ambiguous_entry_bar=True), _config())
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].exit_reason, "stop")
        self.assertLess(result.trades[0].net_pnl, 0)

    def test_wider_costs_reduce_position_size(self) -> None:
        config = _config()
        costly = replace(
            config,
            instrument=replace(config.instrument, commission_per_side=2.0, slippage_ticks_per_side=1.0),
        )
        result = run_backtest(_baseline_day() + _long_setup(), costly)
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].quantity, 1)

    def test_diagnostics_explain_blocked_sessions(self) -> None:
        result = run_backtest(_baseline_day() + _long_setup(include_l2=False), _config(require_l2=True))
        self.assertEqual(result.trades, ())
        self.assertEqual(result.diagnostics["primary"].get("no_confirmed_retest"), 1)


def _wall_execution(**overrides: object) -> ExecutionConfig:
    values: dict[str, object] = {
        "use_wall_entries": True,
        "min_wall_ratio": 4.0,
        "wall_offset_ticks": 2,
        "wall_stop_pad_ticks": 1,
        "max_wall_chase_ticks": 10,
        "wall_entry_timeout_bars": 2,
        "wall_entry_fallback": "market",
        "use_round_levels": False,
    }
    values.update(overrides)
    return ExecutionConfig(**values)  # type: ignore[arg-type]


def _wall_config(execution: ExecutionConfig) -> ResearchConfig:
    base = _config()
    return replace(base, execution=execution)


def _wall_setup(signal_bar_wall: float | None, bars_after_signal: list[Bar]) -> list[Bar]:
    wall_kwargs: dict[str, float] = {}
    if signal_bar_wall is not None:
        wall_kwargs = {"bid_wall_price": signal_bar_wall, "bid_wall_ratio": 5.0}
    return [
        _bar(2, 9, 30, 100, 102, 100, 101, 100),
        _bar(2, 9, 31, 101, 103, 101, 102, 100),
        _bar(2, 9, 32, 102, 104, 102, 103, 100),
        _bar(2, 9, 33, 103, 106, 103, 106, 240),
        replace(_bar(2, 9, 34, 106, 106, 104, 105, 150, l2=True), **wall_kwargs),
        *bars_after_signal,
        _bar(2, 15, 55, 110, 110, 109, 109, 100),
    ]


class WallEntryTests(unittest.TestCase):
    def test_wall_limit_fill_uses_offset_price_and_wall_stop(self) -> None:
        bars = _baseline_day() + _wall_setup(
            102.0, [_bar(2, 9, 35, 105.5, 111, 103, 110, 150)]
        )
        result = run_backtest(bars, _wall_config(_wall_execution()))
        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.entry_type, "wall_limit")
        # Limit rests wall (102) + 2 ticks = 104; fill requires trading through it.
        self.assertEqual(trade.entry_price, 104.0)
        # Stop anchors behind the wall: 102 - 1 tick pad = 101 (3 ticks from entry).
        self.assertEqual(trade.stop_price, 101.0)
        self.assertEqual(trade.exit_reason, "target")

    def test_wall_limit_timeout_falls_back_to_market(self) -> None:
        bars = _baseline_day() + _wall_setup(
            100.0,
            [
                _bar(2, 9, 35, 105.5, 111, 104, 110, 150),
                _bar(2, 9, 36, 110, 112, 108, 111, 150),
                _bar(2, 9, 37, 111, 112, 110, 111, 150),
            ],
        )
        result = run_backtest(bars, _wall_config(_wall_execution()))
        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.entry_type, "wall_fallback_market")
        self.assertEqual(trade.entry_price, 111.0)

    def test_wall_limit_timeout_can_skip_instead(self) -> None:
        bars = _baseline_day() + _wall_setup(
            100.0,
            [
                _bar(2, 9, 35, 105.5, 111, 104, 110, 150),
                _bar(2, 9, 36, 110, 112, 108, 111, 150),
                _bar(2, 9, 37, 111, 112, 110, 111, 150),
            ],
        )
        result = run_backtest(bars, _wall_config(_wall_execution(wall_entry_fallback="skip")))
        self.assertEqual(result.trades, ())
        self.assertEqual(result.diagnostics["primary"].get("wall_limit_timeout_skipped"), 1)

    def test_wall_limit_cancelled_when_thesis_fails_before_fill(self) -> None:
        bars = _baseline_day() + _wall_setup(
            102.0, [_bar(2, 9, 35, 105.5, 106, 104, 102, 150)]
        )
        result = run_backtest(bars, _wall_config(_wall_execution()))
        self.assertEqual(result.trades, ())
        self.assertEqual(result.diagnostics["primary"].get("wall_limit_cancelled"), 1)

    def test_weak_wall_ratio_falls_back_to_plain_market_entry(self) -> None:
        bars = _baseline_day() + _wall_setup(
            102.0, [_bar(2, 9, 35, 105.5, 111, 105, 110, 150)]
        )
        bars = [
            replace(bar, bid_wall_ratio=2.0) if bar.bid_wall_ratio is not None else bar
            for bar in bars
        ]
        result = run_backtest(bars, _wall_config(_wall_execution()))
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].entry_type, "market")


class GthSessionTests(unittest.TestCase):
    def _gth_config(self) -> ResearchConfig:
        base = _config()
        window = SessionWindowConfig(
            name="globex_reopen",
            open="18:00",
            opening_range_minutes=3,
            signal_end="19:00",
            flatten="20:00",
            risk_fraction=0.5,
        )
        return replace(base, signal=replace(base.signal, sessions=(window,)))

    def test_evening_session_trades_and_books_to_next_trade_date(self) -> None:
        warmup = [
            _bar(1, 12, 0, 95, 100, 90, 96, 100),
            _bar(1, 12, 1, 96, 105, 95, 104, 100),
        ]
        evening = [
            _bar(1, 18, 0, 100, 102, 100, 101, 100),
            _bar(1, 18, 1, 101, 103, 101, 102, 100),
            _bar(1, 18, 2, 102, 104, 102, 103, 100),
            _bar(1, 18, 3, 103, 106, 103, 106, 240),
            _bar(1, 18, 4, 106, 106, 104, 105, 150, l2=True),
            _bar(1, 18, 5, 105.5, 111, 105, 110, 150),
            _bar(1, 20, 0, 110, 110, 109, 109, 100),
        ]
        result = run_backtest(warmup + evening, self._gth_config())
        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.window, "globex_reopen")
        # 18:00+ belongs to the next trading date (Globex convention).
        self.assertEqual(trade.session, "2026-01-02")
        # Half risk fraction halves the size versus the equivalent RTH trade.
        self.assertEqual(trade.quantity, 1)
        self.assertEqual(result.daily[-1].session, "2026-01-02")
        self.assertEqual(result.daily[-1].trades, 1)


if __name__ == "__main__":
    unittest.main()
