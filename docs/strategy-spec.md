# Strategy specification

## Hypothesis

The opening range is useful as a level, not automatically as a trade. Conditional continuation should be more likely when:

- the initial range is neither abnormally narrow nor already exhausted;
- price breaks with above-baseline volume and remains on the correct side of session VWAP;
- VWAP is moving in the breakout direction;
- price retests the boundary instead of being chased; and
- short-horizon supply/demand measures remain aligned through the retest.

The strategy is symmetric. Default signals are limited to 09:45–11:30 ET and all positions are flattened at 15:55 ET.

## 1. Opening range and volatility regime

For session (d), the 15-minute opening range is

\[
H_d^{OR}=\max_{09:30\le t<09:45}P_t,\qquad
L_d^{OR}=\min_{09:30\le t<09:45}P_t,
\]

with width (W_d=H_d^{OR}-L_d^{OR}). Let (ATR_d) be the median of the prior 20 completed daily true ranges. The default regime gate is

\[
0.08 \le \frac{W_d}{ATR_d} \le 0.35,
\]

plus absolute instrument tick bounds. The median is used instead of the mean to reduce sensitivity to isolated shock sessions.

## 2. Breakout condition

The breakout buffer is

\[
b_d=\max(2\text{ ticks},\;0.05W_d).
\]

A long breakout bar must close above (H_d^{OR}+b_d); a short must close below (L_d^{OR}-b_d). Its volume must be at least 1.15 times the one-sided intraday volume EMA calculated only from earlier bars.

The strategy records the breakout but does not enter.

## 3. VWAP direction

Trade-based session VWAP is

\[
VWAP_t=\frac{\sum_{i\le t} P_iV_i}{\sum_{i\le t}V_i}.
\]

Longs require (P_t>VWAP_t) and positive three-minute VWAP slope; shorts require the reverse. Calculations use only observations available at the decision time.

The live strategy accumulates every last trade's `price * size`. Historical input should therefore include per-minute `trade_value = sum(price * size)`. If it is absent everywhere in a session, the research engine uses and labels a typical-price approximation; that approximation is not acceptable for final reconciliation.

## 4. Retest

Within 20 minutes of a breakout, a long bar must trade back to (H_d^{OR}+0.12W_d) or lower and close at or above (H_d^{OR}). The short rule is mirrored. A close more than (0.15W_d) back inside the range invalidates the candidate.

Entry occurs at the **next minute's first trade**, not the retest close. This convention is shared by the research simulator and the live state machine and avoids a close-price look-ahead.

## 5. Level 2 confirmation

For five book levels with exponential weight (w_i=e^{-0.6(i-1)}), depth imbalance is

\[
DI_t=\frac{\sum_i w_iB_{i,t}-\sum_i w_iA_{i,t}}
           {\sum_i w_iB_{i,t}+\sum_i w_iA_{i,t}}.
\]

At the best quotes, the Cont–Kukanov–Stoikov event contribution is approximated from throttled snapshots:

\[
e_n=\mathbf{1}_{b_n\ge b_{n-1}}q^b_n
-\mathbf{1}_{b_n\le b_{n-1}}q^b_{n-1}
-\mathbf{1}_{a_n\le a_{n-1}}q^a_n
+\mathbf{1}_{a_n\ge a_{n-1}}q^a_{n-1}.
\]

The code normalizes this by displayed best-level depth, bounds it, and applies an EWMA. The weighted microprice is

\[
MP_t=\frac{a_tq^b_t+b_tq^a_t}{q^b_t+q^a_t}.
\]

Trade delta is signed at the ask/bid, with tick-rule fallback. The fixed composite is

\[
S_t=0.40DI_t+0.30OFI_t+0.20\Delta_t+0.10MPDev_t.
\]

A long requires (S_t\ge0.15), positive sign in at least 60% of the last five throttled observations, one-tick-or-better spread, and a book age no greater than 750 ms. Shorts are mirrored. The historical reducer records the age of the final eligible observation relative to minute end; stale or missing fields cannot be forward-filled. These weights are hypotheses fixed before validation—not fitted facts.

Level 2 is confirmation only. Displayed liquidity can be cancelled, so a single size imbalance or “wall” never triggers a trade.

## 6. Stop, target, and size

The structural long stop reference is the lower of one tick below the retest low and (H_d^{OR}-0.15W_d). Short logic is mirrored. Stop distance is bounded between 8 and 48 ticks by default; a setup needing more is skipped.

For stop distance (s) ticks, tick value (v), per-side commission (c), assumed per-side slippage (l), and risk budget (R), quantity is

\[
q=\min\left(q_{max},\left\lfloor
\frac{R}{sv+2c+2lv}
\right\rfloor\right).
\]

If (q<1), no order is sent. The default target is 1.75 times stop distance. The entry request includes server-side attached stop and target instructions. A failed-retest close, 45-minute time stop, risk shutdown, or 15:55 ET cutoff closes the position early.

## 7. Prop controls

- One trade per session by default.
- Internal daily loss limit is materially below the firm's DLL.
- The current path-dependent max-loss threshold is a required user input; Quantower account balance alone cannot reconstruct it reliably.
- The strategy caps planned all-in risk so a full stop remains above that threshold plus an additional dollar buffer.
- With live prop enforcement, a new ET date requires a restart and a freshly verified dashboard threshold.
- Stale trades, stale Level 2, rejected protection, position mismatch, repeated excessive slippage, and unexpected exposure trigger shutdown.
- Any existing account exposure prevents startup; other-symbol activity in the dedicated account triggers shutdown.
- Hedging and correlated cross-account logic are absent by design.

## 8. QuantData boundary

QuantData is not treated as CME futures Level 2. An external process may write one daily CSV row with allowed direction, a 0–1 risk multiplier, and an expiry. If marked required, missing or stale data produces no trade. The market-data and execution path remains Rithmic → Quantower.
