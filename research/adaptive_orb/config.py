from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def _clock(value: str) -> time:
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid clock value: {value!r}")
    return time(int(parts[0]), int(parts[1]))


@dataclass(frozen=True)
class InstrumentConfig:
    symbol: str = "MES"
    tick_size: float = 0.25
    tick_value: float = 1.25
    commission_per_side: float = 0.50
    slippage_ticks_per_side: float = 1.0


@dataclass(frozen=True)
class SessionWindowConfig:
    """One intraday trading window (a clock interval that must not cross midnight).

    Optional fields fall back to the global signal/instrument values. GTH windows
    typically carry a reduced ``risk_fraction``, wider spread/slippage assumptions,
    and their own opening-range bounds because overnight ranges are structurally
    smaller relative to the daily ATR.
    """

    name: str
    open: str
    opening_range_minutes: int
    signal_end: str
    flatten: str
    risk_fraction: float = 1.0
    min_or_atr: float | None = None
    max_or_atr: float | None = None
    min_or_ticks: int | None = None
    max_or_ticks: int | None = None
    max_spread_ticks: float | None = None
    slippage_ticks_per_side: float | None = None
    min_breakout_relative_volume: float | None = None


@dataclass(frozen=True)
class ExecutionConfig:
    """Execution-price modifiers. These adjust prices; they never veto a signal.

    Wall entries queue a passive limit ``wall_offset_ticks`` in front of a large,
    persistent resting order instead of paying the spread at market. If the limit
    is not filled within ``wall_entry_timeout_bars``, the configured fallback
    (default: market) keeps the base strategy's trade frequency intact.
    """

    use_wall_entries: bool = True
    min_wall_ratio: float = 4.0
    wall_offset_ticks: int = 6
    wall_stop_pad_ticks: int = 4
    max_wall_chase_ticks: int = 24
    wall_entry_timeout_bars: int = 5
    wall_entry_fallback: str = "market"
    use_round_levels: bool = True
    round_level_front_ticks: int = 4
    round_level_target_window_ticks: int = 8
    round_level_stop_trigger_ticks: int = 4
    round_level_stop_pad_ticks: int = 6


@dataclass(frozen=True)
class SignalConfig:
    timezone: str = "America/New_York"
    session_open: str = "09:30"
    opening_range_minutes: int = 15
    signal_end: str = "11:30"
    flatten_time: str = "15:55"
    atr_lookback_sessions: int = 20
    min_or_atr: float = 0.08
    max_or_atr: float = 0.35
    min_or_ticks: int = 8
    max_or_ticks: int = 120
    breakout_buffer_fraction: float = 0.05
    min_breakout_buffer_ticks: int = 2
    retest_tolerance_fraction: float = 0.12
    max_retest_bars: int = 20
    volume_ema_alpha: float = 0.12
    min_breakout_relative_volume: float = 1.15
    vwap_slope_bars: int = 3
    min_vwap_slope_ticks: float = 0.25
    require_l2: bool = True
    min_l2_composite: float = 0.15
    min_l2_persistence: float = 0.60
    max_spread_ticks: float = 1.0
    max_l2_age_ms: float = 750.0
    sessions: tuple[SessionWindowConfig, ...] = ()

    def resolved_sessions(self) -> tuple[SessionWindowConfig, ...]:
        """Explicit windows, or a single legacy window built from the flat fields."""

        if self.sessions:
            return self.sessions
        return (
            SessionWindowConfig(
                name="primary",
                open=self.session_open,
                opening_range_minutes=self.opening_range_minutes,
                signal_end=self.signal_end,
                flatten=self.flatten_time,
            ),
        )


@dataclass(frozen=True)
class RiskConfig:
    max_risk_per_trade: float = 75.0
    max_contracts: int = 4
    min_stop_ticks: int = 8
    max_stop_ticks: int = 48
    structural_stop_fraction: float = 0.15
    reward_to_risk: float = 1.75
    time_stop_bars: int = 45
    failure_close_fraction: float = 0.15
    max_trades_per_session: int = 1
    internal_daily_loss_limit: float = 225.0


