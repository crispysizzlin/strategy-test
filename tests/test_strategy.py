"""Unit tests for RG-VRP-L2 components."""

import numpy as np
import pandas as pd
import pytest

from rg_vrp_l2.inventory_skew import InventorySkew, PortfolioGreeks
from rg_vrp_l2.l2_microstructure import BookLevel, L2Microstructure, OrderBookSnapshot
from rg_vrp_l2.position_sizer import FractionalKellySizer
from rg_vrp_l2.regime_filter import RegimeFilter
from rg_vrp_l2.vrp_estimator import VRPEstimator, realized_volatility


@pytest.fixture
def sample_close():
    rng = np.random.default_rng(42)
    prices = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, 300)))
    return pd.Series(prices)


def test_realized_vol_positive(sample_close):
    rv = realized_volatility(sample_close)
    assert rv > 0


def test_vrp_sell_when_rich(sample_close):
    est = VRPEstimator(threshold_vol_points=2.0, iv_rank_min=0.0)
    sig = est.evaluate(sample_close, implied_vol_30d=30.0)
    assert sig.vrp > 0
    assert sig.should_sell_vol is True


def test_vrp_block_when_cheap(sample_close):
    est = VRPEstimator(threshold_vol_points=50.0)
    rv = realized_volatility(sample_close)
    sig = est.evaluate(sample_close, implied_vol_30d=rv - 5)
    assert sig.should_sell_vol is False


def test_regime_filter_fit_predict(sample_close):
    filt = RegimeFilter()
    filt.fit(sample_close)
    state = filt.predict()
    assert 0 <= state.turbulent_probability <= 1
    assert abs(state.calm_probability + state.turbulent_probability - 1) < 0.01


def test_inventory_skew_short_delta(sample_close):
    skew = InventorySkew()
    greeks = PortfolioGreeks(net_delta=0.5, net_vega=0, net_gamma=0)
    adj = skew.adjust_short_delta(0.16, greeks, float(sample_close.iloc[-1]), 28, 18.0)
    assert adj.adjusted_short_delta < 0.16


def test_l2_blocks_wide_spread():
    l2 = L2Microstructure(max_spread_pct=0.05)
    book = OrderBookSnapshot(
        symbol="SPY",
        bids=[BookLevel(100, 50)],
        asks=[BookLevel(110, 50)],
    )
    sig = l2.evaluate(book, 100, 110)
    assert sig.allow_entry is False


def test_kelly_sizer():
    sizer = FractionalKellySizer(kelly_fraction=0.25)
    size = sizer.size_iron_condor(
        account_equity=20000,
        max_loss_per_contract=400,
        credit_per_contract=120,
        win_prob=0.68,
        regime_multiplier=1.0,
    )
    assert size.contracts >= 0
    assert size.max_loss_dollars <= 20000 * 0.05 + 1
