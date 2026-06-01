"""General-purpose utility functions for the AVRPE system."""

from __future__ import annotations

import functools
import time
from datetime import datetime, date
from typing import Any, Callable, TypeVar

import numpy as np
import pandas as pd

F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------

def retry(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0):
    """Exponential back-off retry decorator."""
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    if attempt == max_attempts - 1:
                        raise
                    time.sleep(current_delay)
                    current_delay *= backoff
        return wrapper  # type: ignore[return-value]
    return decorator


def timer(func: F) -> F:
    """Log function execution time."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        from src.utils.logger import logger
        logger.debug(f"{func.__name__} completed in {elapsed:.3f}s")
        return result
    return wrapper  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def is_trading_day(dt: date | datetime | None = None) -> bool:
    """Return True if the given date is a NYSE trading day (heuristic)."""
    if dt is None:
        dt = datetime.now()
    if isinstance(dt, datetime):
        dt = dt.date()
    # Weekend check
    if dt.weekday() >= 5:
        return False
    # Major holidays (simplified – use pandas_market_calendars in production)
    holidays = {
        date(dt.year, 1, 1),    # New Year
        date(dt.year, 7, 4),    # Independence Day
        date(dt.year, 12, 25),  # Christmas
    }
    return dt not in holidays


def market_minutes_until_close(now: datetime | None = None) -> int:
    """Return estimated minutes remaining in the trading day."""
    if now is None:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/New_York"))
    close = now.replace(hour=16, minute=0, second=0, microsecond=0)
    delta = close - now
    return max(0, int(delta.total_seconds() / 60))


def next_expiry_date(dte: int) -> date:
    """Return the calendar date `dte` business days from today."""
    today = date.today()
    count = 0
    current = today
    while count < dte:
        current = date.fromordinal(current.toordinal() + 1)
        if current.weekday() < 5:  # Mon–Fri
            count += 1
    return current


# ---------------------------------------------------------------------------
# Statistical helpers
# ---------------------------------------------------------------------------

def ewma_volatility(returns: np.ndarray, lam: float = 0.94) -> float:
    """
    RiskMetrics EWMA volatility estimator.

    σ²_t = λ·σ²_{t-1} + (1-λ)·r²_{t-1}

    Parameters
    ----------
    returns : array of log returns
    lam     : decay factor (0.94 for daily, 0.97 for monthly)

    Returns
    -------
    Annualised volatility estimate (float)
    """
    if len(returns) == 0:
        return np.nan
    variance = returns[0] ** 2
    for r in returns[1:]:
        variance = lam * variance + (1 - lam) * r ** 2
    return float(np.sqrt(variance * 252))


def rolling_sharpe(returns: pd.Series, risk_free: float = 0.05, window: int = 21) -> pd.Series:
    """Rolling annualised Sharpe ratio."""
    excess = returns - risk_free / 252
    roll_mean = excess.rolling(window).mean()
    roll_std = excess.rolling(window).std()
    return (roll_mean / roll_std) * np.sqrt(252)


def max_drawdown(equity_curve: np.ndarray) -> float:
    """Compute maximum drawdown from an equity curve."""
    peak = np.maximum.accumulate(equity_curve)
    drawdown = (equity_curve - peak) / peak
    return float(drawdown.min())


def annualised_return(equity_curve: np.ndarray, periods_per_year: int = 252) -> float:
    """Compute annualised return from an equity curve."""
    total = equity_curve[-1] / equity_curve[0] - 1
    years = len(equity_curve) / periods_per_year
    return float((1 + total) ** (1 / years) - 1)


def parkinson_volatility(high: np.ndarray, low: np.ndarray) -> float:
    """
    Parkinson (1980) range-based volatility estimator.

    σ_P = sqrt[ 1/(4·ln2·n) · Σ ln(H_i/L_i)² ]
    """
    n = len(high)
    log_hl = np.log(high / low)
    variance = np.sum(log_hl ** 2) / (4 * np.log(2) * n)
    return float(np.sqrt(variance * 252))


def yang_zhang_volatility(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    k: float = 0.34,
) -> float:
    """
    Yang-Zhang (2000) volatility estimator – minimum variance, unbiased,
    handles overnight gaps.

    σ_YZ² = σ_o² + k·σ_c² + (1-k)·σ_rs²

    where:
      σ_o² = overnight variance (open-to-previous-close)
      σ_c² = close-to-open variance
      σ_rs² = Rogers-Satchell variance (intraday)
    """
    n = len(close)
    if n < 2:
        return np.nan

    prev_close = close[:-1]
    o = open_[1:]
    h = high[1:]
    l = low[1:]
    c = close[1:]

    log_oc = np.log(o / prev_close)
    log_co = np.log(c / o)
    log_hc = np.log(h / c)
    log_lc = np.log(l / c)
    log_ho = np.log(h / o)
    log_lo = np.log(l / o)

    sigma_o2 = np.sum((log_oc - log_oc.mean()) ** 2) / (n - 2)
    sigma_c2 = np.sum((log_co - log_co.mean()) ** 2) / (n - 2)
    sigma_rs2 = np.mean(log_hc * log_ho + log_lc * log_lo)  # Rogers-Satchell

    sigma_yz2 = sigma_o2 + k * sigma_c2 + (1 - k) * sigma_rs2
    return float(np.sqrt(max(sigma_yz2, 0) * 252))


def percentile_rank(series: pd.Series, value: float) -> float:
    """Return the percentile rank of `value` in `series` [0, 1]."""
    return float((series < value).mean())


# ---------------------------------------------------------------------------
# Options helpers
# ---------------------------------------------------------------------------

def option_symbol(
    underlying: str,
    expiry: date,
    option_type: str,
    strike: float,
) -> str:
    """
    Build an OCC option symbol string.
    Format: UNDERLYING YYMMDD C/P STRIKE*1000 (zero-padded to 8 digits)
    Example: SPY   241220C00450000
    """
    exp_str = expiry.strftime("%y%m%d")
    cp = "C" if option_type.upper() == "CALL" else "P"
    strike_int = int(round(strike * 1000))
    return f"{underlying:<6}{exp_str}{cp}{strike_int:08d}"


def parse_option_symbol(symbol: str) -> dict:
    """Parse an OCC option symbol into its components."""
    symbol = symbol.strip()
    underlying = symbol[:6].strip()
    exp_str = symbol[6:12]
    cp = symbol[12]
    strike = int(symbol[13:]) / 1000
    expiry = datetime.strptime(exp_str, "%y%m%d").date()
    return {
        "underlying": underlying,
        "expiry": expiry,
        "option_type": "call" if cp == "C" else "put",
        "strike": strike,
    }


def pnl_from_spread(
    premium_collected: float,
    current_value: float,
    contracts: int,
    multiplier: int = 100,
) -> float:
    """P&L for a short spread position."""
    return (premium_collected - current_value) * contracts * multiplier
