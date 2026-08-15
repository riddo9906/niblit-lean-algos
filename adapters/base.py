"""Universal trading adapter interface for Niblit."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from domain import (
    AdapterHealth,
    Balance,
    ExecutionCommand,
    ExecutionResult,
    MarketState,
    PerformanceSummary,
    Position,
    RuntimeStatus,
    TradingDecision,
)


class TradingAdapter(ABC):
    """Abstract base class for trading adapters."""

    @abstractmethod
    async def health(self) -> AdapterHealth:
        """Return adapter health status."""
        ...

    @abstractmethod
    async def start(self) -> RuntimeStatus:
        """Start the adapter runtime."""
        ...

    @abstractmethod
    async def stop(self) -> RuntimeStatus:
        """Stop the adapter runtime."""
        ...

    @abstractmethod
    async def get_runtime_status(self) -> RuntimeStatus:
        """Return current runtime status."""
        ...

    @abstractmethod
    async def get_balances(self) -> Sequence[Balance]:
        """Return account balances."""
        ...

    @abstractmethod
    async def get_open_positions(self) -> Sequence[Position]:
        """Return open positions."""
        ...

    @abstractmethod
    async def get_closed_positions(
        self,
        limit: int | None = None,
    ) -> Sequence[Position]:
        """Return closed positions."""
        ...

    @abstractmethod
    async def get_performance(self) -> PerformanceSummary:
        """Return performance summary."""
        ...

    @abstractmethod
    async def get_market_state(
        self,
        symbols: Sequence[str] | None = None,
    ) -> MarketState:
        """Return market state for requested symbols."""
        ...

    @abstractmethod
    async def execute_order(self, command: ExecutionCommand) -> ExecutionResult:
        """Execute an order."""
        ...

    @abstractmethod
    async def cancel_order(self, command: ExecutionCommand) -> ExecutionResult:
        """Cancel an open order."""
        ...