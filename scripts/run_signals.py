#!/usr/bin/env python3
"""Run RG-VRP-L2 signal evaluation (dry-run, no orders)."""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rg_vrp_l2.backtest import run_simple_backtest
from rg_vrp_l2.regime_filter import RegimeFilter
from rg_vrp_l2.schwab_orchestrator import MarketSnapshot, SchwabOrchestrator
from rg_vrp_l2.strategy_engine import StrategyEngine
from rg_vrp_l2.vrp_estimator import realized_volatility

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def main() -> None:
    parser = argparse.ArgumentParser(description="RG-VRP-L2 signal runner")
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--backtest", action="store_true")
    parser.add_argument("--iv30", type=float, default=None, help="Override IV30")
    args = parser.parse_args()

    if args.backtest:
        result = run_simple_backtest(args.symbol)
        print(f"Backtest ({args.symbol}):")
        print(f"  Return: {result.total_return_pct:.2f}%")
        print(f"  Sharpe (approx): {result.sharpe_approx:.2f}")
        print(f"  Max DD: {result.max_drawdown_pct:.2f}%")
        print(f"  Trades: {result.trade_count}, Win rate: {result.win_rate:.1%}")
        return

    import yfinance as yf

    hist = yf.download(args.symbol, period="1y", progress=False, auto_adjust=True)
    close = hist["Close"]
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]

    filt, state = RegimeFilter.from_yfinance(args.symbol)
    rv = realized_volatility(close)
    iv30 = args.iv30 if args.iv30 else rv * 1.08

    engine = StrategyEngine()
    orch = SchwabOrchestrator(engine, dry_run=True)
    snap = MarketSnapshot(
        underlying=args.symbol,
        last_price=float(close.iloc[-1]),
        bid=float(close.iloc[-1]) * 0.9999,
        ask=float(close.iloc[-1]) * 1.0001,
        implied_vol_30d=iv30,
        implied_vol_7d=iv30 * 1.02,
        chain=[],
    )
    decision = orch.run_cycle(
        snap,
        credit_per_contract=120,
        max_loss_per_contract=380,
    )

    print(f"\n=== RG-VRP-L2 Signal: {args.symbol} ===")
    print(f"Regime: calm={state.calm_probability:.2f} turb={state.turbulent_probability:.2f}")
    print(f"RV20: {rv:.2f}% | IV30 (input): {iv30:.2f}% | VRP proxy: {iv30 - rv:.2f}")
    print(f"Decision: {decision.action} | {decision.structure.value}")
    print(f"Rationale: {decision.rationale}")
    if decision.action == "ENTER":
        print(
            f"  Structure: {decision.contracts}x @ delta {decision.short_delta:.2f}, "
            f"DTE~{decision.dte_target}, wing ${decision.wing_width}"
        )


if __name__ == "__main__":
    main()
