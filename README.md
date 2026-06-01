# MGO-VPH — Quantitative Income Strategy for Schwab Level 3

Research-backed, algorithmically orchestrable options income system for ~$20k accounts with Charles Schwab Level 3 approval and Level 2 market data.

## What this is

**MGO-VPH** (Microstructure-Gated Optimized Variance Premium Harvester) combines:

- **Variance risk premium** harvesting (Carr & Wu, 2009)
- **Markov-switching GARCH** regime filter (Klaassen, 2002)
- **Order flow imbalance** entry gate from L2 (Cont, Kukanov & Stoikov, 2014)
- **Heston** wing calibration (Heston, 1993)
- **Fractional Kelly** sizing (Thorp, 2006)

Primary structure: **short iron condors** on SPY / QQQ / IWM (defined risk).

## Documentation

| Document | Contents |
|----------|----------|
| [docs/STRATEGY.md](docs/STRATEGY.md) | Full strategy rules, capital budget, schedule |
| [docs/RESEARCH.md](docs/RESEARCH.md) | Papers, equations, bibliography |
| [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md) | Schwab API wiring roadmap |

## Quick start (math core)

```bash
pip install -r requirements.txt
export PYTHONPATH=src
pytest tests/ -q
```

```python
from mgo_vph.signals import evaluate_entry
from mgo_vph.vrp import VRPResult
from mgo_vph.regime import RegimeState
from mgo_vph.ofi import OFIState

signal = evaluate_entry(
    VRPResult(implied_variance=0.22, forecast_realized_variance=0.16, vrp=0.06, vrp_z=1.2),
    RegimeState(p_low_vol=0.72, forecast_vol_5d=0.14, label="low"),
    OFIState(ofi=50, depth=1000, normalized=0.05),
    skew_premium=0.02,
    iv_30d=0.18,
    rv_forecast_5d=0.14,
)
print(signal.allow_entry, signal.reasons)
```

## Status

- [x] Research synthesis and paper mapping  
- [x] Mathematical reference implementation (`src/mgo_vph/`)  
- [ ] Schwab OAuth + live order runner (Phase 2)  
- [ ] Polygon historical backtest (Phase 2)  

## Disclaimer

This repository is for education and systematic trading research. Options involve substantial risk. Past simulated performance does not guarantee future results. Not investment advice.
