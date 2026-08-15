"""
freqtrade_bridge — Production-grade bridge between Lean and Freqtrade.

Architecture:
    Niblit → Lean Strategy Layer → Signal API → Freqtrade Strategy Adapter → Freqtrade Engine → Exchange
"""

from .bridge import FreqtradeBridge
from .signal import Signal, SignalSide
from .signal_server import SignalServer
from .strategy_adapter import NiblitFreqtradeStrategy
from .risk_manager import RiskManager
from .portfolio_manager import PortfolioManager
from .execution_manager import ExecutionManager
from .position_tracker import PositionTracker
from .config import BridgeConfig

__all__ = [
    "FreqtradeBridge",
    "Signal",
    "SignalSide",
    "SignalServer",
    "NiblitFreqtradeStrategy",
    "RiskManager",
    "PortfolioManager",
    "ExecutionManager",
    "PositionTracker",
    "BridgeConfig",
]
