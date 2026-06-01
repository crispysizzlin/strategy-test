# Research references & how each maps to the code

This strategy is grounded in peer-reviewed research **and** the advanced
mathematics used by institutional desks. Every reference below is implemented or
directly motivates a module.

## Variance risk premium (the income engine)

- **Carr, P. & Wu, L. (2009).** *Variance Risk Premiums.* Review of Financial
  Studies 22(3), 1311-1341.
  Model-free variance-swap replication from a strip of OTM options.
  → `src/qist/models/vrp.py::carr_wu_variance_swap_rate`
- **Bakshi, G. & Kapadia, N. (2003).** *Delta-Hedged Gains and the Negative
  Market Volatility Risk Premium.* RFS 16(2).
  Evidence that selling volatility is compensated.
- **Bollerslev, T., Tauchen, G. & Zhou, H. (2009).** *Expected Stock Returns and
  Variance Risk Premia.* RFS 22(11).
  The VRP is time-varying and predicts returns → motivates *timing the premium*.
- **Bekaert, G. & Hoerova, M. (2014).** *The VIX, the Variance Premium and Stock
  Market Volatility.* J. Econometrics. Decomposition guiding RV-model choice.

## Volatility forecasting

- **Corsi, F. (2009).** *A Simple Approximate Long-Memory Model of Realized
  Volatility (HAR-RV).* J. Financial Econometrics 7(2), 174-196.
  → `src/qist/models/har_rv.py`
- **Bollerslev, T. (1986).** *Generalized Autoregressive Conditional
  Heteroskedasticity.* J. Econometrics 31(3). → `src/qist/models/garch.py`
- **Nelson, D. (1991).** *Conditional Heteroskedasticity in Asset Returns
  (EGARCH).* Econometrica 59(2). (asymmetric extension of the GARCH module)
- **Parkinson (1980); Garman & Klass (1980); Rogers & Satchell (1991);
  Yang & Zhang (2000).** Range/OHLC realized-volatility estimators.
  → `src/qist/models/realized_vol.py`

## Regimes & mean reversion

- **Hamilton, J. (1989).** *A New Approach to the Economic Analysis of
  Nonstationary Time Series and the Business Cycle.* Econometrica 57(2).
- **Baum, L. et al. (1970).** Baum-Welch / forward-backward EM for HMMs.
  → `src/qist/models/regime.py` (forward-backward, Baum-Welch, Viterbi)
- **Avellaneda, M. & Lee, J. (2010).** *Statistical Arbitrage in the US Equities
  Market.* Quantitative Finance 10(7). → `src/qist/models/ou_meanrev.py`

## Sizing & risk

- **Kelly, J. (1956).** *A New Interpretation of Information Rate.* BSTJ.
- **Thorp, E. (2006).** *The Kelly Criterion in Blackjack, Sports Betting, and
  the Stock Market.* → `src/qist/sizing/kelly.py`
- **Rockafellar, R.T. & Uryasev, S. (2000).** *Optimization of Conditional
  Value-at-Risk.* J. Risk 2. → `src/qist/sizing/cvar.py`

## Execution / microstructure (Level 2)

- **Avellaneda, M. & Stoikov, S. (2008).** *High-frequency trading in a limit
  order book.* Quantitative Finance 8(3).
  → `src/qist/execution/avellaneda_stoikov.py`
- **Almgren, R. & Chriss, N. (2000).** *Optimal execution of portfolio
  transactions.* J. Risk 3. → `src/qist/execution/almgren_chriss.py`

## Pricing

- **Black, F. & Scholes, M. (1973);** **Merton, R. (1973).** BSM pricing and
  Greeks. → `src/qist/models/black_scholes.py`

## Contemporary evidence shaping the design

- **Vilkov, G. (2026).** *0DTE Trading Rules: Tail Risk, Implementation, and
  Tactical Timing.* Finds unconditional short-dated VRP is weak after costs/tails,
  while conditional out-of-sample-timed rules reach Sharpe ≈ 1.0-1.3. This is the
  empirical basis for the conditioning gate and the tail overlay.
- Industry: AQR/Barclays document the VRP is positive ~86 % of periods since 1990
  but warns its convex exposure means a realized-vol spike causes large losses —
  exactly the risk the convex overlay is built to neutralize.
