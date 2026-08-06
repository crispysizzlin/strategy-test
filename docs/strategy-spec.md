# Strategy specification

## Hypothesis

The opening range is useful as a level, not automatically as a trade. Conditional continuation should be more likely when:

- the initial range is neither abnormally narrow nor already exhausted;
- price breaks with above-baseline volume and remains on the correct side of session VWAP;
- VWAP is moving in the breakout direction;
- price retests the boundary instead of being chased; and
- short-horizon supply/demand measures remain aligned through the retest.

The strategy is symmetric and window-based. The default MNQ profile trades up to three disjoint windows per CME trading day — Globex reopen (18:00 ET), London open (03:00 ET), and New York open (09:30 ET) — each with its own opening range, signal cutoff, and flatten time. All windows finish well before the prop firm's 16:45 ET close-out. Single-window (RTH-only) legacy configurations remain valid.

## 0. Trading date and session windows

CME index futures roll to the next trading date at the 17:00 ET maintenance break, so the 18:00 ET reopen belongs to the following trading day. Daily loss limits, prop accounting, and the ATR reference all key on this trading date, not the calendar date.

Each window is a clock interval `[open, flatten]` that must not cross midnight ET and must not overlap any other window. Per-window overrides exist for the OR/ATR band, absolute OR tick bounds, spread cap, assumed slippage, breakout relative-volume floor, and a `risk_fraction` in `(0, 1]`. GTH defaults: half risk, doubled slippage assumption, wider spread cap, tighter relative-volume requirement, and a lower OR/ATR band (overnight ranges are structurally smaller than the full-day ATR).

One trade per window; an internal daily loss limit halts all later windows of the same trading day.

## 1. Opening range and volatility regime

For window (w) on trading day (d), the opening range over the first `opening_range_minutes` is

\[
H^{OR}=\max_{open\le t<open+OR}P_t,\qquad
L^{OR}=\min_{open\le t<open+OR}P_t,
\]

with width (W=H^{OR}-L^{OR}). Let (ATR_d) be the median of the prior 20 completed **full-trading-day** true ranges (all Globex bars grouped by trading date, matching provider `DAY1` futures bars). The default RTH regime gate is

\[
0.08 \le \frac{W}{ATR_d} \le 0.35,
\]

and the default GTH gate is `0.02–0.15`, plus absolute instrument tick bounds per window. The median is used instead of the mean to reduce sensitivity to isolated shock sessions.

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

Within 20 minutes of a breakout, a long bar must trade back to (H^{OR}+0.12W) or lower and close at or above (H^{OR}). The short rule is mirrored. A close more than (0.15W) back inside the range invalidates the candidate.

Default entry occurs at the **next minute's first trade**, not the retest close. This convention is shared by the research simulator and the live state machine and avoids a close-price look-ahead. Section 5b describes the wall-based passive alternative that can replace this market entry when a qualifying resting-liquidity wall is present.

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

## 5b. Wall-based passive entry (execution modifier, never a signal)

Once a retest signal already exists, the strategy prefers to be filled passively in front of large resting liquidity rather than paying the spread at market:

- **Wall definition.** Scanning 15 displayed levels per side, a *wall* is the largest level whose size is at least `min_wall_ratio` (default 4.0) times the **median displayed level size on the same side**. The adaptive median keeps the definition meaningful in thin GTH books and avoids fitting an absolute contract count.
- **Persistence.** A wall must survive `wall_persistence_snapshots` consecutive throttled snapshots (default 10 × ~200 ms ≈ 2 s) before it is trusted. This filters most spoof-and-cancel behavior but cannot eliminate it, which is why walls only ever *improve the entry price* and never create or veto a trade.
- **Placement.** For a long with a persistent bid wall at price (P_w), a limit rests at (P_w + 6) ticks (`wall_offset_ticks`), queued in front of the wall. The limit must be a passive price between 1 and `max_wall_chase_ticks` (default 24) ticks below the market; otherwise the plain market entry is used.
- **Fill convention.** The research simulator counts the limit as filled only when a later bar trades **strictly through** it (one tick beyond), never on a touch. The live build uses a real limit order with attached protection.
- **Stop anchor.** A wall-based fill anchors its stop `wall_stop_pad_ticks` (default 4) behind the wall: if the wall breaks, the reason for the fill is gone. Minimum/maximum stop-tick bounds still apply.
- **Timeout.** If the limit is untouched after `wall_entry_timeout_bars` (default 5 minutes), the default fallback enters at market provided the breakout thesis is still intact, keeping trade frequency aligned with the base strategy. A `skip` fallback is available for stricter execution research. If price closes back inside the range before the fill, the attempt is cancelled.

