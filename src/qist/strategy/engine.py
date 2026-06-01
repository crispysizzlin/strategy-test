"""Strategy orchestration engine.

The engine turns a market snapshot (price history + option chain + implied vols)
into a concrete, risk-sized set of orders, following this pipeline:

    data -> SignalEngine (VRP + regime + term structure)
         -> structure selection (defined-risk default; Level-3 when gated)
         -> RiskManager sizing (fractional Kelly ∩ max-loss ∩ CVaR)
         -> Greek-limit check
         -> tail overlay (recycle credit into convexity)
         -> orders (Schwab multi-leg payloads)

It is broker-agnostic and side-effect free: it *returns* a plan.  The CLI / live
loop is responsible for actually submitting orders, which keeps the decision
logic fully unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from ..config import Config
from ..data.market_data import OptionChain
from .risk import PortfolioGreeks, RiskManager, RiskState
from .signals import Decision, SignalEngine, SignalState
from .structures import (Structure, broken_wing_butterfly, call_credit_spread,
                         iron_condor, jade_lizard, put_credit_spread,
                         short_strangle)
from .tail_overlay import build_tail_hedge, hedge_units_for_budget


@dataclass
class TradePlan:
    signal: SignalState
    core_structure: Structure | None = None
    core_quantity: int = 0
    tail_structure: Structure | None = None
    tail_quantity: int = 0
    win_prob: float = float("nan")
    notes: list[str] = field(default_factory=list)

    @property
    def will_trade(self) -> bool:
        return self.core_structure is not None and self.core_quantity > 0

    def orders(self):
        out = []
        if self.core_structure and self.core_quantity > 0:
            out.append(self.core_structure.to_order(self.core_quantity))
        if self.tail_structure and self.tail_quantity > 0:
            out.append(self.tail_structure.to_order(self.tail_quantity))
        return out

    def expected_credit(self) -> float:
        c = 0.0
        if self.core_structure and self.core_quantity:
            c += self.core_structure.credit_dollars * self.core_quantity
        if self.tail_structure and self.tail_quantity:
            c += self.tail_structure.credit_dollars * self.tail_quantity
        return c


def structure_expiry_pnl(structure: Structure, spot: float,
                         sample_returns: np.ndarray, mult: int = 100) -> np.ndarray:
    """Per-contract P&L ($) at expiry over a sample of terminal log-returns.

    P&L = credit_received + sum_legs sign * intrinsic, where sign is +1 for long
    legs and -1 for short legs.  This is the *true* defined-risk payoff and is
    used to drive Kelly / CVaR sizing and the win probability.
    """
    finals = spot * np.exp(sample_returns)
    pnl = np.full(finals.shape, structure.credit_dollars, dtype=float)
    for leg in structure.legs:
        k = leg.contract.strike
        is_call = leg.contract.option_type.value == "CALL"
        sign = 1.0 if "BUY" in leg.instruction.value else -1.0
        intrinsic = np.maximum(finals - k, 0.0) if is_call else np.maximum(k - finals, 0.0)
        pnl += sign * intrinsic * mult * leg.quantity
    return pnl


class StrategyEngine:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.signals = SignalEngine(cfg.signal)
        self.risk = RiskManager(cfg)

    def plan(self, ohlc: pd.DataFrame, chain: OptionChain,
             state: RiskState, mult: int = 100) -> TradePlan:
        s = self.cfg.signal
        expiry = chain.nearest_dte(s.target_dte)
        if expiry is None:
            return TradePlan(signal=self._empty_signal("no_expiry"),
                             notes=["no option expiry available"])
        dte = (expiry - date.today()).days

        iv_front = chain.atm_iv(expiry)
        back_expiry = chain.nearest_dte(s.target_dte + 30)
        iv_back = chain.atm_iv(back_expiry) if back_expiry else iv_front

        sig = self.signals.evaluate(ohlc, iv_front, iv_back, horizon_days=dte)
        plan = TradePlan(signal=sig)

        if self.risk.trading_halted(state):
            plan.notes.append("drawdown circuit-breaker: trading halted")
            return plan
        if not sig.harvest:
            plan.notes.append(f"signal={sig.decision.value}; no harvest")
            return plan

        wing = max(round(chain.spot * s.wing_width_pct), 1)
        structure = self._select_structure(chain, expiry, sig, wing, mult)
        if structure is None:
            plan.notes.append("could not construct a structure from chain")
            return plan

        # Size via risk manager using the true expiry-payoff distribution.
        # CRITICAL: simulate under the *physical* measure (the realized-vol
        # forecast), NOT implied vol.  The premium-selling edge is precisely that
        # realized < implied; evaluating under IV would price the trade as a fair
        # (zero-edge) bet and Kelly would correctly refuse to size it.
        rng = np.random.default_rng(123)
        phys_vol = sig.rv_forecast if np.isfinite(sig.rv_forecast) else iv_front
        phys_vol = max(phys_vol, 1e-3)
        horizon_sigma = phys_vol * np.sqrt(dte / 365.0)
        sample_returns = rng.normal(0.0, horizon_sigma, 20_000)
        pnl_samples = structure_expiry_pnl(structure, chain.spot, sample_returns, mult)
        loss_samples = -pnl_samples
        win_prob = float(np.mean(pnl_samples > 0))
        plan.win_prob = win_prob

        qty = self.risk.size_structure(structure, state, win_prob, loss_samples)
        if qty <= 0:
            plan.notes.append("sizer returned 0 contracts (risk budget / thin edge)")
            return plan

        # Greek check; scale down until within limits if needed.
        while qty > 0 and not self.risk.within_greek_limits(
                state, self._structure_greeks(structure, qty)):
            qty -= 1
        if qty <= 0:
            plan.notes.append("greek limits prevented entry")
            return plan

        plan.core_structure = structure
        plan.core_quantity = qty
        plan.notes.append(
            f"{structure.name} x{qty}, credit=${structure.credit_dollars*qty:.0f}, "
            f"maxloss=${structure.max_loss*qty:.0f}, win_p={win_prob:.2f}")

        # Tail overlay: recycle credit into convexity.
        if self.cfg.tail.enabled:
            credit = structure.credit_dollars * qty
            hedge = build_tail_hedge(chain, self.cfg.tail, credit, mult)
            if hedge is not None:
                units = hedge_units_for_budget(credit, self.cfg.tail, hedge.cost)
                if units > 0:
                    plan.tail_structure = hedge.structure
                    plan.tail_quantity = units
                    plan.notes.append(
                        f"tail hedge {hedge.structure.name} x{units}, "
                        f"cost=${hedge.cost*units:.0f}")
        return plan

    # ---- helpers --------------------------------------------------------
    def _select_structure(self, chain: OptionChain, expiry: date,
                          sig: SignalState, wing: float, mult: int) -> Structure | None:
        s = self.cfg.signal
        level3 = self.cfg.account.options_level >= 3

        if sig.decision == Decision.HARVEST_PUT_SIDE:
            return put_credit_spread(chain, expiry, s.short_put_delta, wing, mult)
        if sig.decision == Decision.HARVEST_CALL_SIDE:
            return call_credit_spread(chain, expiry, s.short_call_delta, wing, mult)

        # Neutral harvest: prefer iron condor (defined risk).  In the calmest
        # regime with Level 3, allow a jade lizard (no upside risk, richer credit).
        if level3 and sig.regime_rank == 0 and np.isfinite(sig.vrp_zscore) \
                and sig.vrp_zscore >= s.min_vrp_zscore + 1.0:
            jl = jade_lizard(chain, expiry, s.short_put_delta, s.short_call_delta,
                             wing, mult=mult)
            if jl is not None and jl.is_defined_risk:
                return jl
        ic = iron_condor(chain, expiry, s.short_put_delta, s.short_call_delta,
                         wing, mult)
        if ic is not None:
            return ic
        return broken_wing_butterfly(chain, expiry, s.short_put_delta,
                                     wing, wing * 2, mult)

    def _structure_greeks(self, structure: Structure, qty: int) -> PortfolioGreeks:
        """Approximate aggregate greeks.  Defined-risk neutral structures are
        near delta-neutral by construction; we charge net short vega/long theta
        proportional to the credit so the vega limit can bind on size."""
        return PortfolioGreeks(delta=0.0, gamma=0.0,
                               vega=-abs(structure.credit_dollars) * 0.01 * qty,
                               theta=abs(structure.credit_dollars) * 0.02 * qty)

    def _empty_signal(self, reason: str) -> SignalState:
        return SignalState(
            decision=Decision.STAND_DOWN, iv=float("nan"),
            rv_forecast_har=float("nan"), rv_forecast_garch=float("nan"),
            rv_forecast=float("nan"), vrp_var=float("nan"),
            vrp_zscore=float("nan"), regime_rank=0, term_structure_slope=0.0,
            ou_s_score=float("nan"), reasons=[reason])
