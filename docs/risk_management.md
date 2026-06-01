# Risk-management rulebook

Short-volatility income has a **negatively skewed** payoff: frequent small gains,
rare large losses. Survival — not maximizing carry — is the objective function.
These rules are implemented in `src/qist/strategy/risk.py` and `config.py`.

## Hard limits (defaults; all configurable)

| Rule | Default | Rationale |
|---|---|---|
| Per-trade max loss | **2 %** of equity | One bad trade can't materially dent the account. |
| Portfolio CVaR(99 %) budget | **6 %** of equity | Coherent tail measure (Rockafellar-Uryasev), not VaR. |
| Net delta limit | **1.5 / $1k** equity | Keep the book roughly market-neutral. |
| Net vega limit | **3.0 / $1k** equity | Cap exposure to a vol-of-vol spike. |
| Fractional Kelly | **¼**, capped **30 %** | Full Kelly is reckless under fat tails. |
| Profit target | **50 %** of max credit | Banks theta early, shrinks tail exposure window. |
| Stop loss | **2×** credit received | Cuts a tested side before it becomes max loss. |
| Drawdown circuit-breaker | **12 %** peak-to-trough | Halts *new* risk; forces a human review. |

## Position-sizing pipeline

Quantity = **min** of three independent caps:
1. **Fractional Kelly** on the *true expiry-payoff sample* (honours skew), under
   the **physical** (realized-vol forecast) measure — not implied vol.
2. **Per-trade max-loss** budget.
3. **Portfolio CVaR(99 %)** budget.

Then scaled down further if it would breach the Δ/vega limits.

## Pattern-Day-Trader (PDT) discipline

While equity < **$25,000**, FINRA caps day trades at **3 per rolling 5 business
days**. Therefore:
- The strategy uses **multi-day (≈35 DTE)** structures; it does **not** rely on
  intraday round trips.
- The engine's `can_day_trade` guard blocks any same-day exit that would exceed
  the budget, so risk management never forces a PDT violation.
- Prefer **cash-settled, European** index options (e.g., **XSP**) to avoid early
  assignment and pin risk, which also reduces forced same-day actions.

## Tail overlay (always-on by default)

A fixed fraction (**20 %**) of each collected credit buys **far-OTM put spreads**
(`strategy/tail_overlay.py`). This is a *budgeted, self-funded* long-convexity
sleeve: small bleed in calm markets, large payoff in a crash. It is the primary
defense against the short-vol skew and should generally remain enabled.

## Level-3 (undefined-risk) governance

`short_strangle` / `jade_lizard` are permitted **only** when:
- regime rank = 0 (calmest state), **and**
- the VRP z-score is in the richer part of its distribution, **and**
- the CVaR budget still binds the size to a small number of contracts.

Jade lizards are preferred because, sized so credit ≥ call-spread width, they
carry **no upside risk**. Naked strangles are the most throttled structure.

## Operational guardrails

- Never commit credentials/tokens (see `.gitignore`).
- Always `preview_order` before `place_order` in live mode.
- Cost-aware limit orders only (Avellaneda-Stoikov); never cross wide option
  spreads with market orders.
- Log every decision (signal state + plan notes) for post-trade review.
