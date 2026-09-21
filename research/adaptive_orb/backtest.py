from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
import math
from statistics import mean, pstdev
from typing import Iterable
from zoneinfo import ZoneInfo

from .config import ResearchConfig, SessionWindowConfig
from .levels import pad_stop, shave_target
from .model import Bar, DailyResult, Direction, Trade

# CME index futures roll to the next trading date at the 17:00 ET maintenance break;
# the 18:00 ET Globex reopen therefore belongs to the following trading day.
_TRADE_DATE_ROLLOVER = time(17, 0)


@dataclass(frozen=True)
class BacktestResult:
    trades: tuple[Trade, ...]
    daily: tuple[DailyResult, ...]
    summary: dict[str, float | int | None]
    diagnostics: dict[str, dict[str, int]]

    def trade_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for trade in self.trades:
            row = asdict(trade)
            row["direction"] = trade.direction.name.lower()
            row["signal_time"] = trade.signal_time.isoformat()
            row["entry_time"] = trade.entry_time.isoformat()
            row["exit_time"] = trade.exit_time.isoformat()
            rows.append(row)
        return rows


@dataclass
class _Candidate:
    direction: Direction
    breakout_index: int
    expires_index: int


@dataclass(frozen=True)
class _WindowContext:
    """Per-window parameters after applying session overrides to the globals."""

    window: SessionWindowConfig
    min_or_atr: float
    max_or_atr: float
    min_or_ticks: int
    max_or_ticks: int
    max_spread_ticks: float
    slippage_ticks_per_side: float
    min_breakout_relative_volume: float
    risk_fraction: float


def _window_context(window: SessionWindowConfig, config: ResearchConfig) -> _WindowContext:
    signal, instrument = config.signal, config.instrument
    return _WindowContext(
        window=window,
        min_or_atr=window.min_or_atr if window.min_or_atr is not None else signal.min_or_atr,
        max_or_atr=window.max_or_atr if window.max_or_atr is not None else signal.max_or_atr,
        min_or_ticks=window.min_or_ticks if window.min_or_ticks is not None else signal.min_or_ticks,
        max_or_ticks=window.max_or_ticks if window.max_or_ticks is not None else signal.max_or_ticks,
        max_spread_ticks=(
            window.max_spread_ticks if window.max_spread_ticks is not None else signal.max_spread_ticks
        ),
        slippage_ticks_per_side=(
            window.slippage_ticks_per_side
            if window.slippage_ticks_per_side is not None
            else instrument.slippage_ticks_per_side
        ),
        min_breakout_relative_volume=(
            window.min_breakout_relative_volume
            if window.min_breakout_relative_volume is not None
            else signal.min_breakout_relative_volume
        ),
        risk_fraction=window.risk_fraction,
    )


def _parse_clock(value: str) -> time:
    hour, minute = (int(part) for part in value.split(":"))
    return time(hour, minute)


def _trade_date(local: datetime) -> date:
    if local.time() >= _TRADE_DATE_ROLLOVER:
        return local.date() + timedelta(days=1)
    return local.date()


def _trade_date_groups(bars: Iterable[Bar], timezone: ZoneInfo) -> list[tuple[str, list[Bar]]]:
    grouped: dict[str, list[Bar]] = defaultdict(list)
    for bar in bars:
        local = bar.timestamp.astimezone(timezone)
        grouped[_trade_date(local).isoformat()].append(bar)
    return sorted(grouped.items())


def _window_sort_key(window: SessionWindowConfig) -> tuple[int, time]:
    open_clock = _parse_clock(window.open)
    # Clock times at/after the rollover open the trading date and come first.
    return (0 if open_clock >= _TRADE_DATE_ROLLOVER else 1, open_clock)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return 0.5 * (ordered[middle - 1] + ordered[middle])


def _signed_l2_ok(bar: Bar, direction: Direction, config: ResearchConfig, context: _WindowContext) -> bool:
    signal = config.signal
    if not signal.require_l2:
        return True
    if not bar.has_l2:
        return False
    assert bar.l2_composite is not None
    assert bar.l2_persistence is not None
    assert bar.spread_ticks is not None
    assert bar.l2_age_ms is not None
    signed = float(direction.value)
    return (
        signed * bar.l2_composite >= signal.min_l2_composite
        and signed * bar.l2_persistence >= signal.min_l2_persistence
        and bar.spread_ticks <= context.max_spread_ticks
        and bar.l2_age_ms <= signal.max_l2_age_ms
    )


