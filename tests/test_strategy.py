"""
Tests for strategy components: VRP engine, structure selector, portfolio manager.
"""

import numpy as np
import pandas as pd
import pytest
from datetime import datetime, date

from src.strategy.vrp_engine import VRPEngine, VRPSignal
from src.strategy.structure_selector import StructureSelector
from src.strategy.portfolio_manager import PortfolioManager, PortfolioGreeks
from src.models.regime.hmm_detector import RegimeState, RegimeLabel


def make_price_df(n=100, seed=42):
    """Generate synthetic OHLCV DataFrame."""
    np.random.seed(seed)
    close = 100 * np.exp(np.cumsum(np.random.normal(0.0001, 0.01, n)))
    high = close * (1 + np.abs(np.random.normal(0, 0.005, n)))
    low = close * (1 - np.abs(np.random.normal(0, 0.005, n)))
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    idx = pd.date_range("2024-01-01", periods=n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1e6}, index=idx)


def make_regime(label=RegimeLabel.NORMAL_VOL):
    return RegimeState(
        label=label, state_index=0, confidence=0.80,
        transition_prob=0.85, regime_vol=0.15,
        kelly_multiplier=0.35, allow_new_positions=True,
    )


class TestVRPEngine:
    """Tests for VRP signal generation."""

    def test_signal_generated(self):
        """VRPEngine should produce a VRPSignal."""
        engine = VRPEngine("SPY", rv_window=21)
        df = make_price_df(100)
        signal = engine.update(df, implied_vol=0.17, vix=15.0, vix3m=15.3)
        assert isinstance(signal, VRPSignal)

    def test_signal_strength_in_range(self):
        """Signal strength must be in [0, 1]."""
        engine = VRPEngine("SPY")
        df = make_price_df(100)
        signal = engine.update(df, implied_vol=0.18, vix=15.0, vix3m=15.5)
        assert 0 <= signal.signal_strength <= 1

    def test_vrp_positive_when_iv_above_rv(self):
        """VRP composite should be positive when IV > RV."""
        engine = VRPEngine("SPY")
        df = make_price_df(100)
        signal = engine.update(df, implied_vol=0.25, vix=20.0, vix3m=21.0)
        # IV=25% should be well above realistic RV from our synthetic series
        assert signal.vrp_composite > 0

    def test_high_signal_when_vrp_large(self):
        """Higher IV above RV should increase signal strength."""
        engine1 = VRPEngine("SPY1")
        engine2 = VRPEngine("SPY2")
        df = make_price_df(100)

        sig_low = engine1.update(df, implied_vol=0.14, vix=12.0, vix3m=12.2)
        # Reset history
        for _ in range(20):
            engine1.update(df, implied_vol=0.14, vix=12.0, vix3m=12.2)

        sig_high = engine2.update(df, implied_vol=0.25, vix=20.0, vix3m=20.5)
        for _ in range(20):
            sig_high = engine2.update(df, implied_vol=0.25, vix=20.0, vix3m=20.5)

        # Higher IV should build stronger signal over time
        assert sig_high.vrp_composite >= sig_low.vrp_composite

    def test_exit_conditions_crisis(self):
        """Exit signal triggered in crisis regime."""
        engine = VRPEngine("SPY")
        df = make_price_df(100)
        engine.update(df, implied_vol=0.15, vix=15.0, vix3m=15.3)

        crisis_regime = RegimeState(
            label=RegimeLabel.CRISIS, state_index=2, confidence=0.9,
            transition_prob=0.8, regime_vol=0.35, kelly_multiplier=0.0,
            allow_new_positions=False,
        )
        should_exit = engine.check_exit_conditions(0.15, 0.15, crisis_regime)
        assert should_exit

    def test_no_exit_in_good_conditions(self):
        """No exit signal when VRP strong and regime normal."""
        engine = VRPEngine("SPY", exit_threshold=0.30)
        df = make_price_df(100)
        # Build up signal history
        for _ in range(25):
            engine.update(df, implied_vol=0.18, vix=16.0, vix3m=16.5)

        regime = make_regime()
        should_exit = engine.check_exit_conditions(0.18, 0.13, regime)
        # RV (0.13) < IV (0.18) → should not exit
        assert not should_exit


