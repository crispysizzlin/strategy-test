# $600 supplemental tooling budget — allocation plan

**Principle:** Schwab's Trader API and Schwab **Level 2** market data are *free*
with the account. So the $600 is not spent on data we already have — it is spent
where it most increases the *information ratio* and *operational reliability* of
the system. The mandate allows extending this spend as the account makes money,
so we keep the run-rate low and scalable.

## Priority 1 — Historical options data (highest ROI)

The single most valuable spend: a historical **options** dataset (e.g., a
ThetaData or CBOE DataShop tier, ORATS, or similar) covering SPX/SPY/XSP option
chains with greeks and implied vols.

- **Why:** the entire edge rests on the *variance risk premium*. You cannot
  honestly validate it on simulated data. Real historical option P&L (including
  bid-ask, skew, term structure, and crash days) is what tells you whether the
  conditioning gate and tail overlay actually deliver positive, low-tail income.
- **Approx:** **$30–80 / month** depending on tier/history depth.
- **Used by:** backtest calibration, VRP z-score history, regime training.

## Priority 2 — Always-on execution host (VPS)

A small cloud VPS to run the orchestration loop 24×5 with logging and alerting.

- **Why:** unattended, reliable entries/exits/rolls; no dependence on a laptop;
  consistent latency to Schwab endpoints for limit-order management.
- **Approx:** **$5–20 / month** (1-2 vCPU is plenty; this is not HFT).

## Priority 3 — Volatility / skew data (optional, sharpens signals)

VIX term structure, VIX futures, and a clean skew/term-structure feed.

- **Why:** improves the **regime** and **term-structure** gates and the VRP
  estimate (Carr-Wu strip quality). Schwab L1/L2 already gives live chains, so
  this is incremental, not essential.
- **Approx:** **$0–30 / month** (some sources are free/delayed).

## Indicative run-rate

| Tier | Monthly | Annual | Fits in $600? |
|---|---|---|---|
| Lean (data + VPS) | ~$45 | ~$540 | Yes (≈11 months runway) |
| Standard (+ vol data) | ~$75 | ~$900 | First ~8 months; extend from profits |

## Explicitly *not* bought

- Level 2 data (free from Schwab).
- Expensive "signal" subscriptions / alt-data (no durable edge at this scale).
- Co-location / low-latency infra (irrelevant for 35-DTE swing premium).

## Scaling rule

Re-invest into deeper historical data and redundancy **only** after the account
shows positive, out-of-sample, after-cost performance across at least one full
volatility cycle. Tooling spend should trail realized profits, never lead them.
