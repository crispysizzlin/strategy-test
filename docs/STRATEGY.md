# RG-VRP-L2: Regime-Gated Variance Risk Premium with Level-2 Microstructure Timing

## Executive summary

For a **$20,000** Schwab account with **Level 3** options approval, the recommended algorithmic approach is **not** a plain iron-condor bot (crowded, fragile in correlation spikes). The best fit is a **regime-gated, VRP-filtered credit engine** that:

1. Harvests the **variance risk premium** only when statistically rich (Carr & Wu, 2009).
2. **Stops selling volatility** when a Hamilton-style regime filter assigns high probability to a turbulent state (Hamilton, 1989).
3. Skews strikes and size using **inventory-aware reservation pricing** from market-making theory (Avellaneda & Stoikov, 2008; Stoikov & Saglam, 2009).
4. Times entries using **Level 2 order-book imbalance** on Schwab’s `NASDAQ_BOOK` / `OPTIONS_BOOK` streams (microstructure edge less common at retail).
5. Sizes positions with **quarter- to half-Kelly** caps tied to defined-risk max loss (Kelly, 1956; Thorp extensions).

**Alpha-protection angle:** Few retail systems combine **VRP + HMM regime gating + A-S inventory skew + L2 OBI timing** in one orchestrator. Each layer is published; the **composition and parameterization for a $20k Schwab L3 book** is the proprietary layer.

Target profile (realistic, not guaranteed): **0.8–1.5% monthly** on deployed premium capital in calm regimes, with **hard drawdown brakes** and flat exposure when `P(turbulent) > 0.60`.

---

## Why this beats “simple income” strategies

| Approach | Issue at $20k retail |
|----------|----------------------|
| Wheel / CSP only | Concentrated single-name risk; assignment eats margin |
| Static iron condors | No regime filter → blow-ups in vol spikes (Mar 2020, Aug 2024) |
| 0DTE scalping | Needs speed, commissions, PDT; Schwab API rate limits |
| Full dispersion (institutional) | Needs multi-leg vega across 15+ names; capital & frictions (Driessen et al., 2009) |

**RG-VRP-L2** uses **defined-risk** structures only (L3-allowed): short put spreads, iron condors, iron butterflies on **SPY / QQQ / IWM** (liquid, tight markets, full L2).

---

## Required research foundations

### 1. Variance risk premium — Carr & Wu (2009)

**Paper:** Peter Carr & Liuren Wu, *“Variance Risk Premiums,”* Review of Financial Studies, 22(3), 1311–1341.

**Core idea:** Investors pay more for implied variance than realized variance on average. The premium can be synthesized model-free from OTM options (variance swap replication).

**Implementation proxy (daily):**

\[
\text{VRP}_t = \text{IV}_{30,t} - \text{RV}_{20,t}
\]

where \(\text{IV}_{30}\) is ATM 30-day implied vol from the chain and \(\text{RV}_{20}\) is 20-day close-to-close realized vol annualized.

**Trade rule:** Open new short-volatility structures only if \(\text{VRP}_t > \theta_{\text{vrp}}\) (default **2 vol points**) **and** IV rank (52-week) **> 30%**.

### 2. Regime switching — Hamilton (1989)

**Paper:** James D. Hamilton, *“A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle,”* Econometrica, 57(2), 357–384.

**Core idea:** Returns follow **latent Markov states** (calm vs turbulent) with different means/variances. The **Hamilton filter** yields **filtered** (causal) state probabilities using data only up to \(t\).

**Implementation:** 2-state Gaussian HMM on daily log returns of SPY (features: return, 5d vol, VIX change if available).

| \(P(\text{Turbulent}_t)\) | Action |
|---------------------------|--------|
| ≤ 0.40 | Full sizing (subject to Kelly cap) |
| 0.40 – 0.60 | Half sizing; no new iron flies |
| > 0.60 | **No new short vol**; manage/close losers |
| > 0.80 | Flatten short-vol book |

### 3. Inventory-aware quoting — Avellaneda & Stoikov (2008)

**Paper:** Marco Avellaneda & Sasha Stoikov, *“High-frequency trading in a limit order book,”* Quantitative Finance, 8(3), 217–224.

**Reservation price (adapted for strike selection):**

\[
r_t = S_t - q_t \cdot \gamma \cdot \sigma^2 \cdot \tau
\]

- \(S_t\): underlying mid  
- \(q_t\): normalized portfolio delta inventory \([-1,1]\)  
- \(\gamma\): risk aversion (calibrated 0.1–0.5 for retail)  
- \(\sigma\): realized vol  
- \(\tau\): DTE / 365  

**Effect:** When net short delta (typical after put spreads), **shift short strikes further OTM** (lower assignment risk). When net long delta, allow slightly closer strikes for premium.

### 4. Options inventory & Greeks — Stoikov & Saglam (2009)

**Paper:** Sasha Stoikov & Mehmet Sağlam, *“Option market making under inventory risk.”*

**Core idea:** In incomplete markets, optimal quotes depend on **net Vega and Gamma**, not Delta alone.

**Portfolio limits ($20k book):**

| Greek | Limit |
|-------|-------|
| Net delta | ± 0.15 × account / SPY price (in share equivalents) |
| Net vega | ≤ $80 per 1% IV move |
| Net gamma | ≤ $40 per 1% move² (short gamma capped) |
| Max concurrent structures | 3 |
| Risk per trade | ≤ 5% of equity (\$1,000 max loss) |

### 5. Correlation wedge (optional tilt) — Driessen, Maenhout & Vilkov (2009)

**Paper:** *“The Price of Correlation Risk: Evidence from Equity Options,”* Journal of Finance, 64(3), 1377–1406.

