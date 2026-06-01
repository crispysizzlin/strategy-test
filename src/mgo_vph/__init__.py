"""
MGO-VPH: Microstructure-Gated Optimized Variance Premium Harvester.

Reference implementations of institutional-grade math for Schwab Level-3
defined-risk premium harvesting. Not financial advice.
"""

__version__ = "0.1.0"

from mgo_vph.signals import CompositeSignal, SignalConfig, evaluate_entry

__all__ = ["CompositeSignal", "SignalConfig", "evaluate_entry", "__version__"]
