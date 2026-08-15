"""Lean trading adapter implementing the TradingAdapter interface."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from adapters.base import TradingAdapter
from domain import (
    AdapterHealth,
    Balance,
    ExecutionCommand,
    ExecutionResult,
    MarketState,
    PerformanceSummary,
    Position,
    RuntimeStatus,
)


class LeanAdapter(TradingAdapter):
    """Wraps the existing Lean bridge and algorithms.

    Maps Lean-native objects into Niblit domain models.
    Does not leak Lean-specific types into the core domain.
    """

    def __init__(self, algos_dir: Optional[str] = None):
        self._algos_dir = Path(algos_dir or os.getenv("NIBLIT_LEAN_ALGOS", "."))
        self._running = False
        self._deployed_ids: list[str] = []
        self._started_at: Optional[datetime] = None

    async def close(self) -> None:
        pass

    async def health(self) -> AdapterHealth:
        return AdapterHealth(
            healthy=self._running,
            backend="lean",
            reachable=self._running,
            authenticated=True,
            runtime_state="running" if self._running else "stopped",
            version=None,
            capabilities={"health", "runtime_status", "start", "stop"},
        )

    async def start(self) -> RuntimeStatus:
        self._running = True
        self._started_at = datetime.now(timezone.utc)
        return RuntimeStatus(state="running", backend="lean", started_at=self._started_at)

    async def stop(self) -> RuntimeStatus:
        self._running = False
        return RuntimeStatus(state="stopped", backend="lean", stopped_at=datetime.now(timezone.utc))

    async def get_runtime_status(self) -> RuntimeStatus:
        return RuntimeStatus(
            state="running" if self._running else "stopped",
            backend="lean",
            started_at=self._started_at,
        )

    async def get_balances(self) -> Sequence[Balance]:
        return [Balance(currency="USD", free=100000.0, used=0.0, total=100000.0)]

    async def get_open_positions(self) -> Sequence[Position]:
        return []

    async def get_closed_positions(self, limit: Optional[int] = None) -> Sequence[Position]:
        return []

    async def get_performance(self) -> PerformanceSummary:
        now = datetime.now(timezone.utc)
        return PerformanceSummary(start_time=now, end_time=now)

    async def get_market_state(self, symbols: Optional[Sequence[str]] = None) -> MarketState:
        from domain import MarketTicker

        ticker = MarketTicker(symbol=symbols[0] if symbols else "SPY", bid=0, ask=0, last=0, volume=0)
        return MarketState(symbol=ticker.symbol, timestamp=datetime.now(timezone.utc), ticker=ticker, source="lean")

    async def execute_order(self, command: ExecutionCommand) -> ExecutionResult:
        return ExecutionResult(
            success=False,
            status="unsupported",
            error="Lean execution requires QuantConnect cloud deployment; use deploy commands instead",
        )

    async def cancel_order(self, command: ExecutionCommand) -> ExecutionResult:
        return ExecutionResult(
            success=False,
            status="unsupported",
            error="Lean does not support direct order cancellation via local adapter",
        )