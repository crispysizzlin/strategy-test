import numpy as np
import pytest

from qist.execution.almgren_chriss import optimal_trajectory, slice_sizes
from qist.execution.avellaneda_stoikov import (limit_price_for_credit,
                                               reservation_quote)
from qist.sizing.cvar import (max_contracts_by_cvar,
                              rockafellar_uryasev_cvar, var_cvar)
from qist.sizing.kelly import (fractional_kelly, kelly_binary,
                               kelly_from_samples)


def test_kelly_binary_known_value():
    # p=0.6, b=1 -> f = 0.6 - 0.4 = 0.2
    assert kelly_binary(0.6, 1.0) == pytest.approx(0.2)


def test_kelly_binary_nonpositive_edge():
    assert kelly_binary(0.4, 1.0) == 0.0


def test_kelly_from_samples_reasonable():
    rng = np.random.default_rng(0)
    r = np.where(rng.random(10000) < 0.7, 0.5, -1.0)
    f = kelly_from_samples(r)
    assert 0 < f < 1


def test_fractional_kelly_caps():
    assert fractional_kelly(1.0, 0.25, 0.3) == pytest.approx(0.25)
    assert fractional_kelly(2.0, 0.25, 0.3) == pytest.approx(0.3)


def test_var_cvar_ordering():
    losses = np.concatenate([np.zeros(950), np.linspace(1, 100, 50)])
    var, cvar = var_cvar(losses, 0.99)
    assert cvar >= var > 0


def test_rockafellar_uryasev_matches_empirical_cvar():
    rng = np.random.default_rng(1)
    losses = rng.gamma(2.0, 2.0, 50000)
    _, cvar_emp = var_cvar(losses, 0.95)
    cvar_ru = rockafellar_uryasev_cvar(losses, 0.95)
    assert abs(cvar_emp - cvar_ru) / cvar_emp < 0.05


def test_max_contracts_by_cvar_budget():
    per_contract_losses = np.concatenate([np.zeros(990), np.full(10, 500.0)])
    n = max_contracts_by_cvar(per_contract_losses, equity=20000,
                              cvar_budget_frac=0.05, alpha=0.99)
    assert n >= 0


def test_avellaneda_stoikov_inventory_skew():
    long_inv = reservation_quote(100, inventory=5, gamma=0.1, sigma=0.2,
                                 time_left=1.0, k=1.5)
    short_inv = reservation_quote(100, inventory=-5, gamma=0.1, sigma=0.2,
                                  time_left=1.0, k=1.5)
    assert long_inv.reservation_price < 100 < short_inv.reservation_price
    assert long_inv.half_spread > 0


def test_limit_price_for_credit_does_not_cross():
    mid, spread = 1.00, 0.10
    px = limit_price_for_credit(mid, spread, aggressiveness=0.5, is_sell=True)
    assert mid - spread / 2 <= px <= mid


def test_almgren_chriss_liquidation_monotone():
    x = optimal_trajectory(total_qty=100, n_slices=10, sigma=0.02,
                           eta=0.1, risk_aversion=1.0)
    assert x[0] == 100
    assert abs(x[-1]) < 1e-9
    assert np.all(np.diff(x) <= 1e-9)
    sizes = slice_sizes(100, 10, 0.02, 0.1, 1.0)
    assert sizes.sum() == pytest.approx(100.0)
