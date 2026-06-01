# QIST — Quant Income Strategy Toolkit

**Objective:** the best *quantitatively defensible* method of generating consistent
income that can be **algorithmically orchestrated inside Charles Schwab** for a
**$20,000, Level-3 options-enabled** account, using Schwab Level 1/Level 2 data
and a **$600 supplemental tooling budget**.

This document is the research deliverable. It (1) explains *why* this method was
chosen over the alternatives, (2) grounds every component in peer-reviewed
research **and** the advanced mathematics that institutional desks use, and
(3) describes the **novel alpha-protecting overlay** that differentiates this
implementation. The code in `src/qist/` implements all of it.

> **Important, read first.** This is software and quantitative research, **not
> financial advice or a performance guarantee**. Short-volatility income
> strategies have a *negatively skewed* return profile: many small wins and rare
> large losses. The entire design philosophy here is to *condition* the trade so
> the edge is real and to *buy back convexity* so the rare loss cannot end the
> account. You can still lose money. Validate on real historical option data and
> paper-trade before risking capital.

---

## 1. The decision: what is the "best" method here?

"Best" is constrained by the account, so we start from the constraints:

| Constraint | Consequence for strategy design |
|---|---|
| **$20,000 equity** | Below FINRA's **$25,000 Pattern-Day-Trader** floor → at most **3 day trades / 5 business days**. A day-trading strategy is *structurally* off the table. Favor **multi-day swing** premium structures. |
| **Level 3 options** | Unlocks **uncovered** calls/puts, **short straddles/strangles**, **uncovered ratio spreads**, on top of Level-2 spreads. The Level-3 edge for income is **capital efficiency** (more premium per dollar of buying power) and **dynamic adjustment** (rolling an untested short leg). |
| **Small notional** | Defined-risk structures must use **narrow ($1–$2) wings** on **low-priced index products** (XSP ≈ SPX/10, SPY, IWM) so a single position's max loss fits a ~2 %-of-equity budget. |
| **Schwab API + L2** | Schwab Trader API gives OAuth2 REST (chains, quotes, history, **multi-leg orders**) and a WebSocket stream with **`LEVELONE_OPTIONS`** and **`NASDAQ_BOOK`/`NYSE_BOOK`** (Level 2). L2 is used for **cost-aware execution**, the documented make-or-break for this style. |
| **"Consistent income"** | Points to harvesting a **risk premium that pays steadily**, not to directional alpha. The most durable, capacity-light, academically supported one available to a retail options account is the **Variance Risk Premium (VRP)**. |

**Chosen method: a *conditional* Variance-Risk-Premium harvester with a financed
convex tail overlay.**

Concretely: sell **defined-risk, delta-neutral premium** (iron condors / credit
spreads / broken-wing butterflies; Level-3 jade lizards & short strangles when
gated) on liquid index options at ~35 DTE, **only when** a forecast says implied
volatility is richer than realized volatility *and* the market is in a calm
regime — then **recycle a fixed slice of the collected premium into far-OTM put
convexity** so a crash pays us back.

### Why VRP and not the alternatives?

- **Directional / trend / ML price-prediction:** weak, non-stationary edge at
  retail scale; high turnover collides with PDT; no structural risk premium.
- **Pure market-making (capture the bid-ask):** needs co-location/latency and
  rebates a $20k retail account cannot access.
- **Dispersion / correlation premium:** strong institutional edge, but needs to
  trade an index vs. dozens of single-name options → far too much capital and
  commission for $20k. (We borrow its *logic* via skew, not its capital needs.)
