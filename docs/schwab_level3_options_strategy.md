# Schwab Level 3 Options Strategy Research Memo

This memo is a research-backed design for an algorithmic options strategy that can be
orchestrated through Charles Schwab. It is not financial advice, does not guarantee
income, and should not be traded live until the backtests, paper-trading checks, and
broker order-preview controls described below pass. Short-volatility strategies can
produce small steady gains and sudden large losses; the risk controls are part of the
strategy, not optional overlays.

## Executive decision

The best Schwab-compatible quantitative method for a USD 20,000 account with Level 3
options approval is:

**A regime-conditioned, defined-risk index variance-risk-premium engine using XSP
weekly options, SSVI volatility-surface residuals, HAR realized-volatility forecasts,
CVaR/fractional-Kelly sizing, and Level 2 microprice-aware execution.**

The strategy should **not** be a naive naked short strangle system. Level 3 permission
allows uncovered options, short straddles/strangles, and uncovered ratio spreads, but
on a USD 20,000 account the mathematically correct use of that permission is limited:

- Use Level 3 to permit flexible short-option structures and adjustment orders.
- Keep overnight risk defined with long disaster wings.
- Do not hold uncapped naked calls.
- Permit a naked put only if it is deliberately cash/margin-covered, stress-tested,
  and sized as if assignment or a volatility shock can occur immediately.

Primary instrument: **XSP** rather than SPX or SPY.

- XSP is about 1/10 the size of SPX, fitting a USD 20,000 account better than SPX.
- XSP is cash-settled and European-style, removing early-assignment risk.
- XSP avoids the share-delivery mechanics of SPY options.
- SPY can be used as a liquidity fallback or delta hedge, but SPY options have early
  assignment and dividend/exercise complications that the core system should avoid.

## Why this method, and why not alternatives

### Rejected: high-frequency option market making

Schwab has streaming market data, option chains, multi-leg option order entry, and
options book data, but it is still a retail brokerage API. It is not an exchange
colocation stack. True options market making depends on queue priority, fee tiers,
inventory warehousing, and microsecond response. A USD 20,000 account also sits below
the USD 25,000 pattern-day-trader threshold, so a high-turnover intraday system could
create account restrictions.

Use Level 2 data for **execution quality and adverse-selection filtering**, not for
competing with professional market makers.

### Rejected: always-on short premium

Selling fixed-delta weekly puts, iron condors, or strangles without a volatility regime
model is structurally short crash insurance. It often looks like income until one tail
event erases many months of gains. The research literature supports a volatility risk
premium, but also shows that it is state-dependent and tail-heavy.

### Selected: selective short variance with hard risk budgets

The edge is not "theta decay" alone. The edge is the conditional difference between:

1. the variance implied by current option prices, and
2. the realized variance forecast by a model that accounts for volatility clustering,
   jumps, term structure, and current market regime.

The system sells option structures only when the forecast edge exceeds transaction
costs, tail reserves, and model uncertainty.

## Required research and mathematical basis

The design draws from the following institutional-grade concepts.

### 1. Volatility risk premium and delta-hedged option PnL

Bakshi and Kapadia (2003), "Delta-Hedged Gains and the Negative Market Volatility Risk
Premium", show that delta-hedged option returns reveal the sign and magnitude of the
volatility risk premium. The practical implication is that short option positions earn
money only when the premium collected for implied variance exceeds realized variance,
after jumps, hedging error, and costs.

Useful approximation for a hedged option book:

```text
dPi ~= Theta dt - 0.5 * Gamma * S^2 * realized_variance dt + Vega dIV + jump/error terms
```

For a short-volatility position, expected PnL is favorable when implied variance is
rich versus future realized variance, but loss accelerates nonlinearly when realized
variance or implied volatility spikes.

### 2. Realized-volatility forecasting: HAR-RV

Corsi (2009), "A Simple Approximate Long-Memory Model of Realized Volatility",
motivates a parsimonious model using daily, weekly, and monthly realized-volatility
components. The production model should use an HAR family forecast:

```text
RV_hat(t, h) =
  beta0
  + beta_d * RV_1d
  + beta_w * RV_5d
  + beta_m * RV_21d
  + beta_iv * IV_atm
  + beta_lev * negative_return_indicator
  + beta_ts * volatility_term_structure
  + beta_jump * jump_proxy
```

For the account size here, a transparent HAR-RV/HARQ/HAR-J model is preferred over a
black-box neural net. It is easier to validate, less fragile, and sufficient for the
main decision: trade, shrink, or stand down.

### 3. Stochastic volatility: Heston-style dynamics

