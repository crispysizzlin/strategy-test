# QIST — Quant Income Strategy Toolkit

A research-grade, **conditional Variance-Risk-Premium (VRP) harvesting** engine
with a **financed convex tail overlay**, designed to be algorithmically
orchestrated through the **Charles Schwab Trader API** for a small (~$20k),
**Level-3** options-enabled account.

> Read **[`STRATEGY.md`](STRATEGY.md)** for the full methodology, the academic
> references, and the advanced mathematics behind every module.
>
> **This is software/quant research, not financial advice.** Short-vol income
> strategies can lose money, especially in tail events. The whole point of this
> design is to *condition* the trade and *buy back convexity*. Validate on real
> option data and paper-trade before risking capital.

## What it does

1. Forecasts **realized volatility** (HAR-RV primary, GARCH ensemble) and
   compares it to **implied volatility** to estimate the **forward VRP**.
2. Detects the **volatility regime** with a from-scratch Gaussian **HMM**.
3. Harvests premium **only when** the VRP is positive/rich **and** the regime is
   calm **and** the term structure is not in backwardation.
4. Builds **defined-risk** structures (iron condors, credit spreads, broken-wing
   butterflies) by target delta; uses **Level-3** jade lizards / short strangles
   only in the calmest, richest conditions.
5. Sizes with **fractional Kelly ∩ per-trade max-loss ∩ portfolio CVaR(99%)**.
6. **Recycles a slice of every credit into far-OTM put convexity** so a crash
   pays the book back (the alpha-protecting overlay).
7. Places **cost-aware limit orders** using the **Level-2** book
   (Avellaneda-Stoikov) and schedules unwinds with **Almgren-Chriss**.

## Install

```bash
python3 -m pip install -r requirements.txt
# or, as a package (editable):
python3 -m pip install -e .
```

## Quickstart (no credentials needed — uses the simulator)

```bash
# Write a default config you can edit
PYTHONPATH=src python3 -m qist.cli init --out config.yaml

# Print today's signal/plan on a simulated market
PYTHONPATH=src python3 -m qist.cli signal --config config.yaml

# Run an end-to-end backtest on a Heston-lite path
PYTHONPATH=src python3 -m qist.cli backtest --config config.yaml --days 700
```

If installed as a package, drop `PYTHONPATH=src python3 -m qist.cli` for `qist`.

## Tests

```bash
python3 -m pytest -q        # 46 unit tests across pricing, vol models, VRP,
                            # regime, sizing, execution, structures, brokers, engine
```

## Project layout

```
src/qist/
  brokers/     Schwab REST + WebSocket (Level 1/2) clients and order models
  data/        live + synthetic data providers, option-chain parsing
  models/      black_scholes, realized_vol, har_rv, garch, vrp, regime, ou_meanrev
  sizing/      kelly, cvar
  execution/   avellaneda_stoikov, almgren_chriss, order building
  strategy/    signals, structures, tail_overlay, risk, engine
  backtest/    event-driven backtester (reuses the production decision path)
  cli.py
docs/          research references, budget plan, risk-management rulebook
tests/         unit tests
STRATEGY.md    the research deliverable
```

## Connecting to Schwab (live/paper)

1. Register an app at [developer.schwab.com](https://developer.schwab.com/),
   note the **app key/secret** and **redirect URI**.
2. Provide credentials via environment variables (never commit them):
   `SCHWAB_APP_KEY`, `SCHWAB_APP_SECRET`, `SCHWAB_REDIRECT_URI`.
3. Run the OAuth2 flow in `qist.brokers.schwab_client.SchwabClient`, persist the
   refresh token outside the repo.
4. Backtest on real option data → paper trade → go live tiny.

See [`STRATEGY.md`](STRATEGY.md) §8 for the full operational path.