- **Naked short premium (the "obvious" Level-3 play):** highest carry, but its
  raw negative skew is exactly what blows up accounts (the literature's "46×
  loss day"). We use Level-3 *capabilities* surgically, not as the default.
- **VRP harvesting:** positive **~86 % of the time** since 1990 (AQR/Barclays),
  capacity-light, and — critically — its weakness (tail risk) is *hedgeable*
  with the very options we are already trading. That hedgeability is what makes
  it the best fit here.

### The honest caveat that shapes everything

Vilkov (2026), *0DTE Trading Rules: Tail Risk, Implementation, and Tactical
Timing*, shows the VRP at short horizons is real but **economically small and
regime-unstable once costs and tails are included**, while **conditional,
out-of-sample-timed** rules on selected structures reach **Sharpe ≈ 1.0–1.3**.
Translation: *unconditional* premium selling is a trap; **the alpha is in the
conditioning and the tail control.** That finding is the backbone of this design.

---

## 2. Academic foundation (research papers — REQUIRED)

Every module cites the work it implements (see also `docs/research_references.md`).

1. **Carr, P. & Wu, L. (2009). *Variance Risk Premiums*, RFS 22(3).** Model-free
   replication of the variance-swap rate from a strip of OTM options; defines the
   VRP we harvest. → `models/vrp.py::carr_wu_variance_swap_rate`.
2. **Bollerslev, Tauchen & Zhou (2009). *Expected Stock Returns and Variance Risk
   Premia*, RFS.** VRP predicts returns and is time-varying → motivates *timing*.
3. **Corsi, F. (2009). *A Simple Approximate Long-Memory Model of Realized
   Volatility (HAR-RV)*, J. Financial Econometrics.** Our primary realized-vol
   forecaster. → `models/har_rv.py`.
4. **Bollerslev (1986) GARCH; Nelson (1991) EGARCH.** Volatility-clustering
   forecaster used as an ensemble cross-check. → `models/garch.py`.
5. **Hamilton (1989). *Markov-Switching*, Econometrica;** Baum-Welch (1970).
   Latent volatility-regime detection. → `models/regime.py` (Gaussian HMM).
6. **Avellaneda & Lee (2010). *Statistical Arbitrage in the US Equities Market*,
   Quant. Finance.** The s-score used to bias which side carries risk.
   → `models/ou_meanrev.py`.
7. **Avellaneda & Stoikov (2008). *High-frequency trading in a limit order book*,
   Quant. Finance.** Reservation price / optimal quoting for cost-aware limit
   placement off the Level-2 book. → `execution/avellaneda_stoikov.py`.
8. **Almgren & Chriss (2000). *Optimal execution of portfolio transactions*,
   J. Risk.** Optimal unwind schedule for multi-contract exits/rolls.
   → `execution/almgren_chriss.py`.
9. **Rockafellar & Uryasev (2000). *Optimization of Conditional Value-at-Risk*,
   J. Risk.** Coherent tail-risk budgeting for a skewed book. → `sizing/cvar.py`.
10. **Kelly (1956); Thorp.** Growth-optimal sizing; we use *fractional* Kelly
    fit to the *true* (skewed) payoff sample. → `sizing/kelly.py`.
11. **Vilkov (2026). *0DTE Trading Rules*.** The conditioning/tail-control thesis.

---

## 3. Advanced mathematics (REQUIRED)

The toolkit is not a set of heuristics; the following mathematics is implemented
from first principles and unit-tested.

- **Black-Scholes-Merton PDE & the Greek tensor.** Full first/second-order Greeks
  (Δ, Γ, vega, Θ, ρ, **vanna**, **vomma**) plus Brent-method implied-vol
  inversion. `models/black_scholes.py`. These run the risk limits and Greek
  budgeting.
- **Stochastic volatility & model-free variance replication.** The variance-swap
  rate is `(2/T)·Σ (ΔKᵢ/Kᵢ²)·e^{rT}·Q(Kᵢ) − (1/T)(F/K₀−1)²` — the same identity
  behind the VIX. `models/vrp.py`.
- **Realized-variance estimators.** Close-to-close, **Parkinson**, **Garman-Klass**,
  **Rogers-Satchell**, and the minimum-variance **Yang-Zhang** estimator (drift-
  and gap-robust). `models/realized_vol.py`.
- **HAR cascade regression** of daily/weekly/monthly realized vol (long memory).
  `models/har_rv.py`.
- **GARCH(1,1) maximum-likelihood** estimation with stationarity constraints and
  analytic multi-step variance forecasting. `models/garch.py`.
- **Hidden Markov Model** with the **forward-backward (scaled)**, **Baum-Welch
  (EM)**, and **Viterbi** algorithms, written from scratch. `models/regime.py`.
- **Ornstein-Uhlenbeck** estimation via exact AR(1) mapping → mean-reversion
  speed κ, half-life ln2/κ, equilibrium s-score. `models/ou_meanrev.py`.
- **Kelly growth optimization** maximizing `E[log(1+fR)]` over the empirical
  payoff sample (honours skew), with fractional scaling + cap. `sizing/kelly.py`.
- **CVaR / Expected Shortfall** and the **Rockafellar-Uryasev** convex
  representation; CVaR-bounded position sizing. `sizing/cvar.py`.
- **Avellaneda-Stoikov** reservation price `r = s − qγσ²(T−t)` and optimal spread;
  **Almgren-Chriss** `xⱼ = X·sinh(κ(T−tⱼ))/sinh(κT)` liquidation trajectory.

---

## 4. The novel, alpha-protecting element

Most retail "theta gang" programs sell premium and stop there; their P&L looks
great until a vol spike erases months of gains. Our differentiators:

1. **Trade only the *forecast* VRP, measure-correctly.** We size positions from
   the payoff distribution simulated under the **physical** measure (the HAR-RV/
   GARCH realized-vol forecast), **not** under implied vol. Under implied vol,
   selling premium is a fair bet and Kelly is ~0; the positive expectancy exists
   *only* because realized tends to come in below implied. This forces the engine
   to trade **just the gap**, and to stand down when the gap is thin or negative.

2. **Regime + term-structure gating (Hamilton HMM).** Premium is sold **only** in
   calm/low-vol states and **never** in steep backwardation (acute stress). This
   is the empirically-validated fix for the short-vol tail.

3. **Financed convex tail overlay (`strategy/tail_overlay.py`).** A fixed fraction
   (default 20 %) of every credit is **recycled into far-OTM put *spreads***. In
   calm markets this bleeds a small, budgeted amount; in a crash it pays
   multiples and offsets the short book. This converts a naked short-vol carry
   into a **carry + convexity** portfolio — a financed long-tail hedge sitting on
   a short-vol engine — flattening the very skew that kills accounts. This
   combination (forecast-conditioned VRP + regime gate + *self-funded* convexity,
   sized by skew-aware fractional Kelly under a CVaR budget) is what protects the
   alpha: it is simple to state but rarely implemented as a disciplined system.

4. **Level-3 used as a scalpel, not a hammer.** When (and only when) the regime is
   the calmest and the premium is unusually rich, the engine may deploy a
   **jade lizard** (short put + short call spread with credit ≥ call-spread width
   → **no upside risk**) or a tightly-sized **short strangle**, both Level-3-only,
   to lift carry without taking the naked tail. Sizing for undefined-risk legs is
   deliberately throttled by the CVaR budget.

---

## 5. Architecture (how the code maps to the method)

```
data ─▶ SignalEngine ─▶ structure selection ─▶ RiskManager sizing ─▶ Greek check ─▶ tail overlay ─▶ Schwab orders
        (VRP+regime       (IC / spreads /         (frac-Kelly ∩          (Δ/vega        (recycle credit    (multi-leg
         +term struct)     BWB / jade lizard)      max-loss ∩ CVaR)       limits)        into convexity)    JSON)
```

| Layer | Module | Role |
|---|---|---|
| Brokerage | `brokers/schwab_client.py`, `schwab_stream.py`, `models.py` | OAuth2 REST + WebSocket L1/L2, order/quote/book models |
| Data | `data/providers.py`, `data/market_data.py` | Live Schwab provider + reproducible Heston-lite simulator; option-chain parsing |
| Models | `models/*` | BSM, realized vol, HAR-RV, GARCH, VRP, HMM regime, OU |
| Sizing | `sizing/kelly.py`, `sizing/cvar.py` | Growth-optimal + coherent tail-risk sizing |
| Execution | `execution/*` | Avellaneda-Stoikov quoting, Almgren-Chriss scheduling, Schwab order building |
| Strategy | `strategy/signals.py`, `structures.py`, `tail_overlay.py`, `risk.py`, `engine.py` | The conditional gate, the structures, the overlay, the guardrails, the orchestrator |
| Backtest | `backtest/backtester.py` | Event-driven backtest reusing the *production* decision path |
| CLI | `cli.py` | `init`, `signal`, `backtest` |

---

## 6. Risk management (the part that keeps you solvent)

Implemented in `strategy/risk.py` + `config.py` (all tunable):

- **Per-trade max loss** ≤ 2 % of equity (defined-risk only by default).
- **Portfolio CVaR(99 %)** budget ≤ 6 % of equity (Rockafellar-Uryasev).
- **Fractional Kelly** (¼, capped 30 %) on the *true skewed* payoff sample.
- **Greek limits** scaled to equity (net Δ and vega per $1k).
- **Profit-take at 50 %** of max credit; **stop at 2× credit** received.
- **PDT guard**: blocks behaviour requiring same-day round trips while < $25k.
- **Drawdown circuit-breaker**: halts new risk after a 12 % peak-to-trough draw.
- **Tail overlay** always on by default.

See `docs/risk_management.md` for the full rulebook.

---

## 7. The $600 supplemental budget

The Schwab Trader API and Schwab Level 2 are **free** with the account, so the
$600 is spent where it raises the *information ratio*, not on data we already
have. Recommended allocation (details in `docs/budget_plan.md`):

| Item | Approx. monthly | Why |
|---|---|---|
| Historical options data (e.g., a ThetaData/CBOE tier) for **honest backtesting & VRP calibration** | $30–80 | The single highest-value spend: validates the edge on real option P&L. |
| Low-cost **VPS** (24×5 orchestration, near Schwab endpoints) | $5–20 | Reliable, unattended execution and logging. |
| Optional **vol/skew data** (term structure, VIX futures) | $0–30 | Sharper regime + term-structure signals. |

A ~$60–100/mo run-rate is sustainable well within $600, leaving runway to extend
as the account compounds (per the mandate: "extended as long as the account makes
money").

---

## 8. Going live with Schwab (operational path)

1. Create a developer app at `developer.schwab.com`; set the redirect URI.
2. Run the OAuth2 authorization-code flow (`brokers/schwab_client.py`), store the
   refresh token **outside the repo** (see `.gitignore`).
3. Backtest on **real** historical option data; confirm the edge survives costs.
4. **Paper trade** the exact decision path for a full vol cycle.
5. Go live tiny: 1-contract, defined-risk only, tail overlay on, all circuit
   breakers active. Scale only with realized, out-of-sample performance.

---

## 9. What the included backtest does and does not show

`qist backtest` runs the **real** decision path over a reproducible
**Heston-lite** simulation (stochastic vol + jumps) whose option chain embeds a
realistic variance risk premium. It exists to **validate the pipeline end-to-end**
and to demonstrate the *shape* of the strategy (high win rate, small average
gain, bounded CVaR, shallow drawdown), **not** to predict live returns. Simulated
edges are an assumption, not evidence. Real historical option data is required
before any capital decision.
