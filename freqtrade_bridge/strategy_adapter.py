"""NiblitFreqtradeStrategy — Freqtrade adapter for Niblit signals.

This strategy consumes signals from Niblit (via file, WebSocket, or ZeroMQ)
and executes them through Freqtrade. It never generates trading logic itself.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

from freqtrade.strategy import IStrategy
from freqtrade.strategy import IntParameter, DecimalParameter, BooleanParameter
from freqtrade.persistence import Trade

from .signal import Signal, SignalSide
from .config import BridgeConfig

log = logging.getLogger(__name__)


class NiblitFreqtradeStrategy(IStrategy):
    """Freqtrade strategy that executes Niblit-generated signals.

    Responsibilities:
    - Receive signals from Niblit signal transport
    - Verify signal freshness
    - Verify confidence threshold
    - Verify symbol/exchange availability
    - Open/close positions
    - Update stop-loss/take-profit
    """

    # Strategy parameters
    minimal_roi = {
        "0": 0.10,
        "60": 0.05,
        "180": 0.02,
    }
    stoploss = -0.10  # Default; overridden by signals
    timeframe = "5m"
    process_only_candle_after = 0

    # Niblit-specific parameters
    niblit_confidence_threshold = DecimalParameter(
        0.5, 0.99, default=float(os.getenv("NIBLIT_CONFIDENCE_THRESHOLD", "0.70")),
        space="buy"
    )

    # Internal state
    _config: Optional[BridgeConfig] = None
    _last_signal_time: float = 0.0
    _last_signal_pair: str = ""
    _signal_cache: Dict[str, Any] = {}

    def __init__(self, config_path: Optional[str] = None):
        super().__init__()
        if config_path:
            self._config = BridgeConfig.from_yaml(config_path)
        else:
            self._config = BridgeConfig.from_env()

    def get_signal(self) -> Optional[Signal]:
        """Fetch the latest signal from the configured transport."""
        transport = self._config.lean.signal_transport if self._config else "file"
        signal_path = self._config.lean.signal_file if self._config else "/tmp/niblit_signal.json"

        try:
            if transport == "file":
                return self._read_file_signal(signal_path)
        except Exception as exc:
            log.debug("Signal fetch error (%s): %s", transport, exc)
        return None

    def _read_file_signal(self, path: str) -> Optional[Signal]:
        """Read signal from JSON file."""
        import json
        try:
            if not os.path.isfile(path):
                return None
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return Signal.from_dict(data)
        except Exception:
            return None

    def niblit_generate_signal(self, pair: str) -> Optional[Signal]:
        """Generate a signal for the given pair from Niblit's TradingBrain.

        This method reads the signal file and checks if it applies to the given pair.
        """
        signal = self.get_signal()
        if signal is None or signal.side == SignalSide.HOLD:
            return None

        # Check if signal applies to this pair
        signal_pair = signal.pair.replace("/", "").replace("-", "")
        pair_norm = pair.replace("/", "").replace("-", "")
        if signal_pair != pair_norm:
            return None

        return signal

    def should_long(self, pair: str, **kwargs) -> bool:
        """Determine if we should open a long position based on Niblit signal."""
        signal = self.niblit_generate_signal(pair)
        if signal is None:
            return False

        # Verify freshness
        max_age = (self._config.lean.max_signal_age_secs if self._config else 300)
        if not signal.is_fresh(max_age):
            log.debug("Signal stale for %s (age > %ss)", pair, max_age)
            return False

        # Verify confidence
        threshold = float(self.niblit_confidence_threshold)
        if signal.confidence < threshold:
            log.debug(
                "Signal confidence %.2f below threshold %.2f for %s",
                signal.confidence, threshold, pair
            )
            return False

        return signal.side in (SignalSide.BUY, SignalSide.HOLD)

    def should_short(self, pair: str, **kwargs) -> bool:
        """Determine if we should open a short position based on Niblit signal."""
        signal = self.niblit_generate_signal(pair)
        if signal is None:
            return False

        max_age = (self._config.lean.max_signal_age_secs if self._config else 300)
        if not signal.is_fresh(max_age):
            return False

        threshold = float(self.niblit_confidence_threshold)
        if signal.confidence < threshold:
            return False

        return signal.side == SignalSide.SELL

    def custom_stake_amount(
        self,
        pair: str,
        current_time,
        current_rate: float,
        proposed_stake: float,
        min_stake: Optional[float],
        max_stake: float,
        leverage: float,
        entry_tag: Optional[str],
        side: str,
        **kwargs,
    ) -> float:
        """Calculate stake amount from Niblit signal's risk_pct."""
        signal = self.niblit_generate_signal(pair)
        if signal is None:
            return proposed_stake

        risk_pct = signal.leverage  # Using leverage field for position sizing
        max_position = self._config.risk.max_position_size if self._config else 0.10

        sized = proposed_stake * min(1.0, max_position * risk_pct)
        if min_stake is not None:
            sized = max(sized, min_stake)
        if max_stake and max_stake > 0:
            sized = min(sized, max_stake)

        return float(sized)

    def custom_stoploss(
        self, pair: str, trade: Trade, current_rate: float, **kwargs
    ) -> Optional[float]:
        """Return stop-loss from signal or default."""
        signal = self.get_signal()
        if signal and signal.stop_loss:
            return -signal.stop_loss
        return self.stoploss

    def custom_takeprofit(
        self, pair: str, trade: Trade, current_rate: float, **kwargs
    ) -> Optional[float]:
        """Return take-profit from signal or default."""
        signal = self.get_signal()
        if signal and signal.take_profit:
            return signal.take_profit
        return None

    def get_current_rate(self, pair: str) -> Optional[float]:
        """Get current rate for a pair (stub - Freqtrade handles this)."""
        return None

    def get_trade_stake(self, pair: str) -> float:
        """Calculate trade stake based on signal confidence."""
        signal = self.niblit_generate_signal(pair)
        if signal:
            return signal.confidence * 100  # Base stake on confidence
        return 100.0  # Default stake

    def status(self) -> Dict[str, Any]:
        """Return strategy status for monitoring."""
        signal = self.get_signal()
        return {
            "last_signal": signal.to_dict() if signal else None,
            "config": self._config.model_dump() if self._config else None,
            "timeframe": self.timeframe,
        }