Heston (1993) models spot and variance as correlated stochastic processes:

```text
dS_t = mu S_t dt + sqrt(v_t) S_t dW_S
dv_t = kappa(theta - v_t)dt + xi sqrt(v_t)dW_v
corr(dW_S, dW_v) = rho
```

This matters because index markets typically have negative spot-volatility correlation:
volatility rises when the market falls. Any short-put or short-condor system must
stress both price movement and implied-volatility expansion simultaneously.

### 4. Arbitrage-free implied-volatility surface: SVI/SSVI

Gatheral and Jacquier (2014), "Arbitrage-Free SVI Volatility Surfaces", provide a
framework for fitting smiles without static arbitrage. For each expiration, model total
implied variance by log-moneyness:

```text
w(k) = a + b * (rho * (k - m) + sqrt((k - m)^2 + sigma^2))
```

The strategy should not sell options just because their raw IV is high. It should fit
the surface, remove obvious bad quotes, and look for **rich residuals**:

```text
richness(K, T) = market_IV(K, T) - fitted_surface_IV(K, T)
```

Sell structures where the short legs are locally rich and buy wings where skew offers
cheap convex protection.

### 5. Optimal control and inventory-aware quoting

Avellaneda and Stoikov (2008) model optimal market-making quotes through stochastic
control and inventory penalties. Stoikov and Saglam (2009), Abergel and El Aoud (2015),
and Baldacci, Bergault, and Gueant (2020) extend these ideas toward options inventory
and volatility-surface risk. A retail Schwab system should not quote continuously like
a market maker, but it should borrow the same principle:

```text
desired_price = fair_value
  +/- risk_aversion * inventory_exposure
  +/- adverse_selection_adjustment
  +/- urgency_adjustment
```

The application is order placement: join or improve the option spread only when the
expected edge remains after the execution price.

### 6. Microstructure and Level 2 execution

Stoikov's microprice work uses order-book imbalance to estimate short-horizon fair
value better than a simple midpoint:

```text
microprice = (ask_price * bid_size + bid_price * ask_size) / (bid_size + ask_size)
micro_bias = microprice - midpoint
```

Use Schwab options-book data to:

- avoid selling when the book is thin or one-sided against the order,
- set limit prices around midpoint/microprice rather than crossing spreads,
- cancel/replace slowly with price bands,
- skip contracts whose quoted edge disappears after realistic fills.

### 7. Tail risk: CVaR, EVT, and fractional Kelly

Kelly sizing maximizes expected log wealth, but full Kelly is dangerously sensitive to
estimation error. The strategy should use a constrained fractional-Kelly rule capped by
expected shortfall:

```text
kelly_fraction_raw ~= expected_edge / variance_of_trade_return
kelly_fraction = min(0.25 * kelly_fraction_raw, cvar_budget_cap, broker_margin_cap)
```

Risk constraints should be based on conditional value at risk (CVaR/expected shortfall),
not just win rate or probability of touch. EVT/GARCH-EVT tail estimates should be used
for stress tests because index returns and volatility changes have fat tails.

## Strategy rules

### Universe

Initial live universe:

1. XSP weekly options, 2 to 10 DTE.
2. SPY shares or equivalent ETF exposure only for delta hedging if needed.
3. VIX, VIX9D, VIX3M, VVIX, SPY/SPX realized volatility, and macro-event calendar as
   filters, not necessarily as traded instruments.

Avoid single-name earnings trades in the first version. Earnings volatility can be
modeled, but it adds idiosyncratic jump risk and assignment risk that are unnecessary
for the initial USD 20,000 account.

### Entry window

Default entry window:

- after the opening auction has stabilized, e.g. 10:00 to 15:15 ET,
- no new trades in the final 30 minutes unless closing risk,
- no new trades before CPI, FOMC, NFP, major Fed speakers, or large scheduled macro
  events unless the backtest explicitly validates that event class.

### Regime gate

Open short-volatility structures only if all are true:

```text
VRP_score > threshold
VIX_term_structure is not in severe backwardation
realized_volatility_shock_z < threshold
underlying drawdown / breadth stress filters pass
no scheduled high-impact event inside holding window
option bid/ask and book-depth filters pass
```

Suggested initial thresholds for paper trading:

- ATM implied volatility exceeds HAR forecast realized volatility by at least 2 vol
  points or by 1.25x modeled transaction cost plus tail reserve.
- VIX9D/VIX and front/back volatility term structure are not indicating acute stress.
- Five-day realized volatility is not accelerating above the 80th percentile unless
  the position size is reduced to zero or near-zero.

