"""Convex tail-risk overlay.

The defining failure mode of every short-premium book is the negatively skewed,
fat-tailed loss on a volatility spike (the "46x loss day" in the literature).
Our differentiator is to *systematically recycle a fixed fraction of harvested
credit into long convexity* so the book's payoff skew is flattened rather than
left naked.  Concretely we maintain a laddered position in cheap, far-OTM put
spreads (and optionally VIX-call-equivalents) that explode in value on a crash,
funded by theta from the core book.

This converts the strategy from "pick up pennies in front of a steamroller" into
a *carry + convexity* portfolio - akin to a financed long-vol tail hedge sitting
on top of a short-vol carry engine.  In calm markets the overlay bleeds a small,
budgeted amount; in a crash it pays multiples, capping the drawdown that would
otherwise end the account.

References
----------
Taleb, N. (2004). antifragile convexity / tail-hedging intuition.
Carr & Wu (2009) - the very premium we sell is the buyer's crash insurance,
so we buy a cheaper slice of it back further OTM.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..brokers.models import Instruction
from ..config import TailOverlayConfig
from ..data.market_data import OptionChain
from .structures import Structure, _leg


@dataclass
class TailHedge:
    structure: Structure
    cost: float            # debit paid per 1 unit ($)


def build_tail_hedge(chain: OptionChain, cfg: TailOverlayConfig,
                     credit_collected: float, mult: int = 100) -> TailHedge | None:
    """Buy far-OTM put *spreads* with a budgeted slice of collected credit.

    Using a put *spread* (long 5-delta, short ~2-delta) rather than a naked long
    put lowers the bleed while preserving most crash convexity, so the same
    budget buys more units of protection.
    """
    if not cfg.enabled or credit_collected <= 0:
        return None
    expiry = chain.nearest_dte(cfg.hedge_dte)
    if expiry is None:
        return None
    long_put = chain.by_delta(expiry, cfg.hedge_put_delta, is_call=False)
    short_put = chain.by_delta(expiry, max(cfg.hedge_put_delta / 2.5, 0.01),
                               is_call=False)
    if long_put is None or short_put is None or short_put.strike >= long_put.strike:
        return None
    debit = long_put.mid - short_put.mid
    if debit <= 0:
        return None
    width = long_put.strike - short_put.strike
    structure = Structure(
        name="tail_put_spread",
        legs=[_leg(long_put, Instruction.BUY_TO_OPEN),
              _leg(short_put, Instruction.SELL_TO_OPEN)],
        net_credit=-debit, max_loss=debit * mult,
        max_profit=(width - debit) * mult, capital_req=debit * mult,
        breakevens=[long_put.strike - debit], multiplier=mult)
    return TailHedge(structure=structure, cost=debit * mult)


def hedge_units_for_budget(credit_collected: float, cfg: TailOverlayConfig,
                           hedge_cost_per_unit: float) -> int:
    """Number of tail-hedge units affordable within the credit-recycling budget."""
    if hedge_cost_per_unit <= 0:
        return 0
    budget = cfg.budget_frac_of_credit * credit_collected
    return int(max(budget // hedge_cost_per_unit, 0))
