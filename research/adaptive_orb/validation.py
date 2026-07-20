from __future__ import annotations

from dataclasses import dataclass
import math
import random
from statistics import mean, pstdev
from typing import Sequence


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def sample_skewness(values: Sequence[float]) -> float:
    if len(values) < 3:
        return 0.0
    mu = mean(values)
    sigma = pstdev(values)
    if sigma == 0:
        return 0.0
    return sum(((value - mu) / sigma) ** 3 for value in values) / len(values)


def sample_kurtosis(values: Sequence[float]) -> float:
    if len(values) < 4:
        return 3.0
    mu = mean(values)
    sigma = pstdev(values)
    if sigma == 0:
        return 3.0
    return sum(((value - mu) / sigma) ** 4 for value in values) / len(values)


def probabilistic_sharpe_ratio(
    returns: Sequence[float],
    benchmark_sharpe: float = 0.0,
    periods_per_year: int = 252,
) -> float | None:
    """Probability that the annualized Sharpe exceeds ``benchmark_sharpe``.

    Uses the non-normality adjustment from Bailey and López de Prado. The input must
    be a single, fixed-frequency return/P&L series; do not pass individual trade R
    multiples when trade frequency changes over time.
    """

    if len(returns) < 3:
        return None
    sigma = pstdev(returns)
    if sigma == 0:
        return None
    non_annualized = mean(returns) / sigma
    benchmark = benchmark_sharpe / math.sqrt(periods_per_year)
    skew = sample_skewness(returns)
    kurtosis = sample_kurtosis(returns)
    denominator = math.sqrt(
        max(1e-12, 1.0 - skew * non_annualized + ((kurtosis - 1.0) / 4.0) * non_annualized**2)
    )
    statistic = (non_annualized - benchmark) * math.sqrt(len(returns) - 1.0) / denominator
    return _normal_cdf(statistic)


def max_drawdown(values: Sequence[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


@dataclass(frozen=True)
class BootstrapSummary:
    median_terminal_pnl: float
    fifth_percentile_terminal_pnl: float
    median_max_drawdown: float
    ninety_fifth_percentile_max_drawdown: float
    loss_probability: float


def moving_block_bootstrap(
    daily_pnl: Sequence[float],
    *,
    simulations: int = 2_000,
    block_length: int = 5,
    seed: int = 7,
) -> BootstrapSummary | None:
    """Bootstrap daily P&L in blocks so short volatility clusters are not destroyed."""

    count = len(daily_pnl)
    if count < max(10, block_length * 2) or simulations < 1:
        return None
    rng = random.Random(seed)
    terminal: list[float] = []
    drawdowns: list[float] = []
    starts = range(0, count - block_length + 1)
    for _ in range(simulations):
        sample: list[float] = []
        while len(sample) < count:
            start = rng.choice(starts)
            sample.extend(daily_pnl[start : start + block_length])
        sample = sample[:count]
        terminal.append(sum(sample))
        drawdowns.append(max_drawdown(sample))
    terminal.sort()
    drawdowns.sort()

    def percentile(values: Sequence[float], probability: float) -> float:
        index = max(0, min(len(values) - 1, round(probability * (len(values) - 1))))
        return values[index]

    return BootstrapSummary(
        median_terminal_pnl=percentile(terminal, 0.50),
        fifth_percentile_terminal_pnl=percentile(terminal, 0.05),
        median_max_drawdown=percentile(drawdowns, 0.50),
        ninety_fifth_percentile_max_drawdown=percentile(drawdowns, 0.95),
        loss_probability=sum(value <= 0 for value in terminal) / len(terminal),
    )


@dataclass(frozen=True)
class ValidationGate:
    passed: bool
    failures: tuple[str, ...]


def production_gate(
    *,
    out_of_sample_trades: int,
    net_profit_factor: float | None,
    psr: float | None,
    stressed_net_pnl: float,
    profitable_walk_forward_windows: int,
    total_walk_forward_windows: int,
    live_sim_sessions: int,
) -> ValidationGate:
    """A deliberately strict research gate; passing is not a profit guarantee."""

    failures: list[str] = []
    if out_of_sample_trades < 150:
        failures.append("fewer than 150 out-of-sample trades")
    if net_profit_factor is None or net_profit_factor < 1.15:
        failures.append("net out-of-sample profit factor below 1.15")
    if psr is None or psr < 0.95:
        failures.append("probabilistic Sharpe ratio below 95%")
    if stressed_net_pnl <= 0:
        failures.append("not profitable with doubled commissions and slippage")
    if total_walk_forward_windows < 5 or profitable_walk_forward_windows / total_walk_forward_windows < 0.60:
        failures.append("fewer than 60% profitable walk-forward windows")
    if live_sim_sessions < 20:
        failures.append("fewer than 20 untouched forward paper-trading sessions")
    return ValidationGate(not failures, tuple(failures))

