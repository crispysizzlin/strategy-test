"""Research implementation of the Adaptive ORB strategy.

This package is deliberately dependency-light so its accounting and validation logic can
be audited without a large backtesting framework. It is not an execution engine.
"""

from .backtest import BacktestResult, run_backtest
from .config import ResearchConfig, load_config

__all__ = ["BacktestResult", "ResearchConfig", "load_config", "run_backtest"]

