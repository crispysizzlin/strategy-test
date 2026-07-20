from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .config import PropConfig
from .model import DailyResult, Trade


@dataclass(frozen=True)
class PropSimulation:
    status: str
    ending_balance: float
    max_loss_floor: float
    days_processed: int
    days_to_target: int | None
    soft_daily_locks: int
    largest_profit_day_fraction: float | None
    conservative_worst_intraday_buffer: float


def simulate_eod_trailing_account(
    daily: Iterable[DailyResult],
    trades: Iterable[Trade],
    profile: PropConfig,
) -> PropSimulation:
    """Simulate an EOD-trailing evaluation with a conservative intraday path.

    A minute-bar backtest cannot know the exact tick path before a winning exit. For every
    trade, this simulator assumes the full planned all-in risk could have occurred before
    the final daily P&L. That is intentionally harsher than using daily closes alone.
    """

    if not profile.enabled:
        return PropSimulation("disabled", profile.initial_balance, profile.initial_balance - profile.max_loss, 0, None, 0, None, profile.max_loss)

    daily_rows = list(daily)
    risks_by_session: dict[str, float] = {}
    for trade in trades:
        risks_by_session[trade.session] = risks_by_session.get(trade.session, 0.0) + trade.risk_dollars

    balance = profile.initial_balance
    highest_eod_balance = balance
    floor = profile.initial_balance - profile.max_loss
    soft_locks = 0
    daily_pnls: list[float] = []
    days_to_target: int | None = None
    status = "active"
    worst_buffer = balance - floor

    for index, row in enumerate(daily_rows, start=1):
        planned_risk = risks_by_session.get(row.session, 0.0)
        conservative_low = balance - planned_risk
        worst_buffer = min(worst_buffer, conservative_low - floor)
        if conservative_low <= floor:
            status = "breached"
            return PropSimulation(
                status,
                balance,
                floor,
                index,
                days_to_target,
                soft_locks,
                _best_day_fraction(daily_pnls),
                worst_buffer,
            )
        if profile.daily_loss is not None and planned_risk >= profile.daily_loss:
            soft_locks += 1

        balance += row.net_pnl
        daily_pnls.append(row.net_pnl)
        if balance <= floor:
            status = "breached"
            return PropSimulation(
                status,
                balance,
                floor,
                index,
                days_to_target,
                soft_locks,
                _best_day_fraction(daily_pnls),
                min(worst_buffer, balance - floor),
            )

        highest_eod_balance = max(highest_eod_balance, balance)
        floor = min(profile.locked_floor, highest_eod_balance - profile.max_loss)
        worst_buffer = min(worst_buffer, balance - floor)
        if days_to_target is None and balance >= profile.initial_balance + profile.profit_target:
            days_to_target = index
            status = "passed"
            break

    return PropSimulation(
        status,
        balance,
        floor,
        min(len(daily_rows), days_to_target or len(daily_rows)),
        days_to_target,
        soft_locks,
        _best_day_fraction(daily_pnls),
        worst_buffer,
    )


def _best_day_fraction(daily_pnls: list[float]) -> float | None:
    total_net_profit = sum(daily_pnls)
    if total_net_profit <= 0:
        return None
    return max(0.0, max(daily_pnls, default=0.0)) / total_net_profit
