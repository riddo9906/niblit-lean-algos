"""Risk management layer for Freqtrade-Lean bridge."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


@dataclass
class RiskViolation:
    """Represents a risk check violation."""
    code: str
    message: str
    severity: str = "warning"  # warning, error, critical
    timestamp: float = field(default_factory=time.time)


class RiskManager:
    """Risk management for Niblit-Freqtrade bridge.

    Responsibilities:
    - Max daily loss limits
    - Max drawdown limits
    - Exposure limits
    - Leverage limits
    - Kelly sizing (optional)
    - Volatility sizing
    - AI-adjusted position sizing
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._config = config or {}
        self._daily_pnl: float = 0.0
        self._daily_loss_limit = float(self._config.get("max_daily_loss", 0.03))
        self._drawdown_limit = float(self._config.get("max_drawdown", 0.10))
        self._position_limit = float(self._config.get("max_position_size", 0.10))
        self._leverage_limit = float(self._config.get("max_leverage", 3.0))
        self._use_kelly = bool(self._config.get("use_kelly_sizing", False))
        self._use_volatility = bool(self._config.get("use_volatility_sizing", True))
        self._violations: List[RiskViolation] = []

    def check_trade_allowed(
        self,
        pair: str,
        side: str,
        stake: float,
        equity: float,
        current_drawdown: float,
    ) -> List[RiskViolation]:
        """Check if a trade is allowed under current risk limits."""
        violations = []

        # Daily loss check
        if self._daily_pnl < -equity * self._daily_loss_limit:
            violations.append(RiskViolation(
                code="daily_loss_exceeded",
                message=f"Daily loss limit exceeded: {self._daily_pnl:.2f}",
                severity="critical",
            ))

        # Drawdown check
        if current_drawdown > self._drawdown_limit:
            violations.append(RiskViolation(
                code="drawdown_exceeded",
                message=f"Drawdown {current_drawdown:.2%} exceeds limit {self._drawdown_limit:.2%}",
                severity="critical",
            ))

        # Position size check
        position_pct = stake / equity if equity > 0 else 0
        if position_pct > self._position_limit:
            violations.append(RiskViolation(
                code="position_size_exceeded",
                message=f"Position size {position_pct:.2%} exceeds limit {self._position_limit:.2%}",
                severity="warning",
            ))

        self._violations.extend(violations)
        return violations

    def size_position(
        self,
        confidence: float,
        volatility: Optional[float] = None,
        equity: float = 1000.0,
        base_size: float = 100.0,
    ) -> float:
        """Calculate position size with optional volatility and Kelly adjustments."""
        size = base_size * min(1.0, confidence)

        if self._use_volatility and volatility:
            vol_multiplier = max(0.5, min(1.5, 1.0 / volatility)) if volatility > 0 else 1.0
            size *= vol_multiplier

        if self._use_kelly:
            # Simplified Kelly: f = p - q where p = win rate, q = loss rate
            # Using confidence as proxy for win rate
            kelly_multiplier = max(0.0, min(1.0, confidence * 2 - 1))
            size *= kelly_multiplier

        # Apply position limit
        max_stake = equity * self._position_limit
        return min(size, max_stake)

    def record_trade_result(
        self, pair: str, pnl: float, fees: float = 0.0
    ) -> None:
        """Record trade result for daily PnL tracking."""
        net_pnl = pnl - fees
        self._daily_pnl += net_pnl
        log.info("Trade recorded: %s pnl=%.2f fees=%.2f daily_pnl=%.2f", pair, pnl, fees, self._daily_pnl)

    def reset_daily(self) -> None:
        """Reset daily counters (called at start of new day)."""
        self._daily_pnl = 0.0
        self._violations = []
        log.info("Risk manager daily reset")

    def status(self) -> Dict[str, Any]:
        """Return current risk status."""
        return {
            "daily_pnl": self._daily_pnl,
            "daily_loss_limit": self._daily_loss_limit,
            "drawdown_limit": self._drawdown_limit,
            "position_limit": self._position_limit,
            "leverage_limit": self._leverage_limit,
            "use_kelly": self._use_kelly,
            "use_volatility_sizing": self._use_volatility,
            "violations_today": len(self._violations),
        }