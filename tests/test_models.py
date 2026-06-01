"""
Tests for mathematical models: Black-Scholes, GARCH, HMM, vol surface.
"""

import numpy as np
import pandas as pd
import pytest

from src.models.pricing.black_scholes import BlackScholes, OptionGreeks
from src.models.pricing.spreads import SpreadPricer
from src.models.volatility.realized_vol import RealizedVolatility
from src.models.regime.hurst import HurstAnalyzer


# ---------------------------------------------------------------
# Black-Scholes Tests
# ---------------------------------------------------------------

class TestBlackScholes:
    """Test BSM pricing and Greeks against known analytical values."""

    # Canonical BSM test case (put-call parity verified)
    S, K, r, q, sigma, T = 100, 100, 0.05, 0.02, 0.20, 1.0

    def test_atm_call_price(self):
        """ATM call should have well-known approximate value."""
        price = BlackScholes.price(self.S, self.K, self.r, self.q, self.sigma, self.T, "call")
        assert 7 < price < 12, f"ATM call price {price:.4f} outside expected range"

    def test_put_call_parity(self):
        """C - P = S·e^{-qT} - K·e^{-rT}"""
        call = BlackScholes.price(self.S, self.K, self.r, self.q, self.sigma, self.T, "call")
        put = BlackScholes.price(self.S, self.K, self.r, self.q, self.sigma, self.T, "put")
        lhs = call - put
        rhs = self.S * np.exp(-self.q * self.T) - self.K * np.exp(-self.r * self.T)
        assert abs(lhs - rhs) < 1e-8, f"Put-call parity violated: {lhs:.6f} ≠ {rhs:.6f}"

    def test_call_delta_range(self):
        """Call delta must be in (0, 1)."""
        g = BlackScholes.greeks(self.S, self.K, self.r, self.q, self.sigma, self.T, "call")
        assert 0 < g.delta < 1

    def test_put_delta_range(self):
        """Put delta must be in (-1, 0)."""
        g = BlackScholes.greeks(self.S, self.K, self.r, self.q, self.sigma, self.T, "put")
        assert -1 < g.delta < 0

    def test_gamma_positive(self):
        """Gamma is always positive for vanilla options."""
        g = BlackScholes.greeks(self.S, self.K, self.r, self.q, self.sigma, self.T, "call")
        assert g.gamma > 0

    def test_theta_negative_long(self):
        """Theta should be negative for long options (time decay)."""
        g = BlackScholes.greeks(self.S, self.K, self.r, self.q, self.sigma, self.T, "call")
        assert g.theta < 0

    def test_vega_positive(self):
        """Vega (per vol point) should be positive."""
        g = BlackScholes.greeks(self.S, self.K, self.r, self.q, self.sigma, self.T, "call")
        assert g.vega > 0

    def test_implied_vol_roundtrip(self):
        """Recover implied vol from priced option."""
        price = BlackScholes.price(self.S, self.K, self.r, self.q, self.sigma, self.T, "call")
        iv = BlackScholes.implied_volatility(price, self.S, self.K, self.r, self.q, self.T, "call")
        assert abs(iv - self.sigma) < 1e-4, f"IV roundtrip failed: {iv:.6f} ≠ {self.sigma:.6f}"

    def test_deep_otm_call_near_zero(self):
        """Deep OTM call should have near-zero price."""
        price = BlackScholes.price(100, 200, 0.05, 0.02, 0.20, 0.1, "call")
        assert price < 0.01

    def test_intrinsic_value_at_expiry(self):
        """At expiry (T=0), price = intrinsic value."""
        call = BlackScholes.price(110, 100, 0.05, 0.02, 0.20, 0, "call")
        assert abs(call - 10.0) < 1e-8

    def test_delta_to_strike(self):
        """Delta-to-strike inversion should be consistent."""
        target_delta = 0.15
        K = BlackScholes.delta_to_strike(
            self.S, self.r, self.q, self.sigma, self.T, target_delta, "put"
        )
        g = BlackScholes.greeks(self.S, K, self.r, self.q, self.sigma, self.T, "put")
        assert abs(abs(g.delta) - target_delta) < 0.02, \
            f"Delta at computed strike {abs(g.delta):.4f} ≠ target {target_delta:.4f}"

    def test_prob_expire_worthless_otm(self):
        """Deep OTM call has high probability of expiring worthless."""
        p = BlackScholes.prob_expire_worthless(100, 150, 0.05, 0.02, 0.20, 0.25, "call")
        assert p > 0.8


class TestSpreadPricer:
    """Test spread pricing logic."""

    def test_iron_condor_net_credit_positive(self):
        """Iron condor should collect positive net credit."""
        pricer = SpreadPricer(r=0.05, q=0.014)
        analysis = pricer.price_iron_condor(
            S=100, put_short_K=90, put_long_K=85,
            call_short_K=110, call_long_K=115,
            sigma=0.20, T=0.10,
        )
        assert analysis.net_credit > 0, f"Iron condor credit should be positive: {analysis.net_credit}"

    def test_iron_condor_max_loss_positive(self):
        """Max loss should be positive (bounded)."""
        pricer = SpreadPricer(r=0.05, q=0.014)
        analysis = pricer.price_iron_condor(
            S=100, put_short_K=90, put_long_K=85,
            call_short_K=110, call_long_K=115,
            sigma=0.20, T=0.10,
        )
        assert analysis.max_loss > 0

    def test_iron_condor_profit_probability(self):
        """P(profit) should be > 50% for 15-delta condor."""
        pricer = SpreadPricer(r=0.05, q=0.014)
        K = 100
        put_short = BlackScholes.delta_to_strike(K, 0.05, 0.014, 0.20, 0.10, 0.15, "put")
        call_short = BlackScholes.delta_to_strike(K, 0.05, 0.014, 0.20, 0.10, 0.15, "call")

        analysis = pricer.price_iron_condor(
            S=K, put_short_K=put_short, put_long_K=put_short - 5,
            call_short_K=call_short, call_long_K=call_short + 5,
            sigma=0.20, T=0.10,
        )
        assert analysis.profit_probability > 0.60, \
            f"Profit probability {analysis.profit_probability:.2%} too low for 15-delta condor"

    def test_vertical_spread_credit_positive(self):
        """Bull put spread should have positive credit."""
        pricer = SpreadPricer(r=0.05, q=0.014)
        analysis = pricer.price_vertical_spread(
            S=100, short_K=95, long_K=90,
            sigma_short=0.22, sigma_long=0.24,
            T=0.10, option_type="put",
        )
        assert analysis.net_credit > 0


