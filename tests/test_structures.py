from datetime import date, timedelta

from qist.data.market_data import OptionChain, OptionRow
from qist.models.black_scholes import bs_greeks
from qist.strategy.structures import (broken_wing_butterfly, call_credit_spread,
                                      iron_condor, jade_lizard,
                                      put_credit_spread, short_strangle)


def _synth_chain(spot=450.0, dte=35, r=0.04, iv=0.18):
    expiry = date.today() + timedelta(days=dte)
    t = dte / 365.0
    rows = []
    for k in range(-25, 26):
        strike = round(spot + k * 5)
        for is_call in (True, False):
            g = bs_greeks(spot, strike, t, r, iv, is_call)
            spread = max(0.05, 0.02 * g.price)
            rows.append(OptionRow(
                underlying="XSP", expiry=expiry, dte=dte, strike=float(strike),
                is_call=is_call, bid=max(g.price - spread / 2, 0.0),
                ask=g.price + spread / 2, mid=g.price, iv=iv,
                delta=g.delta, gamma=g.gamma, theta=g.theta, vega=g.vega))
    return OptionChain(underlying="XSP", spot=spot, rows=rows), expiry


def test_put_credit_spread_defined_risk():
    chain, exp = _synth_chain()
    s = put_credit_spread(chain, exp, short_delta=0.16, wing_width=10)
    assert s is not None
    assert s.net_credit > 0
    assert s.max_loss > 0
    assert s.max_profit > 0
    # defined risk: max loss approx (width - credit) * 100
    assert s.is_defined_risk


def test_iron_condor_two_breakevens_and_credit():
    chain, exp = _synth_chain()
    ic = iron_condor(chain, exp, 0.16, 0.16, wing_width=10)
    assert ic is not None
    assert len(ic.legs) == 4
    assert ic.net_credit > 0
    assert len([b for b in ic.breakevens if b == b]) == 2
    assert ic.payoff_ratio > 0


def test_call_credit_spread():
    chain, exp = _synth_chain()
    s = call_credit_spread(chain, exp, 0.16, 10)
    assert s is not None and s.net_credit > 0


def test_broken_wing_butterfly_constructs():
    chain, exp = _synth_chain()
    bwb = broken_wing_butterfly(chain, exp, 0.30, near_width=10, far_width=20)
    assert bwb is not None
    assert len(bwb.legs) == 3


def test_short_strangle_is_undefined_risk():
    chain, exp = _synth_chain()
    ss = short_strangle(chain, exp, 0.16, 0.16)
    assert ss is not None
    assert not ss.is_defined_risk
    assert ss.net_credit > 0
    assert ss.capital_req > 0


def test_jade_lizard_no_upside_when_credit_ge_width():
    chain, exp = _synth_chain()
    jl = jade_lizard(chain, exp, put_delta=0.30, call_short_delta=0.30, call_wing=5)
    assert jl is not None
    assert jl.net_credit > 0


def test_structure_to_order_scales_quantity():
    chain, exp = _synth_chain()
    s = put_credit_spread(chain, exp, 0.16, 10)
    order = s.to_order(3)
    assert all(leg.quantity % 3 == 0 for leg in order.legs)
    assert order.order_type == "NET_CREDIT"
