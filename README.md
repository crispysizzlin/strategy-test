# Adaptive ORB for Quantower

A cost-aware research implementation of an opening-range breakout/retest strategy for CME equity-index futures, designed for Quantower with Rithmic Level 2 and prop-firm risk constraints.

## Status: research prototype, not a profitability claim

The repository contains an executable Quantower strategy, a reproducible Python simulator, Level 2 feature tooling, prop-account path simulation, and validation gates. It does **not** yet contain the historical Rithmic/CME depth data or untouched live-forward results needed to call the strategy profitable.

| Check | Current status |
|---|---|
| Python signal, cost, L2, validation, and prop-account tests | Passing |
| Quantower C# source reviewed against official API examples | Complete |
| C# compiled inside the user's Quantower/Visual Studio installation | Required |
| Historical bar-only test | Data required |
| Historical Level 2 test | Data required |
| Locked out-of-sample test | Not run |
| 20-session Rithmic paper-forward test | Not run |
| Funded deployment | Explicitly blocked until the validation gate passes |

No code or paper can guarantee future profit. The correct target is a positive net expectancy that remains stable out of sample, under worse costs, and within the account's path-dependent drawdown rules.

## Why this is not raw ORB

Simple ORB performance is unstable across instruments and subperiods. The strategy therefore separates four jobs:

1. The 09:30–09:45 ET opening range defines a market-generated level.
2. OR width versus recent daily true range, breakout relative volume, and session VWAP identify a plausible directional regime.
3. Entry waits for a breakout **and retest**, avoiding the first-touch chase.
4. Persistent five-level depth imbalance, order-flow imbalance, microprice, and trade delta confirm the retest. A single displayed “wall” is never enough.

The default market is **MES**, not ES or NQ. MES makes stop-based sizing granular enough for a prop drawdown. QuantData is optional and is used only through a slow daily regime file; Rithmic/Quantower remains the source of futures trades, book data, and execution.

## Repository layout

- `src/Quantower.AdaptiveOrb/AdaptiveOrbStrategy.cs` — live/backtest Quantower strategy.
- `research/adaptive_orb/` — dependency-free event/accounting research engine.
- `config/` — MES examples and a dated Lucid rules reference.
- `docs/strategy-spec.md` — exact state machine and equations.
- `docs/validation-protocol.md` — pass/fail research procedure.
- `docs/quantower-deployment.md` — build, simulation, and launch checklist.
- `docs/data-schema.md` — minute bars and recorded Level 2 format.
- `docs/research-basis.md` — papers, official documentation, and limits of inference.

## Run the auditable tests

Only Python 3.11+ is required:

```bash
PYTHONPATH=research python3 -m unittest discover -s tests -v
```

Run a historical simulation after supplying minute bars enriched with close-of-minute L2 features:

```bash
PYTHONPATH=research python3 -m adaptive_orb.cli backtest \
  --data /path/to/mes_minute_l2.csv \
  --config config/mes_lucid_pro_100k.json \
  --output artifacts/mes_oos
```

The simulator uses next-bar entries, adverse slippage on both sides, published per-side commission, and a pessimistic stop-first rule if both stop and target print within the same minute. Each run automatically repeats with both commission and slippage doubled, and reports a conservative Lucid-style EOD trailing-drawdown path.

To reduce the Quantower recorder's 200 ms snapshots to decision-time features:

```bash
PYTHONPATH=research python3 -m adaptive_orb.cli aggregate-l2 \
  --data /path/to/quantower_l2_raw.csv \
  --output /path/to/l2_by_minute.csv \
  --tail-samples 1
```

Join the resulting feature file to genuine trade-based minute bars by UTC minute. Include `trade_value = sum(price * size)` for exact live-VWAP reconciliation. Never forward-fill L2 across gaps; the engine also rejects a decision book older than 750 ms.

## Quantower use

Create a Strategy project with the Quantower Algo extension for Visual Studio 2022, then place `AdaptiveOrbStrategy.cs` in that project. Start with MES on the Rithmic simulator. Important settings:

- Enter the **current** prop liquidation threshold from the account dashboard. The strategy deliberately defaults this field to zero and will refuse to start until it is set or enforcement is explicitly disabled for research.
- Restart before each RTH session and refresh that threshold. The live default disables itself on a new ET date so yesterday's EOD-trailing floor is not reused.
- Use MES tick value `$1.25` and Lucid's published MES commission `$0.50` per contract per side, then stress both higher.
- Keep Level 2 and the wall-clock watchdog enabled in paper/live mode. Disable them only for a clearly labeled bar-only backtest.
- Give this strategy exclusive control of the entire selected account. It refuses to start with any existing position or order and shuts down on other-symbol activity.
- For exact research/live reconciliation, set `Daily ATR override` to the prior-20-session RTH median from the research dataset; provider `DAY1` bars can use a different session boundary.
- Verify Rithmic attached stop/target behavior in simulation before any forward test.

Full instructions are in [docs/quantower-deployment.md](docs/quantower-deployment.md).

## Promotion gate

The code must remain in research/paper mode unless all of these hold on a locked specification:

- at least 150 out-of-sample trades;
- net profit factor at least 1.15;
- probabilistic Sharpe ratio above 95% versus zero;
- positive P&L with commissions and slippage doubled;
- at least 60% of five or more walk-forward windows profitable;
- neighboring parameters show the same sign of expectancy;
- no prop breach under the conservative account-path simulator;
- at least 20 untouched Rithmic paper sessions with live slippage and rejection logs.

Passing these tests is evidence of robustness, not a guarantee.
