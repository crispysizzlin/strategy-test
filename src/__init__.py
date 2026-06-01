"""
AVRPE - Adaptive Volatility Regime Premium Engine
=================================================
An institutional-grade algorithmic options income system for Charles Schwab
leveraging Level 3 options privileges, Level 2 streaming data, and advanced
quantitative models including GARCH/EGARCH volatility forecasting, Hidden
Markov Model regime detection, SVI/SSVI volatility surface fitting, and
regime-conditioned Kelly criterion position sizing.

Mathematical Foundations:
- Volatility Risk Premium (VRP) harvesting via short premium structures
- GARCH(1,1) / EGARCH for conditional volatility forecasting
- Hidden Markov Models (HMM) for market regime detection
- SVI/SSVI arbitrage-free implied volatility surface fitting
- Fractional Kelly criterion + CVaR risk budgeting
- Black-Scholes Greeks for portfolio-level risk management

Primary Strategy:
- Regime-adaptive options income (Iron Condors, Broken Wing Butterflies,
  Calendar Spreads) on SPX/SPY
- 0DTE to 7DTE expiration range depending on regime
- Delta-neutral portfolio with theta as primary P&L driver
"""

__version__ = "1.0.0"
__author__ = "AVRPE Quant System"