@dataclass(frozen=True)
class PropConfig:
    enabled: bool = True
    initial_balance: float = 100_000.0
    profit_target: float = 6_000.0
    max_loss: float = 3_000.0
    daily_loss: float | None = 1_800.0
    locked_floor: float = 100_100.0


@dataclass(frozen=True)
class ResearchConfig:
    instrument: InstrumentConfig = field(default_factory=InstrumentConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    prop: PropConfig = field(default_factory=PropConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "ResearchConfig":
        allowed = {"instrument", "signal", "risk", "prop", "execution"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"Unknown configuration sections: {sorted(unknown)}")
        signal_mapping = dict(value.get("signal", {}))
        raw_sessions = signal_mapping.pop("sessions", ())
        sessions = tuple(SessionWindowConfig(**item) for item in raw_sessions)
        return cls(
            instrument=InstrumentConfig(**value.get("instrument", {})),
            signal=SignalConfig(sessions=sessions, **signal_mapping),
            risk=RiskConfig(**value.get("risk", {})),
            prop=PropConfig(**value.get("prop", {})),
            execution=ExecutionConfig(**value.get("execution", {})),
        )

    def validate(self) -> None:
        i, s, r, p, e = self.instrument, self.signal, self.risk, self.prop, self.execution
        if i.tick_size <= 0 or i.tick_value <= 0:
            raise ValueError("tick_size and tick_value must be positive")
        if not i.symbol.strip():
            raise ValueError("instrument symbol cannot be empty")
        if i.commission_per_side < 0 or i.slippage_ticks_per_side < 0:
            raise ValueError("cost assumptions cannot be negative")
        if not 1 <= s.opening_range_minutes <= 60:
            raise ValueError("opening_range_minutes must be in [1, 60]")
        ZoneInfo(s.timezone)
        session_open = _clock(s.session_open)
        signal_end = _clock(s.signal_end)
        flatten_time = _clock(s.flatten_time)
        if not session_open < signal_end < flatten_time:
            raise ValueError("session_open, signal_end, and flatten_time must be increasing")
        if not 0 < s.min_or_atr < s.max_or_atr:
            raise ValueError("opening-range ATR bounds are invalid")
        if not 1 <= s.min_or_ticks < s.max_or_ticks:
            raise ValueError("opening-range tick bounds are invalid")
        if s.atr_lookback_sessions < 1 or s.max_retest_bars < 1:
            raise ValueError("ATR lookback and retest duration are invalid")
        if not 0.0 < s.volume_ema_alpha <= 1.0 or s.min_breakout_relative_volume <= 0:
            raise ValueError("volume filter parameters are invalid")
        if s.vwap_slope_bars < 1 or s.min_vwap_slope_ticks < 0:
            raise ValueError("VWAP filter parameters are invalid")
        if not 0 <= s.min_l2_composite <= 1:
            raise ValueError("min_l2_composite must be in [0, 1]")
        if not 0 <= s.min_l2_persistence <= 1:
            raise ValueError("min_l2_persistence must be in [0, 1]")
        if s.max_spread_ticks <= 0 or s.max_l2_age_ms <= 0:
            raise ValueError("L2 spread and freshness bounds must be positive")
        self._validate_sessions()
        if e.min_wall_ratio < 1.0:
            raise ValueError("min_wall_ratio must be at least 1.0")
        if e.wall_offset_ticks < 1 or e.wall_stop_pad_ticks < 0:
            raise ValueError("wall offset/pad ticks are invalid")
        if e.max_wall_chase_ticks < e.wall_offset_ticks:
            raise ValueError("max_wall_chase_ticks must be at least wall_offset_ticks")
        if e.wall_entry_timeout_bars < 1:
            raise ValueError("wall_entry_timeout_bars must be positive")
        if e.wall_entry_fallback not in {"market", "skip"}:
            raise ValueError("wall_entry_fallback must be 'market' or 'skip'")
        if e.round_level_front_ticks < 0 or e.round_level_target_window_ticks < 0:
            raise ValueError("round-level target parameters are invalid")
        if e.round_level_stop_trigger_ticks < 0 or e.round_level_stop_pad_ticks < 0:
            raise ValueError("round-level stop parameters are invalid")
        if r.max_risk_per_trade <= 0 or r.max_contracts < 1:
            raise ValueError("risk budget and max_contracts must be positive")
        if not 1 <= r.min_stop_ticks <= r.max_stop_ticks:
            raise ValueError("stop bounds are invalid")
        if not 0 < r.structural_stop_fraction <= 1 or not 0 < r.failure_close_fraction <= 1:
            raise ValueError("structural and failure fractions must be in (0, 1]")
        if r.reward_to_risk <= 0 or r.time_stop_bars < 1:
            raise ValueError("reward_to_risk and time_stop_bars must be positive")
        if r.internal_daily_loss_limit <= 0:
            raise ValueError("internal_daily_loss_limit must be positive")
        if r.max_trades_per_session != 1:
            raise ValueError("the research engine currently supports exactly one trade per session")
        if p.enabled:
            if p.initial_balance <= 0 or p.profit_target <= 0 or p.max_loss <= 0:
                raise ValueError("prop balance, target, and max loss must be positive")
            if p.daily_loss is not None and p.daily_loss <= 0:
                raise ValueError("prop daily_loss must be positive or null")
            initial_floor = p.initial_balance - p.max_loss
            if not initial_floor < p.locked_floor <= p.initial_balance + p.max_loss:
                raise ValueError("prop locked_floor is inconsistent with the account profile")

    def _validate_sessions(self) -> None:
        windows = self.signal.resolved_sessions()
        names = [window.name.strip() for window in windows]
        if len(set(names)) != len(names) or any(not name for name in names):
            raise ValueError("session names must be unique and non-empty")
        intervals: list[tuple[time, time, str]] = []
        for window in windows:
            open_clock = _clock(window.open)
            signal_end = _clock(window.signal_end)
            flatten = _clock(window.flatten)
            if not open_clock < signal_end < flatten:
                raise ValueError(
                    f"session {window.name!r}: open, signal_end, and flatten must be increasing"
                )
            if not 1 <= window.opening_range_minutes <= 60:
                raise ValueError(f"session {window.name!r}: opening_range_minutes must be in [1, 60]")
            if not 0 < window.risk_fraction <= 1:
                raise ValueError(f"session {window.name!r}: risk_fraction must be in (0, 1]")
            min_or_atr = window.min_or_atr if window.min_or_atr is not None else self.signal.min_or_atr
            max_or_atr = window.max_or_atr if window.max_or_atr is not None else self.signal.max_or_atr
            if not 0 < min_or_atr < max_or_atr:
                raise ValueError(f"session {window.name!r}: opening-range ATR bounds are invalid")
            min_or_ticks = window.min_or_ticks if window.min_or_ticks is not None else self.signal.min_or_ticks
            max_or_ticks = window.max_or_ticks if window.max_or_ticks is not None else self.signal.max_or_ticks
            if not 1 <= min_or_ticks < max_or_ticks:
                raise ValueError(f"session {window.name!r}: opening-range tick bounds are invalid")
            if window.max_spread_ticks is not None and window.max_spread_ticks <= 0:
                raise ValueError(f"session {window.name!r}: max_spread_ticks must be positive")
            if window.slippage_ticks_per_side is not None and window.slippage_ticks_per_side < 0:
                raise ValueError(f"session {window.name!r}: slippage cannot be negative")
            if (
                window.min_breakout_relative_volume is not None
                and window.min_breakout_relative_volume <= 0
            ):
                raise ValueError(f"session {window.name!r}: relative-volume floor must be positive")
            intervals.append((open_clock, flatten, window.name))
        intervals.sort()
        for (_, first_end, first_name), (second_start, _, second_name) in zip(intervals, intervals[1:]):
            if second_start <= first_end:
                raise ValueError(
                    f"sessions {first_name!r} and {second_name!r} overlap; windows must be disjoint"
                )


def load_config(path: str | Path) -> ResearchConfig:
    with Path(path).open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    config = ResearchConfig.from_mapping(raw)
    config.validate()
    return config
