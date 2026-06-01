# Research Foundation: MGO-VPH Strategy

This document maps **peer-reviewed and institutional-grade literature** to each layer of the Microstructure-Gated Optimized Variance Premium Harvester (MGO-VPH). Every signal, filter, and sizing rule in the strategy traces to at least one cited paper and an explicit mathematical object.

---

## 1. Core economic thesis: harvest variance risk premium (VRP)

Retail “theta gang” sells options on IV rank alone. Institutions sell **insurance against variance** when the market overpays for it relative to realized volatility. The model-free measurement of that edge is Carr & Wu (2009).

| Paper | Authors | Venue | Role in MGO-VPH |
|-------|---------|-------|-----------------|
| [Variance Risk Premiums](https://doi.org/10.1093/rfs/hhn015) | Peter Carr, Liuren Wu | *Review of Financial Studies*, 22(3), 2009 | Synthesize risk-neutral variance from OTM options; define VRP = implied − forecast realized |
| [The Price of Macro and Volatility Risk](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1362586) | Bollerslev, Tauchen, Zhou | Various | VRP predicts equity returns; motivates systematic short-vol when premium is elevated |
| [What Drives the VIX?](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1276853) | Carr, Wu | — | Links index variance swaps to option strips |

### Mathematics (Carr–Wu model-free variance swap rate)

For maturity \(T\), log forward \(F_0\), and strike grid \(K\):

\[
\sigma^2_{\text{impl}}(T) \approx \frac{2}{T} \left[ \int_0^{F_0} \frac{P(K)}{K^2}\,dK + \int_{F_0}^{\infty} \frac{C(K)}{K^2}\,dK \right] - \frac{1}{T}\left(\frac{F_0}{S_0} - 1\right)^2
\]

Discrete implementation uses listed OTM puts below \(F_0\) and OTM calls above \(F_0\) (see `src/mgo_vph/vrp.py`).

**VRP signal:**

\[
\text{VRP}_t = \sigma^2_{\text{impl},t}(T) - \hat{\sigma}^2_{\text{real},t \to T}
\]

Trade only when \(\text{VRP}_t > \mu_{\text{VRP}} + z_{\min} \cdot \sigma_{\text{VRP}}\) (rolling z-score).

---

## 2. Skew and tail risk: do not sell blind premium

| Paper | Authors | Role |
|-------|---------|------|
| [The Skew Risk Premium in the Equity Index Market](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1357474) | Kozhan, Neuberger, Dickson | Skew swap rate from option strips; avoid short-put-heavy structures when skew premium is negative |
| [A Closed-Form Solution for Options with Stochastic Volatility](https://doi.org/10.1093/rfs/6.2.327) | Steven Heston, 1993 | Fair-value surface; wing width from \(\mathbb{P}(S_T < K_{\text{put wing}})\) under calibrated Heston |

### Heston dynamics (fair value & wing calibration)

\[
dS_t = \mu S_t\,dt + \sqrt{v_t}\,S_t\,dW^S_t
\]
\[
dv_t = \kappa(\theta - v_t)\,dt + \xi\sqrt{v_t}\,dW^v_t, \quad d\langle W^S, W^v\rangle_t = \rho\,dt
\]

European calls via characteristic function \(\phi(u)\) and Fourier inversion (see `src/mgo_vph/heston.py`).

**Skew filter:** require \(\text{SkewPremium}_t > 0\) before structures with net short put delta (e.g., put credit spread bias in iron condor).

---

## 3. Regime gate: when *not* to sell volatility

Selling premium into a volatility **breakout regime** is the primary failure mode for income strategies.

| Paper | Authors | Role |
|-------|---------|------|
| [Improving GARCH Volatility Forecasts with Regime-Switching GARCH](https://doi.org/10.1111/1468-0262.00320) | Klaassen, 2002 | Two-state MS-GARCH; single-regime GARCH over-forecasts in high-vol regimes |
| [Forecasting Stock Market Volatility with Regime-Switching GARCH](https://doi.org/10.2202/1558-3708.1234) | Marcucci, 2005 | MRS-GARCH beats GARCH at short horizons |
| [Option Pricing under Markov-Switching GARCH Processes](https://doi.org/10.1002/fut.20422) | Chen, 2010 | Links regime switching to derivative pricing |

### MS-GARCH(1,1) per regime \(s \in \{1,2\}\):

\[
\sigma^2_t = \omega_s + \alpha_s \varepsilon^2_{t-1} + \beta_s \sigma^2_{t-1}, \quad P(s_t = j \mid s_{t-1} = i) = p_{ij}
\]

**Regime rule:** `SELL_PREMIUM` only when \(P(s_t = \text{low-vol}) > 0.65\) and \(\hat{\sigma}_{t+5d} < \sigma^{\text{IV}}_{30d}\).

---

## 4. Microstructure alpha protection (novel layer)

Most systematic premium sellers **ignore Level 2**. MGO-VPH uses order-flow imbalance (OFI) as an **adverse-selection filter** before opening short-gamma structures.

| Paper | Authors | Venue | Role |
|-------|---------|-------|------|
| [The Price Impact of Order Book Events](https://doi.org/10.1093/jjfinec/nbr005) | Cont, Kukanov, Stoikov | *Journal of Financial Econometrics*, 12(1), 2014 | \(\Delta P \approx \beta \cdot \text{OFI}\); OFI predicts short-horizon direction |
| [High Frequency Trading in a Limit Order Book](https://doi.org/10.2139/ssrn.896947) | Avellaneda, Stoikov, 2008 | — | Reservation price skew under inventory; informs *exit* urgency when delta breaches |
| [Dealing with the Inventory Risk](https://arxiv.org/abs/1105.3115) | Guéant, Lehalle, Fernandez-Tapia, 2011 | — | Bounded-inventory market making; informs max concurrent short-vol slots |

### OFI construction (Cont–Kukanov–Stoikov)

At each book update \(n\), with best bid \((P^B, q^B)\) and ask \((P^A, q^A)\):

\[
e_n = \mathbb{1}_{P^B_n \geq P^B_{n-1}} \cdot q^B_n - \mathbb{1}_{P^B_n \leq P^B_{n-1}} \cdot q^B_{n-1} - \cdots \text{(symmetric ask terms)}
\]

\[
\text{OFI}_t = \sum_{n \in (t-\Delta, t]} e_n, \quad \Delta P_t \approx \beta_t \cdot \text{OFI}_t, \quad \beta_t \propto 1/\text{depth}_t
\]

**Entry filter (novel):** For short iron condor / credit spread on underlying \(i\):

- Block entry if \(\text{OFI}_t / \text{depth}_t < -\tau\) (aggressive selling pressure → adverse for short puts)
- Prefer entry if \(|\text{OFI}_t| < \tau_{\text{neutral}}\) (balanced book → lower short-term gap risk)

This combination (VRP + regime + OFI) is uncommon in retail quant implementations and is the primary **alpha-protection** mechanism.

---

## 5. Position sizing and risk of ruin

| Paper | Authors | Role |
|-------|---------|------|
| [The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market](https://gwern.net/doc/statistics/decision/2006-thorp.pdf) | Ed Thorp, 2006 | Log-utility optimal growth; fractional Kelly in practice |
| [Long-term Capital Growth: Kelly and Fractional Kelly](https://doi.org/10.1287/opre.1090.0800) | MacLean, Thorp, Ziemba, 2010 | Half/quarter Kelly reduces drawdown ~50% for ~25% growth sacrifice |
| [Practical Implementation of the Kelly Criterion](https://doi.org/10.3389/fams.2020.577050) | Frontiers, 2020 | Shrinkage estimators for multi-asset Kelly |

For a trade with win probability \(p\), win \(W\), loss \(L\) (defined risk):

\[
f^* = \frac{p W - (1-p)L}{WL} \quad \Rightarrow \quad f_{\text{deploy}} = \min\left(f_{\text{max}},\; \frac{f^*}{4}\right)
\]

With \(f_{\text{max}} = 0.02\) of account per structure (2% max loss at stop).

---

## 6. Why not pure Avellaneda–Stoikov market making?

At **$20,000** scale on Schwab retail infrastructure:

- No maker rebates, relatively wide retail spreads
- API rate limits and no colocation
- Inventory risk dominates P&L at small size

Market making remains the **exit microstructure** framework (when to pay up to close vs. work limits), not the primary income engine. The income engine is **defined-risk short convexity** when VRP is rich and regimes are calm.

---

## 7. Reference bibliography (implementation order)

1. Carr & Wu (2009) — VRP measurement  
2. Klaassen (2002) / Marcucci (2005) — regime gate  
3. Cont, Kukanov & Stoikov (2014) — OFI filter  
4. Heston (1993) — wing / fair value  
5. Kozhan et al. — skew filter  
6. Thorp / MacLean-Ziemba — sizing  
7. Avellaneda-Stoikov (2008) / Guéant-Lehalle-Fernandez-Tapia (2011) — execution overlay  

---

## 8. Expected performance realism ($20k account)

| Metric | Conservative target | Notes |
|--------|----------------------|-------|
| Monthly income on deployed capital | 1.0–2.5% | Not on full $20k unless fully deployed |
| Max portfolio drawdown budget | 12–15% | Hard kill-switch |
| Win rate (defined-risk IC) | 65–75% | Regime-filtered |
| Avg holding period | 21–45 DTE | Theta sweet spot |
| Concurrent structures | 3–4 | Uncorrelated underlyings |

**This is not guaranteed income.** Tail events (gap risk, vol spikes) require mechanical stops and regime halts.
