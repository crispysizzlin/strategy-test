"""
RG-VRP-L2 strategy engine: combines all signals into trade decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import inspect


def _filter_kwargs(cls, cfg: dict) -> dict:
    params = inspect.signature(cls.__init__).parameters
    return {k: v for k, v in cfg.items() if k in params}



import yaml

from rg_vrp_l2.correlation_proxy import CorrelationProxy, CorrelationWedge
from rg_vrp_l2.inventory_skew import InventorySkew, PortfolioGreeks, StrikeAdjustment
from rg_vrp_l2.l2_microstructure import L2EntrySignal, L2Microstructure, OrderBookSnapshot
from rg_vrp_l2.position_sizer import FractionalKellySizer, PositionSize
from rg_vrp_l2.regime_filter import RegimeFilter, RegimeState
from rg_vrp_l2.vrp_estimator import VRPEstimator, VRPSignal


class StructureType(str, Enum):
    IRON_CONDOR = "iron_condor"
    PUT_CREDIT_SPREAD = "put_credit_spread"
    IRON_BUTTERFLY = "iron_butterfly"
    NONE = "none"


@dataclass
class TradeDecision:
    action: str  # ENTER, HOLD, CLOSE_ALL, SKIP
    structure: StructureType
    underlying: str
    short_delta: float
    contracts: int
    dte_target: int
    wing_width: float
    signals: dict[str, Any]
    rationale: str


def load_config(path: str | Path = "config/default.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


class StrategyEngine:
    """Orchestrates regime, VRP, inventory, L2, correlation, and sizing."""

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or load_config()
        s = self.config["strategy"]
        self.underlyings = s["underlyings"]
        self.dte_min = s["dte_min"]
        self.dte_max = s["dte_max"]
        self.base_delta = s["short_delta_base"]
        self.max_positions = s["max_positions"]

        self.regime_filter = RegimeFilter(**_filter_kwargs(RegimeFilter, self.config["regime"]))
        self.vrp = VRPEstimator(**_filter_kwargs(VRPEstimator, self.config["vrp"]))
        self.inventory = InventorySkew(**_filter_kwargs(InventorySkew, self.config["inventory"]))
        self.l2 = L2Microstructure(**_filter_kwargs(L2Microstructure, self.config["l2"]))
        self.sizer = FractionalKellySizer(**_filter_kwargs(FractionalKellySizer, self.config["kelly"]))
        self.corr = CorrelationProxy(**_filter_kwargs(CorrelationProxy, self.config["correlation_proxy"]))

        self.equity = self.config["account"]["equity"]
        self._regime_fitted = False

    def evaluate_entry(
        self,
        underlying: str,
        close_prices,
        implied_vol_30d: float,
        implied_vol_7d: float | None,
        iv_history,
        portfolio_greeks: PortfolioGreeks,
        open_position_count: int,
        book: OrderBookSnapshot | None,
        quote_bid: float,
        quote_ask: float,
        credit_per_contract: float,
        max_loss_per_contract: float,
        index_iv: float | None = None,
        component_ivs: dict[str, float] | None = None,
        regime_state: RegimeState | None = None,
        vrp_signal: VRPSignal | None = None,
    ) -> TradeDecision:
        signals: dict[str, Any] = {}

        if regime_state is None:
            if not self._regime_fitted:
                self.regime_filter.fit(close_prices)
                self._regime_fitted = True
            regime_state = self.regime_filter.predict()
        signals["regime"] = regime_state

        if regime_state.should_flatten:
            return TradeDecision(
                action="CLOSE_ALL",
                structure=StructureType.NONE,
                underlying=underlying,
                short_delta=0,
                contracts=0,
                dte_target=0,
                wing_width=0,
                signals=signals,
                rationale="P(turbulent) > flatten threshold",
            )

        if not regime_state.allow_new_short_vol:
            return TradeDecision(
                action="SKIP",
                structure=StructureType.NONE,
                underlying=underlying,
                short_delta=0,
                contracts=0,
                dte_target=0,
                wing_width=0,
                signals=signals,
                rationale="Regime blocks new short vol",
            )

        if open_position_count >= self.max_positions:
            return TradeDecision(
                action="SKIP",
                structure=StructureType.NONE,
                underlying=underlying,
                short_delta=0,
                contracts=0,
                dte_target=0,
                wing_width=0,
                signals=signals,
                rationale="Max positions reached",
            )

        if vrp_signal is None:
            vrp_signal = self.vrp.evaluate(
                close_prices, implied_vol_30d, implied_vol_7d, iv_history
            )
        signals["vrp"] = vrp_signal

        if not vrp_signal.should_sell_vol:
            return TradeDecision(
                action="SKIP",
                structure=StructureType.NONE,
                underlying=underlying,
                short_delta=0,
                contracts=0,
                dte_target=0,
                wing_width=0,
                signals=signals,
                rationale=f"VRP filter: {vrp_signal.reason}",
            )

        wedge: CorrelationWedge | None = None
        if index_iv is not None and component_ivs:
            wedge = self.corr.evaluate(index_iv, component_ivs)
            signals["correlation"] = wedge

        structure = StructureType.IRON_CONDOR
        if (
            vrp_signal.term_structure_slope is not None
            and vrp_signal.term_structure_slope > 1.5
            and regime_state.calm_probability > 0.6
        ):
            structure = StructureType.IRON_BUTTERFLY
        elif wedge and not wedge.prefer_index_structures:
            structure = StructureType.PUT_CREDIT_SPREAD

        dte = (self.dte_min + self.dte_max) // 2
        rv = vrp_signal.realized_vol_20d
        strike_adj: StrikeAdjustment = self.inventory.adjust_short_delta(
            self.base_delta,
            portfolio_greeks,
            float(close_prices.iloc[-1]),
            dte,
            rv if not (rv != rv) else 20.0,  # NaN check
        )
        signals["strike_adj"] = strike_adj

        if book is not None:
            l2_sig: L2EntrySignal = self.l2.evaluate(book, quote_bid, quote_ask)
            signals["l2"] = l2_sig
            if not l2_sig.allow_entry:
                return TradeDecision(
                    action="SKIP",
                    structure=structure,
                    underlying=underlying,
                    short_delta=strike_adj.adjusted_short_delta,
                    contracts=0,
                    dte_target=dte,
                    wing_width=self._wing_width(underlying),
                    signals=signals,
                    rationale=f"L2 block: {l2_sig.reason}",
                )

        size: PositionSize = self.sizer.size_iron_condor(
            self.equity,
            max_loss_per_contract,
            credit_per_contract,
            regime_multiplier=regime_state.size_multiplier,
            max_loss_pct_per_trade=self.config["strategy"]["max_loss_per_trade_pct"],
        )
        signals["size"] = size

        if size.contracts < 1:
            return TradeDecision(
                action="SKIP",
                structure=structure,
                underlying=underlying,
                short_delta=strike_adj.adjusted_short_delta,
                contracts=0,
                dte_target=dte,
                wing_width=self._wing_width(underlying),
                signals=signals,
                rationale=f"Sizing: {size.rationale}",
            )

        return TradeDecision(
            action="ENTER",
            structure=structure,
            underlying=underlying,
            short_delta=strike_adj.adjusted_short_delta,
            contracts=size.contracts,
            dte_target=dte,
            wing_width=self._wing_width(underlying),
            signals=signals,
            rationale="All gates passed",
        )

    def _wing_width(self, underlying: str) -> float:
        key = f"wing_width_{underlying.lower()}"
        return float(self.config["strategy"].get(key, 5))
