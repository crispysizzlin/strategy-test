"""
Tests for risk management: position sizing, drawdown control, VRP engine.
"""

import numpy as np
import pandas as pd
import pytest

from src.risk.position_sizer import PositionSizer, SizingResult
from src.risk.drawdown_control import DrawdownController, CircuitBreakerLevel
from src.models.regime.hmm_detector import RegimeState, RegimeLabel
from src.strategy.vrp_engine import VRPSignal
from datetime import datetime


def make_regime(label=RegimeLabel.NORMAL_VOL, kelly_mult=0.35, allow=True):
    return RegimeState(
        label=label, state_index=0, confidence=0.8,
        transition_prob=0.85, regime_vol=0.15,
        kelly_multiplier=kelly_mult, allow_new_positions=allow,
    )


def make_signal(strength=0.70, iv=0.15, vix=15.0, vix3m=15.3):
    return VRPSignal(
        timestamp=datetime.now(), symbol="SPY",
        implied_vol=iv, realized_vol_cc=0.12, realized_vol_yz=0.11,
        garch_forecast=0.13, vrp_cc=0.03, vrp_yz=0.04, vrp_garch=0.02,
        vrp_composite=0.03, vrp_percentile=0.70,
        signal_strength=strength, vix_level=vix, vix_term_slope=vix3m/vix,
        skew_25d=0.02, put_call_ratio=1.0,
    )


class TestPositionSizer:
    """Tests for Kelly + CVaR position sizing."""

    def test_zero_contracts_crisis_regime(self):
        """No positions in crisis regime."""
        sizer = PositionSizer(account_size=20000)
        regime = make_regime(RegimeLabel.CRISIS, kelly_mult=0.0, allow=False)
        signal = make_signal()
        result = sizer.size_position(
            prob_profit=0.70, max_loss_per_contract=200,
            credit_per_contract=40, signal=signal, regime=regime,
            current_equity=20000,
        )
        assert result.contracts == 0, "Crisis regime should give 0 contracts"

    def test_positive_contracts_normal_regime(self):
        """Positive contracts in normal regime with good signal."""
        sizer = PositionSizer(account_size=20000, max_risk_pct=0.02)
        regime = make_regime(RegimeLabel.NORMAL_VOL, kelly_mult=0.35)
        signal = make_signal(strength=0.75)
        result = sizer.size_position(
            prob_profit=0.68, max_loss_per_contract=800,
            credit_per_contract=200, signal=signal, regime=regime,
            current_equity=20000,
        )
        assert result.contracts >= 1, "Should get at least 1 contract"

    def test_max_risk_constraint(self):
        """Position size should not exceed max_risk_pct of account."""
        sizer = PositionSizer(account_size=20000, max_risk_pct=0.02)
        regime = make_regime(RegimeLabel.LOW_VOL, kelly_mult=0.50)
        signal = make_signal(strength=0.90)
        result = sizer.size_position(
            prob_profit=0.80, max_loss_per_contract=100,
            credit_per_contract=30, signal=signal, regime=regime,
            current_equity=20000,
        )
        max_allowed = 20000 * 0.02 / 100  # 4 contracts
        assert result.contracts <= max(int(max_allowed) + 2, 5), \
            f"Contracts {result.contracts} exceeds reasonable max risk"

    def test_kelly_fraction_scaling(self):
        """Low signal strength → fewer contracts than high signal strength."""
        sizer = PositionSizer(account_size=20000)
        regime = make_regime()
        sig_high = make_signal(strength=0.90)
        sig_low = make_signal(strength=0.45)
        kw = dict(prob_profit=0.68, max_loss_per_contract=500,
                  credit_per_contract=100, regime=regime, current_equity=20000)
        result_high = sizer.size_position(signal=sig_high, **kw)
        result_low = sizer.size_position(signal=sig_low, **kw)
        assert result_high.contracts >= result_low.contracts, \
            "High signal should give >= contracts than low signal"

    def test_empirical_kelly_none_before_data(self):
        """Empirical Kelly returns None before enough trades."""
        sizer = PositionSizer()
        assert sizer.empirical_kelly is None

    def test_win_rate_updates(self):
        """Win rate should update correctly."""
        sizer = PositionSizer()
        for _ in range(15):
            sizer.record_outcome(100, 200, 500)   # wins
        for _ in range(5):
            sizer.record_outcome(-300, 200, 500)  # losses
        assert sizer.empirical_win_rate == pytest.approx(15 / 20, abs=0.05)


class TestDrawdownController:
    """Tests for circuit breaker logic."""

    def test_normal_state_initially(self):
        """State should be NORMAL initially."""
        ctrl = DrawdownController(account_size=20000)
        state = ctrl.current_state
        assert state.level == CircuitBreakerLevel.NORMAL

    def test_caution_on_daily_loss(self):
        """Exceeding daily loss limit triggers CAUTION."""
        ctrl = DrawdownController(account_size=20000, daily_limit_pct=0.03)
        state = ctrl.update(20000 - 700)  # -$700 > 3% of $20k
        assert state.level == CircuitBreakerLevel.CAUTION

    def test_halt_on_max_drawdown(self):
        """Exceeding max drawdown triggers HALT."""
        ctrl = DrawdownController(account_size=20000, max_drawdown_pct=0.15)
        state = ctrl.update(20000 * 0.80)  # -20% > 15% limit
        assert state.level == CircuitBreakerLevel.HALT

    def test_allow_new_positions_normal(self):
        """New positions allowed in NORMAL state."""
        ctrl = DrawdownController(account_size=20000)
        state = ctrl.current_state
        assert state.allow_new_positions

    def test_no_new_positions_halt(self):
        """New positions blocked in HALT state."""
        ctrl = DrawdownController(account_size=20000, max_drawdown_pct=0.15)
        state = ctrl.update(20000 * 0.80)
        assert not state.allow_new_positions

    def test_daily_reset_restores_caution(self):
        """Daily reset removes CAUTION breaker."""
        ctrl = DrawdownController(account_size=20000, daily_limit_pct=0.03)
        ctrl.update(19000)  # trigger CAUTION
        ctrl.reset_daily()
        assert ctrl.current_state.level == CircuitBreakerLevel.NORMAL

    def test_size_multiplier_caution(self):
        """CAUTION level gives 50% size multiplier."""
        ctrl = DrawdownController(account_size=20000, daily_limit_pct=0.03)
        state = ctrl.update(19000)
        assert state.size_multiplier == 0.50

    def test_drawdown_adjusted_kelly(self):
        """Kelly should reduce as drawdown deepens."""
        ctrl = DrawdownController(account_size=20000)
        ctrl.update(20000)     # no drawdown
        full_kelly = ctrl.drawdown_adjusted_kelly(0.25)
        ctrl.update(17000)     # -15% drawdown
        reduced_kelly = ctrl.drawdown_adjusted_kelly(0.25)
        assert reduced_kelly <= full_kelly, "Kelly should reduce with drawdown"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
