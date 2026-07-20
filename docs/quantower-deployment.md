# Quantower build and deployment

## 1. Build on the Quantower machine

The C# file depends on `TradingPlatform.BusinessLayer`, which is distributed with Quantower and is not available in this repository's Linux test environment.

1. Install current Quantower, Visual Studio 2022, and Quantower's Algo extension.
2. In Visual Studio, create a Quantower Strategy project using the installed template.
3. Replace the generated strategy source with `src/Quantower.AdaptiveOrb/AdaptiveOrbStrategy.cs`.
4. Build the project and resolve only SDK-version compile differences; do not change signal logic during this step.
5. Start Quantower and confirm that `Adaptive ORB + VWAP + persistent L2 retest` appears under Strategies.

Record the Quantower build, extension/SDK build, Rithmic connection version, and resulting DLL hash in the validation log. If an SDK signature differs, compare the change with Quantower's official Examples repository before editing.

## 2. Use a dedicated simulator account

Select the current liquid **MES contract**, not a continuous symbol. The strategy requires the selected prop/simulator account to have no position or working order at startup and treats other-symbol activity in that account as a risk shutdown. Do not run a copier, manual trade, or another bot in the same account.

Start after 00:00 ET and before 09:30 ET so the live opening range is observed from trades. With prop enforcement enabled, the default requires a restart on every new ET date. This forces a fresh account-dashboard liquidation threshold instead of silently carrying yesterday's EOD-trailing floor.

## 3. Critical inputs

| Setting | MES base value | Operational rule |
|---|---:|---|
| Tick value | `$1.25` | Confirm against the actual selected contract. |
| Commission per side | `$0.50` | Lucid published rate as of 2026-07-20; verify again in the dashboard/agreement. |
| Assumed slippage | `1 tick/side` | Also test 2 and 4; replace with measured paper fills. |
| Max trade risk | `$75` on 100K; `$25` on 25K | Includes stop, round-trip commissions, and two assumed adverse fills. |
| Max quantity | `4` on 100K; `2` on 25K | Internal cap, intentionally far below firm size limits. |
| Current prop liquidation threshold | no default | Copy the current dollar floor from the prop dashboard before each session. Zero refuses startup. |
| Prop safety buffer | `$100` | Increase if measured latency/slippage warrants it. Planned risk is also capped to stay above this buffer. |
| Daily ATR override | `0` | Zero uses provider `DAY1` history; enter the research RTH median for exact reconciliation. |
| Use Level 2 | on | Turn off only for a labeled bar-only baseline, never to promote the L2 strategy. |
| L2 freshness | `750 ms` | A stale or wide book blocks entry and a stale active feed triggers shutdown. |
| Wall-clock watchdog | on | Disable only in historical replay, where wall time is unrelated to market time. |
| Flatten on stop | on | Verify it closes the selected position and cancels selected orders in simulation. |

The strategy's `$75`/`$25` risk caps are research starting points, not optimized recommendations. Never replace the required liquidation threshold with account balance; EOD-trailing drawdown is path-dependent and the dashboard/agreement is authoritative.

## 4. Verify Rithmic behavior in simulation

Before collecting forward results, deliberately exercise each condition and retain logs/screenshots:

1. A normal entry creates one position and both attached protective instructions at the expected offsets.
2. Stop and target quantities match a partial or complete entry fill; an unexpected partial fill causes conservative flattening.
3. Rejecting an entry or protective order disables the session; a rejected protection attempt closes the position.
4. Disconnect or stale trades for more than five seconds while active causes a close attempt.
5. Stale Level 2, a processing exception, and repeated excessive slippage cause shutdown.
6. The failed-retest and 45-minute exits close through a market action and cancel orphaned protection.
7. The position is flat by 15:55 ET, safely before the cited firm's 16:45 ET sim deadline.
8. Pressing Stop while a position is open flattens the selected symbol when `Flatten on stop` is enabled.
9. A second symbol/order in the account disables this strategy without attempting to manage that foreign exposure.
10. Restart, daylight-saving transitions, early closes, contract rolls, and a Quantower/Rithmic reconnect do not reuse stale session or book state.

Attached protection behavior is connection-specific. Treat a bracket as server-side only after Rithmic/Quantower simulation and disconnect drills demonstrate where it resides and how it behaves.

## 5. Record and reconcile forward data

Choose a writable local path for `L2 recording path`, enable `Record throttled L2 CSV`, and keep system time synchronized. Record the corresponding trade/tick bars, order lifecycle, fill prices, fees, rejects, connection state, full contract ID, and dashboard loss floor.

Aggregate the book snapshots as described in `docs/data-schema.md`, create real trade-value minute bars, and run the Python simulator with the same frozen inputs. For every paper trade compare:

- signal timestamp and direction;
- OR high/low, prior median true range, VWAP, volume ratio, and L2 composite;
- decision price, actual fill, stop/target offsets, and quantity;
- exit reason, gross P&L, commission, and measured slippage; and
- dashboard floor and worst intraday distance to it.

An unexplained signal or accounting mismatch blocks promotion.

## 6. Historical testing boundary

Quantower can model fees and bid/ask offsets, but ordinary minute/tick backtests do not establish that the live `NewLevel2` stream was historically replayed. Run two clearly separated experiments:

- **bar baseline:** `Use Level 2=false`, watchdog off, explicitly labeled as a different strategy;
- **full hypothesis:** externally aligned historical depth or recorded Rithmic paper data with L2 required and freshness enforced.

Do not infer full-strategy profitability from the bar baseline.

## 7. Promotion decision

Keep the DLL on a simulator until every gate in `docs/validation-protocol.md` passes on a frozen specification. A successful compile or a profitable development backtest is not a promotion event. The first funded decision should use the smallest permitted size, remain below the repository's risk caps, and have a separate human-reviewed rollback/disable checklist.
