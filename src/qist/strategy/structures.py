"""Defined-risk and Level-3 option structures.

We construct trade structures from a live ``OptionChain`` by selecting strikes by
target delta.  Each structure reports its net credit, max loss, max profit,
breakevens and capital requirement so the sizer and risk engine can reason about
it uniformly.

Structures
----------
* ``put_credit_spread``   - bullish/neutral, defined risk (Level 2)
* ``iron_condor``         - neutral, defined risk (Level 2)
* ``broken_wing_butterfly`` - neutral with skew, often a credit with no downside
                              risk on one side (Level 2)
* ``short_strangle``      - Level 3, undefined risk, capital-efficient premium
* ``jade_lizard``         - Level 3, short put + short call spread sized so there
                            is *no upside risk* while collecting extra premium

The Level-3 structures are gated by the engine (regime + tight CVaR sizing); the
defended-risk structures are the default workhorses for a $20k account.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..brokers.models import (Instruction, MultiLegOrder, OptionContract,
                              OptionType, OrderLeg)
from ..data.market_data import OptionChain, OptionRow


@dataclass
class Structure:
    name: str
    legs: list[OrderLeg]
    net_credit: float          # per 1 contract, in option points (x100 = $)
    max_loss: float            # per 1 contract, in $ (already x multiplier)
    max_profit: float          # per 1 contract, in $
    capital_req: float         # buying-power reduction per 1 contract, $
    breakevens: list[float] = field(default_factory=list)
    is_defined_risk: bool = True
    multiplier: int = 100

    @property
    def credit_dollars(self) -> float:
        return self.net_credit * self.multiplier

    @property
    def payoff_ratio(self) -> float:
        """Max profit / max loss - the 'b' in the Kelly formula."""
        return self.max_profit / self.max_loss if self.max_loss > 0 else float("inf")

    def to_order(self, quantity: int) -> MultiLegOrder:
        legs = [OrderLeg(contract=l.contract, instruction=l.instruction,
                         quantity=l.quantity * quantity) for l in self.legs]
        order_type = "NET_CREDIT" if self.net_credit > 0 else "NET_DEBIT"
        return MultiLegOrder(legs=legs, net_price=abs(self.net_credit),
                             order_type=order_type)


def _contract(row: OptionRow) -> OptionContract:
    return OptionContract(
        underlying=row.underlying, expiry=row.expiry, strike=row.strike,
        option_type=OptionType.CALL if row.is_call else OptionType.PUT)


def _leg(row: OptionRow, instruction: Instruction, qty: int = 1) -> OrderLeg:
    return OrderLeg(contract=_contract(row), instruction=instruction, quantity=qty)


def put_credit_spread(chain: OptionChain, expiry: date, short_delta: float,
                      wing_width: float, mult: int = 100) -> Structure | None:
    short = chain.by_delta(expiry, short_delta, is_call=False)
    if short is None:
        return None
    long_strike = short.strike - wing_width
    longs = [r for r in chain.for_expiry(expiry) if not r.is_call
             and r.strike <= long_strike]
    if not longs:
        return None
    long_row = max(longs, key=lambda r: r.strike)
    credit = short.mid - long_row.mid
    width = short.strike - long_row.strike
    max_loss = (width - credit) * mult
    return Structure(
        name="put_credit_spread",
        legs=[_leg(short, Instruction.SELL_TO_OPEN),
              _leg(long_row, Instruction.BUY_TO_OPEN)],
        net_credit=credit, max_loss=max(max_loss, 0.01),
        max_profit=credit * mult, capital_req=width * mult,
        breakevens=[short.strike - credit], multiplier=mult)


def call_credit_spread(chain: OptionChain, expiry: date, short_delta: float,
                       wing_width: float, mult: int = 100) -> Structure | None:
    short = chain.by_delta(expiry, short_delta, is_call=True)
    if short is None:
        return None
    long_strike = short.strike + wing_width
    longs = [r for r in chain.for_expiry(expiry) if r.is_call
             and r.strike >= long_strike]
    if not longs:
        return None
    long_row = min(longs, key=lambda r: r.strike)
    credit = short.mid - long_row.mid
    width = long_row.strike - short.strike
    max_loss = (width - credit) * mult
    return Structure(
        name="call_credit_spread",
        legs=[_leg(short, Instruction.SELL_TO_OPEN),
              _leg(long_row, Instruction.BUY_TO_OPEN)],
        net_credit=credit, max_loss=max(max_loss, 0.01),
        max_profit=credit * mult, capital_req=width * mult,
        breakevens=[short.strike + credit], multiplier=mult)


def iron_condor(chain: OptionChain, expiry: date, short_put_delta: float,
                short_call_delta: float, wing_width: float,
                mult: int = 100) -> Structure | None:
    pcs = put_credit_spread(chain, expiry, short_put_delta, wing_width, mult)
    ccs = call_credit_spread(chain, expiry, short_call_delta, wing_width, mult)
    if pcs is None or ccs is None:
        return None
    credit = pcs.net_credit + ccs.net_credit
    # Max loss is on the wider side (here equal width) minus total credit.
    width = max(l.contract.strike for l in ccs.legs) - min(
        l.contract.strike for l in ccs.legs)
    max_loss = (width - credit) * mult
    legs = pcs.legs + ccs.legs
    be_low = pcs.breakevens[0] if pcs.breakevens else float("nan")
    be_high = ccs.breakevens[0] if ccs.breakevens else float("nan")
    return Structure(
        name="iron_condor", legs=legs, net_credit=credit,
        max_loss=max(max_loss, 0.01), max_profit=credit * mult,
        capital_req=width * mult, breakevens=[be_low, be_high], multiplier=mult)


def broken_wing_butterfly(chain: OptionChain, expiry: date, short_delta: float,
                          near_width: float, far_width: float,
                          mult: int = 100) -> Structure | None:
    """Put broken-wing butterfly: long 1 / short 2 / long 1 with unequal wings.

    Sized so the structure is typically a credit with no risk on the upside and
    capped, reduced risk on the downside.
    """
    body = chain.by_delta(expiry, short_delta, is_call=False)
    if body is None:
        return None
    puts = [r for r in chain.for_expiry(expiry) if not r.is_call]
    upper = min((r for r in puts if r.strike >= body.strike + near_width),
                key=lambda r: r.strike, default=None)
    lower = max((r for r in puts if r.strike <= body.strike - far_width),
                key=lambda r: r.strike, default=None)
    if upper is None or lower is None:
        return None
    credit = (2 * body.mid) - upper.mid - lower.mid
    risk_width = body.strike - lower.strike
    max_loss = (risk_width - credit) * mult
    return Structure(
        name="broken_wing_butterfly",
        legs=[_leg(upper, Instruction.BUY_TO_OPEN),
              _leg(body, Instruction.SELL_TO_OPEN, qty=2),
              _leg(lower, Instruction.BUY_TO_OPEN)],
        net_credit=credit, max_loss=max(max_loss, 0.01),
        max_profit=max((body.strike - upper.strike + credit) * mult, credit * mult),
        capital_req=max(risk_width * mult, 0.0),
        breakevens=[body.strike - (risk_width - credit)], multiplier=mult)


def short_strangle(chain: OptionChain, expiry: date, put_delta: float,
                   call_delta: float, naked_margin_frac: float = 0.20,
                   mult: int = 100) -> Structure | None:
    """Level-3 short strangle (undefined risk).

    ``naked_margin_frac`` approximates the Reg-T/portfolio-margin requirement as a
    fraction of notional per short option; the engine sizes this very tightly.
    """
    sp = chain.by_delta(expiry, put_delta, is_call=False)
    sc = chain.by_delta(expiry, call_delta, is_call=True)
    if sp is None or sc is None:
        return None
    credit = sp.mid + sc.mid
    notional = (sp.strike + sc.strike) * mult
    capital = naked_margin_frac * notional
    return Structure(
        name="short_strangle", is_defined_risk=False,
        legs=[_leg(sp, Instruction.SELL_TO_OPEN),
              _leg(sc, Instruction.SELL_TO_OPEN)],
        net_credit=credit,
        # "Max loss" for sizing purposes: a stressed move to one breakeven leg.
        max_loss=max((sp.strike * 0.15) * mult, credit * mult),
        max_profit=credit * mult, capital_req=capital,
        breakevens=[sp.strike - credit, sc.strike + credit], multiplier=mult)


def jade_lizard(chain: OptionChain, expiry: date, put_delta: float,
                call_short_delta: float, call_wing: float,
                naked_margin_frac: float = 0.20, mult: int = 100) -> Structure | None:
    """Level-3 jade lizard: short put + short call spread with total credit >=
    call-spread width, eliminating upside risk."""
    sp = chain.by_delta(expiry, put_delta, is_call=False)
    ccs = call_credit_spread(chain, expiry, call_short_delta, call_wing, mult)
    if sp is None or ccs is None:
        return None
    credit = sp.mid + ccs.net_credit
    call_width = (max(l.contract.strike for l in ccs.legs)
                  - min(l.contract.strike for l in ccs.legs))
    # No upside risk iff credit >= call_width.
    upside_risk = max(call_width - credit, 0.0) * mult
    notional = sp.strike * mult
    capital = naked_margin_frac * notional + ccs.capital_req
    return Structure(
        name="jade_lizard", is_defined_risk=(upside_risk == 0.0),
        legs=[_leg(sp, Instruction.SELL_TO_OPEN)] + ccs.legs,
        net_credit=credit,
        max_loss=max((sp.strike * 0.15) * mult, credit * mult),
        max_profit=credit * mult, capital_req=capital,
        breakevens=[sp.strike - credit], multiplier=mult)
