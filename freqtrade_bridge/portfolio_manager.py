"""Portfolio management for Freqtrade-Lean bridge."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


@dataclass
class Position:
    """Represents a single position."""
    pair: str
    side: str  # long, short
    amount: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    realized_pnl: float
    timestamp: float = field(default_factory=time.time)


class PortfolioManager:
    """Track portfolio state across Lean, Freqtrade, and Exchange.

    Responsibilities:
    - Maintain current positions
    - Track available capital
    - Track realized/unrealized PnL
    - Track sector/pair exposure
    - Track open orders
    """

    def __init__(self):
        self._positions: Dict[str, Position] = {}
        self._available_capital: float = 0.0
        self._total_equity: float = 0.0
        self._realized_pnl: float = 0.0
        self._open_orders: List[Dict[str, Any]] = []
        self._sector_exposure: Dict[str, float] = {}
        self._pair_exposure: Dict[str, float] = {}

    def update_position(self, pair: str, data: Dict[str, Any]) -> None:
        """Update or create a position from Freqtrade/Exchange data."""
        self._positions[pair] = Position(
            pair=pair,
            side=data.get("side", "long"),
            amount=float(data.get("amount", 0)),
            entry_price=float(data.get("entry_price", 0)),
            current_price=float(data.get("current_price", 0)),
            unrealized_pnl=float(data.get("unrealized_pnl", 0)),
            realized_pnl=float(data.get("realized_pnl", 0)),
        )
        self._pair_exposure[pair] = float(data.get("amount", 0))
        log.debug("Position updated: %s", pair)

    def close_position(self, pair: str) -> Optional[Position]:
        """Close a position and return its final state."""
        pos = self._positions.pop(pair, None)
        if pos:
            self._realized_pnl += pos.realized_pnl
            self._pair_exposure.pop(pair, None)
            log.info("Position closed: %s pnl=%.2f", pair, pos.realized_pnl or pos.unrealized_pnl)
        return pos

    def add_order(self, order: Dict[str, Any]) -> None:
        """Track an open order."""
        self._open_orders.append(order)
        log.debug("Order added: %s", order.get("id"))

    def remove_order(self, order_id: str) -> None:
        """Remove a filled/cancelled order."""
        self._open_orders = [o for o in self._open_orders if o.get("id") != order_id]

    def update_sector_exposure(self, sector: str, exposure: float) -> None:
        """Update sector exposure (e.g., 'defi', 'layer1', 'memecoin')."""
        self._sector_exposure[sector] = exposure

    def calculate_drawdown(self, equity_history: List[float]) -> float:
        """Calculate current drawdown from equity history."""
        if not equity_history:
            return 0.0
        peak = max(equity_history)
        current = equity_history[-1]
        return (peak - current) / peak if peak > 0 else 0.0

    def positions(self) -> List[Position]:
        """Return list of current positions."""
        return list(self._positions.values())

    def position(self, pair: str) -> Optional[Position]:
        """Return position for a specific pair."""
        return self._positions.get(pair)

    def total_exposure(self) -> float:
        """Return total portfolio exposure (0-1)."""
        return sum(self._pair_exposure.values())

    def status(self) -> Dict[str, Any]:
        """Return portfolio status summary."""
        return {
            "positions": {p.pair: p.amount for p in self._positions.values()},
            "available_capital": self._available_capital,
            "total_equity": self._total_equity,
            "realized_pnl": self._realized_pnl,
            "open_orders": len(self._open_orders),
            "sector_exposure": dict(self._sector_exposure),
            "pair_exposure": dict(self._pair_exposure),
            "total_exposure": self.total_exposure(),
        }