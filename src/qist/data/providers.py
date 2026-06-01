"""Market-data providers.

Two implementations behind a common protocol so the strategy engine is agnostic
to the source:

* ``SchwabDataProvider`` - wraps the live REST client.
* ``SyntheticDataProvider`` - a self-contained simulator (GBM + stochastic vol +
  Poisson jumps) used for backtests, CI, and offline development without
  credentials.  It produces price history *and* an arbitrage-consistent option
  chain priced off a vol surface so the full pipeline runs end-to-end.  Implied
  vol is priced at a premium over the physical diffusion vol so that a realistic
  variance risk premium (the thing the strategy harvests) is present.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Protocol

import numpy as np
import pandas as pd

from ..brokers.schwab_client import SchwabClient
from ..models.black_scholes import bs_greeks
from .market_data import OptionChain, OptionRow, chain_from_schwab


class DataProvider(Protocol):
    def price_history(self, symbol: str, days: int) -> pd.DataFrame: ...
    def spot(self, symbol: str) -> float: ...
    def option_chain(self, symbol: str) -> OptionChain: ...


@dataclass
class SchwabDataProvider:
    client: SchwabClient

    def price_history(self, symbol: str, days: int) -> pd.DataFrame:
        candles = self.client.get_price_history(
            symbol, period_type="year", period=2,
            frequency_type="daily", frequency=1)
        df = pd.DataFrame(candles)
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["datetime"], unit="ms")
        df = df.set_index("date")[["open", "high", "low", "close", "volume"]]
        return df.tail(days)

    def spot(self, symbol: str) -> float:
        q = self.client.get_quotes([symbol]).get(symbol)
        return q.mid if q else float("nan")

    def option_chain(self, symbol: str) -> OptionChain:
        raw = self.client.get_option_chain(symbol, contract_type="ALL",
                                            strike_count=60)
        return chain_from_schwab(raw)


class SyntheticDataProvider:
    """Heston-lite simulator: GBM spot with mean-reverting stochastic variance
    and Poisson down-jumps, plus a smile-aware synthetic option chain.

    Deterministic given ``seed`` so tests and backtests are reproducible.
    """

    def __init__(self, spot: float = 450.0, mu: float = 0.06,
                 v0: float = 0.04, kappa: float = 3.0, theta: float = 0.04,
                 vol_of_vol: float = 0.4, rho: float = -0.7,
                 jump_intensity: float = 0.1, jump_mean: float = -0.03,
                 jump_std: float = 0.02, r: float = 0.04, seed: int = 11,
                 iv_premium: float = 1.15) -> None:
        self.spot0 = spot
        self.mu = mu
        self.v0 = v0
        self.kappa = kappa
        self.theta = theta
        self.vol_of_vol = vol_of_vol
        self.rho = rho
        self.jump_intensity = jump_intensity
        self.jump_mean = jump_mean
        self.jump_std = jump_std
        self.r = r
        # Implied vol is priced at a multiplicative premium over physical vol;
        # this embeds the variance risk premium the book is designed to harvest.
        self.iv_premium = iv_premium
        self.rng = np.random.default_rng(seed)
        self._path: Optional[pd.DataFrame] = None
        self._current_var = v0

    def simulate(self, days: int) -> pd.DataFrame:
        dt = 1.0 / 252.0
        s = self.spot0
        v = self.v0
        rows = []
        start = date.today() - timedelta(days=days)
        for i in range(days):
            z1, z2 = self.rng.standard_normal(2)
            zv = z1
            zs = self.rho * z1 + np.sqrt(1 - self.rho ** 2) * z2
            v = max(v + self.kappa * (self.theta - v) * dt
                    + self.vol_of_vol * np.sqrt(max(v, 1e-8) * dt) * zv, 1e-8)
            jump = 0.0
            if self.rng.random() < self.jump_intensity * dt:
                jump = self.rng.normal(self.jump_mean, self.jump_std)
            ret = (self.mu - 0.5 * v) * dt + np.sqrt(v * dt) * zs + jump
            s *= np.exp(ret)
            daily_vol = np.sqrt(v * dt)
            high = s * np.exp(abs(self.rng.normal(0, daily_vol)))
            low = s * np.exp(-abs(self.rng.normal(0, daily_vol)))
            open_ = rows[-1]["close"] if rows else s
            rows.append({"date": pd.Timestamp(start + timedelta(days=i)),
                         "open": open_, "high": max(high, s, open_),
                         "low": min(low, s, open_), "close": s,
                         "volume": int(self.rng.integers(5_000_000, 50_000_000)),
                         "inst_var": v})
        df = pd.DataFrame(rows).set_index("date")
        self._path = df
        self._current_var = float(v)
        return df

    def price_history(self, symbol: str, days: int) -> pd.DataFrame:
        df = self._path if self._path is not None else self.simulate(days + 30)
        return df.tail(days)[["open", "high", "low", "close", "volume"]]

    def spot(self, symbol: str) -> float:
        if self._path is None:
            self.simulate(300)
        return float(self._path["close"].iloc[-1])

    def option_chain(self, symbol: str, expiries_days: tuple[int, ...] = (7, 30, 45, 65),
                     n_strikes_each_side: int = 60, strike_step: float = 1.0,
                     spot: Optional[float] = None,
                     var: Optional[float] = None) -> OptionChain:
        """Build a synthetic chain around ``spot`` with $1 strikes (like SPY/XSP).

        ``spot`` defaults to the latest path close and ``var`` to the current
        instantaneous variance.
        """
        spot = self.spot(symbol) if spot is None else float(spot)
        var = self._current_var if var is None else float(var)
        phys_vol = float(np.sqrt(max(var, 1e-6)))
        base_iv = phys_vol * self.iv_premium
        rows: list[OptionRow] = []
        today = date.today()
        atm = round(spot)
        for dte in expiries_days:
            t = dte / 365.0
            expiry = today + timedelta(days=dte)
            for i in range(-n_strikes_each_side, n_strikes_each_side + 1):
                strike = atm + i * strike_step
                if strike <= 0:
                    continue
                moneyness = np.log(strike / spot)
                # Volatility smile: downside skew + curvature.
                iv = base_iv * (1.0 - 1.2 * moneyness + 2.5 * moneyness ** 2)
                iv = float(np.clip(iv, 0.05, 2.0))
                for is_call in (True, False):
                    g = bs_greeks(spot, strike, t, self.r, iv, is_call)
                    spread = max(0.02, 0.015 * g.price)
                    rows.append(OptionRow(
                        underlying=symbol, expiry=expiry, dte=dte, strike=float(strike),
                        is_call=is_call, bid=max(g.price - spread / 2, 0.0),
                        ask=g.price + spread / 2, mid=g.price, iv=iv,
                        delta=g.delta, gamma=g.gamma, theta=g.theta, vega=g.vega,
                        open_interest=1000, volume=500))
        return OptionChain(underlying=symbol, spot=spot, rows=rows)
