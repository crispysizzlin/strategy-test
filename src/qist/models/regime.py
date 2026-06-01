"""Volatility-regime detection via a Gaussian Hidden Markov Model.

Premium selling must be *conditioned* on regime: the literature (Vilkov 2026)
shows unconditional short-vol is dominated by tail losses, while regime-timed
rules reach Sharpe ~1.0-1.3.  We model the latent market state with a Gaussian
HMM on standardized daily log-returns (and optionally realized vol), trained by
Baum-Welch (EM) and decoded by Viterbi.

States are sorted by volatility so that state 0 = calm, ..., K-1 = stress.  The
engine harvests aggressively in calm/low states, throttles in transition, and
stands down (or flips to long convexity) in stress states.

This is a from-scratch, dependency-light HMM (diagonal/scalar Gaussian
emissions) so the toolkit has no heavy ML dependency.

References
----------
Hamilton, J. (1989). A New Approach to the Economic Analysis of Nonstationary
Time Series and the Business Cycle. *Econometrica*, 57(2), 357-384.
Baum, L. et al. (1970). A Maximization Technique ... *Ann. Math. Statist.*
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _gaussian_pdf(x: np.ndarray, mean: float, var: float) -> np.ndarray:
    var = max(var, 1e-10)
    return np.exp(-0.5 * (x - mean) ** 2 / var) / np.sqrt(2 * np.pi * var)


@dataclass
class HMMParams:
    startprob: np.ndarray         # (K,)
    transmat: np.ndarray          # (K, K)
    means: np.ndarray             # (K,)
    variances: np.ndarray         # (K,)
    vol_order: np.ndarray         # indices sorted calm->stress

    @property
    def n_states(self) -> int:
        return len(self.means)


class GaussianHMM:
    """Scalar-emission Gaussian HMM trained with the Baum-Welch algorithm."""

    def __init__(self, n_states: int = 3, n_iter: int = 100,
                 tol: float = 1e-4, seed: int = 7) -> None:
        self.n_states = n_states
        self.n_iter = n_iter
        self.tol = tol
        self.seed = seed
        self.params_: HMMParams | None = None

    # ---- training -------------------------------------------------------
    def fit(self, x: np.ndarray) -> "GaussianHMM":
        x = np.asarray(x, dtype=float).ravel()
        x = x[np.isfinite(x)]
        n = len(x)
        if n < 30:
            raise ValueError("Need >=30 observations to fit the HMM")
        rng = np.random.default_rng(self.seed)
        K = self.n_states

        # Init means via quantiles of the data, equal variance.
        qs = np.quantile(x, np.linspace(0.1, 0.9, K))
        means = qs + rng.normal(0, 1e-6, K)
        variances = np.full(K, np.var(x))
        startprob = np.full(K, 1.0 / K)
        transmat = np.full((K, K), 0.1 / (K - 1)) if K > 1 else np.ones((1, 1))
        np.fill_diagonal(transmat, 0.9)

        prev_ll = -np.inf
        for _ in range(self.n_iter):
            # E-step (forward-backward in scaled form).
            B = np.column_stack([_gaussian_pdf(x, means[k], variances[k])
                                 for k in range(K)])
            B = np.clip(B, 1e-300, None)
            alpha = np.zeros((n, K))
            scale = np.zeros(n)
            alpha[0] = startprob * B[0]
            scale[0] = alpha[0].sum()
            alpha[0] /= scale[0]
            for t in range(1, n):
                alpha[t] = (alpha[t - 1] @ transmat) * B[t]
                scale[t] = alpha[t].sum()
                alpha[t] /= scale[t]
            beta = np.zeros((n, K))
            beta[-1] = 1.0
            for t in range(n - 2, -1, -1):
                beta[t] = (transmat @ (B[t + 1] * beta[t + 1])) / scale[t + 1]

            gamma = alpha * beta
            gamma /= gamma.sum(axis=1, keepdims=True)

            xi_sum = np.zeros((K, K))
            for t in range(n - 1):
                denom = (alpha[t][:, None] * transmat *
                         (B[t + 1] * beta[t + 1])[None, :])
                denom /= denom.sum()
                xi_sum += denom

            # M-step.
            startprob = gamma[0] / gamma[0].sum()
            transmat = xi_sum / xi_sum.sum(axis=1, keepdims=True)
            for k in range(K):
                w = gamma[:, k]
                wsum = w.sum()
                means[k] = (w @ x) / wsum
                variances[k] = max((w @ (x - means[k]) ** 2) / wsum, 1e-10)

            ll = np.sum(np.log(scale))
            if abs(ll - prev_ll) < self.tol:
                break
            prev_ll = ll

        vol_order = np.argsort(variances)
        self.params_ = HMMParams(startprob, transmat, means, variances, vol_order)
        return self

    # ---- inference ------------------------------------------------------
    def predict_states(self, x: np.ndarray) -> np.ndarray:
        """Viterbi most-likely state path (raw state labels)."""
        p = self._require()
        x = np.asarray(x, dtype=float).ravel()
        n, K = len(x), p.n_states
        log_t = np.log(p.transmat + 1e-300)
        log_b = np.column_stack([
            np.log(_gaussian_pdf(x, p.means[k], p.variances[k]) + 1e-300)
            for k in range(K)])
        delta = np.zeros((n, K))
        psi = np.zeros((n, K), dtype=int)
        delta[0] = np.log(p.startprob + 1e-300) + log_b[0]
        for t in range(1, n):
            for k in range(K):
                seq = delta[t - 1] + log_t[:, k]
                psi[t, k] = int(np.argmax(seq))
                delta[t, k] = seq[psi[t, k]] + log_b[t, k]
        path = np.zeros(n, dtype=int)
        path[-1] = int(np.argmax(delta[-1]))
        for t in range(n - 2, -1, -1):
            path[t] = psi[t + 1, path[t + 1]]
        return path

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        """Filtered posterior state probabilities (forward pass), shape (n, K)."""
        p = self._require()
        x = np.asarray(x, dtype=float).ravel()
        n, K = len(x), p.n_states
        B = np.clip(np.column_stack([
            _gaussian_pdf(x, p.means[k], p.variances[k]) for k in range(K)]),
            1e-300, None)
        alpha = np.zeros((n, K))
        alpha[0] = p.startprob * B[0]
        alpha[0] /= alpha[0].sum()
        for t in range(1, n):
            alpha[t] = (alpha[t - 1] @ p.transmat) * B[t]
            alpha[t] /= alpha[t].sum()
        return alpha

    def current_vol_rank(self, x: np.ndarray) -> int:
        """Volatility rank of the latest state: 0=calm ... K-1=stress."""
        p = self._require()
        last_state = int(self.predict_states(x)[-1])
        return int(np.where(p.vol_order == last_state)[0][0])

    def _require(self) -> HMMParams:
        if self.params_ is None:
            raise RuntimeError("HMM must be fit before inference")
        return self.params_
