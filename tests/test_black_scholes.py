import math

import pytest

from qist.models.black_scholes import (bs_greeks, bs_price, forward_price,
                                        implied_vol)


def test_put_call_parity():
    S, K, t, r, sigma = 100.0, 100.0, 0.5, 0.04, 0.2
    c = bs_price(S, K, t, r, sigma, is_call=True)
    p = bs_price(S, K, t, r, sigma, is_call=False)
    # c - p == S - K e^{-rt}
    assert c - p == pytest.approx(S - K * math.exp(-r * t), abs=1e-9)


def test_atm_price_reasonable():
    px = bs_price(100, 100, 1.0, 0.0, 0.2, is_call=True)
    # ATM call with 20% vol ~ 0.4 * S * sigma * sqrt(t) ~ 7.97
    assert 7.5 < px < 8.3


def test_intrinsic_at_expiry():
    assert bs_price(110, 100, 0.0, 0.04, 0.2, is_call=True) == pytest.approx(10.0)
    assert bs_price(90, 100, 0.0, 0.04, 0.2, is_call=False) == pytest.approx(10.0)


def test_implied_vol_roundtrip():
    S, K, t, r, sigma = 100, 105, 0.3, 0.03, 0.25
    px = bs_price(S, K, t, r, sigma, is_call=True)
    iv = implied_vol(px, S, K, t, r, is_call=True)
    assert iv == pytest.approx(sigma, abs=1e-4)


def test_implied_vol_invalid_returns_nan():
    # price below intrinsic -> no solution
    iv = implied_vol(0.001, 150, 100, 0.5, 0.04, is_call=True)
    assert math.isnan(iv)


def test_greeks_signs():
    g_call = bs_greeks(100, 100, 0.5, 0.04, 0.2, is_call=True)
    g_put = bs_greeks(100, 100, 0.5, 0.04, 0.2, is_call=False)
    assert 0 < g_call.delta < 1
    assert -1 < g_put.delta < 0
    assert g_call.gamma > 0 and g_put.gamma > 0
    assert g_call.vega > 0
    assert g_call.theta < 0      # long option decays


def test_delta_finite_difference():
    S, K, t, r, sigma = 100, 100, 0.5, 0.04, 0.2
    h = 1e-4
    up = bs_price(S + h, K, t, r, sigma, True)
    dn = bs_price(S - h, K, t, r, sigma, True)
    fd_delta = (up - dn) / (2 * h)
    g = bs_greeks(S, K, t, r, sigma, True)
    assert g.delta == pytest.approx(fd_delta, abs=1e-4)


def test_vega_finite_difference():
    S, K, t, r, sigma = 100, 100, 0.5, 0.04, 0.2
    h = 1e-5
    up = bs_price(S, K, t, r, sigma + h, True)
    dn = bs_price(S, K, t, r, sigma - h, True)
    fd_vega = (up - dn) / (2 * h)
    g = bs_greeks(S, K, t, r, sigma, True)
    assert g.vega == pytest.approx(fd_vega, rel=1e-3)


def test_forward_price():
    assert forward_price(100, 1.0, 0.05) == pytest.approx(100 * math.exp(0.05))
