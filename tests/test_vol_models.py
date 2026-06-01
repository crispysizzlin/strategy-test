import numpy as np
import pandas as pd

from qist.models.garch import GARCH11
from qist.models.har_rv import HARRV
from qist.models.realized_vol import (close_to_close_vol, parkinson_vol,
                                       realized_variance_series,
                                       yang_zhang_vol)


def _gbm_ohlc(n=400, mu=0.0, sigma=0.2, seed=1, intraday_steps=24):
    """Simulate daily OHLC by sub-stepping a GBM within each day, so the high/low
    range is *consistent* with the daily volatility (range-based estimators can
    therefore recover sigma)."""
    rng = np.random.default_rng(seed)
    dt = 1 / 252
    sub_dt = dt / intraday_steps
    opens, highs, lows, closes = [], [], [], []
    price = 100.0
    for _ in range(n):
        o = price
        path = [o]
        p = o
        for _ in range(intraday_steps):
            p *= np.exp((mu - 0.5 * sigma ** 2) * sub_dt
                        + sigma * np.sqrt(sub_dt) * rng.standard_normal())
            path.append(p)
        c = path[-1]
        opens.append(o)
        highs.append(max(path))
        lows.append(min(path))
        closes.append(c)
        price = c
    idx = pd.date_range("2020-01-01", periods=n, freq="B")
    return pd.DataFrame({"open": opens, "high": highs, "low": lows,
                         "close": closes}, index=idx)


def test_realized_vol_estimators_recover_sigma():
    df = _gbm_ohlc(sigma=0.2, n=900)
    cc = close_to_close_vol(df["close"])
    pk = parkinson_vol(df["high"], df["low"])
    yz = yang_zhang_vol(df["open"], df["high"], df["low"], df["close"])
    for est in (cc, pk, yz):
        assert 0.14 < est < 0.27   # recovers ~0.2 within sampling error


def test_har_rv_fit_and_forecast_positive():
    df = _gbm_ohlc(sigma=0.2, n=600)
    rv = realized_variance_series(df["close"], 21).pow(0.5).dropna()
    model = HARRV().fit(rv)
    fc = model.forecast(rv, horizon_days=5)
    assert fc > 0
    assert 0.0 <= model.fit_.r2 <= 1.0


def test_garch_fit_persistence_bounds():
    df = _gbm_ohlc(sigma=0.2, n=700)
    ret = np.log(df["close"] / df["close"].shift(1)).dropna().to_numpy()
    g = GARCH11().fit(ret)
    assert 0 <= g.fit_.persistence < 1.0
    fc = g.forecast_vol(horizon_days=5)
    assert fc > 0


def test_har_forecast_bounded_under_clustered_vol():
    rng = np.random.default_rng(3)
    vol = 0.15 + 0.1 * np.abs(np.sin(np.linspace(0, 12, 600)))
    close = 100 * np.exp(np.cumsum(rng.normal(0, vol / np.sqrt(252))))
    s = pd.Series(close, index=pd.date_range("2020", periods=600, freq="B"))
    rv = realized_variance_series(s, 21).pow(0.5).dropna()
    fc = HARRV().fit(rv).forecast(rv, 1)
    assert 0.05 < fc < 0.6
