# RG-VRP-L2: Schwab Level-3 Income Strategy Engine

Research-backed, algorithmically orchestrated options income system for **Charles Schwab** accounts (~$20k, Level 3, Level 2 book data).

## Strategy at a glance

**RG-VRP-L2** = **R**egime-**G**ated **V**ariance **R**isk **P**remium harvesting with **L**evel-**2** microstructure entry timing.

| Layer | Source | Role |
|-------|--------|------|
| VRP filter | Carr & Wu (2009) | Only sell vol when IV ≫ realized |
| Regime gate | Hamilton (1989) | Halt new shorts in turbulent states |
| Strike skew | Avellaneda & Stoikov (2008) | Inventory-aware delta targeting |
| Greek limits | Stoikov & Saglam (2009) | Portfolio vega/gamma caps |
| Correlation tilt | Driessen et al. (2009) | Prefer index vs single-name credits |
| Entry timing | L2 OBI | Schwab `NASDAQ_BOOK` / `OPTIONS_BOOK` |
| Sizing | Kelly (1956), ¼-Kelly | Defined-risk contract count |

Full design: [docs/STRATEGY.md](docs/STRATEGY.md)

## Quick start

```bash
pip install -r requirements.txt
export PYTHONPATH=src

# Dry-run signals (no Schwab credentials needed)
python scripts/run_signals.py --symbol SPY

# Simple regime+VRP backtest proxy
python scripts/run_signals.py --symbol SPY --backtest
```

## Live Schwab setup

1. Register app at [Schwab Developer Portal](https://developer.schwab.com/) (approval may take days).
2. `pip install schwab-py`
3. Set environment variables:

```bash
export SCHWAB_APP_KEY=...
export SCHWAB_APP_SECRET=...
export SCHWAB_CALLBACK_URL=https://127.0.0.1:8182
export SCHWAB_ACCOUNT_HASH=...
```

4. Run orchestrator with `dry_run=False` only after internal backtesting.

## Project layout

```
config/default.yaml      # $20k account parameters
docs/STRATEGY.md         # Research + math + allocation
src/rg_vrp_l2/           # Strategy modules
scripts/run_signals.py   # CLI entry point
tests/                   # Unit tests
```

## $600 tool budget

See [docs/STRATEGY.md](docs/STRATEGY.md#600-supplemental-tool-budget). Prioritize a **VPS** for scheduled runs; Schwab API and L2 streams are free with the account.

## Risk

Options trading involves substantial risk. This repository is for engineering and research, not financial advice. Short volatility strategies can incur large losses in regime shifts.

## License

MIT
