"""Command-line entry point for scoring normalized option-chain snapshots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .engine import StrategyEngine
from .models import OptionQuote, StrategyConfig, UnderlyingSnapshot
from .schwab_orders import build_opening_order


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score defined-risk options income candidates from snapshot JSON."
    )
    parser.add_argument("snapshot_file", type=Path, help="JSON file containing snapshots")
    parser.add_argument("--equity", type=float, default=20_000.0, help="Account equity")
    parser.add_argument("--existing-risk", type=float, default=0.0, help="Existing defined risk")
    args = parser.parse_args()

    payload = json.loads(args.snapshot_file.read_text(encoding="utf-8"))
    snapshots = [_snapshot_from_dict(item) for item in payload["snapshots"]]
    engine = StrategyEngine(StrategyConfig(account_equity=args.equity))
    candidates = engine.find_trades(snapshots, existing_defined_risk=args.existing_risk)

    print(json.dumps([_candidate_to_dict(candidate) for candidate in candidates], indent=2))


def _snapshot_from_dict(item: dict) -> UnderlyingSnapshot:
    return UnderlyingSnapshot(
        symbol=item["symbol"],
        price=float(item["price"]),
        realized_vol_20d=float(item["realized_vol_20d"]),
        realized_vol_5d=(
            None if item.get("realized_vol_5d") is None else float(item["realized_vol_5d"])
        ),
        iv_rank=None if item.get("iv_rank") is None else float(item["iv_rank"]),
        vix=None if item.get("vix") is None else float(item["vix"]),
        vix3m=None if item.get("vix3m") is None else float(item["vix3m"]),
        event_risk=bool(item.get("event_risk", False)),
        options=tuple(_option_from_dict(option) for option in item.get("options", [])),
    )


def _option_from_dict(item: dict) -> OptionQuote:
    return OptionQuote(
        underlying=item["underlying"],
        option_symbol=item["option_symbol"],
        expiration=item["expiration"],
        dte=int(item["dte"]),
        option_type=item["option_type"],
        strike=float(item["strike"]),
        bid=float(item["bid"]),
        ask=float(item["ask"]),
        delta=None if item.get("delta") is None else float(item["delta"]),
        implied_volatility=float(item["implied_volatility"]),
        gamma=None if item.get("gamma") is None else float(item["gamma"]),
        theta=None if item.get("theta") is None else float(item["theta"]),
        vega=None if item.get("vega") is None else float(item["vega"]),
        bid_size=int(item.get("bid_size", 0)),
        ask_size=int(item.get("ask_size", 0)),
        open_interest=int(item.get("open_interest", 0)),
        volume=int(item.get("volume", 0)),
    )


def _candidate_to_dict(candidate) -> dict:
    return {
        "strategy_type": candidate.strategy_type,
        "underlying": candidate.underlying,
        "dte": candidate.dte,
        "contracts": candidate.contracts,
        "net_credit": candidate.net_credit,
        "suggested_limit_credit": candidate.suggested_limit_credit,
        "max_loss": candidate.max_loss,
        "risk_dollars": candidate.risk_dollars,
        "probability_of_profit": candidate.probability_of_profit,
        "expected_value": candidate.expected_value,
        "cvar95": candidate.cvar95,
        "score": candidate.score,
        "legs": [
            {
                "instruction": leg.instruction,
                "symbol": leg.quote.option_symbol,
                "strike": leg.quote.strike,
                "type": leg.quote.option_type,
            }
            for leg in candidate.legs
        ],
        "rationale": list(candidate.rationale),
        "schwab_order": build_opening_order(candidate),
    }


if __name__ == "__main__":
    main()
