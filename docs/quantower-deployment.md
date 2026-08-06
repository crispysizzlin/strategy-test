# Quantower build and deployment

## 1. Build on the Quantower machine

The C# file depends on `TradingPlatform.BusinessLayer`, which is distributed with Quantower and is not available in this repository's Linux test environment.

1. Install current Quantower, Visual Studio 2022, and Quantower's Algo extension.
2. In Visual Studio, create a Quantower Strategy project using the installed template.
3. Replace the generated strategy source with `src/Quantower.AdaptiveOrb/AdaptiveOrbStrategy.cs`.
4. Build the project and resolve only SDK-version compile differences; do not change signal logic during this step.
5. Start Quantower and confirm that `Adaptive ORB multi-session + L2 walls + round numbers` appears under Strategies.

Record the Quantower build, extension/SDK build, Rithmic connection version, and resulting DLL hash in the validation log. If an SDK signature differs, compare the change with Quantower's official Examples repository before editing.

## 2. Use a dedicated simulator account

Select the current liquid **MNQ contract**, not a continuous symbol. The strategy requires the selected prop/simulator account to have no position or working order at startup and treats other-symbol activity in that account as a risk shutdown. Do not run a copier, manual trade, or another bot in the same account.

With GTH windows enabled, start the strategy between the 16:45 ET close-out and the 18:00 ET Globex reopen so every window's opening range is observed live from trades. A window whose opening range already began when the strategy joins is skipped, not approximated. With prop enforcement enabled, the default requires a restart on every new **trading date** (the 17:00 ET rollover, one restart per day in the 16:45–18:00 gap). This forces a fresh account-dashboard liquidation threshold instead of silently carrying yesterday's EOD-trailing floor.

Lucid Pro/Flex/Direct accounts may trade from the 18:00 ET reopen but must be flat by 16:45 ET; the strategy's windows all flatten well before that, and an independent 16:40 ET hard guard closes any residual exposure.

## 3. Critical inputs

| Setting | MNQ base value | Operational rule |
|---|---:|---|
| Tick value | `$0.50` | Confirm against the actual selected contract (MNQ: 0.25 pts = $0.50). |
| Commission per side | `$0.50` | Lucid published MNQ rate as of 2026-07-20; verify again in the dashboard/agreement. |
| Assumed slippage | `1 tick/side` RTH, `2` GTH | Also test doubled values; replace with measured paper fills. |
| Max trade risk | `$150` on 100K | GTH windows automatically apply the `GTH risk fraction` (default 0.5 → `$75`). |
| Max quantity | `10` | Internal cap, intentionally far below Lucid's 60-micro limit on 100K. |
| Sessions | NY + Globex reopen + London | Disable individual windows for isolation studies; at least one must stay enabled. |
| Current prop liquidation threshold | no default | Copy the current dollar floor from the prop dashboard before each trading day. Zero refuses startup. |
| Prop safety buffer | `$100` | Increase if measured latency/slippage warrants it. Planned risk is also capped to stay above this buffer. |
| Daily ATR override | `0` | Zero uses provider `DAY1` history (full Globex day), which now matches the research engine's grouping. |
| Use Level 2 | on | Turn off only for a labeled bar-only baseline, never to promote the L2 strategy. |
| L2 freshness | `750 ms` | A stale or wide book blocks entry and a stale active feed triggers shutdown. |
| Wall entries | on | Wall logic only improves entry price; disabling it reverts to pure market entries. |
| Wall persistence snapshots | `10` (~2 s) | Raise on instruments with heavy spoofing; lower values trust the book more than it deserves. |
| Round-number exits | on | Adjusts targets/stops around …00/20/40/50/60/80 levels; never creates trades. |
| Wall-clock watchdog | on | Disable only in historical replay, where wall time is unrelated to market time. |
| Flatten on stop | on | Verify it closes the selected position and cancels selected orders in simulation. |

The `$150` risk cap and 0.5 GTH fraction are research starting points, not optimized recommendations. Never replace the required liquidation threshold with account balance; EOD-trailing drawdown is path-dependent and the dashboard/agreement is authoritative.

## 4. Verify Rithmic behavior in simulation

Before collecting forward results, deliberately exercise each condition and retain logs/screenshots:

1. A normal market entry creates one position and both attached protective instructions at the expected offsets.
2. A wall-limit entry rests at the wall price plus the offset, carries attached protection, and its timeout cancels the limit and (when configured) replaces it with one market entry — never two positions.
3. A wall-limit partial fill keeps the filled quantity protected and cancels the remainder; other unexpected fills cause conservative flattening.
4. Rejecting an entry or protective order disables the trading day; a rejected protection attempt closes the position.
5. Disconnect or stale trades for more than five seconds while exposed causes a close attempt.
6. Stale Level 2, a processing exception, and repeated excessive slippage cause shutdown.
7. The failed-retest and 45-minute exits close through a market action and cancel orphaned protection.
8. Each window flattens at its own cutoff (default 21:30 / 08:30 / 15:55 ET), and nothing survives the 16:40 ET hard guard, safely before the firm's 16:45 ET sim deadline.
9. Pressing Stop while a position is open flattens the selected symbol when `Flatten on stop` is enabled.
10. A second symbol/order in the account disables this strategy without attempting to manage that foreign exposure.
11. Restart, daylight-saving transitions, early closes, contract rolls, and a Quantower/Rithmic reconnect do not reuse stale session or book state. Verify the London window's ET open against the intended London time during the ~3-week US/UK DST divergence.
12. GTH behavior on a quiet tape: a pending wall limit on a printless tape is cancelled by the watchdog rather than left resting.

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
