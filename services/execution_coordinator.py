"""Execution coordinator for validated trading decisions."""
from __future__ import annotations

import logging
from typing import Optional, Sequence

from adapters.base import TradingAdapter
from domain import (
    ExecutionCommand,
    ExecutionResult,
    MarketState,
    Position,
    TradingDecision,
)
from persistence.execution_repository import ExecutionRepository
from risk.engine import RiskEngine

from .decision_validator import DecisionValidator

log = logging.getLogger("ExecutionCoordinator")


class ExecutionCoordinator:
    """Coordinates validation → risk → execution for trading decisions.

    Pipeline:
      1. Idempotency check
      2. Schema + policy + market validation
      3. Risk assessment
      4. Execution command creation
      5. Execution mode verification
      6. Adapter health check
      7. Adapter invocation
      8. Result persistence
    """

    def __init__(
        self,
        adapter: TradingAdapter,
        validator: Optional[DecisionValidator] = None,
        risk_engine: Optional[RiskEngine] = None,
        repository: Optional[ExecutionRepository] = None,
        execution_mode: str = "observe",
        live_acknowledgement: Optional[str] = None,
    ):
        self._adapter = adapter
        self._validator = validator or DecisionValidator()
        self._risk_engine = risk_engine or RiskEngine()
        self._repository = repository or ExecutionRepository()
        self._execution_mode = execution_mode
        self._live_acknowledgement = live_acknowledgement

    async def process_decision(
        self,
        decision: TradingDecision,
        open_positions: Sequence[Position] = (),
        whitelist: Sequence[str] = (),
        blacklist: Sequence[str] = (),
        pair_locks: Sequence[str] = (),
        portfolio_value: float = 0.0,
        market_state: Optional[MarketState] = None,
        already_executed_ids: Optional[set[str]] = None,
        require_market_state: bool = False,
    ) -> ExecutionResult:
        if self._repository.has_decision(decision.decision_id):
            return ExecutionResult(
                success=False,
                status="duplicate",
                error=f"Decision {decision.decision_id} already executed",
            )

        validation = self._validator.validate(
            decision=decision,
            open_positions=open_positions,
            whitelist=whitelist,
            blacklist=blacklist,
            pair_locks=pair_locks,
            portfolio_value=portfolio_value,
            already_executed_ids=already_executed_ids,
            market_state=market_state,
            require_market_state=require_market_state,
        )
        if not validation.valid:
            log.info("[Trading] Validation: rejected errors=%s", validation.errors)
            return ExecutionResult(
                success=False,
                status="validation_failed",
                error="; ".join(validation.errors),
            )
        log.info("[Trading] Validation: approved decision_id=%s", decision.decision_id)

        risk = self._risk_engine.assess(
            decision=decision,
            portfolio_value=portfolio_value,
            open_positions=open_positions,
        )
        if not risk.approved:
            log.info("[Trading] Risk: rejected violations=%s", risk.violations)
            return ExecutionResult(
                success=False,
                status="risk_rejected",
                error="; ".join(risk.violations),
            )
        log.info("[Trading] Risk: approved decision_id=%s", decision.decision_id)

        command = validation.adjusted_command
        if command is None:
            return ExecutionResult(
                success=False,
                status="internal_gateway_error",
                error="No execution command was produced",
            )
        if risk.adjusted_stake_amount is not None:
            command.stake_amount = risk.adjusted_stake_amount

        mode_error = self._check_execution_mode(decision, command)
        if mode_error:
            log.info("[Trading] Execution: blocked by observe mode")
            return ExecutionResult(success=False, status="execution_blocked", error=mode_error)

        # Adapter health must be verified before any order request is sent.
        try:
            adapter_health = await self._adapter.health()
            healthy = bool(getattr(adapter_health, "healthy", False))
        except Exception as exc:
            log.warning("[Trading] Adapter health check failed: %s", exc)
            return ExecutionResult(
                success=False,
                status="adapter_unavailable",
                error=str(exc),
            )
        if not healthy:
            return ExecutionResult(
                success=False,
                status="adapter_unavailable",
                error=getattr(adapter_health, "error", None) or "adapter unhealthy",
            )

        self._repository.record_request(decision.decision_id, vars(command), "freqtrade")

        try:
            result = await self._adapter.execute_order(command)
            self._repository.record_result(
                decision.decision_id,
                vars(result),
                result.provider_trade_id,
                result.status,
            )
            return result
        except Exception as exc:
            self._repository.record_result(
                decision.decision_id,
                {"error": str(exc)},
                None,
                "error",
            )
            return ExecutionResult(
                success=False,
                status="execution_error",
                error=str(exc),
            )

    def _check_execution_mode(self, decision: TradingDecision, command: ExecutionCommand) -> Optional[str]:
        """Enforce observe / paper / live safety modes."""
        if self._execution_mode == "observe":
            return "execution_blocked: observe mode is read-only"
        if self._execution_mode == "paper":
            return None
        if self._execution_mode == "live":
            if self._live_acknowledgement != "I_UNDERSTAND_LIVE_TRADING_RISK":
                return "execution_blocked: live trading acknowledgement missing or invalid"
            return None
        return f"execution_blocked: unknown mode {self._execution_mode}"
