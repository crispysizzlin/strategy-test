# Data contract

The research engine accepts UTF-8 CSV with one row per one-minute bar. Timestamps identify the **start** of the minute and must be unique, strictly increasing, and timezone-aware. UTC with a `Z` suffix is preferred.

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

`trade_value` must be populated for every bar in a session or omitted from every bar in that session. When omitted, the engine explicitly reports and uses `(high + low + close) / 3 * volume` as an approximation. Locked validation should use real `trade_value`; the approximation is only for a bar-data baseline.

With `require_l2=true`, all six L2 components plus `l2_age_ms` must be present on a decision bar. The default gate rejects a spread above one tick or book age above 750 ms. Missing or stale depth is a no-trade, and L2 fields must never be forward-filled.

See `examples/minute_bars_schema.csv` for a syntactic example. Its row is illustrative, not market evidence.

## Raw Quantower recorder

With `RecordLevel2=true`, the strategy writes a throttled snapshot approximately every 200 ms when book updates arrive:

```text
received_utc,best_bid,best_ask,best_bid_size,best_ask_size,depth_imbalance,ofi_norm,trade_delta_norm,microprice_ticks,composite,signed_persistence,spread_ticks
```

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

## Daily true range

The Python engine forms true range from completed 09:30–15:55 ET bars and uses the median of the previous 20 sessions. Quantower's provider-defined `DAY1` history may use a different session boundary. For exact reconciliation, calculate the research value from the same RTH data and set `Daily ATR override` in the live strategy.

## Optional external regime

The optional daily regime CSV has:

```text
session_date,direction,risk_multiplier,expires_utc
2026-07-20,both,0.50,2026-07-20T16:00:00Z
```

Allowed directions are `long`, `short`, `both`, or `none`; the multiplier is clamped to `[0, 1]`. This interface is for a slow, independently validated regime source. It is not a substitute for CME depth.
