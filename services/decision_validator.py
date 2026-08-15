"""Decision validation pipeline for trading decisions."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional, Sequence

from domain import (
    ExecutionCommand,
    MarketState,
    Position,
    TradingDecision,
    ValidationResult,
)
from risk.policy import RiskPolicy

SUPPORTED_ACTIONS = {
    "hold",
    "enter_long",
    "exit_long",
    "enter_short",
    "exit_short",
    "cancel",
}


class DecisionValidator:
    """Validates a TradingDecision before it may become an ExecutionCommand.

    Pipeline: schema → policy → market freshness → portfolio → command.
    """

    def __init__(self, policy: Optional[RiskPolicy] = None):
        self._policy = policy or RiskPolicy.from_env()

    def validate(
        self,
        decision: TradingDecision,
        open_positions: Sequence[Position] = (),
        whitelist: Sequence[str] = (),
        blacklist: Sequence[str] = (),
        pair_locks: Sequence[str] = (),
        portfolio_value: float = 0.0,
        already_executed_ids: Optional[set[str]] = None,
        market_state: Optional[MarketState] = None,
        require_market_state: bool = False,
    ) -> ValidationResult:
        errors: List[str] = []
        warnings: List[str] = []

        # ── Schema validation ────────────────────────────────────────────────
        if not decision.decision_id:
            errors.append("decision_id_missing")
        if not decision.symbol:
            errors.append("symbol_missing")
        if decision.action not in SUPPORTED_ACTIONS:
            errors.append(f"unsupported_action:{decision.action}")
        if not 0.0 <= decision.confidence <= 1.0:
            errors.append("confidence_out_of_range")
        if decision.action != "hold" and (decision.stake_amount is None or decision.stake_amount <= 0):
            errors.append("stake_amount_invalid")

        # ── Policy validation ────────────────────────────────────────────────
        if blacklist and decision.symbol in blacklist:
            errors.append("symbol_blacklisted")
        if whitelist and decision.symbol not in whitelist:
            errors.append("symbol_not_in_whitelist")
        if pair_locks and decision.symbol in pair_locks:
            errors.append("pair_locked")
        if decision.action not in self._policy.allowed_actions:
            errors.append("action_not_allowed_by_policy")

        # Decision expiry
        age = (datetime.now(timezone.utc) - decision.created_at).total_seconds()
        if age > self._policy.maximum_decision_age_seconds:
            errors.append("decision_expired")

        # ── Market state / freshness ─────────────────────────────────────────
        # Hold may remain valid without market context. Runtime paths set
        # require_market_state=True so non-hold decisions fail closed.
        if decision.action != "hold" and require_market_state:
            if market_state is None:
                errors.append("market_data_unavailable")
            else:
                if market_state.symbol and market_state.symbol != decision.symbol:
                    errors.append("market_symbol_mismatch")
                if market_state.is_fresh is False:
                    errors.append("stale_market_data")

        # ── Portfolio validation ─────────────────────────────────────────────
        if len(open_positions) >= self._policy.maximum_open_positions:
            errors.append("maximum_open_positions_exceeded")
        symbol_positions = [p for p in open_positions if p.symbol == decision.symbol]
        if len(symbol_positions) >= self._policy.maximum_positions_per_symbol:
            errors.append("maximum_positions_per_symbol_exceeded")

        # Sufficient funds — fail closed when portfolio cannot be valued on
        # runtime paths that opt into require_market_state (Phase 2).
        if decision.action != "hold" and decision.stake_amount:
            if require_market_state and portfolio_value <= 0:
                errors.append("portfolio_value_unknown")
            elif portfolio_value > 0 and decision.stake_amount > portfolio_value:
                errors.append("insufficient_funds")

        # Idempotency
        if already_executed_ids and decision.decision_id in already_executed_ids:
            errors.append("decision_already_executed")

        # ── Build command if valid ────────────────────────────────────────────
        adjusted_command: Optional[ExecutionCommand] = None
        if not errors:
            adjusted_command = ExecutionCommand(
                symbol=decision.symbol,
                action=decision.action,
                stake_amount=decision.stake_amount,
                stake_currency=decision.stake_currency,
                order_type=decision.order_type,
                limit_price=decision.limit_price,
                stop_loss=decision.stop_loss,
                take_profit=decision.take_profit,
                leverage=decision.leverage,
                decision_id=decision.decision_id,
            )

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            adjusted_command=adjusted_command,
        )
