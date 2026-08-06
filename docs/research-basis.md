# Research basis and inference limits

## What the literature supports

| Source | Finding used | What it does **not** prove |
|---|---|---|
| Gao, Han, Li & Zhou, “Market Intraday Momentum,” *Journal of Financial Economics* (2018), [publisher page](https://www.sciencedirect.com/science/article/abs/pii/S0304405X18301351) | Early-session market returns contain conditional information about later intraday returns; the effect is stronger on high-volume/high-volatility days. | It is not a test of this MES breakout-retest rule and does not validate a 09:45 entry. |
| Holmberg, Lönnbark & Lundström, “Assessing the profitability of intraday opening range breakout strategies,” *International Review of Financial Analysis* (2013), [publisher page](https://www.sciencedirect.com/science/article/abs/pii/S1544612312000438) | ORB profitability can look strong in a full sample yet fail subperiod robustness. | It does not imply ORB is universally unprofitable; it requires regime and stability testing. |
| Cont, Kukanov & Stoikov, “The Price Impact of Order Book Events,” *Journal of Financial Econometrics* (2014), [journal page](https://academic.oup.com/jfec/article-abstract/12/1/47/816163), [preprint](https://arxiv.org/abs/1011.6402) | Short-horizon price changes relate more robustly to order-flow imbalance than raw traded volume in their sample. | The sample is U.S. equities, not Rithmic MES; OFI is therefore a feature hypothesis, not imported alpha. |
| Stoikov, “The micro-price: a high-frequency estimator of future prices,” *Quantitative Finance* (2018), [journal page](https://www.tandfonline.com/doi/abs/10.1080/14697688.2018.1489139), [working paper](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2970694) | Book imbalance can improve a short-horizon fair-price estimate relative to midprice. | Microprice alone is not a trade and can be overwhelmed by fees, latency, and cancellation. |
| Cont, Cucuringu & Zhang, “Cross-Impact of Order Flow Imbalance in Equity Markets” (2023), [paper](https://arxiv.org/abs/2112.13213) | Combining multiple book levels can explain more contemporaneous impact than best-level OFI alone; predictive effects decay quickly. | It does not justify using book signals as a slow directional forecast. This is why L2 only confirms an already-defined retest. |
| Bailey, Borwein, López de Prado & Zhu, “The Probability of Backtest Overfitting,” *Journal of Computational Finance*, [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253) | Selecting the best of many backtests creates severe selection bias; CSCV/PBO and corrected performance statistics are needed. | A high in-sample Sharpe or influencer screenshot is not evidence of a repeatable edge. |
| Osler, “Currency Orders and Exchange Rate Dynamics: An Explanation for the Predictive Success of Technical Analysis,” *Journal of Finance* (2003), [journal page](https://onlinelibrary.wiley.com/doi/10.1111/1540-6261.00556) | Stop-loss and take-profit orders cluster at round numbers, producing predictable price behavior near those levels (support/resistance and stop cascades). | The evidence is FX dealer order books, not CME index futures. Round-number clustering here motivates only bounded *exit-placement* adjustments, not standalone signals. |
| Bhattacharya, Holden & Jacobsen, “Penny Wise, Dollar Foolish: Buy–Sell Imbalances On and Around Round Numbers,” *Management Science* (2012), [journal page](https://pubsonline.informs.org/doi/10.1287/mnsc.1110.1364) | Order flow shows systematic imbalances at and around round-number price points. | Documented for U.S. equities; magnitude and sign for MNQ must be measured from recorded data, not assumed. |
| Brogaard, Hendershott & Riordan, “Price Discovery without Trading: Evidence from Limit Orders,” *Journal of Finance* (2019), [journal page](https://onlinelibrary.wiley.com/doi/10.1111/jofi.12769) | Resting limit orders contribute meaningfully to price discovery; large visible depth carries information. | Displayed size can be spoofed or cancelled; a wall is a fill-quality anchor here, never a directional signal on its own. |

## Platform and contract facts

- Quantower's official [Level 2 documentation](https://help.quantower.com/quantower/quantower-algo/level2-data) exposes `NewLevel2` plus aggregated bid/ask collections. The implementation follows the vendor's [official examples repository](https://github.com/Quantower/Examples), including market-data subscriptions, strategy lifecycle, orders, positions, and attached protection.
- Quantower's [Backtest & Optimize documentation](https://help.quantower.com/quantower/quantower-algo/backtest-and-optimize) supports explicit per-side fees and bid/ask offsets. Historical depth availability still depends on the selected connection/data, so a bar-only test cannot validate the L2-gated live strategy.
- CME states that MES has a 0.25 index-point tick worth $1.25 and a $5 index multiplier: [CME MES contract page](https://www.cmegroup.com/markets/equities/sp/micro-e-mini-sandp-500.html).
- CME states that MNQ has a 0.25 index-point tick worth $0.50 and a $2 index multiplier, trading Sunday–Friday 18:00–17:00 ET with a daily 17:00–18:00 ET maintenance halt: [CME micro E-mini FAQ](https://www.cmegroup.com/articles/faqs/frequently-asked-questions-micro-e-mini-equity-index-futures.html).
- The 17:00 ET break defines the futures **trading date**; the 18:00 ET reopen belongs to the next trading day. All daily accounting in this repository keys on that boundary.
- Overnight (GTH) MNQ liquidity is materially thinner than RTH: wider effective spreads, sparser prints, and shallower books. The GTH defaults (half risk, doubled slippage assumption, wider spread cap, higher relative-volume floor, lower OR/ATR band) are conservative hypotheses, not measured optima.

## Prop-firm facts used as of 2026-07-20

Lucid's official help center lists:

- Quantower among supported Rithmic platforms, permits automated strategies, requires sim positions flat by 16:45 ET, prohibits hedging and abusive HFT/microscalping: [general FAQ](https://lucidtrading.com/general-faq/).
- Sim accounts (Pro/Flex/Direct) may trade from the 18:00 ET Sunday–Thursday reopen through 16:45 ET; overnight *holding* is auto-flattened at 16:45 ET but intraday trading during Globex overnight hours is permitted: [allowed trading times](https://support.lucidtrading.com/en/articles/11404729-allowed-trading-times).
- MES and MNQ commission of $0.50 per contract per side; ES and NQ $1.75: [approved products and commissions](https://support.lucidtrading.com/en/articles/11508978-approved-products-and-commissions).
- LucidPro evaluation targets, max loss, DLL, and size by account: [evaluation account](https://support.lucidtrading.com/en/articles/12890029-lucidpro-evaluation-account).
- LucidPro EOD trailing max-loss mechanics: [drawdown](https://support.lucidtrading.com/en/articles/12890136-lucidpro-drawdown).
- LucidPro funded payout buffer and 40% best-day consistency: [payouts](https://support.lucidtrading.com/en/articles/12890092-lucidpro-payouts).

Rules can change and the user's account agreement/dashboard controls. `config/lucid_rules_reference.json` is dated reference data, not an authority.

## Rejected evidence

- Social-media profitability claims and short marketing backtests are not used as parameter evidence.
- The recent six-month ES ORB claims found online are too short, optimized, and insufficiently documented for deployment decisions.
- Equity ORB studies based on selecting thousands of “stocks in play” do not transfer to a single index-futures contract.
- Level 2 screenshots, cumulative DOM, or one displayed liquidity wall are not causal proof because orders may be cancelled before execution. The wall logic therefore requires multi-second persistence and still only adjusts execution prices.
- "Futures respect round numbers" is treated as an order-clustering hypothesis with equity/FX evidence, not a law; the exit adjustments it motivates are bounded to a few ticks so a wrong hypothesis costs little.

## Research conclusion

The literature supports testing a **conditional** early-session continuation hypothesis and using fast order-book variables only at the execution horizon. It does not establish that this exact strategy is profitable in 2026. That claim requires the repository's data and forward-validation protocol.
