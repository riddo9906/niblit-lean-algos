"""Trading decision domain models."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Versioned canonical cross-repo execution contract.
# niblit.trading.decision.v1 is the documented, versioned JSON/API representation
# that all proposal sources (Niblit memory, Cloud AI, Lean/strategy) converge on
# before the authoritative TradingRuntimeManager may act on them.
CANONICAL_CONTRACT_VERSION = "niblit.trading.decision.v1"

CANONICAL_STATE_FIELDS = (
    "decision_id",
    "symbol",
    "action",
    "confidence",
    "rationale",
    "strategy",
    "stake_amount",
    "stake_currency",
    "order_type",
    "limit_price",
    "stop_loss",
    "take_profit",
    "leverage",
    "metadata",
    "created_at",
)


@dataclass
class TradingDecision:
    """Canonical trading decision contract (niblit.trading.decision.v1).

    This is the single domain object that the authoritative
    ``TradingRuntimeManager`` accepts for validation, risk, and execution.
    All other trading-domain objects (legacy Niblit decision, Cloud
    TradeProposal) must be explicitly translated into this shape before they
    may reach the execution pipeline.
    """

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

    def to_canonical_dict(
        self, contract_version: str = CANONICAL_CONTRACT_VERSION
    ) -> Dict[str, Any]:
        """Serialize to the versioned canonical JSON/API representation.

        The dict is proposal-shaped only: it carries no execution approval.
        The authoritative DecisionValidator/RiskEngine still decide whether the
        decision may actually be executed.
        """
        return {
            "schema_version": contract_version,
            "decision_id": self.decision_id,
            "symbol": self.symbol,
            "action": self.action,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "strategy": self.strategy,
            "stake_amount": self.stake_amount,
            "stake_currency": self.stake_currency,
            "order_type": self.order_type,
            "limit_price": self.limit_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "leverage": self.leverage,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    @classmethod
    def from_canonical_dict(cls, data: Dict[str, Any]) -> "TradingDecision":
        """Deserialize from the canonical dict (accepts with/without schema)."""
        created_raw = data.get("created_at")
        created_at: datetime
        if isinstance(created_raw, datetime):
            created_at = created_raw
        elif isinstance(created_raw, str):
            created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        else:
            created_at = datetime.now(timezone.utc)
        return cls(
            decision_id=str(data["decision_id"]),
            symbol=str(data["symbol"]),
            action=str(data["action"]),
            confidence=float(data["confidence"]),
            rationale=str(data.get("rationale", "")),
            strategy=data.get("strategy"),
            stake_amount=data.get("stake_amount"),
            stake_currency=data.get("stake_currency"),
            order_type=data.get("order_type"),
            limit_price=data.get("limit_price"),
            stop_loss=data.get("stop_loss"),
            take_profit=data.get("take_profit"),
            leverage=data.get("leverage"),
            metadata=data.get("metadata") or {},
            created_at=created_at,
        )

    def provider_entry_tag(self, max_length: int = 20) -> str:
        """Deterministic, safe provenance tag for the provider.

        Freqtrade ``enter_tag`` is bounded; we never rely solely on
        symbol+timestamp for attribution when a provider tag is available.
        Full decision_id is always persisted locally in the repository.
        """
        return _safe_provenance_tag(self.decision_id, max_length=max_length)


def _safe_provenance_tag(decision_id: str, max_length: int = 20) -> str:
    """Produce ``niblit:<safe>`` where safe is truncated or hashed to fit.

    Uses a leading slice if it already fits, otherwise a deterministic SHA-256
    hex hash slice so the same decision_id always maps to the same tag.
    """
    import hashlib

    prefix = "niblit"
    budget = max(max_length - len(prefix) - 1, 4)
    if len(decision_id) <= budget:
        safe = decision_id
    else:
        digest = hashlib.sha256(decision_id.encode("utf-8")).hexdigest()
        safe = decision_id[: budget // 2] + digest[: budget - budget // 2]
    return f"{prefix}:{safe}"
