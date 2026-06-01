"""Heterogeneous Autoregressive model of Realized Volatility (HAR-RV).

Corsi (2009) models realized volatility as a linear cascade of daily, weekly and
monthly components, capturing the long-memory of volatility with a parsimonious
OLS regression:

    RV_{t+1} = c + b_d * RV_t^{(d)} + b_w * RV_t^{(w)} + b_m * RV_t^{(m)} + eps

where RV^{(w)} and RV^{(m)} are 5- and 22-day averages of daily RV.  Despite its
simplicity, HAR-RV is the de-facto institutional benchmark and routinely beats
GARCH out-of-sample because it ingests high/low-frequency information directly.

We regress on volatility (sqrt of variance) rather than variance, which Corsi
notes is closer to Gaussian and improves OLS efficiency.

Reference
---------
Corsi, F. (2009). A Simple Approximate Long-Memory Model of Realized
Volatility. *Journal of Financial Econometrics*, 7(2), 174-196.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class HARFit:
    coef: np.ndarray          # [const, b_d, b_w, b_m]
    r2: float
    resid_std: float
    n_obs: int

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        c = self.coef
        return (f"HARFit(const={c[0]:.4f}, b_d={c[1]:.3f}, b_w={c[2]:.3f}, "
                f"b_m={c[3]:.3f}, R2={self.r2:.3f}, n={self.n_obs})")


def _har_design(rv: pd.Series) -> pd.DataFrame:
    """Build daily/weekly/monthly regressors from a daily RV (vol) series."""
    df = pd.DataFrame({"rv_d": rv})
    df["rv_w"] = rv.rolling(5).mean()
    df["rv_m"] = rv.rolling(22).mean()
    return df


class HARRV:
    """Estimate and forecast with a HAR-RV model on daily realized volatility."""

    def __init__(self) -> None:
        self.fit_: HARFit | None = None

    def fit(self, rv_daily: pd.Series) -> "HARRV":
        """Fit HAR-RV.  ``rv_daily`` is a daily realized-volatility series.

        Target is RV_{t+1}; regressors are the (d,w,m) components observed at t.
        """
        design = _har_design(rv_daily)
        target = rv_daily.shift(-1)        # predict next day
        data = pd.concat([target.rename("y"), design], axis=1).dropna()
        if len(data) < 30:
            raise ValueError("Need >=30 aligned observations to fit HAR-RV")

        y = data["y"].to_numpy()
        X = np.column_stack([
            np.ones(len(data)),
            data["rv_d"].to_numpy(),
            data["rv_w"].to_numpy(),
            data["rv_m"].to_numpy(),
        ])
        coef, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ coef
        ss_res = float(resid @ resid)
        ss_tot = float(((y - y.mean()) ** 2).sum())
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        dof = max(len(y) - X.shape[1], 1)
        self.fit_ = HARFit(coef=coef, r2=r2,
                           resid_std=float(np.sqrt(ss_res / dof)),
                           n_obs=len(y))
        return self

    def forecast(self, rv_daily: pd.Series, horizon_days: int = 1) -> float:
        """Forecast annualized realized volatility ``horizon_days`` ahead.

        For multi-day horizons we iterate the one-step model, appending each
        forecast and rolling the (d,w,m) aggregates forward.  Because the
        regressors are slow-moving averages this is stable.
        """
        if self.fit_ is None:
            raise RuntimeError("HARRV must be fit before forecasting")
        c = self.fit_.coef
        series = rv_daily.dropna().copy()
        last = float("nan")
        for _ in range(horizon_days):
            rv_d = float(series.iloc[-1])
            rv_w = float(series.iloc[-5:].mean())
            rv_m = float(series.iloc[-22:].mean())
            last = c[0] + c[1] * rv_d + c[2] * rv_w + c[3] * rv_m
            last = max(last, 1e-6)
            series = pd.concat([series, pd.Series([last])], ignore_index=True)
        return last
