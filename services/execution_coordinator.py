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


def _result_dict(result: "ExecutionResult") -> dict:
    return {
        "success": result.success,
        "status": result.status,
        "error": result.error,
        "provider_trade_id": result.provider_trade_id,
        "raw": result.raw,
    }


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
        realized_pnl_today: float = 0.0,
    ) -> ExecutionResult:
        if self._repository.has_decision(decision.decision_id):
            # Persistent idempotency: any in-flight or terminal record blocks a
            # second provider submission. Return structured existing-execution info.
            existing = self._repository.get_execution(decision.decision_id) or {}
            return ExecutionResult(
                success=False,
                status="duplicate",
                error=f"Decision {decision.decision_id} already executed",
                raw={
                    "decision_id": decision.decision_id,
                    "duplicate": True,
                    "existing_status": existing.get("status"),
                    "provider_trade_id": existing.get("provider_trade_id"),
                    "reconciliation_status": existing.get("reconciliation_status"),
                },
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
            realized_pnl_today=realized_pnl_today,
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
            # Deterministic blockage — no provider call, no execution record.
            return ExecutionResult(
                success=False, status="execution_blocked", error=mode_error
            )

        # Adapter health must be verified before any order request is sent.
        try:
            adapter_health = await self._adapter.health()
            healthy = bool(getattr(adapter_health, "healthy", False))
        except Exception as exc:
            log.warning("[Trading] Adapter health check failed: %s", exc)
            return self._reject(
                decision, command, "adapter_unavailable", str(exc),
                status="adapter_unavailable",
            )
        if not healthy:
            return self._reject(
                decision, command, "adapter_unavailable",
                getattr(adapter_health, "error", None) or "adapter unhealthy",
                status="adapter_unavailable",
            )

        # Remote provider gate — verified from the provider payload immediately
        # before submission. Fail closed on any uncertainty.
        gate = getattr(self._adapter, "verify_execution_gate", None)
        if callable(gate):
            try:
                gate_ok, gate_error = await gate(command)  # type: ignore[operator]
            except Exception as exc:
                gate_ok, gate_error = False, f"execution_gate_error:{exc}"
            if not gate_ok:
                return self._reject(
                    decision, command, "execution_blocked", gate_error
                )

        # Persist a grounded execution record, then move to submitting
        # (distinct from submitted — acceptance is only proven by the provider
        # response below).
        provider_tag = decision.provider_entry_tag() if decision.decision_id else None
        self._repository.record_request(
            decision.decision_id, vars(command), adapter_health.backend,
            execution_mode=self._execution_mode,
            validation_result={
                "valid": True,
                "errors": [],
                "warnings": validation.warnings,
            },
            risk_result={
                "approved": True,
                "risk_score": risk.risk_score,
                "violations": risk.violations,
                "warnings": risk.warnings,
            },
            provider_tag=provider_tag,
            status="received",
        )
        self._repository.mark_submitting(decision.decision_id)
        if risk.adjusted_stake_amount is not None:
            self._repository.mark_status(
                decision.decision_id, "submitting",
                adjusted_stake=risk.adjusted_stake_amount,
            )

        try:
            command.metadata = {**command.metadata, "provider_tag": provider_tag}
            result = await self._adapter.execute_order(command)
        except Exception as exc:
            log.error("[Trading] Execution provider error: %s", exc)
            self._repository.mark_failed(decision.decision_id, error_category="execution_error")
            return ExecutionResult(
                success=False,
                status="execution_error",
                error=str(exc),
                raw={"decision_id": decision.decision_id, "provider_tag": provider_tag},
            )

        if result.status == "ambiguous":
            # Provider outcome unknown (e.g. timeout after receipt). Do NOT
            # retry. Persist ambiguous and rely on reconciliation.
            self._repository.mark_ambiguous(decision.decision_id, _result_dict(result))
            return result

        if result.success:
            tag_from_result = None
            if isinstance(result.raw, dict):
                tag_from_result = result.raw.get("provider_tag")
            self._repository.mark_submitted(
                decision.decision_id,
                _result_dict(result),
                result.provider_trade_id,
                provider_tag=tag_from_result or provider_tag,
            )
            if adapter_health.backend == "paper" and result.status == "filled":
                confirmed = "confirmed_closed" if command.action.startswith("exit_") else "confirmed_open"
                self._repository.mark_status(decision.decision_id, confirmed)
                self._repository.update_reconciliation(decision.decision_id, confirmed,
                    result=_result_dict(result), provider_trade_id=result.provider_trade_id)
        else:
            self._repository.mark_failed(decision.decision_id, error_category="provider_rejected")
        return result

    def _reject(
        self,
        decision: TradingDecision,
        command: ExecutionCommand,
        error_category: str,
        reason: str,
        status: str = "execution_blocked",
    ) -> ExecutionResult:
        """Return a deterministic rejection (NO provider order call, no record)."""
        return ExecutionResult(
            success=False,
            status=status,
            error=reason,
            raw={"error_category": error_category, "decision_id": decision.decision_id},
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
