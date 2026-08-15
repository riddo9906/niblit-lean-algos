"""Read-only reconciliation of execution records against the provider.

The reconciler NEVER submits orders. It correlates a persisted execution
record to the provider state using the strongest available evidence in order:

  1. provider_trade_id
  2. provider-supported Niblit tag (entry_tag)
  3. decision/provider request id

It only uses symbol + side + time as a last-resort diagnostic heuristic, and
never treats "a trade appeared after the request" as proof of ownership.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from persistence.execution_repository import ExecutionRepository

log = logging.getLogger("ExecutionReconciler")

NORMALIZED_STATES = ("confirmed_open", "confirmed_closed", "rejected", "not_found", "ambiguous")


class ExecutionReconciler:
    """Correlates execution records to provider trade state (read-only)."""

    def __init__(
        self,
        repository: ExecutionRepository,
        provider: Any = None,
    ):
        self._repository = repository
        # provider is expected to expose async ``get_trade(trade_id)`` and
        # ``search_by_tag(tag)`` (see FreqtradeAdapter / its client).
        self._provider = provider

    def _provider(self) -> Any:
        if self._provider is None:
            raise RuntimeError("ExecutionReconciler has no provider configured")
        return self._provider

    async def reconcile(self, decision_id: str) -> Dict[str, Any]:
        """Reconcile one execution record and persist the result.

        Returns a structured reconciliation result (read-only). Never submits
        an order.
        """
        record = self._repository.get_execution(decision_id)
        if record is None:
            return {"decision_id": decision_id, "state": "not_found", "reason": "no_execution_record"}

        provider = self._provider()

        # 1. provider_trade_id — strongest evidence.
        trade_id = record.get("provider_trade_id")
        if trade_id:
            try:
                trade = await provider.get_trade(trade_id)
            except Exception as exc:
                log.warning("[Reconciler] trade lookup failed for %s: %s", trade_id, exc)
                trade = None
            if trade is not None and isinstance(trade, dict) and trade.get("trade_id"):
                return await self._finish(record, self._state_from_trade(trade), trade)

        # 2. provider tag / decision id — strong evidence.
        tag = record.get("provider_tag")
        if tag:
            try:
                matched = await provider.search_by_tag(tag)
            except Exception as exc:
                log.warning("[Reconciler] tag search failed: %s", exc)
                matched = None
            if matched is not None:
                return await self._finish(record, self._state_from_trade(matched), matched)

        # 3. decision/provider request id.
        decision_id_probe = record.get("decision_id")
        if decision_id_probe:
            try:
                matched = await provider.search_by_tag(decision_id_probe)
            except Exception as exc:
                log.warning("[Reconciler] decision_id search failed: %s", exc)
                matched = None
            if matched is not None:
                return await self._finish(record, self._state_from_trade(matched), matched)

        # Provider explicitly confirmed absence with reliable evidence → not_found.
        try:
            explicit = await provider.explicit_absence(record)
        except Exception:
            explicit = False
        if explicit:
            return await self._finish(record, "not_found", {})

        # Cannot determine provider state reliably → ambiguous.
        return await self._finish(record, "ambiguous", {}, reason="provider_state_undetermined")

    def _state_from_trade(self, trade: Dict[str, Any]) -> str:
        """Normalize a Freqtrade trade object to a reconciliation state."""
        is_open = trade.get("is_open")
        if is_open is True:
            return "confirmed_open"
        if is_open is False:
            return "confirmed_closed"
        status = str(trade.get("status", "")).lower()
        if status in ("closed", "cancelled", "rejected"):
            return "confirmed_closed"
        if status in ("open", "filled"):
            return "confirmed_open"
        return "ambiguous"

    async def _finish(
        self,
        record: Dict[str, Any],
        state: str,
        trade: Optional[Dict[str, Any]],
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        result = {
            "decision_id": record.get("decision_id"),
            "reconciliation_state": state,
            "provider_trade_id": (trade or {}).get("trade_id"),
            "provider_tag": record.get("provider_tag"),
            "reason": reason or "reconciled",
        }
        self._repository.update_reconciliation(
            record["decision_id"],
            state,
            result=result,
            provider_trade_id=result["provider_trade_id"],
        )
        if state == "confirmed_open":
            self._repository.mark_confirmed_open(record["decision_id"], result=result)
        elif state == "confirmed_closed":
            self._repository.mark_confirmed_closed(record["decision_id"], result=result)
        elif state == "rejected":
            self._repository.mark_rejected(record["decision_id"], error_category="provider_rejected")
        elif state == "not_found":
            # Keep status; only reconciliation_status records not_found.
            pass
        return result

    def reconcile_many(self, decision_ids: Optional[list[str]] = None, limit: int = 50) -> Any:
        """Reconcile a list of decisions (sync wrapper for async callers)."""
        raise NotImplementedError("Use reconcile() per decision or call via async bridge")


# re-export for clarity
__all__ = ["ExecutionReconciler", "NORMALIZED_STATES"]
