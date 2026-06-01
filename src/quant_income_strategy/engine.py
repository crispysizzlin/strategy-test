"""Strategy engine for regime-adaptive defined-risk VRP harvesting."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from math import sqrt

from .math_utils import (
    black_scholes_delta,
    clamp,
    cvar_from_pnl,
    terminal_price_from_sigma,
)
from .models import (
    CandidateTrade,
    OptionQuote,
    SpreadLeg,
    StrategyConfig,
    UnderlyingSnapshot,
)


class StrategyEngine:
    """Select conservative credit spreads/iron condors from option-chain data.

    The engine looks for variance-risk-premium carry only when implied volatility
    is sufficiently rich versus realized volatility, then caps losses through
    vertical spreads. Level 2 fields are used to reject poor liquidity and improve
    entry-limit placement; order routing remains outside this pure signal layer.
    """

    def __init__(self, config: StrategyConfig | None = None) -> None:
        self.config = config or StrategyConfig()

    def find_trades(
        self,
        snapshots: list[UnderlyingSnapshot],
        existing_defined_risk: float = 0.0,
    ) -> list[CandidateTrade]:
        candidates: list[CandidateTrade] = []
        remaining_risk = max(
            0.0,
            self.config.account_equity * self.config.max_portfolio_risk_pct
            - existing_defined_risk,
        )
        if remaining_risk <= 0:
            return []

        for snapshot in snapshots:
            if not self._passes_regime_filter(snapshot):
                continue
            candidates.extend(self._vertical_candidates(snapshot, "PUT"))
            candidates.extend(self._vertical_candidates(snapshot, "CALL"))
            candidates.extend(self._iron_condor_candidates(snapshot))

        feasible = []
        for candidate in sorted(candidates, key=lambda item: item.score, reverse=True):
            if candidate.contracts < 1:
                continue
            clipped = self._clip_to_remaining_risk(candidate, remaining_risk)
            if clipped.contracts < 1:
                continue
            feasible.append(clipped)
            remaining_risk -= clipped.risk_dollars
            if remaining_risk <= 0:
                break
        return feasible

    def _passes_regime_filter(self, snapshot: UnderlyingSnapshot) -> bool:
        if snapshot.price <= 0 or snapshot.event_risk:
            return False
        if snapshot.vix is not None and snapshot.vix > self.config.max_vix:
            return False
        if (
            snapshot.vix is not None
            and snapshot.vix3m is not None
            and snapshot.vix3m > 0
            and snapshot.vix / snapshot.vix3m > self.config.max_vix_front_to_3m_ratio
        ):
            return False
        return True

    def _vertical_candidates(
        self,
        snapshot: UnderlyingSnapshot,
        option_type: str,
    ) -> list[CandidateTrade]:
        by_expiry: dict[tuple[str, int], list[OptionQuote]] = defaultdict(list)
        for quote in snapshot.options:
            if quote.option_type != option_type:
                continue
            if self.config.min_dte <= quote.dte <= self.config.max_dte:
                by_expiry[(quote.expiration, quote.dte)].append(self._with_delta(snapshot, quote))

        candidates: list[CandidateTrade] = []
        for (_, dte), quotes in by_expiry.items():
            liquid = [quote for quote in quotes if self._is_liquid(quote)]
            short_quotes = [quote for quote in liquid if self._is_short_delta_target(quote)]
            for short in short_quotes:
                long = self._choose_long_leg(short, liquid, snapshot.price)
                if long is None:
                    continue
                candidate = self._build_vertical(snapshot, short, long)
                if candidate is not None:
                    candidates.append(candidate)
        return candidates

    def _iron_condor_candidates(self, snapshot: UnderlyingSnapshot) -> list[CandidateTrade]:
        puts = self._vertical_candidates(snapshot, "PUT")
        calls = self._vertical_candidates(snapshot, "CALL")
        candidates: list[CandidateTrade] = []
        for put in puts:
            for call in calls:
                if put.dte != call.dte:
                    continue
                short_put = put.legs[0].quote
                short_call = call.legs[0].quote
                if short_put.strike >= short_call.strike:
                    continue
                candidate = self._combine_condor(snapshot, put, call)
                if candidate is not None:
                    candidates.append(candidate)
        return candidates

    def _with_delta(self, snapshot: UnderlyingSnapshot, quote: OptionQuote) -> OptionQuote:
        if quote.delta is not None:
            return quote
        delta = black_scholes_delta(
            snapshot.price,
            quote.strike,
            max(quote.dte / 365.0, 1.0 / 365.0),
            quote.implied_volatility,
            quote.option_type,
        )
        return replace(quote, delta=delta)

    def _is_liquid(self, quote: OptionQuote) -> bool:
        return (
            quote.bid > 0
            and quote.ask > quote.bid
            and quote.open_interest >= self.config.min_open_interest
            and quote.spread_pct_mid <= self.config.max_bid_ask_pct
        )

    def _is_short_delta_target(self, quote: OptionQuote) -> bool:
        if quote.delta is None:
            return False
        delta = abs(quote.delta)
        return self.config.min_short_delta <= delta <= self.config.max_short_delta

    def _choose_long_leg(
        self,
        short: OptionQuote,
        quotes: list[OptionQuote],
        spot: float,
    ) -> OptionQuote | None:
        if short.delta is None:
            return None
        if short.option_type == "PUT":
            eligible = [
                quote
                for quote in quotes
                if quote.strike < short.strike
                and quote.delta is not None
                and abs(short.delta) - abs(quote.delta) >= self.config.min_long_delta_gap
            ]
            eligible.sort(key=lambda quote: (abs(short.strike - quote.strike), quote.spread_pct_mid))
        else:
            eligible = [
                quote
                for quote in quotes
                if quote.strike > short.strike
                and quote.delta is not None
                and abs(short.delta) - abs(quote.delta) >= self.config.min_long_delta_gap
            ]
            eligible.sort(key=lambda quote: (abs(short.strike - quote.strike), quote.spread_pct_mid))

        max_width = max(1.0, spot * 0.035)
        for quote in eligible:
            if abs(short.strike - quote.strike) <= max_width:
                return quote
        return eligible[0] if eligible else None

    def _build_vertical(
        self,
        snapshot: UnderlyingSnapshot,
        short: OptionQuote,
        long: OptionQuote,
    ) -> CandidateTrade | None:
        width = abs(short.strike - long.strike)
        if width <= 0:
            return None
        net_credit = short.mid - long.mid
        if net_credit <= 0:
            return None
        max_loss = (width - net_credit) * 100.0
        if max_loss <= 0:
            return None
        credit_to_risk = (net_credit * 100.0) / max_loss
        if credit_to_risk < self.config.min_credit_to_risk:
            return None

        iv_rv_ratio = self._iv_rv_ratio(snapshot, (short, long))
        if iv_rv_ratio < self.config.min_iv_rv_ratio:
            return None

        probability = self._probability_of_profit(short, iv_rv_ratio, snapshot)
        expected_value = probability * net_credit * 100.0 - (1.0 - probability) * max_loss
        if expected_value <= 0:
            return None

        legs = (
            SpreadLeg("SELL_TO_OPEN", short),
            SpreadLeg("BUY_TO_OPEN", long),
        )
        pnl_scenarios = self._scenario_pnls(snapshot, legs, net_credit)
        cvar95 = cvar_from_pnl(pnl_scenarios, self.config.cvar_alpha)
        kelly = self._fractional_kelly(probability, net_credit * 100.0, max_loss)
        contracts = self._contracts_for_risk(kelly, max_loss)
        if contracts < 1:
            return None

        score = self._score(expected_value, max_loss, cvar95, iv_rv_ratio, (short, long))
        limit = self._limit_credit_for_entry(legs, net_credit)
        strategy = "PUT_CREDIT_SPREAD" if short.option_type == "PUT" else "CALL_CREDIT_SPREAD"
        rationale = (
            f"IV/RV ratio {iv_rv_ratio:.2f} clears {self.config.min_iv_rv_ratio:.2f} threshold.",
            f"Short {short.option_type.lower()} delta {short.delta:.2f} targets low-gamma income zone.",
            f"Defined risk ${max_loss:.0f} per contract; CVaR95 ${cvar95:.0f}.",
        )
        return CandidateTrade(
            strategy_type=strategy,
            underlying=snapshot.symbol,
            dte=short.dte,
            legs=legs,
            net_credit=round(net_credit, 2),
            max_loss=round(max_loss, 2),
            probability_of_profit=round(probability, 4),
            expected_value=round(expected_value, 2),
            credit_to_risk=round(credit_to_risk, 4),
            cvar95=round(cvar95, 2),
            kelly_fraction=round(kelly, 4),
            contracts=contracts,
            risk_dollars=round(contracts * max_loss, 2),
            score=round(score, 4),
            suggested_limit_credit=round(limit, 2),
            rationale=rationale,
        )

    def _combine_condor(
        self,
        snapshot: UnderlyingSnapshot,
        put: CandidateTrade,
        call: CandidateTrade,
    ) -> CandidateTrade | None:
        legs = put.legs + call.legs
        net_credit = put.net_credit + call.net_credit
        max_loss = max(put.max_loss, call.max_loss) - min(put.net_credit, call.net_credit) * 100.0
        # Use the wider side as the binding tail loss; condor cannot lose both sides at expiry.
        max_loss = max(put.max_loss, call.max_loss)
        if max_loss <= 0:
            return None

        short_put = put.legs[0].quote
        short_call = call.legs[0].quote
        iv_rv_ratio = self._iv_rv_ratio(snapshot, tuple(leg.quote for leg in legs))
        probability = clamp(
            1.0 - abs(short_put.delta or 0.0) - abs(short_call.delta or 0.0)
            + self._vrp_probability_adjustment(iv_rv_ratio)
            - (0.04 if snapshot.event_risk else 0.0),
            0.45,
            0.92,
        )
        expected_value = probability * net_credit * 100.0 - (1.0 - probability) * max_loss
        credit_to_risk = (net_credit * 100.0) / max_loss
        if expected_value <= 0 or credit_to_risk < self.config.min_credit_to_risk:
            return None

        pnl_scenarios = self._scenario_pnls(snapshot, legs, net_credit)
        cvar95 = cvar_from_pnl(pnl_scenarios, self.config.cvar_alpha)
        kelly = self._fractional_kelly(probability, net_credit * 100.0, max_loss)
        contracts = self._contracts_for_risk(kelly, max_loss)
        if contracts < 1:
            return None

        score = self._score(
            expected_value,
            max_loss,
            cvar95,
            iv_rv_ratio,
            tuple(leg.quote for leg in legs),
        )
        rationale = (
            f"Two-sided IV/RV ratio {iv_rv_ratio:.2f} supports selling expensive variance.",
            "Iron condor uses level 3 multi-leg capability while avoiding naked short options.",
            f"Expected value ${expected_value:.0f} per contract with max defined loss ${max_loss:.0f}.",
        )
        return CandidateTrade(
            strategy_type="IRON_CONDOR",
            underlying=snapshot.symbol,
            dte=put.dte,
            legs=legs,
            net_credit=round(net_credit, 2),
            max_loss=round(max_loss, 2),
            probability_of_profit=round(probability, 4),
            expected_value=round(expected_value, 2),
            credit_to_risk=round(credit_to_risk, 4),
            cvar95=round(cvar95, 2),
            kelly_fraction=round(kelly, 4),
            contracts=contracts,
            risk_dollars=round(contracts * max_loss, 2),
            score=round(score, 4),
            suggested_limit_credit=round(self._limit_credit_for_entry(legs, net_credit), 2),
            rationale=rationale,
        )

    def _iv_rv_ratio(
        self,
        snapshot: UnderlyingSnapshot,
        quotes: tuple[OptionQuote, ...],
    ) -> float:
        avg_iv = sum(quote.implied_volatility for quote in quotes) / len(quotes)
        rv = max(0.01, snapshot.forecast_realized_vol)
        return avg_iv / rv

    def _probability_of_profit(
        self,
        short: OptionQuote,
        iv_rv_ratio: float,
        snapshot: UnderlyingSnapshot,
    ) -> float:
        base = 1.0 - abs(short.delta or 0.0)
        return clamp(
            base
            + self._vrp_probability_adjustment(iv_rv_ratio)
            - (0.04 if snapshot.event_risk else 0.0),
            0.50,
            0.94,
        )

    def _vrp_probability_adjustment(self, iv_rv_ratio: float) -> float:
        return clamp((iv_rv_ratio - 1.0) * 0.12, 0.0, 0.08)

    def _fractional_kelly(
        self,
        probability: float,
        win_amount: float,
        loss_amount: float,
    ) -> float:
        if win_amount <= 0 or loss_amount <= 0:
            return 0.0
        odds = win_amount / loss_amount
        full_kelly = probability - (1.0 - probability) / odds
        return clamp(full_kelly * self.config.fractional_kelly, 0.0, 0.10)

    def _contracts_for_risk(self, kelly_fraction: float, max_loss: float) -> int:
        hard_risk_budget = self.config.account_equity * self.config.max_risk_per_trade_pct
        kelly_budget = self.config.account_equity * kelly_fraction
        budget = min(hard_risk_budget, kelly_budget)
        return int(budget // max_loss)

    def _clip_to_remaining_risk(
        self,
        candidate: CandidateTrade,
        remaining_risk: float,
    ) -> CandidateTrade:
        allowed_contracts = int(remaining_risk // candidate.max_loss)
        contracts = min(candidate.contracts, allowed_contracts)
        return replace(
            candidate,
            contracts=contracts,
            risk_dollars=round(contracts * candidate.max_loss, 2),
        )

    def _scenario_pnls(
        self,
        snapshot: UnderlyingSnapshot,
        legs: tuple[SpreadLeg, ...],
        net_credit: float,
    ) -> list[float]:
        sigma_grid = [-3.5, -3.0, -2.5, -2.0, -1.5, -1.0, -0.5, 0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
        dte = legs[0].quote.dte
        vol = max(snapshot.forecast_realized_vol, 0.01)
        pnls = []
        for sigma_move in sigma_grid:
            terminal = terminal_price_from_sigma(snapshot.price, vol, dte, sigma_move)
            pnl = net_credit * 100.0
            for leg in legs:
                intrinsic = self._option_intrinsic(leg.quote, terminal) * 100.0
                if leg.instruction == "SELL_TO_OPEN":
                    pnl -= intrinsic
                else:
                    pnl += intrinsic
            pnls.append(pnl)
        return pnls

    def _option_intrinsic(self, quote: OptionQuote, terminal_price: float) -> float:
        if quote.option_type == "CALL":
            return max(terminal_price - quote.strike, 0.0)
        return max(quote.strike - terminal_price, 0.0)

    def _score(
        self,
        expected_value: float,
        max_loss: float,
        cvar95: float,
        iv_rv_ratio: float,
        quotes: tuple[OptionQuote, ...],
    ) -> float:
        liquidity = sum(self._liquidity_score(quote) for quote in quotes) / len(quotes)
        return (
            expected_value / max_loss
            + 0.20 * clamp(iv_rv_ratio - 1.0, 0.0, 1.0)
            + 0.10 * liquidity
            - 0.10 * cvar95 / max_loss
        )

    def _liquidity_score(self, quote: OptionQuote) -> float:
        spread_component = 1.0 - clamp(quote.spread_pct_mid / self.config.max_bid_ask_pct, 0.0, 1.0)
        oi_component = clamp(quote.open_interest / 2_000.0, 0.0, 1.0)
        volume_component = clamp(quote.volume / 500.0, 0.0, 1.0)
        return 0.55 * spread_component + 0.30 * oi_component + 0.15 * volume_component

    def _limit_credit_for_entry(
        self,
        legs: tuple[SpreadLeg, ...],
        net_credit: float,
    ) -> float:
        pessimistic_credit = 0.0
        total_spread = 0.0
        imbalance_adjustment = 0.0
        for leg in legs:
            quote = leg.quote
            total_spread += quote.spread
            if leg.instruction == "SELL_TO_OPEN":
                pessimistic_credit += quote.bid
                imbalance_adjustment += quote.book_imbalance * 0.01
            else:
                pessimistic_credit -= quote.ask
                imbalance_adjustment -= quote.book_imbalance * 0.01

        improvement = 0.25 * total_spread + imbalance_adjustment
        return max(0.01, max(pessimistic_credit, net_credit - improvement))
