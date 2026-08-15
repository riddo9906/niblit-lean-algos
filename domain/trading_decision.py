"""Trading decision domain models."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class TradingDecision:
    decision_id: str
    symbol: str
    action: str
    confidence: float
    rationale: str
    strategy: Optional[str] = None
    stake_amount: Optional[float] = None
    stake_currency: Optional[str] = None
    order_type: Optional[str] = None
    limit_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    leverage: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