### Structure selection

Core structure: **XSP defined-risk short iron condor or broken-wing iron condor**.

Strike selection:

1. Forecast the h-day distribution of XSP returns using HAR-RV plus jump/tail overlay.
2. Fit SSVI/SVI to current option mids after quote-quality filtering.
3. Choose short put and short call strikes outside the model's 80% to 90% expected
   interval, adjusted for skew and current delta.
4. Prefer short legs with positive SVI residual richness.
5. Buy wings where convexity is cheap relative to the surface and maximum loss fits
   the risk budget.

Initial parameters for paper trading:

- DTE: 3 to 7 days.
- Short-leg absolute delta: 0.10 to 0.20, but never from delta alone.
- Wing width: USD 5 to USD 10 in XSP, selected so one-lot max loss is affordable.
- Credit requirement: at least 20% to 30% of wing width after estimated fees/slippage.
- One-lot trade until the live/paper evidence supports scaling.

Level 3 extension, disabled by default:

- Broken-wing or ratio-like structures may be considered only if a disaster wing caps
  loss and order preview confirms margin. The extra short option must reduce portfolio
  risk-adjusted CVaR, not merely increase credit.

### Position sizing for USD 20,000

Initial hard caps:

```text
max_loss_per_new_position <= min(USD 400, 2.0% of net_liq)
aggregate_open_max_loss <= min(USD 1,200, 6.0% of net_liq)
daily_realized_loss_stop <= USD 300
weekly_realized_loss_stop <= USD 800
minimum_cash_buffer >= 50% of net_liq
```

These caps are deliberately conservative. The objective is to survive enough regimes to
collect a real sample of fills and PnL. The engine should increase size only after the
model demonstrates live fill quality and risk behavior, not after a short winning streak.

Sizing formula:

```text
trade_units = floor(
  min(
    per_trade_max_loss_budget / structure_max_loss,
    aggregate_max_loss_remaining / structure_max_loss,
    cvar_budget / simulated_CVaR_99,
    broker_margin_available / preview_margin_requirement
  )
)
```

If `trade_units < 1`, do not trade.

### Exit rules

Profit-taking:

- Close at 40% to 60% of max credit captured.
- Close earlier if the modeled VRP edge collapses or book liquidity deteriorates.

Risk exits:

- Close or reduce when mark-to-market loss reaches 1.0x to 1.5x original credit.
- Close or roll risk if either short leg reaches 0.30 absolute delta.
- Close if XSP enters the model's updated 70% interval around a short strike.
- Close before expiration-day settlement unless a tested 0DTE module exists.

Time exits:

- Avoid holding unmanaged positions through expiration.
- Avoid repeatedly opening and closing same-day positions because the account is below
  the pattern-day-trader threshold.

No averaging down:

- Do not add to a losing short-volatility position.
- New trades are allowed only after the old risk is closed or the aggregate risk budget
  has been recalculated from current net liquidation.

## Schwab orchestration design

Schwab API features to use:

- OAuth token flow.
- Account/positions endpoint for net liquidation, balances, and current exposure.
- Option chains endpoint for expirations, strikes, quotes, and greeks when available.
- Quotes and price-history endpoints for underlying data and realized-volatility inputs.
- Streaming market data for live quotes and options book.
- Order preview endpoint for margin/risk validation.
- Multi-leg option order endpoint for limit orders, replace, cancel, and close.

Execution loop:

```text
1. Refresh account state.
2. Pull XSP/SPY market data and option chains.
3. Clean quotes: remove stale, crossed, zero-bid, too-wide, and low-depth contracts.
4. Fit SVI/SSVI surface.
5. Forecast realized volatility with HAR model.
6. Compute VRP score and regime gates.
7. Generate candidate structures.
8. Simulate PnL distribution and CVaR under stochastic-vol/jump scenarios.
9. Size trade under risk and broker margin caps.
10. Preview order through Schwab.
11. Place midpoint/microprice-aware limit order.
12. Cancel/replace only within the edge-preserving price band.
13. Monitor risk, exits, and day-trade count.
14. Log every decision, quote snapshot, preview response, order response, and fill.
```

Order placement rules:

- Never use market orders for option spreads.
- Use net-credit limit orders.
- Start near midpoint when edge allows.
- Improve gradually; do not cross beyond modeled fair value.
- Reject if expected edge after fill is below zero.
- Reject if Schwab preview margin exceeds model margin by a configured tolerance.

## Data and tool budget

Use the USD 600 supplemental budget for data, not signal subscriptions.

Priority:

