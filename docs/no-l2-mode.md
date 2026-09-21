# Triple ORB without Level 2

The MNQ default now trades price/volume ORB retests across Globex reopen, London,
and New York. This is a changed strategy specification and needs its own historical
and forward validation. No real-data profitability result is included.

## Inputs and behavior

| Function | No-L2 behavior |
|---|---|
| Signal | Completed-minute breakout, relative volume, retest, session VWAP alignment and slope |
| Range regime | Existing OR width / previous-session true-range median |
| Entry | Market entry after the completed retest; next-bar open plus modeled slippage in Python |
| Depth confirmation | Disabled: no imbalance, OFI, trade delta, microprice, persistence, or book-age requirement |
| Wall entries | Disabled whenever L2 is off, even if a saved wall setting is still on |
| Quantower depth subscription/recording | Neither is started while UseLevel2 is false |
| Live spread protection | Ordinary Level 1 best bid/ask, valid positive uncrossed prices, per-window spread cap, quote age at most 2 seconds by default |
| Stops and targets | Existing structural stop, bounded round-number adjustments, 1.75R target before adjustment |
| Risk | Existing sizing, shared daily loss limit, prop-floor controls, time stops and flattening |

No new directional filter substitutes for L2. The existing thresholds and GTH
half-risk sizing are retained. Removing a filter may increase trades and may help
or hurt expectancy; test that on actual data.

The live L1 spread/freshness check runs before entry, including legacy wall-market
fallbacks. It is not a directional signal and does not inspect displayed depth or
queue sizes. Quantower's separate NewQuote and NewLevel2 events and cached Bid,
Ask, and QuoteDateTime are documented in the official
[Symbol API](https://api.quantower.com/docs/TradingPlatform.BusinessLayer.Symbol.html).
Trade-feed watchdogs and existing protective orders remain active. Disable the
wall-clock watchdog only for replay; the quote-age check then uses market time.

## Quantower

Build src/Quantower.AdaptiveOrb/AdaptiveOrbStrategy.cs against the installed
Quantower SDK. The display name is now
**Adaptive ORB multi-session (price/volume default)**.

New instances default to UseLevel2=false, UseWallEntries=false, RecordLevel2=false.
**Saved instances may preserve old inputs:** explicitly set "Use Level 2
confirmation" off. Wall-entry and recording inputs are then ignored. Keep the
wall-clock watchdog on for live/paper operation, configure actual costs and the
current account liquidation floor, and use a dedicated simulator account first.

The adapter has not been compiled against the native Quantower SDK in this Linux
environment. The Python tests do not establish native API or broker behavior.
Before collecting paper results, confirm the strategy starts on an L1/trades-only
feed, a missing/wide/stale L1 quote blocks new orders, and brackets/flattening work.

## Python backtest

Run from the repository root with Python 3.11 or later:

```bash
PYTHONPATH=research python3 -m adaptive_orb.cli backtest \
  --data /path/to/mnq_full_session_minutes.csv \
  --config config/mnq_lucid_pro_100k.json \
  --no-l2 \
  --output artifacts/mnq_no_l2
```

The supplied MNQ config already disables L2. The explicit --no-l2 switch also
overrides older configurations and disables wall entries. The input requires
timestamp, open, high, low, close, volume. Use minute-start UTC timestamps and full
Globex coverage with actual contract/roll metadata. Provide trade_value when
available; otherwise VWAP uses the documented bar-typical-price approximation.
The first 20 trading dates seed the median true-range baseline. Missing data,
holidays, partial sessions, roll transitions and input provenance still require
review before reporting market performance.

summary.json includes the effective configuration, signal mode, per-window
results, per-window slippage assumptions and corrected double-cost stress.
Window summaries attribute one combined account run, including its daily lockout;
they are not isolated runs. Drawdown in these summaries is sampled at day close,
not intraday equity. Plain OHLCV cannot reproduce the live L1 quote gate, order
latency, rejected orders, queue priority or actual tick VWAP. The report explicitly
marks the live L1 guard as not simulated.

## Backtest fixes included

- Double-cost stress now doubles explicit session overrides: GTH 2 to 4 ticks per
  side, NY 1 to 2, with doubled per-side commission in all windows.
- A long stop gapped through fills at min(stop, open) minus adverse slippage;
  a short stop uses max(stop, open) plus adverse slippage.
- No-L2 outcomes ignore depth/wall values even when these columns are present.

The optional legacy L2/wall mode is retained for separate experiments. Its OHLC
entry-minute limit/target ordering ambiguity is not resolved by this change;
no-L2 mode uses market entries and never takes that path. Do not treat legacy
wall-mode results as execution validation without ordered ticks or a separate
conservative ambiguity fix.

## Validation performed

42 deterministic Python tests pass, including new checks for all three windows
trading without depth, contradictory depth having no effect, OHLCV-only CLI input,
GTH cost stress, and long/short gap-through-stop execution. These are synthetic
fixtures and establish software behavior only. Historical MNQ data is still
needed to compare profitability and consistency between sessions.