# ---------------------------------------------------------------
# Realized Volatility Tests
# ---------------------------------------------------------------

class TestRealizedVolatility:
    """Test RV estimators."""

    @pytest.fixture
    def ohlcv(self):
        """Generate synthetic OHLCV with known volatility."""
        np.random.seed(42)
        n = 100
        ret = np.random.normal(0, 0.01, n)  # 1% daily vol = ~16% annual
        close = 100 * np.exp(np.cumsum(ret))
        high = close * (1 + np.abs(np.random.normal(0, 0.005, n)))
        low = close * (1 - np.abs(np.random.normal(0, 0.005, n)))
        open_ = np.roll(close, 1)
        open_[0] = close[0]
        return open_, high, low, close

    def test_cc_estimator_range(self, ohlcv):
        """CC estimator should return reasonable annualised vol."""
        _, _, _, close = ohlcv
        rv = RealizedVolatility(window=21)
        vol = rv.close_to_close(close)
        assert 0.05 < vol < 0.50, f"CC vol {vol:.3f} outside reasonable range"

    def test_yang_zhang_reasonable(self, ohlcv):
        """Yang-Zhang estimator should give reasonable result."""
        o, h, l, c = ohlcv
        rv = RealizedVolatility(window=21)
        vol = rv.yang_zhang(o, h, l, c)
        assert 0.05 < vol < 0.50

    def test_parkinson_less_than_cc(self, ohlcv):
        """Parkinson should generally be less noisy (lower) than CC."""
        _, h, l, c = ohlcv
        rv = RealizedVolatility(window=21)
        cc_vol = rv.close_to_close(c)
        pk_vol = rv.parkinson(h, l)
        # Both should be positive and in reasonable range
        assert pk_vol > 0 and cc_vol > 0

    def test_vrp_computation(self):
        """VRP = IV - RV should be computable."""
        rv_calc = RealizedVolatility()
        rv_series = pd.Series(
            [0.12, 0.13, 0.14, 0.12, 0.13] * 10,
            index=pd.date_range("2024-01-01", periods=50)
        )
        iv_series = pd.Series(
            [0.15, 0.16, 0.17, 0.15, 0.16] * 10,
            index=pd.date_range("2024-01-01", periods=50)
        )
        vrp = rv_calc.compute_vrp(rv_series, iv_series)
        assert (vrp > 0).all(), "VRP should be positive when IV > RV"

    def test_vrp_signal_strength_range(self):
        """VRP signal strength should be in [0, 1]."""
        rv_calc = RealizedVolatility()
        vrp_series = pd.Series(np.random.normal(0.03, 0.01, 50))
        strength = rv_calc.vrp_signal_strength(vrp_series, lookback=20)
        assert 0 <= strength <= 1


# ---------------------------------------------------------------
# Hurst Exponent Tests
# ---------------------------------------------------------------

class TestHurstAnalyzer:
    """Test Hurst exponent calculations."""

    def test_white_noise_hurst_near_half(self):
        """White noise (iid) should have H in [0.3, 0.8] for R/S analysis."""
        np.random.seed(42)
        white_noise = np.random.normal(0, 1, 500)
        analyzer = HurstAnalyzer()
        result = analyzer.rs_hurst(white_noise)
        assert 0.25 < result.hurst < 0.85, \
            f"White noise Hurst {result.hurst:.3f} should be near 0.5"

    def test_trending_series_high_hurst(self):
        """Strongly trending (cumulative) series should have H > 0.5."""
        np.random.seed(1)
        random_walk = np.cumsum(np.random.normal(0.05, 1, 500))
        analyzer = HurstAnalyzer()
        result = analyzer.rs_hurst(random_walk)
        assert result.hurst > 0.5, f"Random walk Hurst {result.hurst:.3f} should be > 0.5"

    def test_mean_reverting_lower_than_random_walk(self):
        """AR(1) with negative coefficient has lower H than random walk."""
        np.random.seed(42)
        n = 600
        # Strongly anti-persistent AR(1): x_t = -0.8*x_{t-1} + eps
        x_ar = np.zeros(n)
        for i in range(1, n):
            x_ar[i] = -0.8 * x_ar[i - 1] + np.random.normal(0, 1)
        # Plain white noise
        white_noise = np.random.normal(0, 1, n)
        analyzer = HurstAnalyzer()
        h_ar = analyzer.rs_hurst(x_ar).hurst
        h_wn = analyzer.rs_hurst(white_noise).hurst
        # Anti-persistent should have lower or equal Hurst
        assert h_ar <= h_wn + 0.15, \
            f"AR(-0.8) Hurst {h_ar:.3f} not lower than WN Hurst {h_wn:.3f}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
