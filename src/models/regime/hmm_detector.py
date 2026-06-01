"""
Hidden Markov Model (HMM) regime detector.

Mathematical Foundation
-----------------------
A Gaussian HMM models market states as discrete latent variables S_t ∈ {1,...,K}
with continuous observations X_t.

Model components:
  π_i = P(S_1 = i)                    Initial state distribution
  A_ij = P(S_t = j | S_{t-1} = i)     Transition matrix
  μ_k, Σ_k                             Emission: Gaussian N(μ_k, Σ_k) per state

Estimation (Baum-Welch / EM):
  1. E-step: Forward-backward algorithm to compute γ_t(k) = P(S_t=k | X_{1:T}, θ)
  2. M-step: Update π, A, μ, Σ using γ

Decoding (Viterbi):
  S* = argmax P(S_{1:T} | X_{1:T}, θ)

Features used as observations:
  1. Log returns (mean)
  2. 21-day realized volatility (standard)
  3. VIX level (market fear gauge)
  4. VIX term structure slope (VIX3M/VIX contango/backwardation)
  5. VRP = IV - RV (volatility risk premium)

States are assigned post-fit by sorting on realized volatility:
  State 0 (Low Vol / Bull):     σ < 12%, VRP positive
  State 1 (Normal / Transition): 12% < σ < 20%, mixed signals
  State 2 (High Vol / Crisis):  σ > 20%, VRP possibly negative

This regime identification drives strategy selection:
  - Low Vol → Iron Condors / short strangles (sell premium aggressively)
  - Normal  → Conservative condors / butterflies
  - High Vol → Calendar spreads or flat (no new premium selling)
  - Crisis  → Exit all short vega positions
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from src.utils.logger import logger


class RegimeLabel(str, Enum):
    LOW_VOL = "low_vol"
    NORMAL_VOL = "normal_vol"
    HIGH_VOL = "high_vol"
    CRISIS = "crisis"


@dataclass
class RegimeState:
    """Current regime state and associated metadata."""
    label: RegimeLabel
    state_index: int
    confidence: float         # Posterior probability of current state
    transition_prob: float    # P(staying in current state)
    regime_vol: float         # Average volatility in this state
    kelly_multiplier: float   # Position size multiplier for this regime
    allow_new_positions: bool
    
    @property
    def is_favourable_for_premium_selling(self) -> bool:
        return self.label in (RegimeLabel.LOW_VOL, RegimeLabel.NORMAL_VOL)


class HMMRegimeDetector:
    """
    Fits and queries a Gaussian Hidden Markov Model for market regime detection.

    The model is trained on historical data and updated weekly (or on-demand).
    In live trading, the current regime is determined by computing the posterior
    state probabilities given the most recent observations.

    Parameters
    ----------
    n_states : number of hidden states (3 recommended: low/normal/high vol)
    """

    # Kelly multipliers per regime (conservative)
    KELLY_MULTIPLIERS = {
        0: 0.50,   # Low vol → 50% of Kelly
        1: 0.35,   # Normal → 35%
        2: 0.15,   # High vol → 15%
        3: 0.00,   # Crisis → 0% (no new positions)
    }

    def __init__(
        self,
        n_states: int = 3,
        covariance_type: str = "full",
        n_iter: int = 200,
        random_state: int = 42,
    ) -> None:
        self.n_states = n_states
        self.covariance_type = covariance_type
        self.n_iter = n_iter
        self.random_state = random_state
        self._model = None
        self._scaler = StandardScaler()
        self._state_to_label: Dict[int, RegimeLabel] = {}
        self._fitted = False

    # ------------------------------------------------------------------
    # Feature engineering
    # ------------------------------------------------------------------

    @staticmethod
    def build_features(
        df: pd.DataFrame,
        vix: Optional[pd.Series] = None,
        vix3m: Optional[pd.Series] = None,
        vrp: Optional[pd.Series] = None,
        rv_window: int = 21,
    ) -> pd.DataFrame:
        """
        Build the observation matrix from price data and optional auxiliary series.

        Minimum required columns in df: close
        Optional: open, high, low (for better RV estimates)

        Returns DataFrame with features:
          log_return, realized_vol_21d, vix_level,
          vix_term_slope, vrp, trend_strength
        """
        feats = pd.DataFrame(index=df.index)

        # Log returns
        feats["log_return"] = np.log(df["close"] / df["close"].shift(1))

        # Realized volatility (21-day close-to-close)
        feats["realized_vol_21d"] = (
            feats["log_return"].rolling(rv_window).std() * np.sqrt(252)
        )

        # VIX level (use as-is if provided)
        if vix is not None:
            feats["vix_level"] = vix.reindex(df.index)
        else:
            # Proxy: 30-day annualised vol of close
            feats["vix_level"] = feats["realized_vol_21d"]

        # VIX term structure slope: VIX3M/VIX (>1 = contango, <1 = backwardation)
        if vix3m is not None and vix is not None:
            feats["vix_term_slope"] = vix3m.reindex(df.index) / vix.reindex(df.index).replace(0, np.nan)
        else:
            feats["vix_term_slope"] = 1.0  # neutral default

        # VRP (if provided)
        if vrp is not None:
            feats["vrp"] = vrp.reindex(df.index)
        else:
            # Rough proxy: use VIX - realized_vol
            feats["vrp"] = feats["vix_level"] - feats["realized_vol_21d"]

        # Trend strength (absolute z-score of 21-day return)
        feats["trend_z"] = (
            feats["log_return"].rolling(rv_window).sum()
            / feats["log_return"].rolling(rv_window).std().replace(0, np.nan)
        )

        return feats.dropna()

    # ------------------------------------------------------------------
    # Fitting
    # ------------------------------------------------------------------

    def fit(
        self,
        features: pd.DataFrame,
        n_attempts: int = 5,
    ) -> "HMMRegimeDetector":
        """
        Fit the HMM model to feature data.

        Multiple random restarts are used to find the global likelihood maximum.
        State labels are assigned by sorting states on realized volatility.

        Parameters
        ----------
        features  : DataFrame from build_features()
        n_attempts: number of random restarts for EM (pick best log-likelihood)

        Returns self for chaining.
        """
        try:
            from hmmlearn.hmm import GaussianHMM
        except ImportError:
            raise ImportError("hmmlearn required: pip install hmmlearn")

        X = features.values.astype(float)
        X_scaled = self._scaler.fit_transform(X)

        best_model = None
        best_score = -np.inf

        for attempt in range(n_attempts):
            model = GaussianHMM(
                n_components=self.n_states,
                covariance_type=self.covariance_type,
                n_iter=self.n_iter,
                random_state=self.random_state + attempt,
            )
            try:
                model.fit(X_scaled)
                score = model.score(X_scaled)
                if score > best_score:
                    best_score = score
                    best_model = model
            except Exception as e:
                logger.debug(f"HMM attempt {attempt} failed: {e}")
                continue

        if best_model is None:
            raise RuntimeError("HMM fitting failed on all attempts")

        self._model = best_model
        self._assign_state_labels(features, X_scaled)
        self._fitted = True

        logger.info(
            f"HMM fitted | n_states={self.n_states} | "
            f"log-likelihood={best_score:.2f} | "
            f"state_labels={self._state_to_label}"
        )
        return self

    def _assign_state_labels(
        self, features: pd.DataFrame, X_scaled: np.ndarray
    ) -> None:
        """
        Assign semantic labels (low_vol, normal, high_vol) to states
        by sorting on the mean realized volatility per state.
        """
        states = self._model.predict(X_scaled)
        rv_col_idx = list(features.columns).index("realized_vol_21d")

        state_vols = {}
        for s in range(self.n_states):
            mask = states == s
            if mask.any():
                state_vols[s] = features.iloc[mask.tolist(), rv_col_idx].mean()
            else:
                state_vols[s] = 0.0

        # Sort states by volatility (ascending)
        sorted_states = sorted(state_vols.keys(), key=lambda s: state_vols[s])

        label_list = [RegimeLabel.LOW_VOL, RegimeLabel.NORMAL_VOL, RegimeLabel.HIGH_VOL]
        if len(sorted_states) > 3:
            label_list.append(RegimeLabel.CRISIS)

        for rank, state in enumerate(sorted_states):
            if rank < len(label_list):
                self._state_to_label[state] = label_list[rank]
            else:
                self._state_to_label[state] = RegimeLabel.CRISIS

        logger.debug(f"State → label mapping: {self._state_to_label}")

    # ------------------------------------------------------------------
    # Prediction
    # ------------------------------------------------------------------

    def predict(self, features: pd.DataFrame) -> List[RegimeLabel]:
        """Return state labels for each row in features."""
        if not self._fitted:
            raise RuntimeError("HMM not fitted. Call .fit() first.")
        X_scaled = self._scaler.transform(features.values.astype(float))
        states = self._model.predict(X_scaled)
        return [self._state_to_label.get(s, RegimeLabel.NORMAL_VOL) for s in states]

    def current_regime(self, features: pd.DataFrame) -> RegimeState:
        """
        Determine the current regime from the most recent observations.

        Uses the last row of features + posterior probabilities.

        Returns
        -------
        RegimeState with label, confidence, and trading parameters
        """
        if not self._fitted:
            logger.warning("HMM not fitted — returning default NORMAL regime")
            return RegimeState(
                label=RegimeLabel.NORMAL_VOL,
                state_index=0,
                confidence=0.5,
                transition_prob=0.8,
                regime_vol=0.15,
                kelly_multiplier=0.35,
                allow_new_positions=True,
            )

        X_scaled = self._scaler.transform(features.values.astype(float))

        # Get posterior probabilities for all time steps (forward-backward)
        _, posteriors = self._model.score_samples(X_scaled)

        # Current state = last posterior
        current_posteriors = posteriors[-1]
        state_idx = int(np.argmax(current_posteriors))
        confidence = float(current_posteriors[state_idx])

        label = self._state_to_label.get(state_idx, RegimeLabel.NORMAL_VOL)
        trans_prob = float(self._model.transmat_[state_idx, state_idx])

        # Estimate volatility of current state
        rv_col = list(features.columns).index("realized_vol_21d") \
            if "realized_vol_21d" in features.columns else 0
        state_series = self._model.predict(X_scaled)
        state_rvs = features.values[state_series == state_idx, rv_col] \
            if any(state_series == state_idx) else np.array([0.15])
        regime_vol = float(np.mean(state_rvs))

        # Kelly multiplier
        vol_rank = sorted(
            self._state_to_label.keys(),
            key=lambda s: self._state_to_label[s].value,
        )
        state_rank = vol_rank.index(state_idx) if state_idx in vol_rank else 1
        kelly_mult = self.KELLY_MULTIPLIERS.get(state_rank, 0.25)

        return RegimeState(
            label=label,
            state_index=state_idx,
            confidence=confidence,
            transition_prob=trans_prob,
            regime_vol=regime_vol,
            kelly_multiplier=kelly_mult,
            allow_new_positions=label not in (RegimeLabel.CRISIS,),
        )

    def regime_history(self, features: pd.DataFrame) -> pd.DataFrame:
        """Return a DataFrame of regime states over the full feature history."""
        if not self._fitted:
            raise RuntimeError("HMM not fitted.")
        X_scaled = self._scaler.transform(features.values.astype(float))
        states = self._model.predict(X_scaled)
        _, posteriors = self._model.score_samples(X_scaled)

        labels = [self._state_to_label.get(s, RegimeLabel.NORMAL_VOL) for s in states]
        confidences = posteriors.max(axis=1)

        return pd.DataFrame({
            "state": states,
            "label": labels,
            "confidence": confidences,
            **{f"p_state_{i}": posteriors[:, i] for i in range(self.n_states)},
        }, index=features.index)

    def transition_matrix(self) -> pd.DataFrame:
        """Return the estimated transition matrix with state labels."""
        if not self._fitted:
            return pd.DataFrame()
        labels = [self._state_to_label.get(i, f"State_{i}").value
                  for i in range(self.n_states)]
        return pd.DataFrame(
            self._model.transmat_,
            index=labels,
            columns=labels,
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        """Save fitted model to disk."""
        obj = {
            "model": self._model,
            "scaler": self._scaler,
            "state_to_label": self._state_to_label,
            "n_states": self.n_states,
        }
        with open(path, "wb") as f:
            pickle.dump(obj, f)
        logger.info(f"HMM saved to {path}")

    def load(self, path: str) -> "HMMRegimeDetector":
        """Load a previously fitted model."""
        with open(path, "rb") as f:
            obj = pickle.load(f)
        self._model = obj["model"]
        self._scaler = obj["scaler"]
        self._state_to_label = obj["state_to_label"]
        self.n_states = obj["n_states"]
        self._fitted = True
        logger.info(f"HMM loaded from {path}")
        return self
