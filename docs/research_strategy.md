# Regime-Adaptive Defined-Risk Variance Risk Premium Strategy

This repository implements a research-backed strategy selector for a $20,000
Charles Schwab account with level 3 options approval. It is not financial advice,
does not guarantee income, and should be paper-traded and independently reviewed
before capital is committed.

## Selected method

The best fit for the stated constraints is a **regime-adaptive, defined-risk
variance risk premium (VRP) strategy**:

1. Trade only highly liquid ETF/index-proxy option chains such as SPY, QQQ, IWM,
   DIA, and TLT unless a separate single-name event model exists.
2. Enter 21-60 DTE put credit spreads, call credit spreads, or iron condors only
   when implied volatility is rich versus a realized-volatility forecast.
3. Use level 3 multi-leg capability for defined-risk structures, not naked short
   options. Level 3 adds orchestration flexibility, but a $20,000 account cannot
   survive uncontrolled short-gamma tail exposure.
4. Size by fractional Kelly and hard CVaR/defined-risk caps:
   - per-trade max risk: 2% of equity by default,
   - portfolio max defined risk: 8% of equity by default,
   - use 25% Kelly after shrinking the edge.
5. Exit mechanically: target 40-60% of max credit captured; cut or hedge when the
   short strike is threatened, realized volatility exceeds entry forecast, or the
   position reaches 14-21 DTE with material gamma risk.

The strategy is designed to monetize the empirically observed tendency for
option-implied variance to exceed subsequent realized variance while explicitly
controlling crash risk.

## Research base

Required research papers and institutional concepts reviewed:

- **Coval and Shumway (2001), "Expected Option Returns"**: documents systematic
  option return patterns and losses for zero-beta straddles, consistent with
  priced stochastic volatility risk.
- **Carr and Wu (2009), "Variance Risk Premiums"**: shows how vanilla options can
  synthesize variance swap rates and quantify the gap between risk-neutral and
  realized variance.
- **Bakshi, Kapadia, and Madan (2003), "Stock Return Characteristics, Skew Laws,
  and the Differential Pricing of Individual Equity Options"**: motivates using
  risk-neutral skewness and kurtosis from option surfaces instead of relying only
  on Black-Scholes IV.
- **Driessen, Maenhout, and Vilkov (2009), correlation-risk-premium research**:
  explains why index implied correlation is often expensive, but crisis regimes
  can erase dispersion alpha. This is why this implementation avoids retail-size
  dispersion as the primary strategy.
- **Avellaneda and Stoikov (2008), "High Frequency Trading in a Limit Order
  Book"**: provides the inventory-aware reservation-price framework used here as
  inspiration for execution limits and order-book filters.
- **Stoikov and Saglam, option market-making under inventory risk**: emphasizes
  that option quotes must account for delta, gamma, vega, stochastic volatility,
  liquidity, and overnight gap risk.
- **Heston (1993), stochastic volatility model**: motivates modeling volatility
  as mean-reverting and state-dependent, not constant.
- **Gatheral and Jacquier, arbitrage-free SVI/SSVI volatility surfaces**: supports
  future upgrades that fit an arbitrage-aware implied volatility surface before
  detecting skew/smile dislocations.
- **Rockafellar and Uryasev (2000/2001), CVaR optimization**: supports using CVaR
  rather than only win probability or max profit when sizing tail-risk trades.
- **Buehler et al. (2019), "Deep Hedging"**: supports the long-term roadmap of
  optimizing hedge/roll decisions under transaction costs and liquidity frictions
  with convex risk measures.

## Advanced mathematical concepts used

- **Risk-neutral vs physical probability measures**: the edge is the compensated
  difference between option-implied variance under the risk-neutral measure and
  realized variance under the physical measure.
- **Stochastic volatility**: realized and implied volatility are treated as
  state variables, not constants.
- **Greeks and short-gamma path risk**: short options earn theta but lose convexly
  in large moves, so the engine limits DTE, delta, and defined risk.
- **CVaR / expected shortfall**: candidate spreads are scenario-tested and ranked
  by expected value penalized for tail loss.
- **Fractional Kelly sizing**: sizing is based on edge and odds, then clipped by
  conservative hard risk limits to reduce parameter-estimation risk.
- **Limit-order-book microstructure**: bid/ask width, size imbalance, volume, and
  open interest influence trade rejection and entry-limit placement.
- **Volatility-surface logic**: the current engine uses per-leg IV/RV ratios; the
  next institutional upgrade is SSVI surface fitting and residual detection.

## Why not use naked options?

Schwab level 3 approval may permit more advanced option structures, but "income"
from naked short puts/calls is a negatively skewed insurance business. For a
$20,000 account, one gap event can dominate months of premium. Defined-risk
verticals and iron condors give up some carry in exchange for survivability,
clear margin, and simpler automated kill-switch logic.

## Schwab orchestration plan

1. Pull option chains from Schwab market-data chains endpoint.
2. Stream `LEVELONE_OPTIONS` and `OPTIONS_BOOK` to keep bid/ask, sizes, and book
   imbalance fresh.
3. Normalize chain rows into `UnderlyingSnapshot` and `OptionQuote`.
4. Run `StrategyEngine.find_trades(...)`.
5. Convert the selected candidate to a Schwab `NET_CREDIT` multi-leg order with
   `build_opening_order(...)`.
6. Submit only after final risk checks:
   - market is open and spread is still liquid,
   - no scheduled CPI/FOMC/earnings event for the underlying,
   - total defined risk remains below portfolio cap,
   - order price is not worse than the engine's limit.
7. After fill, place/monitor a profit-taking close order and risk exits.

## Supplemental budget recommendation

Do not spend the $600 immediately on signal vendors. The first dollars should buy
better validation and historical options data:

- historical options chains and greeks for backtesting (ThetaData, ORATS,
  OptionMetrics via academic access, Polygon, or similar),
- earnings/economic calendar API if single names are ever added,
- a low-cost VPS only if Schwab streaming must run continuously.

Keep paid tools only if live/paper results show the strategy's after-slippage
edge exceeds data and infrastructure costs.

## Alpha protection idea

The less-crowded enhancement is a **surface-residual plus book-imbalance gate**:
fit an arbitrage-aware implied-volatility surface, estimate each candidate
spread's fair credit from the smoothed surface, then trade only when the live
level 2 book offers excess credit and the imbalance does not indicate adverse
selection. Many retail short-premium bots screen IV rank and delta; fewer combine
surface residuals, CVaR sizing, and options-book microstructure in one gate.
