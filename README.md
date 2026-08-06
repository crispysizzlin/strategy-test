# Adaptive ORB for Quantower

A cost-aware research implementation of a multi-session opening-range breakout/retest strategy for CME equity-index futures, designed for Quantower with Rithmic Level 2 and prop-firm risk constraints. The default profile targets **MNQ on a Lucid Pro 100k** and trades up to three windows per CME trading day: the Globex reopen (18:00 ET), the London open (03:00 ET), and the New York open (09:30 ET).

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

Simple ORB performance is unstable across instruments and subperiods. The strategy therefore separates the jobs:

1. Each enabled window's opening range (default 15 minutes) defines a market-generated level; GTH windows carry their own regime bounds, spread caps, slippage assumptions, and a reduced risk fraction.
2. OR width versus recent full-day true range, breakout relative volume, and window-anchored VWAP identify a plausible directional regime.
3. Entry waits for a breakout **and retest**, avoiding the first-touch chase.
4. Persistent five-level depth imbalance, order-flow imbalance, microprice, and trade delta confirm the retest. A single displayed “wall” is never enough.

Two execution modifiers are deliberately **not** additional filters, so they cannot silently starve the strategy of trades:

- **Wall-offset entries.** When a large resting order (adaptively defined as ≥ 4× the median displayed level size, persistent for ~2 seconds) supports the trade, the entry becomes a passive limit resting 6 ticks in front of the wall, with the stop anchored behind the wall. If the limit is untouched within the timeout, the strategy falls back to the ordinary market entry.
- **Round-number exits.** Targets landing near …00/20/40/50/60/80 levels are shaved a few ticks in front of the level; stops resting near such a level are padded beyond it. Both adjustments are bounded and never create or veto a signal.

The backtester reports `no_trade_diagnostics_by_window` (range rejected, no breakout, no confirmed retest, L2 blocked, and so on) so an over-filtered configuration is visible immediately instead of silently "not executing".

The default market is **MNQ** (micro Nasdaq-100). Micros make stop-based sizing granular enough for a prop drawdown. QuantData is optional and is used only through a slow daily regime file; Rithmic/Quantower remains the source of futures trades, book data, and execution.

## Repository layout

- `src/Quantower.AdaptiveOrb/AdaptiveOrbStrategy.cs` — live/backtest Quantower strategy (multi-session, wall entries, round-number exits).
- `research/adaptive_orb/` — dependency-free event/accounting research engine (`levels.py` holds the round-number math).
- `config/` — MNQ and MES examples and a dated Lucid rules reference (`mnq_lucid_pro_100k.json` is the default profile).
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
  --data /path/to/mnq_minute_l2.csv \
  --config config/mnq_lucid_pro_100k.json \
  --output artifacts/mnq_oos
```

With GTH sessions enabled, the input must cover the full Globex day; bars at or after 17:00 ET are booked to the next trading date, matching CME's session convention.

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

Create a Strategy project with the Quantower Algo extension for Visual Studio 2022, then place `AdaptiveOrbStrategy.cs` in that project. Start with MNQ on the Rithmic simulator. Important settings:

- Enter the **current** prop liquidation threshold from the account dashboard. The strategy deliberately defaults this field to zero and will refuse to start until it is set or enforcement is explicitly disabled for research.
- With GTH windows enabled, restart once per trading day between the 16:45 ET close-out and the 18:00 ET reopen and refresh that threshold. The live default disables itself on a new trading date (17:00 ET rollover) so yesterday's EOD-trailing floor is not reused.
- Use MNQ tick value `$0.50` and Lucid's published MNQ commission `$0.50` per contract per side, then stress both higher. GTH windows assume 2 ticks of slippage per side and half risk by default.
- Keep Level 2 and the wall-clock watchdog enabled in paper/live mode. Disable them only for a clearly labeled bar-only backtest.
- Wall-offset entries and round-number exits are execution modifiers; each can be disabled independently for ablation tests without touching the signal logic.
- Give this strategy exclusive control of the entire selected account. It refuses to start with any existing position or order and shuts down on other-symbol activity.
- Provider `DAY1` futures bars (full Globex day) now match the research engine's ATR grouping; use `Daily ATR override` only if your provider's boundary still differs.
- Verify Rithmic attached stop/target behavior — including limit entries with attached protection and partial fills — in simulation before any forward test.

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
