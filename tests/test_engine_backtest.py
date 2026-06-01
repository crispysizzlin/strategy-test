import numpy as np

from qist.backtest.backtester import Backtester
from qist.config import Config
from qist.data.providers import SyntheticDataProvider
from qist.strategy.engine import StrategyEngine
from qist.strategy.risk import RiskManager, RiskState


def test_synthetic_provider_chain_consistency():
    p = SyntheticDataProvider(seed=3)
    p.simulate(300)
    chain = p.option_chain("XSP")
    assert chain.spot > 0
    assert len(chain.rows) > 0
    exp = chain.nearest_dte(35)
    assert exp is not None
    iv = chain.atm_iv(exp)
    assert 0.01 < iv < 2.0
    put16 = chain.by_delta(exp, 0.16, is_call=False)
    assert put16 is not None
    assert abs(put16.delta) < 0.5


def test_risk_manager_drawdown_halt():
    cfg = Config()
    rm = RiskManager(cfg)
    state = RiskState(equity=17000, high_water=20000)  # 15% drawdown
    assert rm.trading_halted(state)
    state2 = RiskState(equity=19500, high_water=20000)  # 2.5%
    assert not rm.trading_halted(state2)


def test_risk_manager_pdt_guard():
    cfg = Config()
    rm = RiskManager(cfg)
    small = RiskState(equity=20000, high_water=20000, day_trades_used=3)
    assert not rm.can_day_trade(small)
    big = RiskState(equity=30000, high_water=30000, day_trades_used=5)
    assert rm.can_day_trade(big)


def test_engine_produces_plan():
    cfg = Config()
    engine = StrategyEngine(cfg)
    p = SyntheticDataProvider(seed=7)
    p.simulate(300)
    hist = p.price_history("XSP", 250)
    chain = p.option_chain("XSP")
    state = RiskState(equity=cfg.account.equity, high_water=cfg.account.equity)
    plan = engine.plan(hist, chain, state)
    assert plan.signal is not None
    assert isinstance(plan.notes, list)
    if plan.will_trade:
        assert plan.core_quantity > 0
        assert plan.core_structure.max_loss > 0
        assert (plan.core_structure.max_loss * plan.core_quantity
                <= cfg.risk.max_loss_per_trade_frac * state.equity + 1e-6)


def test_backtester_runs_and_reports_stats():
    cfg = Config()
    bt = Backtester(cfg, provider=SyntheticDataProvider(seed=42),
                    total_days=400, warmup_days=150)
    result = bt.run()
    stats = result.stats()
    assert "sharpe" in stats
    assert "max_drawdown" in stats
    assert np.isfinite(stats["final_equity"])
    assert len(result.equity_curve) > 0
    assert 0.0 <= stats["max_drawdown"] <= 1.0
