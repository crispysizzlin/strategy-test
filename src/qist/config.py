"""Strategy configuration.

All tunable parameters live here so the engine is fully declarative.  Defaults
are calibrated for a ~$20k, Level-3 account trading liquid index products
(XSP / SPY / IWM) with conservative, CVaR-bounded risk.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

import yaml


@dataclass
class AccountConfig:
    equity: float = 20_000.0
    options_level: int = 3
    is_margin: bool = True
    pdt_threshold: float = 25_000.0       # FINRA pattern-day-trader equity floor
    max_day_trades_per_5d: int = 3        # applies while equity < pdt_threshold


@dataclass
class RiskConfig:
    # Per-trade max loss as a fraction of equity (defined-risk structures).
    max_loss_per_trade_frac: float = 0.02
    # Portfolio-level CVaR(99%) budget as a fraction of equity.
    portfolio_cvar_budget_frac: float = 0.06
    # Hard portfolio stop: halt new entries if drawdown from high-water exceeds.
    max_drawdown_halt_frac: float = 0.12
    # Greek limits (per $1k equity to scale with account).
    max_net_delta_per_1k: float = 1.5
    max_net_vega_per_1k: float = 3.0
    # Fraction of Kelly to actually deploy.
    kelly_fraction: float = 0.25
    kelly_cap: float = 0.30
    # Per-position profit-take and stop (as fraction of max credit / max loss).
    take_profit_frac: float = 0.50        # close at 50% of credit captured
    stop_loss_mult: float = 2.0           # stop if loss == 2x credit received


@dataclass
class SignalConfig:
    # Minimum forward VRP (in variance units, annualized) to sell premium.
    min_vrp_var: float = 0.0
    # VRP z-score floor: stand down only when the forward premium is in the
    # *lower tail* of its own history (i.e. unusually cheap vol).  Negative by
    # design - an income book wants to harvest the steady, positive premium and
    # merely avoid selling when premium is depressed.
    min_vrp_zscore: float = -0.5
    # Max volatility-regime rank (0=calm) in which we still harvest.
    max_regime_rank_to_harvest: int = 1
    # Stand down if IV term structure is in backwardation beyond this slope.
    backwardation_block: float = -0.02
    # Short strike target deltas.
    short_put_delta: float = 0.16
    short_call_delta: float = 0.16
    wing_width_pct: float = 0.005         # vertical wing as % of spot (~$2-3 on index ETFs)
    target_dte: int = 35
    min_dte: int = 21
    max_dte: int = 49


@dataclass
class TailOverlayConfig:
    enabled: bool = True
    # Fraction of net harvested credit recycled into convex tail protection.
    budget_frac_of_credit: float = 0.20
    hedge_put_delta: float = 0.05         # far-OTM put we buy for crash convexity
    hedge_dte: int = 60


@dataclass
class UniverseConfig:
    # Liquid, cash-settled-preferred index products suited to a small account.
    symbols: list[str] = field(default_factory=lambda: ["XSP", "SPY", "IWM"])
    primary: str = "XSP"


@dataclass
class ExecutionConfig:
    use_level2: bool = True
    entry_aggressiveness: float = 0.35    # 0=mid, 1=touch
    max_spread_pct: float = 0.08          # skip illiquid contracts
    contract_multiplier: int = 100


@dataclass
class Config:
    account: AccountConfig = field(default_factory=AccountConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
    tail: TailOverlayConfig = field(default_factory=TailOverlayConfig)
    universe: UniverseConfig = field(default_factory=UniverseConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    mode: str = "backtest"                 # backtest | paper | live

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Config":
        return cls(
            account=AccountConfig(**d.get("account", {})),
            risk=RiskConfig(**d.get("risk", {})),
            signal=SignalConfig(**d.get("signal", {})),
            tail=TailOverlayConfig(**d.get("tail", {})),
            universe=UniverseConfig(**d.get("universe", {})),
            execution=ExecutionConfig(**d.get("execution", {})),
            mode=d.get("mode", "backtest"),
        )

    @classmethod
    def load(cls, path: str) -> "Config":
        with open(path) as fh:
            return cls.from_dict(yaml.safe_load(fh) or {})

    def save(self, path: str) -> None:
        with open(path, "w") as fh:
            yaml.safe_dump(self.to_dict(), fh, sort_keys=False)