## 5c. Round-number ("even") levels

Index futures cluster resting orders and reactions at prices whose last two digits are **00, 20, 40, 50, 60, 80**. The strategy never trades a round number by itself; it applies two bounded exit adjustments:

- **Target shave.** A profit target landing within `round_level_target_window_ticks` (default 8) of a round level is pulled to `round_level_front_ticks` (default 4) in front of the level, in the direction of travel, so the exit rests before the crowd's orders at the level. The target is only ever moved *closer* to the entry.
- **Stop pad.** A protective stop resting within `round_level_stop_trigger_ticks` (default 4) of a round level is moved `round_level_stop_pad_ticks` (default 6) beyond the level, so an ordinary sweep of the level does not take the stop. The pad applies only if the widened stop stays inside the maximum stop distance; otherwise the original stop is kept and sizing is unchanged.

Both adjustments are deterministic and parameter-light by design; neither creates nor vetoes trades.

## 6. Stop, target, and size

The structural long stop reference is the lower of one tick below the retest low and (H^{OR}-0.15W). Short logic is mirrored. Wall-based entries replace the structural reference with the wall anchor from section 5b. Stop distance is bounded per instrument (default MNQ: 16–240 ticks); a setup needing more is skipped.

For stop distance (s) ticks, tick value (v), per-side commission (c), assumed per-side slippage (l), and risk budget (R), quantity is

\[
q=\min\left(q_{max},\left\lfloor
\frac{R}{sv+2c+2lv}
\right\rfloor\right).
\]

If (q<1), no order is sent. The risk budget (R) is scaled by the window's `risk_fraction` (default 0.5 for GTH windows) and the assumed slippage (l) uses the window override (default 2 ticks GTH). The default target is 1.75 times stop distance, then round-number shaved per section 5c. The entry request includes server-side attached stop and target instructions. A failed-retest close, 45-minute time stop, risk shutdown, or the window's flatten time closes the position early.

## 7. Prop controls

- One trade per window; an internal daily loss limit (per trading date) halts later windows.
- Internal daily loss limit is materially below the firm's DLL.
- The current path-dependent max-loss threshold is a required user input; Quantower account balance alone cannot reconstruct it reliably.
- The strategy caps planned all-in risk so a full stop remains above that threshold plus an additional dollar buffer.
- With live prop enforcement, a new trading date (17:00 ET rollover) requires a restart and a freshly verified dashboard threshold — in practice, one restart between the 16:45 close-out and the 18:00 reopen.
- A hard flatten guard closes any residual exposure at 16:40 ET, before the firm's 16:45 ET deadline, independent of window configuration.
- Stale trades, stale Level 2, rejected protection, position mismatch, repeated excessive slippage, and unexpected exposure trigger shutdown.
- Any existing account exposure prevents startup; other-symbol activity in the dedicated account triggers shutdown.
- Hedging and correlated cross-account logic are absent by design.

## 8. QuantData boundary

QuantData is not treated as CME futures Level 2. An external process may write one daily CSV row with allowed direction, a 0–1 risk multiplier, and an expiry. If marked required, missing or stale data produces no trade. The market-data and execution path remains Rithmic → Quantower.