class TestStructureSelector:
    """Tests for options structure selection."""

    def test_returns_none_in_crisis(self):
        """No structure selected in crisis regime."""
        selector = StructureSelector()
        crisis = RegimeState(
            label=RegimeLabel.CRISIS, state_index=2, confidence=0.9,
            transition_prob=0.8, regime_vol=0.35, kelly_multiplier=0.0,
            allow_new_positions=False,
        )
        from src.strategy.vrp_engine import VRPSignal
        signal = VRPSignal(
            timestamp=datetime.now(), symbol="SPY",
            implied_vol=0.25, realized_vol_cc=0.20, realized_vol_yz=0.20,
            garch_forecast=0.22, vrp_cc=0.05, vrp_yz=0.05, vrp_garch=0.03,
            vrp_composite=0.04, vrp_percentile=0.55, signal_strength=0.60,
            vix_level=25.0, vix_term_slope=0.95, skew_25d=0.05, put_call_ratio=1.2,
        )
        result = selector.select("SPY", 450, 0.25, signal, crisis)
        assert result is None

    def test_structure_selected_low_vol(self):
        """Iron condor selected in low vol regime."""
        selector = StructureSelector()
        regime = RegimeState(
            label=RegimeLabel.LOW_VOL, state_index=0, confidence=0.90,
            transition_prob=0.90, regime_vol=0.10, kelly_multiplier=0.50,
            allow_new_positions=True,
        )
        from src.strategy.vrp_engine import VRPSignal
        signal = VRPSignal(
            timestamp=datetime.now(), symbol="SPY",
            implied_vol=0.15, realized_vol_cc=0.11, realized_vol_yz=0.11,
            garch_forecast=0.12, vrp_cc=0.04, vrp_yz=0.04, vrp_garch=0.03,
            vrp_composite=0.04, vrp_percentile=0.75, signal_strength=0.75,
            vix_level=12.0, vix_term_slope=1.03, skew_25d=0.02, put_call_ratio=0.9,
        )
        result = selector.select("SPY", 450, 0.15, signal, regime)
        assert result is not None, "Should select a structure in low vol regime"
        assert result.net_credit > 0

    def test_returns_none_weak_signal(self):
        """No structure with weak VRP signal."""
        selector = StructureSelector()
        regime = make_regime()
        from src.strategy.vrp_engine import VRPSignal
        signal = VRPSignal(
            timestamp=datetime.now(), symbol="SPY",
            implied_vol=0.15, realized_vol_cc=0.14, realized_vol_yz=0.14,
            garch_forecast=0.14, vrp_cc=0.01, vrp_yz=0.01, vrp_garch=0.01,
            vrp_composite=0.01, vrp_percentile=0.25, signal_strength=0.25,  # WEAK
            vix_level=14.0, vix_term_slope=1.01, skew_25d=0.01, put_call_ratio=1.0,
        )
        result = selector.select("SPY", 450, 0.15, signal, regime)
        assert result is None


class TestPortfolioManager:
    """Tests for portfolio Greeks and P&L management."""

    def test_initial_equity(self):
        """Starting equity equals account size."""
        pm = PortfolioManager(account_size=20000)
        assert pm.current_equity == 20000.0

    def test_pnl_update(self):
        """P&L updates correctly."""
        pm = PortfolioManager(account_size=20000)
        pm.update_pnl(500)
        assert pm.current_equity == 20500.0

    def test_drawdown_calculation(self):
        """Drawdown correctly computed from peak."""
        pm = PortfolioManager(account_size=20000)
        pm.update_pnl(2000)   # peak at 22000
        pm.update_pnl(-4000)  # now at 18000
        dd = pm.drawdown
        assert dd == pytest.approx(-4000 / 22000, abs=0.01)

    def test_empty_portfolio_greeks(self):
        """Empty portfolio has zero Greeks."""
        pm = PortfolioManager()
        greeks = pm.compute_portfolio_greeks(100, 0.20)
        assert greeks.delta == 0.0
        assert greeks.gamma == 0.0

    def test_breakeven_vol_infinite_no_gamma(self):
        """Breakeven vol is infinite when gamma is zero."""
        greeks = PortfolioGreeks(theta=-50 / 365, gamma=0)
        assert greeks.breakeven_vol == float("inf")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
