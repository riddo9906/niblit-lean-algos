"""Portfolio domain models."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class Order:
    order_id: str
    symbol: str
    side: str
    order_type: str
    amount: float
    price: Optional[float] = None
    status: str = "open"
    filled: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Position:
    position_id: str
    symbol: str
    side: str
    status: str
    opened_at: datetime
    trade_id: Optional[str] = None
    closed_at: Optional[datetime] = None
    entry_price: float = 0.0
    current_price: float = 0.0
    exit_price: Optional[float] = None
    stake_amount: float = 0.0
    quantity: float = 0.0
    profit_amount: float = 0.0
    profit_ratio: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    leverage: Optional[float] = None
    strategy: Optional[str] = None
    entry_tag: Optional[str] = None
    exit_reason: Optional[str] = None
    exchange: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PerformanceSummary:
    start_time: datetime
    end_time: datetime
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_profit: float = 0.0
    total_loss: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: Optional[float] = None
    win_rate: float = 0.0
    profit_factor: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PortfolioState:
    balances: List[Any] = field(default_factory=list)
    positions: List[Position] = field(default_factory=list)
    orders: List[Order] = field(default_factory=list)
    total_equity: float = 0.0
    available_capital: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
