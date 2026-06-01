"""Command-line interface for QIST.

Subcommands:
    qist backtest [--config path] [--days N]   run a simulated backtest
    qist signal   [--config path]              print today's signal/plan (synthetic)
    qist init     [--out config.yaml]          write a default config file

Live/paper trading is intentionally gated behind explicit credentials and a
``--i-understand-the-risks`` flag; see STRATEGY.md and docs/risk_management.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from .config import Config
from .data.providers import SyntheticDataProvider
from .strategy.engine import StrategyEngine
from .strategy.risk import RiskState


def _cmd_init(args: argparse.Namespace) -> int:
    cfg = Config()
    cfg.save(args.out)
    print(f"Wrote default config to {args.out}")
    return 0


def _cmd_backtest(args: argparse.Namespace) -> int:
    from .backtest.backtester import Backtester
    cfg = Config.load(args.config) if args.config else Config()
    bt = Backtester(cfg, total_days=args.days)
    result = bt.run()
    stats = result.stats()
    print("=== Backtest results (simulated Heston-lite path) ===")
    for k, v in stats.items():
        print(f"  {k:>20}: {v:,.4f}")
    print("\nNOTE: simulated data is for pipeline validation, not a performance"
          " promise. Validate on real historical option data before risking capital.")
    return 0


def _cmd_signal(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config) if args.config else Config()
    provider = SyntheticDataProvider()
    provider.simulate(300)
    symbol = cfg.universe.primary
    hist = provider.price_history(symbol, 250)
    chain = provider.option_chain(symbol)
    engine = StrategyEngine(cfg)
    state = RiskState(equity=cfg.account.equity, high_water=cfg.account.equity)
    plan = engine.plan(hist, chain, state)
    sig = plan.signal
    out = {
        "date": str(date.today()),
        "decision": sig.decision.value,
        "iv": round(sig.iv, 4),
        "rv_forecast": round(sig.rv_forecast, 4),
        "vrp_var": round(sig.vrp_var, 5),
        "vrp_zscore": round(sig.vrp_zscore, 3),
        "regime_rank": sig.regime_rank,
        "term_structure_slope": round(sig.term_structure_slope, 4),
        "will_trade": plan.will_trade,
        "notes": plan.notes,
    }
    if plan.will_trade:
        out["structure"] = plan.core_structure.name
        out["quantity"] = plan.core_quantity
        out["expected_credit_usd"] = round(plan.expected_credit(), 2)
    print(json.dumps(out, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qist", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="write a default config file")
    p_init.add_argument("--out", default="config.yaml")
    p_init.set_defaults(func=_cmd_init)

    p_bt = sub.add_parser("backtest", help="run a simulated backtest")
    p_bt.add_argument("--config", default=None)
    p_bt.add_argument("--days", type=int, default=600)
    p_bt.set_defaults(func=_cmd_backtest)

    p_sig = sub.add_parser("signal", help="print today's signal/plan (synthetic)")
    p_sig.add_argument("--config", default=None)
    p_sig.set_defaults(func=_cmd_signal)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
