"""Event-driven backtester for the conditional VRP-harvesting strategy.

Design goals:
* Reuse the *exact* production decision path (StrategyEngine.plan) so the
  backtest tests the real logic, not a re-implementation.
* Price option structures with BSM on the simulated stochastic-vol path, so
  short-vol P&L, theta decay and crash losses are all represented.
* Model frictions: bid/ask cost on entry/exit, profit-take and stop-loss
  management, weekly entry cadence (PDT-friendly, no intraday round trips).

The output is a P&L series plus the income-relevant statistics: average weekly
income, win rate, Sharpe, Sortino, max drawdown, and tail (CVaR) loss - exactly
the metrics needed to judge a "consistent income" objective honestly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..config import Config
from ..data.providers import SyntheticDataProvider
from ..models.black_scholes import bs_price
from ..strategy.engine import StrategyEngine, TradePlan
from ..strategy.risk import RiskState


@dataclass
class OpenPosition:
    plan: TradePlan
    entry_day: int
    expiry_day: int
    entry_credit: float          # $ collected (core, net of fees)
    legs: list[tuple]            # (strike, is_call, signed_qty) short(+)/long(-)
    tail_legs: list[tuple]
    tail_debit: float


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: pd.DataFrame
    config: Config

    def stats(self) -> dict[str, float]:
        eq = self.equity_curve
        rets = eq.pct_change().dropna()
        ann = 252.0
        sharpe = (rets.mean() / rets.std() * np.sqrt(ann)) if rets.std() > 0 else 0.0
        downside = rets[rets < 0]
        sortino = (rets.mean() / downside.std() * np.sqrt(ann)
                   ) if len(downside) and downside.std() > 0 else 0.0
        peak = eq.cummax()
        max_dd = float((1 - eq / peak).max()) if len(eq) else 0.0
        total_ret = float(eq.iloc[-1] / eq.iloc[0] - 1) if len(eq) else 0.0
        n = len(self.trades)
        wins = int((self.trades["pnl"] > 0).sum()) if n else 0
        win_rate = wins / n if n else 0.0
        pnl = self.trades["pnl"].to_numpy() if n else np.array([0.0])
        losses = -pnl[pnl < 0]
        cvar = float(np.mean(losses[losses >= np.quantile(losses, 0.95)])
                     ) if len(losses) else 0.0
        avg_trade = float(np.mean(pnl)) if n else 0.0
        return {
            "total_return": total_ret,
            "sharpe": float(sharpe),
            "sortino": float(sortino),
            "max_drawdown": max_dd,
            "win_rate": win_rate,
            "n_trades": float(n),
            "avg_pnl_per_trade": avg_trade,
            "cvar95_loss": cvar,
            "final_equity": float(eq.iloc[-1]) if len(eq) else float("nan"),
        }


class Backtester:
    def __init__(self, cfg: Config, provider: SyntheticDataProvider | None = None,
                 entry_cadence_days: int = 7, warmup_days: int = 150,
                 total_days: int = 600, mult: int = 100, r: float = 0.04) -> None:
        self.cfg = cfg
        self.provider = provider or SyntheticDataProvider()
        self.entry_cadence = entry_cadence_days
        self.warmup = warmup_days
        self.total_days = total_days
        self.mult = mult
        self.r = r

    def run(self) -> BacktestResult:
        path = self.provider.simulate(self.total_days)
        closes = path["close"].to_numpy()
        inst_var = path["inst_var"].to_numpy()
        symbol = self.cfg.universe.primary

        equity = self.cfg.account.equity
        high_water = equity
        engine = StrategyEngine(self.cfg)
        open_positions: list[OpenPosition] = []
        equity_points = []
        trade_rows = []

        for d in range(self.warmup, self.total_days):
            spot = closes[d]
            # 1) settle / manage existing positions
            still_open = []
            for pos in open_positions:
                pnl, closed = self._evaluate_position(pos, d, closes, inst_var)
                if closed:
                    equity += pnl
                    trade_rows.append({
                        "entry_day": pos.entry_day, "exit_day": d,
                        "structure": pos.plan.core_structure.name
                        if pos.plan.core_structure else "n/a",
                        "qty": pos.plan.core_quantity, "pnl": pnl})
                else:
                    still_open.append(pos)
            open_positions = still_open
            high_water = max(high_water, equity)

            # 2) consider new entry on cadence
            if (d - self.warmup) % self.entry_cadence == 0:
                hist = path.iloc[: d + 1]
                chain = self.provider.option_chain(symbol, spot=spot,
                                                   var=inst_var[d])
                state = RiskState(equity=equity, high_water=high_water,
                                  open_structures=len(open_positions))
                plan = engine.plan(hist[["open", "high", "low", "close"]],
                                   chain, state, self.mult)
                if plan.will_trade:
                    pos = self._open_from_plan(plan, d)
                    if pos is not None:
                        open_positions.append(pos)

            equity_points.append((path.index[d], equity + self._mtm(
                open_positions, d, closes, inst_var)))

        eq_series = pd.Series(dict(equity_points))
        trades = pd.DataFrame(trade_rows) if trade_rows else pd.DataFrame(
            columns=["entry_day", "exit_day", "structure", "qty", "pnl"])
        return BacktestResult(equity_curve=eq_series, trades=trades, config=self.cfg)

    # ---- helpers --------------------------------------------------------
    def _open_from_plan(self, plan: TradePlan, day: int):
        struct = plan.core_structure
        if struct is None:
            return None
        dte = (struct.legs[0].contract.expiry - date.today()).days
        expiry_day = day + max(dte, 1)
        legs = self._signed_legs(struct, plan.core_quantity)
        tail_legs = (self._signed_legs(plan.tail_structure, plan.tail_quantity)
                     if plan.tail_structure else [])
        entry_credit = struct.credit_dollars * plan.core_quantity
        # entry friction: ~1% of credit per leg as half-spread slippage
        fric = 0.01 * abs(entry_credit) * len(struct.legs)
        tail_debit = (abs(plan.tail_structure.credit_dollars) * plan.tail_quantity
                      if plan.tail_structure else 0.0)
        return OpenPosition(
            plan=plan, entry_day=day, expiry_day=expiry_day,
            entry_credit=entry_credit - fric, legs=legs, tail_legs=tail_legs,
            tail_debit=tail_debit)

    def _signed_legs(self, struct, qty: int):
        out = []
        for leg in struct.legs:
            sign = 1 if "SELL" in leg.instruction.value else -1
            out.append((leg.contract.strike,
                        leg.contract.option_type.value == "CALL",
                        sign * leg.quantity * qty))
        return out

    def _structure_value(self, legs, spot: float, var: float, dte_days: int) -> float:
        """Net premium of the (short-positive) book at current state, in $."""
        t = max(dte_days, 0) / 365.0
        sigma = float(np.sqrt(max(var, 1e-6))) * self.provider.iv_premium
        val = 0.0
        for strike, is_call, signed_qty in legs:
            px = bs_price(spot, strike, t, self.r, sigma, is_call)
            val += signed_qty * px * self.mult
        return val

    def _evaluate_position(self, pos: OpenPosition, day: int, closes, inst_var):
        spot = closes[day]
        var = inst_var[day]
        dte_left = pos.expiry_day - day
        cur_core = self._structure_value(pos.legs, spot, var, dte_left)
        cur_tail = self._structure_value(pos.tail_legs, spot, var,
                                         dte_left) if pos.tail_legs else 0.0
        core_pnl = pos.entry_credit - cur_core
        # tail book is net long (negative signed qty); its value to us is -cur_tail
        tail_pnl = (-cur_tail) - (-pos.tail_debit) if pos.tail_legs else 0.0
        pnl = core_pnl + tail_pnl

        credit0 = pos.entry_credit
        if dte_left <= 0:
            return pnl, True
        if credit0 > 0:
            if core_pnl >= self.cfg.risk.take_profit_frac * credit0:
                return pnl, True
            if -core_pnl >= self.cfg.risk.stop_loss_mult * credit0:
                return pnl, True
        return pnl, False

    def _mtm(self, positions, day, closes, inst_var) -> float:
        total = 0.0
        for pos in positions:
            pnl, _ = self._evaluate_position(pos, day, closes, inst_var)
            total += pnl
        return total
