"""
Simplified backtest for VRP + regime gating on ETF daily data.

Uses synthetic short-vol P&L proxy: +credit when RV < IV entry, -loss on vol spikes.
Not a substitute for options chain backtest (use optopsy/ORATS for that).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import yfinance as yf

from rg_vrp_l2.regime_filter import RegimeFilter
from rg_vrp_l2.vrp_estimator import VRPEstimator, realized_volatility


@dataclass
class BacktestResult:
    total_return_pct: float
    sharpe_approx: float
    max_drawdown_pct: float
    trade_count: int
    win_rate: float


def run_simple_backtest(
    symbol: str = "SPY",
    years: int = 5,
    vrp_threshold: float = 2.0,
    turbulence_halt: float = 0.60,
) -> BacktestResult:
    """
    Proxy backtest: enter synthetic short-straddle-like carry when VRP rich & calm.
    """
    hist = yf.download(symbol, period=f"{years}y", progress=False, auto_adjust=True)
    close = hist["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]

    regime = RegimeFilter(turbulence_halt=turbulence_halt)
    vrp_est = VRPEstimator(threshold_vol_points=vrp_threshold)

    # Rolling IV proxy: 21d realized vol shifted (stand-in for IV when no chain history)
    iv_proxy = close.pct_change().rolling(21).std() * np.sqrt(252) * 100 * 1.05

    equity = 20000.0
    peak = equity
    max_dd = 0.0
    returns = []
    wins = 0
    trades = 0

    warmup = 252
    for i in range(warmup, len(close) - 21):
        window = close.iloc[: i + 1]
        regime.fit(window)
        state = regime.predict()

        iv30 = float(iv_proxy.iloc[i]) if not np.isnan(iv_proxy.iloc[i]) else 20.0
        vrp_sig = vrp_est.evaluate(window, iv30)

        if not state.allow_new_short_vol or not vrp_sig.should_sell_vol:
            continue

        risk = equity * 0.05
        fwd_rv = realized_volatility(close.iloc[i : i + 21], 20)
        if np.isnan(fwd_rv):
            continue
        edge = (vrp_sig.implied_vol_30d - fwd_rv) / 100
        daily_pnl = edge * risk / 21

        equity += daily_pnl
        returns.append(daily_pnl / 20000)
        trades += 1
        if daily_pnl > 0:
            wins += 1
        peak = max(peak, equity)
        dd = (peak - equity) / peak
        max_dd = max(max_dd, dd)

    ret_pct = (equity / 20000 - 1) * 100
    if returns:
        sharpe = np.mean(returns) / (np.std(returns) + 1e-9) * np.sqrt(252)
        win_rate = wins / trades if trades else 0
    else:
        sharpe = 0.0
        win_rate = 0.0

    return BacktestResult(
        total_return_pct=ret_pct,
        sharpe_approx=float(sharpe),
        max_drawdown_pct=max_dd * 100,
        trade_count=trades,
        win_rate=win_rate,
    )
