from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path

from .backtest import run_backtest
from .config import load_config
from .data import load_bars, write_rows
from .l2 import aggregate_l2_csv
from .prop import simulate_eod_trailing_account
from .validation import moving_block_bootstrap, probabilistic_sharpe_ratio


def _backtest(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    bars = load_bars(args.data, args.assumed_timezone)
    result = run_backtest(bars, config)
    stressed_config = replace(
        config,
        instrument=replace(
            config.instrument,
            commission_per_side=2.0 * config.instrument.commission_per_side,
            slippage_ticks_per_side=2.0 * config.instrument.slippage_ticks_per_side,
        ),
    )
    stressed_result = run_backtest(bars, stressed_config)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    trade_rows = result.trade_rows()
    if trade_rows:
        write_rows(output / "trades.csv", trade_rows[0].keys(), trade_rows)
    else:
        write_rows(
            output / "trades.csv",
            [
                "session",
                "direction",
                "signal_time",
                "entry_time",
                "exit_time",
                "decision_price",
                "entry_price",
                "exit_price",
                "quantity",
                "exit_reason",
                "gross_pnl",
                "commission",
                "net_pnl",
            ],
            [],
        )

    daily_values = [item.net_pnl for item in result.daily]
    psr = probabilistic_sharpe_ratio(daily_values)
    bootstrap = moving_block_bootstrap(
        daily_values,
        simulations=args.bootstrap_simulations,
        block_length=args.bootstrap_block,
        seed=args.seed,
    )
    prop = simulate_eod_trailing_account(result.daily, result.trades, config.prop)
    report = {
        "summary": result.summary,
        "double_cost_stress_summary": stressed_result.summary,
        "probabilistic_sharpe_ratio_vs_zero": psr,
        "moving_block_bootstrap": asdict(bootstrap) if bootstrap else None,
        "prop_evaluation_simulation": asdict(prop),
        "assumptions": {
            "same_bar_stop_target_policy": "stop_first",
            "entry_timing": "next_bar_open",
            "commission_per_side": config.instrument.commission_per_side,
            "slippage_ticks_per_side": config.instrument.slippage_ticks_per_side,
            "l2_required": config.signal.require_l2,
            "vwap_source": (
                "sum_trade_price_times_size"
                if bars and all(bar.trade_value is not None for bar in bars)
                else "bar_typical_price_approximation"
            ),
        },
    }
    with (output / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Adaptive ORB research tools")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backtest = subparsers.add_parser("backtest", help="Run a cost-aware historical simulation")
    backtest.add_argument("--data", required=True, help="Minute-bar CSV, optionally enriched with L2 features")
    backtest.add_argument("--config", required=True, help="Research JSON configuration")
    backtest.add_argument("--output", default="artifacts/latest", help="Output directory")
    backtest.add_argument("--assumed-timezone", default="UTC", help="Timezone for naive input timestamps")
    backtest.add_argument("--bootstrap-simulations", type=int, default=2_000)
    backtest.add_argument("--bootstrap-block", type=int, default=5)
    backtest.add_argument("--seed", type=int, default=7)
    backtest.set_defaults(func=_backtest)
    aggregate = subparsers.add_parser("aggregate-l2", help="Aggregate Quantower L2 recorder rows by minute")
    aggregate.add_argument("--data", required=True, help="Raw Quantower L2 CSV")
    aggregate.add_argument("--output", required=True, help="Output feature CSV")
    aggregate.add_argument("--tail-samples", type=int, default=1)
    aggregate.set_defaults(
        func=lambda args: (
            print(aggregate_l2_csv(args.data, args.output, args.tail_samples)) or 0
        )
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
