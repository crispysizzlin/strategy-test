# Validation protocol

## 1. Freeze the hypothesis first

Before viewing locked results, tag a release containing:

- instrument and roll rule;
- opening-range window;
- exact signal/exit logic;
- fixed L2 construction;
- fee and slippage model;
- parameter neighborhoods to be tested;
- prop-account profile; and
- the promotion gates below.

Do not repeatedly edit rules after seeing the locked set. A changed rule starts a new research trial and must be counted in data-snooping controls.

## 2. Required data

Use trade-based MES and, separately, ES data with:

- exchange timestamps and unambiguous UTC conversion;
- bid/ask and actual trade volume;
- per-minute sum of trade price times trade size for exact session VWAP;
- at least top-five depth snapshots or event data at 100–250 ms resolution;
- contract identifier on every observation;
- rejects, fills, and measured slippage for forward tests; and
- a documented rollover rule based on liquidity, never a back-adjusted price series joined to the wrong contract's order book.

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
- Charge commission per contract per side.
- Apply adverse slippage on entry and exit; base case is one MES tick per side, then stress two and four ticks.
- The CLI automatically reports a double-commission/double-slippage run; a four-tick and empirical-tail stress remains required for the final report.
- Include rejected orders, missed entries, partial fills, disconnects, and stale-book shutdowns in forward testing.
- Apply the prop max-loss floor to conservative intraday equity, not merely EOD P&L.
- Compute payout best-day consistency as best positive day divided by **net cumulative profit**, including losing days in the denominator.
- Do not count a limit order as filled merely because price touched it; the live build currently uses market entry with a server-side bracket to avoid fictional queue priority.

## 5. Walk-forward design

Use expanding or rolling chronological windows. A reasonable minimum is 24 months train, 6 months validate, 3–6 months test, stepped forward without overlap in test observations. Purge any feature whose lookback crosses a boundary and embargo at least one session around selection boundaries.

The main report must show each window, not only the aggregate. At least 60% of five or more test windows must be net profitable.

## 6. Parameter discipline

The current numbers are hypotheses. Test small predeclared neighborhoods, not a broad optimizer:

- OR: 5, 15, 30 minutes;
- OR/ATR bounds: adjacent ±20% values;
- retest tolerance: 0.08, 0.12, 0.16;
- L2 threshold: 0.10, 0.15, 0.20;
- reward/risk: 1.5, 1.75, 2.0.

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