def _vwap_path(bars: list[Bar]) -> list[float]:
    exact_flags = [bar.trade_value is not None for bar in bars]
    if any(exact_flags) and not all(exact_flags):
        raise ValueError("trade_value must be present on every bar in a session or omitted on every bar")
    use_exact_trade_value = all(exact_flags)
    values: list[float] = []
    cumulative_volume = 0.0
    cumulative_value = 0.0
    for bar in bars:
        volume = max(0.0, bar.volume)
        typical = (bar.high + bar.low + bar.close) / 3.0
        if volume > 0:
            cumulative_volume += volume
            cumulative_value += bar.trade_value if use_exact_trade_value else typical * volume
        values.append(cumulative_value / cumulative_volume if cumulative_volume else bar.close)
    return values


def _volume_ema_before(bars: list[Bar], alpha: float) -> list[float | None]:
    result: list[float | None] = []
    ema: float | None = None
    for bar in bars:
        result.append(ema)
        ema = bar.volume if ema is None else alpha * bar.volume + (1.0 - alpha) * ema
    return result


def _regime_ok(width: float, atr: float, config: ResearchConfig, context: _WindowContext) -> bool:
    ticks = width / config.instrument.tick_size
    return (
        context.min_or_ticks <= ticks <= context.max_or_ticks
        and context.min_or_atr <= width / atr <= context.max_or_atr
    )


def _trend_ok(
    direction: Direction,
    index: int,
    bars: list[Bar],
    vwaps: list[float],
    config: ResearchConfig,
) -> bool:
    signal, instrument = config.signal, config.instrument
    lookback = signal.vwap_slope_bars
    if index < lookback:
        return False
    signed = float(direction.value)
    price_alignment = signed * (bars[index].close - vwaps[index]) > 0
    slope_ticks = signed * (vwaps[index] - vwaps[index - lookback]) / instrument.tick_size
    return price_alignment and slope_ticks >= signal.min_vwap_slope_ticks


def _all_in_risk_per_contract(stop_ticks: int, config: ResearchConfig, context: _WindowContext) -> float:
    instrument = config.instrument
    return (
        stop_ticks * instrument.tick_value
        + 2.0 * instrument.commission_per_side
        + 2.0 * context.slippage_ticks_per_side * instrument.tick_value
    )


