"""Realized-volatility estimators.

Accurate realized variance (RV) is the empirical anchor of the variance risk
premium: VRP = IV^2 - E[RV^2].  We expose several estimators with different
efficiency / bias trade-offs:

* close-to-close          (unbiased, high variance)
* Parkinson (1980)        (uses the high-low range)
* Garman-Klass (1980)     (OHLC, ~7x more efficient than close-close)
* Rogers-Satchell (1991)  (handles drift)
* Yang-Zhang (2000)       (handles drift *and* overnight jumps - our default)

All functions return *annualized* volatility (sigma) using 252 trading days,
unless ``annualize=False``.

References
----------
Parkinson, M. (1980). The Extreme Value Method for Estimating the Variance of
the Rate of Return. *Journal of Business*.
Yang, D. & Zhang, Q. (2000). Drift-Independent Volatility Estimation Based on
High, Low, Open, and Close Prices. *Journal of Business*.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _ann(daily_var: float, annualize: bool) -> float:
    var = daily_var * (TRADING_DAYS if annualize else 1.0)
    return math.sqrt(max(var, 0.0))


def close_to_close_vol(close: pd.Series, annualize: bool = True) -> float:
    log_ret = np.log(close / close.shift(1)).dropna()
    if len(log_ret) < 2:
        return float("nan")
    return _ann(float(np.var(log_ret, ddof=1)), annualize)


def parkinson_vol(high: pd.Series, low: pd.Series, annualize: bool = True) -> float:
    hl = np.log(high / low) ** 2
    daily_var = float(np.mean(hl)) / (4.0 * math.log(2.0))
    return _ann(daily_var, annualize)


def garman_klass_vol(open_: pd.Series, high: pd.Series, low: pd.Series,
                     close: pd.Series, annualize: bool = True) -> float:
    log_hl = np.log(high / low)
    log_co = np.log(close / open_)
    daily_var = float(np.mean(0.5 * log_hl ** 2 - (2 * math.log(2) - 1) * log_co ** 2))
    return _ann(daily_var, annualize)


def rogers_satchell_vol(open_: pd.Series, high: pd.Series, low: pd.Series,
                        close: pd.Series, annualize: bool = True) -> float:
    log_ho = np.log(high / open_)
    log_lo = np.log(low / open_)
    log_co = np.log(close / open_)
    daily_var = float(np.mean(log_ho * (log_ho - log_co) + log_lo * (log_lo - log_co)))
    return _ann(daily_var, annualize)


def yang_zhang_vol(open_: pd.Series, high: pd.Series, low: pd.Series,
                   close: pd.Series, annualize: bool = True) -> float:
    """Yang-Zhang estimator: minimum-variance, drift- and gap-robust."""
    n = len(close)
    if n < 3:
        return float("nan")
    log_oc = np.log(open_ / close.shift(1)).dropna()        # overnight
    log_co = np.log(close / open_)                          # intraday open->close
    log_co = log_co.iloc[1:]                                # align with overnight

    overnight_var = float(np.var(log_oc, ddof=1))
    openclose_var = float(np.var(log_co, ddof=1))

    rs = rogers_satchell_vol(open_.iloc[1:], high.iloc[1:], low.iloc[1:],
                             close.iloc[1:], annualize=False)
    rs_var = rs ** 2

    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    daily_var = overnight_var + k * openclose_var + (1 - k) * rs_var
    return _ann(daily_var, annualize)


def realized_variance_series(close: pd.Series, window: int) -> pd.Series:
    """Rolling annualized realized variance (sigma^2) from close-to-close returns.

    Used as the regression target and VRP realized leg.
    """
    log_ret = np.log(close / close.shift(1))
    return (log_ret.rolling(window).var(ddof=1) * TRADING_DAYS)


def daily_log_returns(close: pd.Series) -> pd.Series:
    return np.log(close / close.shift(1)).dropna()
