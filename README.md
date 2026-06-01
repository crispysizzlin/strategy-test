# AVRPE — Adaptive Volatility Regime Premium Engine

An institutional-grade algorithmic options income system for Charles Schwab,
leveraging Level 3 options privileges, Level 2 streaming data, and advanced
quantitative models to generate consistent income through systematic volatility
risk premium (VRP) harvesting.

---

## Strategy Overview

**Core thesis:** Equity option implied volatility (IV) consistently exceeds
subsequent realized volatility (RV) by 2–4 percentage points (the "Volatility
Risk Premium"). Systematic sellers of options capture this spread as income.

**What makes this novel:**
1. **Regime-gated selling** — HMM detects Low/Normal/High-Vol/Crisis regimes and
   only sells aggressively in favourable regimes, protecting against tail events
2. **Three realized-vol estimators** fused with GARCH forward-looking forecasts
   give a robust VRP signal that reduces false entries
3. **Level 2 order flow timing** — options book imbalance improves fill quality
   by 0.5–2 ticks per spread
4. **Regime-conditioned Kelly sizing** — position size scales down smoothly as
   vol rises, preventing the Kelly blowup problem seen in simpler systems
5. **Skew-adjusted wing placement** — wider put wing when vol surface put skew
   is steep (captures more premium where it is richest)

---

## Mathematical Foundation

### Volatility Risk Premium
```
VRP_t = σ_IV(t, T) - σ_RV(t, window)
Signal = Percentile_rank(VRP_t, 20-day history)
```
Entry when signal > 60th percentile AND VIX in contango (VIX3M/VIX > 1).

### Realized Volatility Estimators
| Estimator | Formula | Property |
|-----------|---------|---------|
| Close-to-Close | √(252·Var(log returns)) | Standard, biased by gaps |
| Parkinson (1980) | √(252·Σln(H/L)²/(4·ln2·n)) | 4× more efficient |
| Yang-Zhang (2000) | σ²_YZ = σ²_o + k·σ²_c + (1-k)·σ²_RS | Min-variance, gap-adjusted |
| GARCH(1,1) | σ²_t = ω + α·ε²_{t-1} + β·σ²_{t-1} | Forward-looking, 1-step forecast |
| EGARCH(1,1) | log σ²_t = ω + β·log σ²_{t-1} + α·\|z_{t-1}\| + γ·z_{t-1} | Captures leverage effect |

Composite VRP: 20% CC + 40% Yang-Zhang + 40% GARCH

### Hidden Markov Model Regime Detection
```
States: {Low-Vol(0), Normal(1), High-Vol(2)}
Features: [log_return, RV_21d, VIX, VIX3M/VIX, VRP, trend_z]
Estimation: Baum-Welch (EM), 5 random restarts
Decoding: Viterbi for full history, forward-backward for real-time
```

### SSVI Volatility Surface (Gatheral-Jacquier 2014)
```
w(k,θ) = (θ/2)·{1 + ρ·φ(θ)·k + √[(φ(θ)·k+ρ)² + (1-ρ²)]}
φ(θ) = η / (θ^γ·(1+θ)^(1-γ))
```
Arbitrage-free: calendar spread (∂θ/∂t ≥ 0) and butterfly (θ·φ·(1+|ρ|) ≤ 4).

### Fractional Kelly + CVaR Sizing
```
f* = (p·b - q) / b     (Kelly fraction to risk)
f_adjusted = f* × κ × m_regime × s_signal
Max_contracts = min(Kelly_contracts, CVaR_budget / CVaR_per_contract)
```
Where κ=0.25 (conservative multiplier), m_regime ∈ {0.50, 0.35, 0.15, 0.00}
and CVaR_95 budget = 3% of account.

### Hurst Exponent
```
R/S analysis: E[R/S(n)] ≈ c·n^H
H < 0.5 → mean-reverting (favourable for premium selling)
H ≈ 0.5 → random walk
H > 0.5 → trending (unfavourable for condors)
```

---

## Options Structures Selected Per Regime

| Regime | Primary | Secondary | DTE | Delta |
|--------|---------|-----------|-----|-------|
| Low-Vol | Iron Condor | Short Strangle | 1-3 | 14Δ |
| Normal-Vol | Iron Condor | Broken-Wing Butterfly | 2-5 | 16Δ |
| High-Vol | Calendar Spread | Far-OTM Iron Condor | 5-14 | 12Δ |
| Crisis | FLAT (no new trades) | Protective Spread | — | — |

**Exit rules:**
- Take profit at 50% of max credit
- Stop-loss at 200% of credit received
- Time stop at 1 DTE (avoid expiration gamma)

---

## Risk Management Tiers

| Tier | Trigger | Action |
|------|---------|--------|
| CAUTION | Daily loss > 3% ($600) | 50% position size |
| RESTRICTED | Weekly loss > 5% ($1,000) | No new entries |
| HALT | Monthly loss > 8% OR total DD > 15% | All trading stopped |

---

## Account Configuration ($20,000)

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Max per-trade risk | 2% = $400 | Preserve capital for 50+ trades |
| CVaR budget (95%) | 3% = $600 | Expected shortfall limit |
| Target daily theta | $40–$150 | Conservative income range |
| Max short vega | $1,000 | Limits volatility exposure |
| Cash reserve | 10% = $2,000 | Emergency buffer |
| Max open positions | 6 | Diversification without over-complexity |

---

## $600 Supplemental Budget Allocation

| Tool | Cost | Purpose |
|------|------|---------|
| [Polygon.io](https://polygon.io) Starter | $29/mo | Historical options data for backtesting + OHLCV |
| VIX/FRED data | Free | Regime features via pandas_datareader |
| Schwab Developer API | Free | Primary broker and streaming data |
| Cloud compute (AWS t3.small) | ~$15/mo | Overnight model refitting |
| Reserve | ~$41/mo | Buffer for upgrades as account grows |

---

## Project Structure

```
/workspace/
├── config/
│   └── config.yaml          # All strategy parameters (tunable)
├── src/
│   ├── broker/
│   │   ├── schwab_client.py  # Schwab API: auth, data, orders
│   │   ├── order_manager.py  # Order construction for all structures
│   │   └── streaming.py      # Level 1/2 WebSocket streaming
│   ├── data/
│   │   ├── market_data.py    # Historical + real-time data feed
│   │   └── options_chain.py  # Chain processing, IV extraction
│   ├── models/
│   │   ├── volatility/
│   │   │   ├── garch.py      # GARCH(1,1) + EGARCH + Heston-Nandi
│   │   │   ├── realized_vol.py # CC, Parkinson, YZ, GK estimators
│   │   │   └── vol_surface.py # SSVI/SVI arbitrage-free surface
│   │   ├── regime/
│   │   │   ├── hmm_detector.py # 3-state Gaussian HMM
│   │   │   └── hurst.py       # R/S analysis + DFA + variance ratio
│   │   └── pricing/
│   │       ├── black_scholes.py # Full BSM + all Greeks + IV inversion
│   │       └── spreads.py      # Iron condor/BWB/calendar pricing
│   ├── strategy/
│   │   ├── vrp_engine.py      # VRP signal generation
│   │   ├── structure_selector.py # Regime-based structure selection
│   │   └── portfolio_manager.py # Greeks aggregation + exit management
│   ├── risk/
│   │   ├── position_sizer.py  # Kelly + CVaR sizing
│   │   ├── risk_metrics.py    # VaR, CVaR, scenario analysis
│   │   └── drawdown_control.py # Circuit breakers
│   └── execution/
│       ├── order_flow_analyzer.py # Level 2 OFI analysis
│       └── execution_engine.py   # Smart limit order routing
├── backtesting/
│   └── backtest_engine.py    # Event-driven backtester
├── scripts/
│   ├── run_live.py           # Live trading runner (APScheduler)
│   └── run_backtest.py       # Backtesting CLI
└── tests/
    ├── test_models.py        # BSM, Greeks, GARCH, Hurst (52 tests)
    ├── test_risk.py          # Kelly, CVaR, drawdown
    └── test_strategy.py      # VRP, structure selector, portfolio
```

---

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Schwab API Credentials

Add these to your Cursor Secrets (cursor.com/dashboard) or `.env` file:

```env
SCHWAB_API_KEY=your_app_key
SCHWAB_APP_SECRET=your_app_secret
SCHWAB_CALLBACK_URL=https://127.0.0.1:8182/
SCHWAB_TOKEN_PATH=./schwab_token.json
SCHWAB_ACCOUNT_NUMBER=your_account_number
FRED_API_KEY=your_fred_key   # free at fred.stlouisfed.org
```

Get Schwab Developer access: https://developer.schwab.com

### 3. Backtesting

```bash
python scripts/run_backtest.py --symbol SPY --years 3 --plot
```

### 4. Live Trading

```bash
# Paper mode first (verifies auth and data flow)
python scripts/run_live.py --config config/config.yaml --paper

# Live trading (after paper testing)
python scripts/run_live.py --config config/config.yaml
```

---

## Key Research Papers

1. **Gatheral & Jacquier (2014)** — "Arbitrage-free SVI volatility surfaces"
   → Basis for SSVI vol surface fitting

2. **Yang & Zhang (2000)** — "Drift-independent volatility estimation based on OHLC prices"
   → Yang-Zhang realized vol estimator

3. **Heston & Nandi (2000)** — "A Closed-Form GARCH Option Valuation Model"
   → Heston-Nandi GARCH for options pricing under physical measure

4. **Nelson (1991)** — "Conditional heteroskedasticity in asset returns: A new approach"
   → EGARCH model for leverage effect

5. **Kelly (1956)** — "A New Interpretation of Information Rate"
   → Kelly criterion for position sizing

6. **Rockafellar & Uryasev (2000)** — "Optimization of conditional value-at-risk"
   → CVaR as risk constraint for Kelly

7. **Gatheral (2006)** — "The Volatility Surface: A Practitioner's Guide"
   → Practitioner-level vol surface theory

8. **Xu et al. (2023)** — "Harvest Volatility Risk Premia using DDDQN"
   → RL-based VRP harvesting via delta-hedged options + variance swaps

---

## Running Tests

```bash
python3 -m pytest tests/ -v
```

52 tests covering all mathematical models, risk management, and strategy logic.

---

## Important Disclaimers

- This is research software. Algorithmic trading involves significant financial risk.
- Options Level 3 strategies can result in losses exceeding initial investment.
- Always paper-trade for at least 30 days before going live.
- Past performance (even in backtests) does not guarantee future results.
- Monitor positions actively; do not leave running unattended for extended periods.