**Retail-scale proxy:** Compare SPY ATM IV to cap-weighted IV of top-3 holdings (e.g. AAPL, MSFT, NVDA). When **index IV > component IV + wedge**, favor **index iron condors** over single-name credits (correlation risk premium embedded in index).

---

## Advanced mathematics used in code

| Concept | Role |
|---------|------|
| **Hamilton filter / HMM EM** | Causal regime probabilities |
| **Model-free VRP** | Entry filter for selling vol |
| **Stochastic control (A-S)** | Strike skew from inventory |
| **Fractional Kelly** | \(f = \frac{1}{4} f^*\), \(f^* = \frac{p(b+1)-1}{b}\) |
| **Order book imbalance (L2)** | \(\text{OBI} = \frac{\sum bid - \sum ask}{\sum bid + \sum ask}\) on top 5 levels |
| **Vol term structure slope** | \(\text{IV}_{7d} - \text{IV}_{30d}\); prefer short structures when front > back |

---

## Trade structures (Level 3, defined risk)

### Primary: Iron condor (SPY/QQQ/IWM)

- **DTE:** 21–35  
- **Short deltas:** 0.12–0.18 (adjusted by inventory skew)  
- **Wing width:** $2–5 depending on underlying  
- **Profit target:** 50% of max credit  
- **Stop:** 2× credit or breach of short strike − 0.5 ATR  

### Secondary: Put credit spread (bullish calm regime)

- When \(P(\text{calm}) > 0.6\) and trend filter (price > 50d SMA)  
- Short put 0.15–0.20 delta, long put 5-wide  

### Tertiary: Iron butterfly (term-structure carry)

- When \(\text{IV}_{7d} - \text{IV}_{30d} > 1.5\) vol pts **and** VRP rich  
- ATM short straddle body with 5-wide wings — **half Kelly only**

---

## Level 2 microstructure layer (Schwab)

Subscribe via streaming API (`schwab-py`):

- `NASDAQ_BOOK` / `NYSE_BOOK` for underlying  
- `OPTIONS_BOOK` for short-leg series  

**Entry gate:** Place limit order at mid only if:

1. Short-leg spread < 8% of mid  
2. \(\text{OBI}\) between −0.15 and +0.15 (avoid leaning into aggressive flow)  
3. Book depth at short strike ≥ 20 contracts aggregate (top 3 levels)

This reduces adverse selection vs blind market orders.

---

## $20,000 capital allocation

| Bucket | % | $ | Purpose |
|--------|---|---|---------|
| Short-vol structures | 70% | 14,000 | 2–3 concurrent defined-risk trades |
| Cash / SPY buffer | 20% | 4,000 | Assignment, margin buffer |
| Hedge reserve | 10% | 2,000 | Long OTM puts when \(P(\text{turb}) > 0.5\) |

**Notional discipline:** Max **35%** of account in margin requirement at any time.

---

## $600 supplemental tool budget

| Item | Est. cost | Purpose |
|------|-----------|---------|
| VPS (Hetzner/DO) | $6–12/mo | 24/5 orchestration near market hours |
| ORATS or similar (optional) | $0–99/mo | Historical IV/Greeks backtest — start free with Schwab chains |
| Polygon/Massive (optional) | $29/mo | Backup historical bars if needed |
| **Reserve** | ~$400 | Scale data after profitability |

Schwab API + L2 streaming: **$0**. Primary spend: **reliable VPS**, not redundant data feeds initially.

---

## Schwab orchestration architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│ L2 Stream       │────▶│ Signal Engine    │────▶│ Risk Manager    │
│ (BOOK services) │     │ VRP,HMM,Inv,OBI  │     │ Kelly,Greeks    │
└─────────────────┘     └────────┬─────────┘     └────────┬────────┘
                                 │                        │
┌─────────────────┐              ▼                        ▼
│ REST: chains,   │     ┌──────────────────┐     ┌─────────────────┐
│ quotes, acct    │────▶│ Strategy Engine  │────▶│ Order Builder   │
└─────────────────┘     └──────────────────┘     │ (multi-leg L3)  │
                                                   └────────┬────────┘
                                                            ▼
                                                   Schwab placeOrder
```

**API limits:** 120 market-data / 60 trading calls per minute — batch chain pulls, cache IV surfaces 60s.

**Paper trading:** Schwab API is live-only; use internal simulator (`backtest/`) before capital deployment.

---

## Risk disclosures

- Past academic premiums do not guarantee future returns.  
- Short volatility has **negative skew**; regime filters reduce but do not eliminate tail risk.  
- Options involve risk of loss; automation requires monitoring and kill switches.  
- This document is research/engineering, not investment advice.

---

## References

1. Avellaneda, M., & Stoikov, S. (2008). High-frequency trading in a limit order book. *Quantitative Finance*, 8(3), 217–224.  
2. Carr, P., & Wu, L. (2009). Variance risk premiums. *Review of Financial Studies*, 22(3), 1311–1341.  
3. Driessen, J., Maenhout, P. J., & Vilkov, G. (2009). The price of correlation risk: Evidence from equity options. *Journal of Finance*, 64(3), 1377–1406.  
4. Hamilton, J. D. (1989). A new approach to the economic analysis of nonstationary time series and the business cycle. *Econometrica*, 57(2), 357–384.  
5. Kelly, J. L. (1956). A new interpretation of information rate. *Bell System Technical Journal*, 35(4), 917–926.  
6. Stoikov, S., & Sağlam, M. (2009). Option market making under inventory risk. *Mathematical Finance* (working paper / journal version).
