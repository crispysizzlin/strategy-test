"""Domain models for the options income strategy engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


OptionType = Literal["CALL", "PUT"]
StrategyType = Literal["PUT_CREDIT_SPREAD", "CALL_CREDIT_SPREAD", "IRON_CONDOR"]


@dataclass(frozen=True)
class OptionQuote:
    """Normalized option quote from Schwab chains/streams or another provider."""

    underlying: str
    option_symbol: str
    expiration: str
    dte: int
    option_type: OptionType
    strike: float
    bid: float
    ask: float
    delta: float | None
    implied_volatility: float
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    bid_size: int = 0
    ask_size: int = 0
    open_interest: int = 0
    volume: int = 0

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return max(0.0, self.ask - self.bid)

    @property
    def spread_pct_mid(self) -> float:
        if self.mid <= 0:
            return 1.0
        return self.spread / self.mid

    @property
    def book_imbalance(self) -> float:
        total = self.bid_size + self.ask_size
        if total <= 0:
            return 0.0
        return (self.bid_size - self.ask_size) / total


@dataclass(frozen=True)
class UnderlyingSnapshot:
    """A point-in-time market snapshot for one optionable underlying."""

    symbol: str
    price: float
    realized_vol_20d: float
    realized_vol_5d: float | None = None
    iv_rank: float | None = None
    vix: float | None = None
    vix3m: float | None = None
    event_risk: bool = False
    options: tuple[OptionQuote, ...] = field(default_factory=tuple)

    @property
    def forecast_realized_vol(self) -> float:
        if self.realized_vol_5d is None:
            return self.realized_vol_20d
        return max(self.realized_vol_20d, 0.7 * self.realized_vol_20d + 0.3 * self.realized_vol_5d)


@dataclass(frozen=True)
class StrategyConfig:
    """Conservative defaults for a small account using level 3 options."""

    account_equity: float = 20_000.0
    max_risk_per_trade_pct: float = 0.02
    max_portfolio_risk_pct: float = 0.08
    fractional_kelly: float = 0.25
    min_dte: int = 21
    max_dte: int = 60
    min_short_delta: float = 0.10
    max_short_delta: float = 0.22
    min_long_delta_gap: float = 0.04
    max_bid_ask_pct: float = 0.20
    min_open_interest: int = 100
    min_credit_to_risk: float = 0.16
    min_iv_rv_ratio: float = 1.12
    max_vix: float = 32.0
    max_vix_front_to_3m_ratio: float = 1.05
    cvar_alpha: float = 0.95


@dataclass(frozen=True)
class SpreadLeg:
    instruction: Literal["BUY_TO_OPEN", "SELL_TO_OPEN"]
    quote: OptionQuote


@dataclass(frozen=True)
class CandidateTrade:
    strategy_type: StrategyType
    underlying: str
    dte: int
    legs: tuple[SpreadLeg, ...]
    net_credit: float
    max_loss: float
    probability_of_profit: float
    expected_value: float
    credit_to_risk: float
    cvar95: float
    kelly_fraction: float
    contracts: int
    risk_dollars: float
    score: float
    suggested_limit_credit: float
    rationale: tuple[str, ...]

    @property
    def order_price(self) -> str:
        return f"{self.suggested_limit_credit:.2f}"
