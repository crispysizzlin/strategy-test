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

## Platform and contract facts

- Quantower's official [Level 2 documentation](https://help.quantower.com/quantower/quantower-algo/level2-data) exposes `NewLevel2` plus aggregated bid/ask collections. The implementation follows the vendor's [official examples repository](https://github.com/Quantower/Examples), including market-data subscriptions, strategy lifecycle, orders, positions, and attached protection.
- Quantower's [Backtest & Optimize documentation](https://help.quantower.com/quantower/quantower-algo/backtest-and-optimize) supports explicit per-side fees and bid/ask offsets. Historical depth availability still depends on the selected connection/data, so a bar-only test cannot validate the L2-gated live strategy.
- CME states that MES has a 0.25 index-point tick worth $1.25 and a $5 index multiplier: [CME MES contract page](https://www.cmegroup.com/markets/equities/sp/micro-e-mini-sandp-500.html).

## Prop-firm facts used as of 2026-07-20

Lucid's official help center lists:

- Quantower among supported Rithmic platforms, permits automated strategies, requires sim positions flat by 16:45 ET, prohibits hedging and abusive HFT/microscalping: [general FAQ](https://lucidtrading.com/general-faq/).
- MES commission of $0.50 per contract per side and ES of $1.75: [approved products and commissions](https://support.lucidtrading.com/en/articles/11508978-approved-products-and-commissions).
- LucidPro evaluation targets, max loss, DLL, and size by account: [evaluation account](https://support.lucidtrading.com/en/articles/12890029-lucidpro-evaluation-account).
- LucidPro EOD trailing max-loss mechanics: [drawdown](https://support.lucidtrading.com/en/articles/12890136-lucidpro-drawdown).
- LucidPro funded payout buffer and 40% best-day consistency: [payouts](https://support.lucidtrading.com/en/articles/12890092-lucidpro-payouts).

Rules can change and the user's account agreement/dashboard controls. `config/lucid_rules_reference.json` is dated reference data, not an authority.

## Rejected evidence

- Social-media profitability claims and short marketing backtests are not used as parameter evidence.
- The recent six-month ES ORB claims found online are too short, optimized, and insufficiently documented for deployment decisions.
- Equity ORB studies based on selecting thousands of “stocks in play” do not transfer to a single index-futures contract.
- Level 2 screenshots, cumulative DOM, or one displayed liquidity wall are not causal proof because orders may be cancelled before execution.

## Research conclusion

The literature supports testing a **conditional** early-session continuation hypothesis and using fast order-book variables only at the execution horizon. It does not establish that this exact strategy is profitable in 2026. That claim requires the repository's data and forward-validation protocol.
