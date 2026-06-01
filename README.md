# Quant Income Strategy

Research-backed tooling for selecting conservative, defined-risk options income
trades that can be orchestrated through Charles Schwab's option-chain, streaming,
and order APIs.

The selected method is a **regime-adaptive defined-risk variance risk premium
strategy**: sell liquid ETF/index-proxy credit spreads or iron condors only when
implied volatility is rich versus realized volatility, then size positions with
fractional Kelly, CVaR, and hard account-risk caps.

See [`docs/research_strategy.md`](docs/research_strategy.md) for the research
rationale, mathematical concepts, Schwab orchestration plan, and budget guidance.

## Quick start

```bash
python -m pytest
python -m quant_income_strategy.cli examples/sample_snapshot.json
```

The CLI expects normalized option-chain snapshots. In production, populate those
snapshots from Schwab option chains plus `LEVELONE_OPTIONS` / `OPTIONS_BOOK`
streaming data.

## What the engine does

- filters out high-risk regimes and event-risk windows,
- finds 21-60 DTE short strikes around 0.10-0.22 delta,
- buys wings to keep every trade defined-risk,
- requires implied volatility to exceed forecast realized volatility,
- rejects illiquid/wide option quotes,
- estimates expected value, CVaR, and fractional Kelly sizing,
- emits Schwab-compatible `NET_CREDIT` multi-leg order payloads.

## Safety note

This project is educational/research tooling, not financial advice. Short options
can lose money quickly, and consistent income is not guaranteed. Paper trade,
backtest on historical options data, and keep broker/risk controls outside the
strategy engine before using real capital.