def _entry_quantity(stop_ticks: int, config: ResearchConfig, context: _WindowContext) -> int:
    risk = config.risk
    per_contract = _all_in_risk_per_contract(stop_ticks, config, context)
    if per_contract <= 0:
        return 0
    budget = risk.max_risk_per_trade * context.risk_fraction
    return min(risk.max_contracts, int(budget // per_contract))


def _fill_at_market(
    reference: float,
    direction: Direction,
    is_entry: bool,
    config: ResearchConfig,
    context: _WindowContext,
) -> float:
    slip = context.slippage_ticks_per_side * config.instrument.tick_size
    # Entry moves with the trade direction; exit moves against it.
    sign = direction.value if is_entry else -direction.value
    return reference + sign * slip


@dataclass(frozen=True)
class _EntryPlan:
    entry_index: int
    entry_price: float
    entry_type: str
    stop_reference: float | None  # wall-based stop anchor, if any


def _supportive_wall(bar: Bar, direction: Direction, config: ResearchConfig) -> tuple[float, float] | None:
    execution = config.execution
    # Disabling L2 is a complete depth-independent mode, even with a saved
    # configuration that still requests wall entries or a CSV containing walls.
    if not config.signal.require_l2 or not execution.use_wall_entries:
        return None
    if direction is Direction.LONG:
        price, ratio = bar.bid_wall_price, bar.bid_wall_ratio
    else:
        price, ratio = bar.ask_wall_price, bar.ask_wall_ratio
    if price is None or ratio is None or ratio < execution.min_wall_ratio:
        return None
    return price, ratio


def _plan_entry(
    direction: Direction,
    signal_index: int,
    bars: list[Bar],
    opening_high: float,
    opening_low: float,
    width: float,
    config: ResearchConfig,
    context: _WindowContext,
) -> tuple[_EntryPlan | None, str]:
    """Choose market entry or a passive limit queued in front of a resting wall.

    Wall fills are simulated conservatively: the limit counts as filled only when a
    later bar trades strictly through it. If the limit is untouched within the
    timeout, the configured fallback (market by default) keeps trade frequency
    aligned with the base strategy.
    """

    entry_index = signal_index + 1
    if entry_index >= len(bars):
        return None, "no_entry_bar"
    execution = config.execution
    tick = config.instrument.tick_size
    decision_reference = bars[entry_index].open

    wall = _supportive_wall(bars[signal_index], direction, config)
    if wall is not None:
        wall_price, _ = wall
        limit = wall_price + direction.value * execution.wall_offset_ticks * tick
        chase_ticks = direction.value * (decision_reference - limit) / tick
        if 1.0 <= chase_ticks <= execution.max_wall_chase_ticks:
            last_scan = min(entry_index + execution.wall_entry_timeout_bars, len(bars))
            failure_level = (
                opening_high - config.risk.failure_close_fraction * width
                if direction is Direction.LONG
                else opening_low + config.risk.failure_close_fraction * width
            )
            for index in range(entry_index, last_scan):
                bar = bars[index]
                traded_through = (
                    bar.low <= limit - tick if direction is Direction.LONG else bar.high >= limit + tick
                )
                if traded_through:
                    stop_reference = wall_price - direction.value * execution.wall_stop_pad_ticks * tick
                    return _EntryPlan(index, limit, "wall_limit", stop_reference), ""
                thesis_dead = (
                    bar.close < failure_level if direction is Direction.LONG else bar.close > failure_level
                )
                if thesis_dead:
                    return None, "wall_limit_cancelled"
            if execution.wall_entry_fallback == "market" and last_scan < len(bars):
                still_valid = (
                    bars[last_scan - 1].close >= opening_high
                    if direction is Direction.LONG
                    else bars[last_scan - 1].close <= opening_low
                )
                if not still_valid:
                    return None, "wall_limit_cancelled"
                reference = bars[last_scan].open
                price = _fill_at_market(reference, direction, True, config, context)
                return _EntryPlan(last_scan, price, "wall_fallback_market", None), ""
            return None, "wall_limit_timeout_skipped"

    price = _fill_at_market(decision_reference, direction, True, config, context)
    return _EntryPlan(entry_index, price, "market", None), ""


def _simulate_trade(
    session: str,
    window_name: str,
    flatten_clock: time,
    direction: Direction,
    signal_index: int,
    retest_extreme: float,
    bars: list[Bar],
    vwaps: list[float],
    opening_high: float,
    opening_low: float,
    atr: float,
    config: ResearchConfig,
    context: _WindowContext,
) -> tuple[Trade | None, str]:
    instrument, risk, execution = config.instrument, config.risk, config.execution
    tick = instrument.tick_size
    width = opening_high - opening_low

    plan, reason = _plan_entry(
        direction, signal_index, bars, opening_high, opening_low, width, config, context
    )
    if plan is None:
        return None, reason
    entry_index = plan.entry_index
    entry = plan.entry_price
    decision_reference = bars[signal_index + 1].open

    if direction is Direction.LONG:
        structural = min(
            retest_extreme - tick,
            opening_high - risk.structural_stop_fraction * width,
        )
    else:
        structural = max(
            retest_extreme + tick,
            opening_low + risk.structural_stop_fraction * width,
        )
    if plan.stop_reference is not None:
        # Wall-based entries anchor their stop behind the wall: if the wall breaks,
        # the reason for the fill is gone.
        structural = plan.stop_reference
    raw_stop_ticks = math.ceil(direction.value * (entry - structural) / tick)
    stop_ticks = max(risk.min_stop_ticks, raw_stop_ticks)
    if stop_ticks > risk.max_stop_ticks:
        return None, "stop_too_wide"

    stop = entry - direction.value * stop_ticks * instrument.tick_size
    if execution.use_round_levels:
        stop = pad_stop(
            entry,
            stop,
            direction.value,
            tick,
            execution.round_level_stop_trigger_ticks,
            execution.round_level_stop_pad_ticks,
            risk.max_stop_ticks,
        )
        stop_ticks = max(stop_ticks, math.ceil(direction.value * (entry - stop) / tick))
    quantity = _entry_quantity(stop_ticks, config, context)
    if quantity < 1:
        return None, "size_zero"

    target_ticks = math.ceil(stop_ticks * risk.reward_to_risk)
    target = entry + direction.value * target_ticks * instrument.tick_size
    if execution.use_round_levels:
        target = shave_target(
            entry,
            target,
            direction.value,
            tick,
            execution.round_level_front_ticks,
            execution.round_level_target_window_ticks,
        )

    timezone = ZoneInfo(config.signal.timezone)
    exit_price: float | None = None
    exit_time: datetime | None = None
    exit_reason = ""
    hold_bars = 0

    for index in range(entry_index, len(bars)):
        bar = bars[index]
        local_time = bar.timestamp.astimezone(timezone).time()
        hold_bars = index - entry_index + 1

        if local_time >= flatten_clock:
            exit_price = _fill_at_market(bar.open, direction, False, config, context)
            exit_time = bar.timestamp
            exit_reason = "session_flatten"
            break

        hit_stop = bar.low <= stop if direction is Direction.LONG else bar.high >= stop
        hit_target = bar.high >= target if direction is Direction.LONG else bar.low <= target

        # Minute bars do not reveal within-bar order. A stop-first convention is the
        # conservative choice when both levels print in the same bar.
        if hit_stop:
            # A stop-market cannot fill at the old stop after a gap through it.
            reference = min(stop, bar.open) if direction is Direction.LONG else max(stop, bar.open)
            exit_price = _fill_at_market(reference, direction, False, config, context)
            exit_time = bar.timestamp
            exit_reason = "stop"
            break
        if hit_target:
            exit_price = _fill_at_market(target, direction, False, config, context)
            exit_time = bar.timestamp
            exit_reason = "target"
            break

        failure_level = (
            opening_high - risk.failure_close_fraction * width
            if direction is Direction.LONG
            else opening_low + risk.failure_close_fraction * width
        )
        failed_level = bar.close < failure_level if direction is Direction.LONG else bar.close > failure_level
        failed_vwap = bar.close < vwaps[index] if direction is Direction.LONG else bar.close > vwaps[index]
        if failed_level and failed_vwap:
            next_index = min(index + 1, len(bars) - 1)
            reference = bars[next_index].open if next_index > index else bar.close
            exit_price = _fill_at_market(reference, direction, False, config, context)
            exit_time = bars[next_index].timestamp if next_index > index else bar.timestamp
            exit_reason = "failed_retest"
            hold_bars = next_index - entry_index + 1
            break

        if hold_bars >= risk.time_stop_bars:
            next_index = min(index + 1, len(bars) - 1)
            reference = bars[next_index].open if next_index > index else bar.close
            exit_price = _fill_at_market(reference, direction, False, config, context)
            exit_time = bars[next_index].timestamp if next_index > index else bar.timestamp
            exit_reason = "time_stop"
            hold_bars = next_index - entry_index + 1
            break

    if exit_price is None or exit_time is None:
        exit_price = _fill_at_market(bars[-1].close, direction, False, config, context)
        exit_time = bars[-1].timestamp
        exit_reason = "end_of_data"

    price_ticks = direction.value * (exit_price - entry) / instrument.tick_size
    gross = price_ticks * instrument.tick_value * quantity
    commission = 2.0 * instrument.commission_per_side * quantity
    net = gross - commission
    risk_dollars = _all_in_risk_per_contract(stop_ticks, config, context) * quantity
    trade = Trade(
        session=session,
        direction=direction,
        signal_time=bars[signal_index].timestamp,
        entry_time=bars[entry_index].timestamp,
        exit_time=exit_time,
        decision_price=decision_reference,
        entry_price=entry,
        exit_price=exit_price,
        stop_price=stop,
        target_price=target,
        quantity=quantity,
        exit_reason=exit_reason,
        gross_pnl=gross,
        commission=commission,
        net_pnl=net,
        risk_dollars=risk_dollars,
        hold_bars=hold_bars,
        opening_range=width,
        atr=atr,
        l2_composite=bars[signal_index].l2_composite if config.signal.require_l2 else None,
        window=window_name,
        entry_type=plan.entry_type,
    )
    return trade, ""


def _window_trade(
    session: str,
    bars: list[Bar],
    atr: float,
    config: ResearchConfig,
    context: _WindowContext,
) -> tuple[Trade | None, str]:
    window = context.window
    timezone = ZoneInfo(config.signal.timezone)
    open_clock = _parse_clock(window.open)
    signal_end = _parse_clock(window.signal_end)
    flatten_clock = _parse_clock(window.flatten)
    open_dt = datetime.combine(bars[0].timestamp.astimezone(timezone).date(), open_clock, timezone)
    range_end_dt = open_dt + timedelta(minutes=window.opening_range_minutes)

    opening_indices = [
        index
        for index, bar in enumerate(bars)
        if open_dt <= bar.timestamp.astimezone(timezone) < range_end_dt
    ]
    expected_opening_minutes = {
        open_dt + timedelta(minutes=offset) for offset in range(window.opening_range_minutes)
    }
    observed_opening_minutes = {
        bars[index].timestamp.astimezone(timezone).replace(second=0, microsecond=0)
        for index in opening_indices
    }
    if observed_opening_minutes != expected_opening_minutes:
        return None, "incomplete_opening_range"
    opening_high = max(bars[index].high for index in opening_indices)
    opening_low = min(bars[index].low for index in opening_indices)
    width = opening_high - opening_low
    if width <= 0 or not _regime_ok(width, atr, config, context):
        return None, "range_regime_rejected"

    vwaps = _vwap_path(bars)
    volume_before = _volume_ema_before(bars, config.signal.volume_ema_alpha)
    first_signal_index = max(opening_indices) + 1
    buffer = max(
        config.signal.min_breakout_buffer_ticks * config.instrument.tick_size,
        config.signal.breakout_buffer_fraction * width,
    )
    tolerance = config.signal.retest_tolerance_fraction * width
    candidate: _Candidate | None = None
    saw_breakout = False

    for index in range(first_signal_index, len(bars) - 1):
        bar = bars[index]
        local_time = bar.timestamp.astimezone(timezone).time()
        if local_time > signal_end:
            break
        prior_volume = volume_before[index]
        relative_volume = bar.volume / prior_volume if prior_volume and prior_volume > 0 else 0.0

        if candidate is None:
            if relative_volume < context.min_breakout_relative_volume:
                continue
            if (
                bar.close >= opening_high + buffer
                and _trend_ok(Direction.LONG, index, bars, vwaps, config)
            ):
                candidate = _Candidate(Direction.LONG, index, index + config.signal.max_retest_bars)
                saw_breakout = True
            elif (
                bar.close <= opening_low - buffer
                and _trend_ok(Direction.SHORT, index, bars, vwaps, config)
            ):
                candidate = _Candidate(Direction.SHORT, index, index + config.signal.max_retest_bars)
                saw_breakout = True
            continue

        if index > candidate.expires_index:
            candidate = None
            continue

        direction = candidate.direction
        if direction is Direction.LONG:
            invalid = bar.close < opening_high - config.risk.failure_close_fraction * width
            retest = bar.low <= opening_high + tolerance and bar.close >= opening_high
            retest_extreme = bar.low
        else:
            invalid = bar.close > opening_low + config.risk.failure_close_fraction * width
            retest = bar.high >= opening_low - tolerance and bar.close <= opening_low
            retest_extreme = bar.high
        if invalid:
            candidate = None
            continue
        if not retest:
            continue
        if not _trend_ok(direction, index, bars, vwaps, config):
            continue
        if not _signed_l2_ok(bar, direction, config, context):
            continue
        return _simulate_trade(
            session,
            window.name,
            flatten_clock,
            direction,
            index,
            retest_extreme,
            bars,
            vwaps,
            opening_high,
            opening_low,
            atr,
            config,
            context,
        )
    return None, ("no_confirmed_retest" if saw_breakout else "no_breakout")


def _summary(trades: list[Trade], daily: list[DailyResult]) -> dict[str, float | int | None]:
    net = [item.net_pnl for item in trades]
    daily_pnl = [item.net_pnl for item in daily]
    wins = [value for value in net if value > 0]
    losses = [value for value in net if value < 0]
    gross_profit = sum(wins)
    gross_loss = -sum(losses)
    equity = 0.0
    high_water = 0.0
    max_drawdown = 0.0
    for value in daily_pnl:
        equity += value
        high_water = max(high_water, equity)
        max_drawdown = max(max_drawdown, high_water - equity)
    daily_std = pstdev(daily_pnl) if len(daily_pnl) > 1 else 0.0
    sharpe = (mean(daily_pnl) / daily_std * math.sqrt(252.0)) if daily_std > 0 else None
    return {
        "trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": len(wins) / len(trades) if trades else None,
        "gross_pnl": sum(item.gross_pnl for item in trades),
        "commission": sum(item.commission for item in trades),
        "net_pnl": sum(net),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else None,
        "average_trade": mean(net) if net else None,
        "average_hold_bars": mean(item.hold_bars for item in trades) if trades else None,
        "max_drawdown": max_drawdown,
        "annualized_daily_sharpe": sharpe,
        "cost_to_abs_gross": (
            sum(item.commission for item in trades) / sum(abs(item.gross_pnl) for item in trades)
            if trades and sum(abs(item.gross_pnl) for item in trades) > 0
            else None
        ),
    }


def run_backtest(bars: Iterable[Bar], config: ResearchConfig) -> BacktestResult:
    config.validate()
    timezone = ZoneInfo(config.signal.timezone)
    windows = sorted(config.signal.resolved_sessions(), key=_window_sort_key)
    contexts = [_window_context(window, config) for window in windows]
    groups = _trade_date_groups(bars, timezone)
    trades: list[Trade] = []
    daily: list[DailyResult] = []
    diagnostics: dict[str, Counter[str]] = {context.window.name: Counter() for context in contexts}
    completed_true_ranges: list[float] = []
    previous_close: float | None = None

    for session, day_bars in groups:
        if len(completed_true_ranges) >= config.signal.atr_lookback_sessions:
            atr_values = completed_true_ranges[-config.signal.atr_lookback_sessions :]
            atr = _median(atr_values)
            assert atr is not None
            day_net = 0.0
            day_gross = 0.0
            day_commission = 0.0
            day_trades = 0
            for context in contexts:
                if day_net <= -config.risk.internal_daily_loss_limit:
                    diagnostics[context.window.name]["daily_loss_lockout"] += 1
                    continue
                open_clock = _parse_clock(context.window.open)
                flatten_clock = _parse_clock(context.window.flatten)
                window_bars = [
                    bar
                    for bar in day_bars
                    if open_clock <= bar.timestamp.astimezone(timezone).time() <= flatten_clock
                ]
                if not window_bars:
                    diagnostics[context.window.name]["no_data"] += 1
                    continue
                trade, reason = _window_trade(session, window_bars, atr, config, context)
                if trade is not None:
                    trades.append(trade)
                    day_net += trade.net_pnl
                    day_gross += trade.gross_pnl
                    day_commission += trade.commission
                    day_trades += 1
                    diagnostics[context.window.name]["trades"] += 1
                else:
                    diagnostics[context.window.name][reason] += 1
            daily.append(DailyResult(session, day_net, day_gross, day_commission, day_trades))

        # Full-trading-day true range (all Globex bars in the trade date). This matches
        # the platform's DAY1 futures bars used by the live strategy.
        day_high = max(bar.high for bar in day_bars)
        day_low = min(bar.low for bar in day_bars)
        true_range = day_high - day_low
        if previous_close is not None:
            true_range = max(true_range, abs(day_high - previous_close), abs(day_low - previous_close))
        completed_true_ranges.append(true_range)
        previous_close = day_bars[-1].close

    return BacktestResult(
        tuple(trades),
        tuple(daily),
        _summary(trades, daily),
        {name: dict(counter) for name, counter in diagnostics.items()},
    )