1. Historical option chains with quotes, greeks, IV, and open interest for XSP/SPX/SPY.
   Providers to evaluate: ThetaData, ORATS, Polygon.io options data, Cboe DataShop.
2. Historical VIX/VIX9D/VIX3M/VVIX and macro-event calendars.
3. A small VPS or monitoring service only after the strategy passes paper trading.

Free or low-cost data can prototype the model, but live deployment needs historical
option quotes to estimate fill slippage, surface residual stability, and tail behavior.

## Backtest and validation requirements before live trading

Minimum validation:

- At least five years of historical option-chain backtest if data is available.
- Explicit inclusion of February 2018, March 2020, 2022 inflation/Fed regime, and
  recent low-volatility regimes.
- Walk-forward model fitting; no full-sample parameter leakage.
- Realistic bid/ask fills: midpoint-only backtests are not acceptable.
- Commission and regulatory fees included.
- Slippage stress: midpoint, 25% of spread, 50% of spread, and adverse fill cases.
- Day-trade count simulation for the USD 20,000 margin account.
- Assignment/settlement modeling for any non-XSP fallback instrument.

Pass/fail targets for paper trading:

```text
paper_trade_count >= 100 candidate decisions
actual_fill_slippage <= modeled_slippage + tolerance
max_drawdown <= predeclared threshold
profit_factor > 1.2 after costs
no single loss > designed max_loss
all order-preview and risk logs reproducible
```

These are not promises of future profitability. They are minimum evidence that the
engine behaves as designed.

## Alpha protection

The protectable part of the strategy is not "sell 15-delta iron condors." That rule is
crowded and easy to copy. The less commoditized components are:

1. **Surface residual selection**: sell strikes rich to an arbitrage-filtered SVI/SSVI
   surface and buy wings cheap to the same surface.
2. **Conditional VRP gating**: trade only when implied variance exceeds a regime-aware
   realized-volatility forecast by enough to pay for tails and costs.
3. **Microstructure-aware execution**: keep more edge by avoiding adverse option-book
   states and not crossing spreads unnecessarily.
4. **CVaR-constrained sizing**: avoid the common short-volatility failure mode of
   scaling up during calm regimes and being oversized when volatility changes state.

This combination is more defensible than a public fixed-delta income strategy, while
remaining simple enough to implement and audit.

## Implementation sequence

1. Build data ingestion and quote normalization.
2. Build Black-Scholes greeks and implied-volatility inversion for validation.
3. Build SVI/SSVI fitting and static-arbitrage checks.
4. Build HAR-RV/HAR-J forecast model.
5. Build candidate structure generator.
6. Build Monte Carlo/stress risk engine with CVaR.
7. Build Schwab order-preview and order-ticket adapter.
8. Build paper-trading mode with full audit logs.
9. Run walk-forward backtests and paper trade.
10. Enable live trading with one-lot caps only after validation passes.

## Bibliography

- Avellaneda, M. and Stoikov, S. (2008). "High-frequency trading in a limit order
  book." Quantitative Finance.
- Bakshi, G. and Kapadia, N. (2003). "Delta-Hedged Gains and the Negative Market
  Volatility Risk Premium." Review of Financial Studies.
- Baldacci, B., Bergault, P., and Gueant, O. (2020). "Algorithmic market making for
  options."
- Carr, P. and Wu, L. (2010). "Analyzing Volatility Risk and Risk Premium in Option
  Contracts: A New Theory."
- Corsi, F. (2009). "A Simple Approximate Long-Memory Model of Realized Volatility."
  Journal of Financial Econometrics.
- Driessen, J., Maenhout, P., and Vilkov, G. (2009). "The Price of Correlation Risk:
  Evidence from Equity Options." Journal of Finance.
- Gatheral, J. and Jacquier, A. (2014). "Arbitrage-Free SVI Volatility Surfaces."
  Quantitative Finance.
- Heston, S. (1993). "A Closed-Form Solution for Options with Stochastic Volatility
  with Applications to Bond and Currency Options." Review of Financial Studies.
- Kelly, J. (1956). "A New Interpretation of Information Rate." Bell System Technical
  Journal.
- McNeil, A. and Frey, R. (2000). "Estimation of Tail-Related Risk Measures for
  Heteroscedastic Financial Time Series." Journal of Empirical Finance.
- Stoikov, S. (2018). "The Micro-Price: A High Frequency Estimator of Future Prices."
- Stoikov, S. and Saglam, M. (2009). "Option Market Making under Inventory Risk."
  Review of Derivatives Research.
- Thorp, E. (2006). "The Kelly Criterion in Blackjack, Sports Betting, and the Stock
  Market."
