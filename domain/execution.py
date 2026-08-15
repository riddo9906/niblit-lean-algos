"""Execution domain models."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class ExecutionCommand:
    symbol: str
    action: str
    stake_amount: Optional[float] = None
    stake_currency: Optional[str] = None
    order_type: Optional[str] = None
    limit_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    leverage: Optional[float] = None
    decision_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    adjusted_command: Optional[ExecutionCommand] = None


@dataclass
class RiskAssessment:
    approved: bool
    risk_score: float
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    adjusted_stake_amount: Optional[float] = None
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ExecutionResult:
    success: bool
    order_id: Optional[str] = None
    provider_trade_id: Optional[str] = None
    status: str = "unknown"
    error: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)
    executed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class CancelCommand:
    order_id: str
    symbol: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
