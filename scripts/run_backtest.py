"""
AVRPE Backtesting Script
========================
Run the full strategy backtest on historical data.

Usage:
  python scripts/run_backtest.py [--symbol SPY] [--years 3] [--plot]

Downloads historical OHLCV and VIX data, then runs the complete
AVRPE strategy simulation including:
  - GARCH + HMM regime detection
  - VRP signal computation
  - Iron Condor / Butterfly structure selection
  - Fractional Kelly position sizing
  - Realistic fill model and transaction costs

Output:
  - Trade-by-trade P&L log
  - Equity curve chart
  - Summary statistics (Sharpe, Calmar, Max DD, etc.)
  - Regime analysis breakdown
"""

from __future__ import annotations

import sys
from datetime import datetime, date
from pathlib import Path

import click
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtesting.backtest_engine import BacktestEngine
from src.data.market_data import MarketDataFeed
from src.utils.logger import logger, setup_logger


@click.command()
@click.option("--symbol", default="SPY", help="Underlying symbol (SPY or SPX)")
@click.option("--years", default=3, type=int, help="Years of backtest history")
@click.option("--account", default=20000.0, type=float, help="Starting account size")
@click.option("--plot", is_flag=True, help="Generate equity curve plot")
@click.option("--output", default="backtesting/results", help="Output directory")
def main(symbol: str, years: int, account: float, plot: bool, output: str) -> None:
    """Run the AVRPE backtest."""
    setup_logger(level="INFO")

    logger.info(f"Starting backtest: {symbol} {years}Y account=${account:,.0f}")

    # Fetch data
    data_feed = MarketDataFeed()
    price_df = data_feed.get_price_history(symbol, period_years=years)

    if price_df is None or price_df.empty:
        logger.error(f"Could not fetch price data for {symbol}")
        return

    vix, vix3m = data_feed.get_vix_data(period_years=years)

    logger.info(
        f"Data loaded: {symbol} {len(price_df)} bars "
        f"({price_df.index[0].date()} → {price_df.index[-1].date()})"
    )

    # Run backtest
    engine = BacktestEngine(
        account_size=account,
        r=data_feed.get_risk_free_rate(),
        q=data_feed.get_dividend_yield(symbol),
    )

    result = engine.run(
        price_df=price_df,
        vix_series=vix if not vix.empty else None,
        vix3m_series=vix3m if not vix3m.empty else None,
        warm_up_days=252,
        refit_interval=21,
    )

    # Print summary
    summary = result.summary()
    print("\n" + "=" * 60)
    print("AVRPE BACKTEST RESULTS")
    print("=" * 60)
    print(f"Symbol:          {symbol}")
    print(f"Period:          {years} years")
    print(f"Account:         ${account:,.0f}")
    print("-" * 60)
    print(f"Total Trades:    {summary['n_trades']}")
    print(f"Win Rate:        {summary['win_rate']:.1%}")
    print(f"Avg Win:         ${summary['avg_win']:.2f}")
    print(f"Avg Loss:        ${summary['avg_loss']:.2f}")
    print(f"Profit Factor:   {summary['profit_factor']:.2f}")
    print("-" * 60)
    print(f"Total P&L:       ${summary['total_pnl']:,.2f}")
    print(f"Ann. Return:     {summary['annualised_return']:.1%}")
    print(f"Sharpe Ratio:    {summary['sharpe_ratio']:.2f}")
    print(f"Max Drawdown:    {summary['max_drawdown']:.1%}")
    print(f"Calmar Ratio:    {summary['calmar_ratio']:.2f}")
    print(f"Daily Income:    ${summary['expected_daily_income']:.2f}")
    print("=" * 60)

    # Regime breakdown
    if result.trades:
        trade_df = pd.DataFrame([
            {
                "regime": t.regime_at_entry,
                "pnl": t.pnl_net,
                "win": t.win,
                "exit": t.exit_reason,
            }
            for t in result.trades
        ])

        print("\nPerformance by Regime:")
        regime_stats = trade_df.groupby("regime").agg(
            n_trades=("pnl", "count"),
            win_rate=("win", "mean"),
            avg_pnl=("pnl", "mean"),
            total_pnl=("pnl", "sum"),
        )
        print(regime_stats.to_string())

        print("\nExit Reason Distribution:")
        print(trade_df["exit"].value_counts().to_string())

    # Save results
    output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)

    if result.trades:
        trade_records = []
        for t in result.trades:
            trade_records.append({
                "date_entry": t.date_entry,
                "date_exit": t.date_exit,
                "structure": t.structure_type,
                "dte": t.dte_at_entry,
                "spot_entry": t.spot_at_entry,
                "spot_exit": t.spot_at_exit,
                "iv_entry": t.iv_at_entry,
                "regime": t.regime_at_entry,
                "contracts": t.contracts,
                "credit": t.credit_collected,
                "exit_debit": t.exit_debit,
                "pnl_gross": t.pnl_gross,
                "pnl_net": t.pnl_net,
                "tc": t.transaction_cost,
                "win": t.win,
                "exit_reason": t.exit_reason,
            })
        pd.DataFrame(trade_records).to_csv(output_dir / "trades.csv", index=False)

    if not result.equity_curve.empty:
        result.equity_curve.to_csv(output_dir / "equity_curve.csv")

    logger.info(f"Results saved to {output_dir}")

    # Plot
    if plot:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates

            fig, axes = plt.subplots(3, 1, figsize=(14, 12))
            fig.suptitle(f"AVRPE Backtest — {symbol} ({years}Y)", fontsize=14, fontweight="bold")

            # Equity curve
            ax1 = axes[0]
            ec = result.equity_curve
            ax1.plot(ec.index, ec.values, color="#2196F3", linewidth=2, label="Portfolio")
            ax1.axhline(account, color="gray", linestyle="--", alpha=0.5, label="Starting capital")
            ax1.fill_between(ec.index, ec.values, account,
                             where=ec.values >= account, alpha=0.2, color="#4CAF50")
            ax1.fill_between(ec.index, ec.values, account,
                             where=ec.values < account, alpha=0.2, color="#F44336")
            ax1.set_ylabel("Portfolio Value ($)")
            ax1.set_title("Equity Curve")
            ax1.legend()
            ax1.grid(True, alpha=0.3)

            # Daily P&L
            ax2 = axes[1]
            dpnl = result.daily_pnl
            colors = ["#4CAF50" if v >= 0 else "#F44336" for v in dpnl.values]
            ax2.bar(dpnl.index, dpnl.values, color=colors, alpha=0.7)
            ax2.set_ylabel("Daily P&L ($)")
            ax2.set_title("Daily P&L")
            ax2.grid(True, alpha=0.3)

            # Drawdown
            ax3 = axes[2]
            peak = ec.cummax()
            dd = (ec - peak) / peak * 100
            ax3.fill_between(dd.index, dd.values, 0, alpha=0.5, color="#F44336", label="Drawdown")
            ax3.set_ylabel("Drawdown (%)")
            ax3.set_title("Drawdown")
            ax3.legend()
            ax3.grid(True, alpha=0.3)

            for ax in axes:
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
                plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)

            plt.tight_layout()
            plot_path = output_dir / "backtest_results.png"
            plt.savefig(plot_path, dpi=150, bbox_inches="tight")
            logger.info(f"Plot saved to {plot_path}")
            plt.close()

        except Exception as e:
            logger.warning(f"Plotting failed: {e}")


if __name__ == "__main__":
    main()
