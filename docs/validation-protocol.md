# Validation protocol

## 1. Freeze the hypothesis first

Before viewing locked results, tag a release containing:

- instrument and roll rule;
- opening-range window;
- exact signal/exit logic;
- signal mode (default price/volume; fixed L2 construction only for the optional L2 variant);
- fee and slippage model;
- parameter neighborhoods to be tested;
- prop-account profile; and
- the promotion gates below.

Do not repeatedly edit rules after seeing the locked set. A changed rule starts a new research trial and must be counted in data-snooping controls.

## 2. Required data

Use trade-based MNQ and, separately, NQ data (or MES/ES for the legacy profile) with full Globex-session coverage when GTH windows are enabled, including:

- exchange timestamps and unambiguous UTC conversion;
- bid/ask and actual trade volume;
- per-minute sum of trade price times trade size for exact session VWAP;
- only for the optional L2 variant: at least top-five depth snapshots or event data at 100–250 ms resolution;
- contract identifier on every observation;
- rejects, fills, and measured slippage for forward tests; and
- a documented rollover rule based on liquidity, never a back-adjusted price series joined to the wrong contract's order book.

A preliminary no-L2 run accepts OHLCV; exact VWAP reconciliation and the live L1 spread/freshness guard require additional trade/quote records. Missing depth is not a limitation of the no-L2 signal itself.

QuantData may add a dated daily regime field, but it cannot substitute for historical CME depth.

## 3. Chronological partitions

A practical initial study, subject to data availability:

- development: 2018–2022;
- model-selection validation: 2023–2024;
- locked out of sample: 2025 through the latest complete month in 2026;
- untouched live-forward paper test: at least 20 later Rithmic sessions.

Also report event/regime slices: 2020 shock, 2022 inflation/rate volatility, low-volatility periods, quarterly rolls, CPI/FOMC/NFP, half days, and expiration weeks. Do not delete bad regimes after observing them.

## 4. Simulation conventions

- Decide on bar close, enter at next bar open/first trade.
- If a minute bar hits stop and target, assume stop first unless tick data resolves order.
- Stops gapped through fill at the adverse open plus adverse slippage.
- Charge commission per contract per side.
- Apply adverse slippage on entry and exit; base case is one tick per side RTH and two ticks GTH, then stress doubled values.
- The CLI automatically reports a double-commission/double-slippage run; additional empirical-tail stress remains required for the final report.
- Include rejected orders, missed entries, partial fills, disconnects, and stale-feed shutdowns (book shutdowns only in L2 mode) in forward testing.
- Apply the prop max-loss floor to conservative intraday equity, not merely EOD P&L.
- Compute payout best-day consistency as best positive day divided by **net cumulative profit**, including losing days in the denominator.
- Do not count a limit order as filled merely because price touched it. Wall-offset limit entries count as filled only when price trades **strictly through** the limit by at least one tick; queue priority is never assumed. Non-wall entries remain market orders with a server-side bracket.
- Report results **per window** (Globex reopen, London, NY) in addition to the aggregate, together with the engine's `no_trade_diagnostics_by_window` counters. A window that never trades, or trades only via one gate, must be visible — silently inactive windows are how over-filtered strategies hide.

## 5. Walk-forward design

Use expanding or rolling chronological windows. A reasonable minimum is 24 months train, 6 months validate, 3–6 months test, stepped forward without overlap in test observations. Purge any feature whose lookback crosses a boundary and embargo at least one session around selection boundaries.

The main report must show each window, not only the aggregate. At least 60% of five or more test windows must be net profitable.

## 6. Parameter discipline

The current numbers are hypotheses. Test small predeclared neighborhoods, not a broad optimizer:

- OR: 5, 15, 30 minutes;
- OR/ATR bounds: adjacent ±20% values (separately for RTH and GTH windows);
- retest tolerance: 0.08, 0.12, 0.16;
- L2 threshold: 0.10, 0.15, 0.20;
- reward/risk: 1.5, 1.75, 2.0;
- wall offset: 4, 6, 8 ticks; wall ratio: 3, 4, 6; wall timeout: 3, 5, 8 bars;
- round-number front ticks: 2, 4, 6.

Also run the three ablations `use_wall_entries=false`, `use_round_levels=false`, and GTH windows disabled. Each mechanism must not *degrade* the base strategy net of costs; a mechanism that only helps in one narrow parameter cell is noise and should be switched off rather than tuned.

Require a plateau: neighboring settings should preserve the sign of expectancy. Count every tested combination when estimating backtest-overfitting risk. Do not choose the isolated maximum.

## 7. Minimum report

Report gross and net:

- trade count, win rate, average win/loss, expectancy, profit factor;
- daily Sharpe plus probabilistic/deflated Sharpe context;
- maximum drawdown, worst day, time under water;
- turnover, commission, modeled slippage, measured slippage;
- long/short and regime attribution;
- MAE/MFE and holding-time distribution;
- fill/reject/stale-data counts;
- block-bootstrap terminal P&L and drawdown distribution; and
- prop pass/breach rate across block-bootstrapped paths.

## 8. Hard promotion gate

Remain in research/paper mode unless all conditions hold:

1. At least 150 locked out-of-sample trades.
2. Net OOS profit factor ≥ 1.15.
3. Probabilistic Sharpe ratio versus zero ≥ 95%.
4. Positive OOS P&L with both commissions and slippage doubled.
5. At least 60% of five or more walk-forward test windows profitable.
6. Stable expectancy sign in neighboring parameters.
7. Positive long and short results, or a direction restriction declared before the locked test.
8. No breach in the conservative target prop profile and acceptable 95th-percentile bootstrap drawdown.
9. Twenty untouched Rithmic paper sessions with bracket, rejection, restart, and stale-feed drills.
10. A separate micro-live/funded risk decision made only after paper results reconcile with the backtest.

The `production_gate` function automates the objective subset. Human review is still required for data lineage, trial count, execution reconciliation, and rule compliance.

