"""Main bridge module connecting Niblit to Freqtrade."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .config import BridgeConfig
from .signal import Signal
from .risk_manager import RiskManager
from .portfolio_manager import PortfolioManager
from .execution_manager import ExecutionManager
from .position_tracker import PositionTracker

log = logging.getLogger(__name__)


class FreqtradeBridge:
    """Main bridge between Niblit AI and Freqtrade execution.

    Architecture:
        Niblit → Lean Strategy → Signal → Freqtrade Strategy → Exchange
    """

    def __init__(self, config_path: Optional[str] = None):
        self._config = BridgeConfig.from_yaml(config_path) if config_path else BridgeConfig.from_env()
        self._risk_manager = RiskManager(self._config.risk.model_dump())
        self._portfolio_manager = PortfolioManager()
        self._execution_manager = ExecutionManager(
            self._config.freqtrade.model_dump() if self._config else None
        )
        self._position_tracker = PositionTracker()
        self._paused = False

    def process_signal(self, signal: Signal) -> Optional[str]:
        """Process a signal through the bridge.

        Returns the action taken ("executed", "rejected", "skipped", or None).
        """
        if self._paused:
            log.warning("Signal ignored - trading paused: %s", signal.pair)
            return "skipped"

        # Risk check
        stake = 100.0  # Would be calculated from signal
        equity = 1000.0  # Would come from Freqtrade
        violations = self._risk_manager.check_trade_allowed(
            pair=signal.pair,
            side=signal.side.value,
            stake=stake,
            equity=equity,
            current_drawdown=0.0,
        )
        if violations:
            log.warning("Signal rejected due to risk violations: %s", signal.pair)
            return "rejected"

        # Execute (in production, this would go to Freqtrade)
        log.info("Signal approved for execution: %s %s", signal.side.value, signal.pair)
        return "approved"

    def generate_signal(
        self,
        symbol: str,
        side: str,
        confidence: float,
        leverage: float = 1.0,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        reason: str = "",
    ) -> Signal:
        """Create a standardized signal for Freqtrade consumption."""
        return Signal(
            symbol=symbol,
            side=side if isinstance(side, str) else str(side),
            confidence=confidence,
            leverage=leverage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reason=reason,
        )

    def approve_trade(self, signal: Signal) -> bool:
        """Approve a trade based on governance and risk rules."""
        return self.process_signal(signal) == "approved"

    def reject_trade(self, signal: Signal) -> bool:
        """Reject a trade (always returns True after logging)."""
        log.info("Trade explicitly rejected: %s", signal.pair)
        return True

    def pause_trading(self) -> None:
        """Pause trading activity."""
        self._paused = True
        self._position_tracker.reconcile_all = lambda: None  # type: ignore
        log.warning("Trading paused")

    def resume_trading(self) -> None:
        """Resume trading activity."""
        self._paused = False
        log.info("Trading resumed")

    def emergency_stop(self) -> None:
        """Emergency stop all positions."""
        self._paused = True
        self._execution_manager.emergency_liquidation("*")
        log.critical("Emergency stop executed")

    def status(self) -> Dict[str, Any]:
        """Return full bridge status."""
        return {
            "paused": self._paused,
            "config": self._config.model_dump() if self._config else None,
            "risk": self._risk_manager.status(),
            "portfolio": self._portfolio_manager.status(),
            "execution": self._execution_manager.status(),
            "positions": self._position_tracker.status(),
        }


# Singleton instance for convenience
_bridge: Optional[FreqtradeBridge] = None


def get_bridge(config_path: Optional[str] = None) -> FreqtradeBridge:
    """Get or create the global bridge instance."""
    global _bridge
    if _bridge is None:
        _bridge = FreqtradeBridge(config_path)
    return _bridge