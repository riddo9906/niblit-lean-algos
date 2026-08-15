"""Execution domain models."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Execution lifecycle states. Semantics:
#   received       → request accepted into repository (idempotency anchor)
#   validated      → DecisionValidator passed
#   risk_approved  → RiskEngine approved
#   prepared       → execution command built
#   submitting     → provider request in-flight (NOT yet accepted by provider)
#   submitted      → provider response proved acceptance
#   confirmed_open → reconciliation found a live provider trade
#   confirmed_closed → reconciliation found a closed provider trade
#   rejected       → deterministic gate refused the order (no provider call)
#   failed         → hard failure before/without provider acceptance
#   ambiguous      → provider outcome cannot be determined (no auto-retry)
EXECUTION_STATES = (
    "received",
    "validated",
    "risk_approved",
    "prepared",
    "submitting",
    "submitted",
    "confirmed_open",
    "confirmed_closed",
    "rejected",
    "failed",
    "ambiguous",
)

# States that indicate the decision is "taken" and must never be re-submitted.
# If a second request arrives for the same decision_id in any of these states,
# the runtime returns a structured existing-execution response and issues NO
# provider call (persistent idempotency).
FINAL_EXECUTION_STATES = (
    "submitted",
    "confirmed_open",
    "confirmed_closed",
    "rejected",
    "failed",
    "ambiguous",
)

RECONCILIATION_STATES = ("none", "confirmed_open", "confirmed_closed", "rejected", "not_found", "ambiguous")


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
