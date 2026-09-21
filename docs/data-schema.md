# Data contract

The research engine accepts UTF-8 CSV with one row per one-minute bar. Timestamps identify the **start** of the minute and must be unique, strictly increasing, and timezone-aware. UTC with a `Z` suffix is preferred.

The default no-L2 MNQ profile needs only OHLCV (plus optional trade_value). All depth, wall, OFI, microprice and trade-delta columns can be omitted; they do not affect no-L2 trades. The live L1 quote guard is not simulated by this OHLCV loader.

## Minute bars

| Column | Required | Meaning |
|---|---:|---|
| `timestamp` | yes | Minute start, for example `2026-01-02T14:30:00Z`. |
| `open`, `high`, `low`, `close` | yes | Actual contract prices; never back-adjusted values joined to an unadjusted book. |
| `volume` | yes | Sum of exchange-reported trade size in the minute. |
| `trade_value` | recommended | Sum of trade price times trade size in the minute. This reproduces the live trade-based VWAP. |
| `depth_imbalance` | when L2 is required | Weighted top-five depth imbalance in `[-1, 1]`. |
| `ofi_norm` | when L2 is required | Bounded, EWMA-smoothed best-level order-flow imbalance in `[-1, 1]`. |
| `trade_delta_norm` | when L2 is required | Bounded signed-trade EWMA divided by trade-volume EWMA. |
| `microprice_ticks` | when L2 is required | Microprice deviation from mid, in ticks and clipped to `[-1, 1]`. |
| `l2_persistence` | when L2 is required | Signed fraction of the most recent five composites having the prevailing sign. |
| `spread_ticks` | when L2 is required | Best-ask minus best-bid in ticks. |
| `l2_age_ms` | when L2 is required | Milliseconds from the final eligible book observation to minute end. |
| `bid_wall_price` | optional | Price of the persistent bid wall visible at minute close; blank when no wall qualified. |
| `bid_wall_ratio` | optional | Wall size divided by the median displayed bid-level size (must be ≥ 1 when present). |
| `ask_wall_price` | optional | Price of the persistent ask wall visible at minute close; blank when no wall qualified. |
| `ask_wall_ratio` | optional | Wall size divided by the median displayed ask-level size (must be ≥ 1 when present). |

`trade_value` must be populated for every bar in a session or omitted from every bar in that session. When omitted, the engine explicitly reports and uses `(high + low + close) / 3 * volume` as an approximation. Locked validation should use real `trade_value`; the approximation is only for a bar-data baseline.

With `require_l2=true`, all six L2 components plus `l2_age_ms` must be present on a decision bar. The optional L2 gate uses each window's spread cap (MNQ: 2 ticks NY, 3 GTH) and rejects book age above 750 ms. Missing or stale depth is a no-trade, and L2 fields must never be forward-filled.

See `examples/minute_bars_schema.csv` for a syntactic example. Its row is illustrative, not market evidence.

## Raw Quantower recorder

With `RecordLevel2=true`, the strategy writes a throttled snapshot approximately every 200 ms when book updates arrive:

```text
received_utc,best_bid,best_ask,best_bid_size,best_ask_size,depth_imbalance,ofi_norm,trade_delta_norm,microprice_ticks,composite,signed_persistence,spread_ticks,bid_wall_price,bid_wall_ratio,ask_wall_price,ask_wall_ratio
```

The four wall columns are blank on snapshots where no level passed both the adaptive size-ratio test and the persistence requirement. The reducer copies the **final** snapshot's wall values into the minute row; wall prices must never be averaged across snapshots because different snapshots can hold different walls.

Reduce it to decision-minute fields with:

```bash
PYTHONPATH=research python3 -m adaptive_orb.cli aggregate-l2 \
  --data /path/to/quantower_l2_raw.csv \
  --output /path/to/l2_by_minute.csv \
  --tail-samples 1
```

The default uses the final snapshot in each UTC minute to match the live state machine. A value above one takes the median of the last `N` snapshots and is a separate robustness transform, not the live-equivalent base case. The reducer requires ordered, timezone-aware raw timestamps and computes `l2_age_ms` against the next minute boundary.

Join bar and L2 rows on UTC minute with an inner or left join. A left join is useful for measuring missingness, but missing fields must remain missing so the strategy blocks the signal.

## Contract and roll metadata

Keep the following beside the input even though the minimal loader does not consume it: exchange, root, full contract identifier, contract month, feed, timezone conversion version, and roll decision. Run each actual contract separately or join only after signals/P&L are computed. Never attach one contract's depth to another contract's price.

Use a predeclared liquidity roll, such as switching when the next contract's sustained volume exceeds the front contract. Do not choose roll dates after inspecting strategy P&L.

## Trading date and daily true range

Bars are grouped into CME trading dates: an ET timestamp at or after 17:00 belongs to the **next** trading date, so the 18:00 ET Globex reopen opens the following day. Supply full-session bars (including overnight) when GTH windows are enabled; RTH-only data remains valid for RTH-only configurations.

The Python engine forms the daily true range from **all bars in each trading date** (the full Globex day) and uses the median of the previous 20 trading dates. This matches provider-defined `DAY1` futures history used by the live strategy. For exact reconciliation, compute the research median from the same dataset and set `Daily ATR override` in the live strategy if the provider boundary still differs.

## Optional external regime

The optional daily regime CSV has:

```text
session_date,direction,risk_multiplier,expires_utc
2026-07-20,both,0.50,2026-07-20T16:00:00Z
```

Allowed directions are `long`, `short`, `both`, or `none`; the multiplier is clamped to `[0, 1]`. This interface is for a slow, independently validated regime source. It is not a substitute for CME depth.

