from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
import math
from statistics import mean, pstdev
from typing import Iterable
from zoneinfo import ZoneInfo

from .config import ResearchConfig
from .model import Bar, DailyResult, Direction, Trade


@dataclass(frozen=True)
class BacktestResult:
    trades: tuple[Trade, ...]
    daily: tuple[DailyResult, ...]
    summary: dict[str, float | int | None]

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


def _parse_clock(value: str) -> time:
    hour, minute = (int(part) for part in value.split(":"))
    return time(hour, minute)


def _session_groups(bars: Iterable[Bar], timezone: ZoneInfo) -> list[tuple[str, list[Bar]]]:
    grouped: dict[str, list[Bar]] = defaultdict(list)
    for bar in bars:
        local = bar.timestamp.astimezone(timezone)
        grouped[local.date().isoformat()].append(bar)
    return sorted(grouped.items())


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return 0.5 * (ordered[middle - 1] + ordered[middle])


def _signed_l2_ok(bar: Bar, direction: Direction, config: ResearchConfig) -> bool:
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
        and bar.spread_ticks <= signal.max_spread_ticks
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


def _regime_ok(width: float, atr: float, config: ResearchConfig) -> bool:
    signal, instrument = config.signal, config.instrument
    ticks = width / instrument.tick_size
    return (
        signal.min_or_ticks <= ticks <= signal.max_or_ticks
        and signal.min_or_atr <= width / atr <= signal.max_or_atr
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


def _entry_quantity(stop_ticks: int, config: ResearchConfig) -> int:
    instrument, risk = config.instrument, config.risk
    all_in_risk_per_contract = (
        stop_ticks * instrument.tick_value
        + 2.0 * instrument.commission_per_side
        + 2.0 * instrument.slippage_ticks_per_side * instrument.tick_value
    )
    if all_in_risk_per_contract <= 0:
        return 0
    return min(risk.max_contracts, int(risk.max_risk_per_trade // all_in_risk_per_contract))


def _fill_at_market(reference: float, direction: Direction, is_entry: bool, config: ResearchConfig) -> float:
    slip = config.instrument.slippage_ticks_per_side * config.instrument.tick_size
    # Entry moves with the trade direction; exit moves against it.
    sign = direction.value if is_entry else -direction.value
    return reference + sign * slip


def _simulate_trade(
    session: str,
    direction: Direction,
    signal_index: int,
    retest_extreme: float,
    bars: list[Bar],
    vwaps: list[float],
    opening_high: float,
    opening_low: float,
    atr: float,
    config: ResearchConfig,
) -> Trade | None:
    entry_index = signal_index + 1
    if entry_index >= len(bars):
        return None

    instrument, risk = config.instrument, config.risk
    width = opening_high - opening_low
    decision_reference = bars[entry_index].open
    entry = _fill_at_market(decision_reference, direction, True, config)
    if direction is Direction.LONG:
        structural = min(
            retest_extreme - instrument.tick_size,
            opening_high - risk.structural_stop_fraction * width,
        )
        raw_stop_ticks = math.ceil((decision_reference - structural) / instrument.tick_size)
    else:
        structural = max(
            retest_extreme + instrument.tick_size,
            opening_low + risk.structural_stop_fraction * width,
        )
        raw_stop_ticks = math.ceil((structural - decision_reference) / instrument.tick_size)

    stop_ticks = max(risk.min_stop_ticks, raw_stop_ticks)
    if stop_ticks > risk.max_stop_ticks:
        return None
    quantity = _entry_quantity(stop_ticks, config)
    if quantity < 1:
        return None

    stop = entry - direction.value * stop_ticks * instrument.tick_size
    target_ticks = math.ceil(stop_ticks * risk.reward_to_risk)
    target = entry + direction.value * target_ticks * instrument.tick_size
    flatten = _parse_clock(config.signal.flatten_time)
    exit_price: float | None = None
    exit_time: datetime | None = None
    exit_reason = ""
    hold_bars = 0

    for index in range(entry_index, len(bars)):
        bar = bars[index]
        local_time = bar.timestamp.astimezone(ZoneInfo(config.signal.timezone)).time()
        hold_bars = index - entry_index + 1

        if local_time >= flatten:
            exit_price = _fill_at_market(bar.open, direction, False, config)
            exit_time = bar.timestamp
            exit_reason = "session_flatten"
            break

        hit_stop = bar.low <= stop if direction is Direction.LONG else bar.high >= stop
        hit_target = bar.high >= target if direction is Direction.LONG else bar.low <= target

        # Minute bars do not reveal within-bar order. A stop-first convention is the
        # conservative choice when both levels print in the same bar.
        if hit_stop:
            exit_price = _fill_at_market(stop, direction, False, config)
            exit_time = bar.timestamp
            exit_reason = "stop"
            break
        if hit_target:
            exit_price = _fill_at_market(target, direction, False, config)
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
            exit_price = _fill_at_market(reference, direction, False, config)
            exit_time = bars[next_index].timestamp if next_index > index else bar.timestamp
            exit_reason = "failed_retest"
            hold_bars = next_index - entry_index + 1
            break

        if hold_bars >= risk.time_stop_bars:
            next_index = min(index + 1, len(bars) - 1)
            reference = bars[next_index].open if next_index > index else bar.close
            exit_price = _fill_at_market(reference, direction, False, config)
            exit_time = bars[next_index].timestamp if next_index > index else bar.timestamp
            exit_reason = "time_stop"
            hold_bars = next_index - entry_index + 1
            break

    if exit_price is None or exit_time is None:
        exit_price = _fill_at_market(bars[-1].close, direction, False, config)
        exit_time = bars[-1].timestamp
        exit_reason = "end_of_data"

    price_ticks = direction.value * (exit_price - entry) / instrument.tick_size
    gross = price_ticks * instrument.tick_value * quantity
    commission = 2.0 * instrument.commission_per_side * quantity
    net = gross - commission
    risk_dollars = (
        stop_ticks * instrument.tick_value
        + 2.0 * instrument.commission_per_side
        + 2.0 * instrument.slippage_ticks_per_side * instrument.tick_value
    ) * quantity
    return Trade(
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
        l2_composite=bars[signal_index].l2_composite,
    )


def _session_trade(
    session: str,
    bars: list[Bar],
    atr: float,
    config: ResearchConfig,
) -> Trade | None:
    timezone = ZoneInfo(config.signal.timezone)
    open_clock = _parse_clock(config.signal.session_open)
    signal_end = _parse_clock(config.signal.signal_end)
    open_dt = datetime.combine(bars[0].timestamp.astimezone(timezone).date(), open_clock, timezone)
    range_end_dt = open_dt + timedelta(minutes=config.signal.opening_range_minutes)

    rth = [bar for bar in bars if open_clock <= bar.timestamp.astimezone(timezone).time()]
    opening_indices = [
        index
        for index, bar in enumerate(rth)
        if open_dt <= bar.timestamp.astimezone(timezone) < range_end_dt
    ]
    expected_opening_minutes = {
        open_dt + timedelta(minutes=offset)
        for offset in range(config.signal.opening_range_minutes)
    }
    observed_opening_minutes = {
        rth[index].timestamp.astimezone(timezone).replace(second=0, microsecond=0)
        for index in opening_indices
    }
    if observed_opening_minutes != expected_opening_minutes:
        return None
    opening_high = max(rth[index].high for index in opening_indices)
    opening_low = min(rth[index].low for index in opening_indices)
    width = opening_high - opening_low
    if width <= 0 or not _regime_ok(width, atr, config):
        return None

    vwaps = _vwap_path(rth)
    volume_before = _volume_ema_before(rth, config.signal.volume_ema_alpha)
    first_signal_index = max(opening_indices) + 1
    buffer = max(
        config.signal.min_breakout_buffer_ticks * config.instrument.tick_size,
        config.signal.breakout_buffer_fraction * width,
    )
    tolerance = config.signal.retest_tolerance_fraction * width
    candidate: _Candidate | None = None

    for index in range(first_signal_index, len(rth) - 1):
        bar = rth[index]
        local_time = bar.timestamp.astimezone(timezone).time()
        if local_time > signal_end:
            break
        prior_volume = volume_before[index]
        relative_volume = bar.volume / prior_volume if prior_volume and prior_volume > 0 else 0.0

        if candidate is None:
            if relative_volume < config.signal.min_breakout_relative_volume:
                continue
            if (
                bar.close >= opening_high + buffer
                and _trend_ok(Direction.LONG, index, rth, vwaps, config)
            ):
                candidate = _Candidate(Direction.LONG, index, index + config.signal.max_retest_bars)
            elif (
                bar.close <= opening_low - buffer
                and _trend_ok(Direction.SHORT, index, rth, vwaps, config)
            ):
                candidate = _Candidate(Direction.SHORT, index, index + config.signal.max_retest_bars)
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
        if not _trend_ok(direction, index, rth, vwaps, config):
            continue
        if not _signed_l2_ok(bar, direction, config):
            continue
        return _simulate_trade(
            session,
            direction,
            index,
            retest_extreme,
            rth,
            vwaps,
            opening_high,
            opening_low,
            atr,
            config,
        )
    return None


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
    sessions = _session_groups(bars, timezone)
    trades: list[Trade] = []
    daily: list[DailyResult] = []
    completed_true_ranges: list[float] = []
    previous_close: float | None = None
    open_clock = _parse_clock(config.signal.session_open)
    flatten_clock = _parse_clock(config.signal.flatten_time)

    for session, all_session_bars in sessions:
        rth = [
            bar
            for bar in all_session_bars
            if open_clock <= bar.timestamp.astimezone(timezone).time() <= flatten_clock
        ]
        if not rth:
            continue

        if len(completed_true_ranges) >= config.signal.atr_lookback_sessions:
            atr_values = completed_true_ranges[-config.signal.atr_lookback_sessions :]
            atr = _median(atr_values)
            assert atr is not None
            trade = _session_trade(session, rth, atr, config)
            if trade is not None:
                trades.append(trade)
                daily.append(
                    DailyResult(
                        session=session,
                        net_pnl=trade.net_pnl,
                        gross_pnl=trade.gross_pnl,
                        commission=trade.commission,
                        trades=1,
                    )
                )
            else:
                daily.append(DailyResult(session, 0.0, 0.0, 0.0, 0))

        day_high = max(bar.high for bar in rth)
        day_low = min(bar.low for bar in rth)
        true_range = day_high - day_low
        if previous_close is not None:
            true_range = max(true_range, abs(day_high - previous_close), abs(day_low - previous_close))
        completed_true_ranges.append(true_range)
        previous_close = rth[-1].close

    return BacktestResult(tuple(trades), tuple(daily), _summary(trades, daily))
