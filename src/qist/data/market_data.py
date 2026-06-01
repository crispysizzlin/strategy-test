"""Option-chain data structures and Schwab chain parsing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

import numpy as np


@dataclass
class OptionRow:
    underlying: str
    expiry: date
    dte: int
    strike: float
    is_call: bool
    bid: float
    ask: float
    mid: float
    iv: float
    delta: float
    gamma: float = float("nan")
    theta: float = float("nan")
    vega: float = float("nan")
    open_interest: int = 0
    volume: int = 0

    @property
    def spread(self) -> float:
        return max(self.ask - self.bid, 0.0)

    @property
    def spread_pct(self) -> float:
        return self.spread / self.mid if self.mid > 0 else float("inf")


@dataclass
class OptionChain:
    underlying: str
    spot: float
    rows: list[OptionRow]

    def expiries(self) -> list[date]:
        return sorted({r.expiry for r in self.rows})

    def for_expiry(self, expiry: date) -> list[OptionRow]:
        return [r for r in self.rows if r.expiry == expiry]

    def nearest_dte(self, target_dte: int) -> Optional[date]:
        exps = self.expiries()
        if not exps:
            return None
        return min(exps, key=lambda e: abs((e - date.today()).days - target_dte))

    def by_delta(self, expiry: date, target_delta: float, is_call: bool) -> Optional[OptionRow]:
        """Find the option whose |delta| is closest to ``target_delta``."""
        cands = [r for r in self.for_expiry(expiry) if r.is_call == is_call
                 and np.isfinite(r.delta)]
        if not cands:
            return None
        return min(cands, key=lambda r: abs(abs(r.delta) - target_delta))

    def atm_iv(self, expiry: date) -> float:
        rows = self.for_expiry(expiry)
        if not rows:
            return float("nan")
        atm = min(rows, key=lambda r: abs(r.strike - self.spot))
        same = [r for r in rows if r.strike == atm.strike]
        return float(np.nanmean([r.iv for r in same])) if same else atm.iv


def chain_from_schwab(raw: dict[str, Any]) -> OptionChain:
    """Parse a Schwab ``/chains`` response into an OptionChain.

    Schwab returns ``callExpDateMap`` / ``putExpDateMap`` keyed by
    ``"YYYY-MM-DD:dte"`` -> ``{strike: [contract,...]}``.
    """
    underlying = raw.get("symbol", "")
    spot = float(raw.get("underlyingPrice", float("nan")) or float("nan"))
    rows: list[OptionRow] = []
    for is_call, mapname in ((True, "callExpDateMap"), (False, "putExpDateMap")):
        for exp_key, strikes in (raw.get(mapname) or {}).items():
            exp_str, _, dte_str = exp_key.partition(":")
            expiry = datetime.strptime(exp_str, "%Y-%m-%d").date()
            dte = int(dte_str) if dte_str else (expiry - date.today()).days
            for strike_str, contracts in strikes.items():
                for c in contracts:
                    bid = float(c.get("bid", 0.0) or 0.0)
                    ask = float(c.get("ask", 0.0) or 0.0)
                    mid = 0.5 * (bid + ask) if (bid > 0 and ask > 0) else float(
                        c.get("mark", c.get("last", 0.0)) or 0.0)
                    rows.append(OptionRow(
                        underlying=underlying, expiry=expiry, dte=dte,
                        strike=float(strike_str), is_call=is_call,
                        bid=bid, ask=ask, mid=mid,
                        iv=float(c.get("volatility", float("nan")) or float("nan")) / 100.0,
                        delta=float(c.get("delta", float("nan")) or float("nan")),
                        gamma=float(c.get("gamma", float("nan")) or float("nan")),
                        theta=float(c.get("theta", float("nan")) or float("nan")),
                        vega=float(c.get("vega", float("nan")) or float("nan")),
                        open_interest=int(c.get("openInterest", 0) or 0),
                        volume=int(c.get("totalVolume", 0) or 0)))
    return OptionChain(underlying=underlying, spot=spot, rows=rows)
