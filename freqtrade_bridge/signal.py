"""Signal model for Lean→Freqtrade communication."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class SignalSide(str, Enum):
    """Trade direction."""
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class Signal:
    """Standardized trading signal from Niblit/Lean strategies.

    Signals are emitted by LEAN strategies and consumed by Freqtrade via
    the bridge. They are NEVER placed directly by the strategy itself.

    Attributes:
        symbol: Trading pair (e.g., "BTC/USDT")
        side: Signal direction (BUY/SELL/HOLD)
        confidence: Signal confidence score (0.0-1.0)
        leverage: Leverage multiplier (1.0 = no leverage)
        stop_loss: Stop loss distance as fraction (e.g., 0.03 = 3%)
        take_profit: Take profit distance as fraction (e.g., 0.08 = 8%)
        reason: Human-readable reason for the signal
        timestamp: Unix timestamp (defaults to now)
        metadata: Additional signal metadata
    """
    symbol: str
    side: SignalSide
    confidence: float
    leverage: float = 1.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reason: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Optional["Signal"]:
        """Create a Signal from a dictionary, validating required fields."""
        if not isinstance(data, dict):
            return None

        symbol = data.get("symbol", "")
        if not symbol:
            return None

        side_str = str(data.get("side", "hold")).lower()
        try:
            side = SignalSide(side_str)
        except ValueError:
            side = SignalSide.HOLD

        return cls(
            symbol=str(symbol).replace(":", "/").replace("_", "/"),
            side=side,
            confidence=float(data.get("confidence", 0.5)),
            leverage=float(data.get("leverage", 1.0)),
            stop_loss=float(data["stop_loss"]) if data.get("stop_loss") else None,
            take_profit=float(data["take_profit"]) if data.get("take_profit") else None,
            reason=str(data.get("reason", "")),
            timestamp=float(data.get("timestamp", time.time())),
            metadata=data.get("metadata", {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert signal to dictionary for serialization."""
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "confidence": self.confidence,
            "leverage": self.leverage,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @property
    def is_fresh(self, max_age_secs: float = 300.0) -> bool:
        """Check if signal is within freshness window."""
        return (time.time() - self.timestamp) <= max_age_secs

    @property
    def pair(self) -> str:
        """Freqtrade-compatible pair format (e.g., 'BTC/USDT')."""
        return self.symbol