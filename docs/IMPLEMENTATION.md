# Schwab Implementation Roadmap

## Prerequisites

1. Schwab Developer Portal app (Trader + Market Data products)
2. Level 3 options approval on target account
3. OAuth callback URL on VPS or localhost tunnel for initial auth

## Recommended stack

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Language | Python 3.11+ | `schwab-py` ecosystem, numpy/scipy |
| API client | [schwab-py](https://github.com/alexgolec/schwab-py) | Mature order builder, streaming |
| Scheduler | APScheduler or systemd timers | Daily signal windows |
| Secrets | `.env` + VPS env vars | Never commit tokens |
| Backtest data | Polygon.io options | Schwab lacks historical chains |
| Monitoring | Telegram bot + Grafana | Free alert path |

## Module map

```
src/mgo_vph/
  vrp.py         → Carr-Wu implied var from chain snapshot
  regime.py      → MS-GARCH P(low vol)
  ofi.py         → L2 stream → OFI normalized
  heston.py      → Wing strikes, fair value check
  signals.py     → Composite ENTRY boolean
  sizing.py      → Quarter-Kelly contracts
  structures.py  → IronCondorSpec for order builder

schwab_runner/   (Phase 2 — not yet wired)
  auth.py
  stream_l2.py
  orders.py
  main_loop.py
```

## Order flow (iron condor)

1. Fetch chain for target expiration  
2. Run `evaluate_entry()` on live signals  
3. `build_iron_condor()` → `IronCondorSpec`  
4. Map legs to Schwab option symbols (OCC format)  
5. `client.preview_order()` → verify margin  
6. `client.place_order()` with `ComplexOrderStrategyType.IRON_CONDOR`  
7. Store order id; poll until filled  

## Streaming OFI

Subscribe via schwab-py stream to `LEVEL_TWO_EQUITIES` for SPY/QQQ/IWM.

On each book update:

```python
acc.update(BookSnapshot(bid, bid_size, ask, ask_size))
state = acc.state(current_snap)
```

Reset accumulator at 09:45 ET daily after opening auction stabilizes.

## Testing without live capital

1. **Unit tests:** `pytest tests/` (math core)  
2. **Replay:** Record L2 + chain snapshots to parquet; replay signals  
3. **Polygon backtest:** Simulate IC entries on historical VRP proxy  
4. **Live最小:** 1-lot SPY only after 2 weeks replay match  

## Environment variables

```
SCHWAB_APP_KEY=
SCHWAB_APP_SECRET=
SCHWAB_CALLBACK_URL=
SCHWAB_ACCOUNT_HASH=
POLYGON_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
ACCOUNT_EQUITY=20000
MAX_RISK_PCT=0.02
```

## Rate limits and safety

- Cache option chains 60 seconds unless entry window  
- Max 10 order submissions per day  
- Duplicate signal suppression: 1 entry per symbol per 5 days  
- Manual override file `HALT_TRADING` on VPS stops loop  
