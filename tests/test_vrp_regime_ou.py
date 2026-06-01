import numpy as np

from qist.models.ou_meanrev import fit_ou
from qist.models.regime import GaussianHMM
from qist.models.vrp import (OptionQuote, carr_wu_variance_swap_rate,
                             forecast_vrp, realized_vrp)


def test_forecast_vrp_positive_when_iv_above_rv():
    sig = forecast_vrp(iv=0.25, rv_forecast=0.18)
    assert sig.vrp_var > 0
    assert sig.is_favorable
    assert sig.vrp_vol_points > 0
    assert sig.vrp_ratio > 1


def test_forecast_vrp_zscore_with_history():
    hist = list(np.random.default_rng(0).normal(0.01, 0.005, 100))
    sig = forecast_vrp(0.3, 0.15, hist)
    assert np.isfinite(sig.z_score)
    assert sig.z_score > 1   # 0.09-0.0225 ~ big positive vs ~0.01 mean


def test_carr_wu_variance_swap_rate_positive():
    # Build a symmetric OTM strip around forward=100, T=0.25.
    forward, t, r = 100.0, 0.25, 0.03
    quotes = []
    for k in range(80, 121, 5):
        is_call = k > 100
        # toy prices decaying away from the money
        mid = max(2.5 - 0.04 * abs(k - 100), 0.05)
        quotes.append(OptionQuote(strike=float(k), is_call=is_call, mid=mid))
    kvar = carr_wu_variance_swap_rate(forward, t, r, quotes)
    assert kvar > 0


def test_realized_vrp_sign():
    assert realized_vrp(0.06, 0.04) > 0
    assert realized_vrp(0.04, 0.06) < 0


def test_ou_recovers_mean_reversion():
    rng = np.random.default_rng(5)
    kappa_true, theta_true, sigma = 0.1, 5.0, 0.2
    x = np.zeros(2000)
    x[0] = 5.0
    for i in range(1, len(x)):
        x[i] = x[i - 1] + kappa_true * (theta_true - x[i - 1]) + sigma * rng.normal()
    params = fit_ou(x)
    assert params.is_mean_reverting
    assert params.theta == __import__("pytest").approx(theta_true, abs=0.5)
    assert params.half_life > 0


def test_hmm_separates_vol_regimes():
    rng = np.random.default_rng(9)
    calm = rng.normal(0, 0.5, 300)
    stress = rng.normal(0, 3.0, 150)
    x = np.concatenate([calm, stress, calm])
    hmm = GaussianHMM(n_states=2, n_iter=50).fit(x)
    # During the stress block the vol-rank should be the high state.
    rank_stress = hmm.current_vol_rank(np.concatenate([calm, stress]))
    rank_calm = hmm.current_vol_rank(calm)
    assert rank_stress >= rank_calm
    probs = hmm.predict_proba(x)
    assert probs.shape == (len(x), 2)
    assert np.allclose(probs.sum(axis=1), 1.0)
