"""Unit tests for MGO-VPH math core."""

from __future__ import annotations

import numpy as np

from mgo_vph.heston import HestonParams, heston_call_price
from mgo_vph.ofi import BookSnapshot, OFIAccumulator
from mgo_vph.regime import estimate_ms_garch_regime
from mgo_vph.signals import SignalConfig, evaluate_entry
from mgo_vph.sizing import fractional_kelly_contracts
from mgo_vph.vrp import OptionQuote, model_free_implied_variance, vrp_zscore


def test_ofi_accumulator_increases_on_bid_pressure() -> None:
    acc = OFIAccumulator()
    s0 = BookSnapshot(100.0, 500, 100.01, 400)
    s1 = BookSnapshot(100.0, 600, 100.01, 400)
    acc.update(s0)
    acc.update(s1)
    assert acc.ofi > 0


def test_vrp_zscore_positive_when_implied_high() -> None:
    hist = np.array([0.01, 0.012, 0.011, 0.013, 0.01, 0.011])
    res = vrp_zscore(0.25, 0.15, hist)
    assert res.vrp > 0
    assert res.vrp_z > 0


def test_kelly_returns_zero_contracts_on_negative_edge() -> None:
    r = fractional_kelly_contracts(20_000, 0.4, 100, 400)
    assert r.contracts == 0


def test_entry_blocked_on_bearish_ofi() -> None:
    from mgo_vph.ofi import OFIState
    from mgo_vph.regime import RegimeState
    from mgo_vph.vrp import VRPResult

    vrp = VRPResult(0.2, 0.15, 0.05, 1.5)
    regime = RegimeState(0.8, 0.12, "low")
    ofi = OFIState(-500, 100, -5.0)
    sig = evaluate_entry(vrp, regime, ofi, 0.01, 0.18, 0.14)
    assert not sig.allow_entry


def test_heston_call_positive() -> None:
    p = HestonParams(v0=0.04, kappa=2.0, theta=0.04, xi=0.3, rho=-0.7)
    price = heston_call_price(100, 100, 0.25, p, n_points=64)
    assert price > 0


def test_model_free_variance_runs() -> None:
    opts = [
        OptionQuote(95, 0.5, "put"),
        OptionQuote(97, 0.3, "put"),
        OptionQuote(103, 0.3, "call"),
        OptionQuote(105, 0.5, "call"),
    ]
    iv = model_free_implied_variance(100, 100, 30 / 365, opts)
    assert iv > 0
