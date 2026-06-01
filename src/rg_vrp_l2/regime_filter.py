"""
Hamilton-style regime detection via Gaussian Hidden Markov Model.

Reference: Hamilton (1989), Econometrica 57(2), 357-384.
Uses filtered (causal) state probabilities only — no look-ahead.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression


@dataclass
class RegimeState:
    """Current regime inference."""

    calm_probability: float
    turbulent_probability: float
    allow_new_short_vol: bool
    size_multiplier: float
    should_flatten: bool


class RegimeFilter:
    """Two-state Markov switching filter on daily returns."""

    def __init__(
        self,
        n_states: int = 2,
        turbulence_halt: float = 0.60,
        turbulence_flatten: float = 0.80,
        half_size_threshold: float = 0.40,
    ) -> None:
        self.n_states = n_states
        self.turbulence_halt = turbulence_halt
        self.turbulence_flatten = turbulence_flatten
        self.half_size_threshold = half_size_threshold
        self._model_result = None
        self._turbulent_idx: int | None = None

    def fit(self, close_prices: pd.Series) -> "RegimeFilter":
        """Fit HMM on log returns. Higher-variance state = turbulent."""
        prices = close_prices.dropna().astype(float)
        log_returns = np.log(prices / prices.shift(1)).dropna()

        model = MarkovRegression(
            log_returns.values,
            k_regimes=self.n_states,
            trend="c",
            switching_variance=True,
        )
        self._model_result = model.fit(disp=False)

        # Regime variances are the last k_regimes parameters when switching_variance=True
        params = np.asarray(self._model_result.params, dtype=float)
        regime_vars = list(params[-self.n_states :])
        if len(regime_vars) < self.n_states:
            regime_vars = [float(np.var(log_returns))] * self.n_states
        self._turbulent_idx = int(np.argmax(regime_vars))
        return self

    def predict(self, close_prices: pd.Series | None = None) -> RegimeState:
        """Return causal regime state for latest observation."""
        if self._model_result is None:
            raise RuntimeError("RegimeFilter must be fit before predict")

        filtered = self._model_result.filtered_marginal_probabilities
        if filtered is None or len(filtered) == 0:
            p_turb = 0.5
        else:
            arr = np.asarray(filtered)
            if arr.ndim == 1:
                p_turb = float(arr[self._turbulent_idx])
            else:
                p_turb = float(arr[-1, self._turbulent_idx])

        p_calm = 1.0 - p_turb

        allow_new = p_turb <= self.turbulence_halt
        flatten = p_turb > self.turbulence_flatten

        if p_turb <= self.half_size_threshold:
            size_mult = 1.0
        elif p_turb <= self.turbulence_halt:
            size_mult = 0.5
        else:
            size_mult = 0.0

        return RegimeState(
            calm_probability=p_calm,
            turbulent_probability=p_turb,
            allow_new_short_vol=allow_new,
            size_multiplier=size_mult,
            should_flatten=flatten,
        )

    @classmethod
    def from_yfinance(
        cls,
        symbol: str = "SPY",
        lookback_days: int = 504,
        **kwargs,
    ) -> tuple["RegimeFilter", RegimeState]:
        """Convenience: download history, fit, return filter + current state."""
        import yfinance as yf

        hist = yf.download(
            symbol,
            period=f"{max(lookback_days, 60)}d",
            progress=False,
            auto_adjust=True,
        )
        if hist.empty:
            raise ValueError(f"No data for {symbol}")

        close = hist["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]

        filt = cls(**kwargs)
        filt.fit(close)
        return filt, filt.predict(close)
