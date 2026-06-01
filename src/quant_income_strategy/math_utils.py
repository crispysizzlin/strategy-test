"""Small numerical helpers with no third-party runtime dependencies."""

from __future__ import annotations

from math import erf, exp, log, pi, sqrt


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return exp(-0.5 * x * x) / sqrt(2.0 * pi)


def black_scholes_delta(
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    volatility: float,
    option_type: str,
    risk_free_rate: float = 0.045,
) -> float:
    """Estimate Black-Scholes delta when an option chain omits greeks."""

    if spot <= 0 or strike <= 0 or time_to_expiry_years <= 0 or volatility <= 0:
        raise ValueError("spot, strike, time and volatility must be positive")

    d1 = (
        log(spot / strike)
        + (risk_free_rate + 0.5 * volatility * volatility) * time_to_expiry_years
    ) / (volatility * sqrt(time_to_expiry_years))

    if option_type.upper() == "CALL":
        return norm_cdf(d1)
    if option_type.upper() == "PUT":
        return norm_cdf(d1) - 1.0
    raise ValueError(f"Unsupported option type: {option_type}")


def terminal_price_from_sigma(
    spot: float,
    annual_volatility: float,
    dte: int,
    sigma_move: float,
) -> float:
    """Convert a standard-deviation move into a lognormal terminal price."""

    if dte <= 0:
        return spot
    horizon_vol = annual_volatility * sqrt(dte / 365.0)
    return spot * exp(sigma_move * horizon_vol)


def cvar_from_pnl(pnls: list[float], alpha: float = 0.95) -> float:
    """Return positive-loss CVaR from a list of P&L outcomes."""

    if not pnls:
        raise ValueError("pnls cannot be empty")
    sorted_losses = sorted((-pnl for pnl in pnls), reverse=True)
    tail_count = max(1, int(round(len(sorted_losses) * (1.0 - alpha))))
    return sum(sorted_losses[:tail_count]) / tail_count
