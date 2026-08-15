"""Trading runtime facade for the Niblit main runtime."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union

from adapters.base import TradingAdapter
from adapters.freqtrade.adapter import FreqtradeAdapter
from adapters.freqtrade.config import FreqtradeAdapterConfig
from domain import (
    AdapterHealth,
    ExecutionResult,
    Position,
    TradingDecision,
    ValidationResult,
)
from persistence.execution_repository import ExecutionRepository
from risk.engine import RiskEngine

from .decision_validator import DecisionValidator
from .execution_coordinator import ExecutionCoordinator

EXECUTION_MODE_OBSERVE = "observe"
EXECUTION_MODE_PAPER = "paper"
EXECUTION_MODE_LIVE = "live"
LIVE_ACKNOWLEDGEMENT = "I_UNDERSTAND_LIVE_TRADING_RISK"


@dataclass
class TradingRuntimeHealth:
    backend: str
    healthy: bool
    adapter_health: Optional[AdapterHealth] = None
    error: Optional[str] = None


@dataclass
class TradingSnapshot:
    backend: str
    balances: List[object] = field(default_factory=list)
    open_positions: List[Position] = field(default_factory=list)
    total_equity: float = 0.0
    available_capital: float = 0.0


class TradingRuntime:
    """Stable internal API for the main Niblit runtime.

    Supports backend selection: 'freqtrade' or 'lean'.
    Does not initialize Lean when Freqtrade is selected.
    """

    def __init__(
        self,
        backend: Optional[str] = None,
        execution_mode: Optional[str] = None,
        freqtrade_config: Optional[FreqtradeAdapterConfig] = None,
    ):
        self._backend = backend or os.getenv("NIBLIT_TRADING_BACKEND", "freqtrade")
        self._execution_mode = execution_mode or os.getenv("NIBLIT_TRADING_MODE", EXECUTION_MODE_OBSERVE)
        self._freqtrade_config = freqtrade_config or FreqtradeAdapterConfig.from_env()
        self._adapter: Optional[TradingAdapter] = None
        self._coordinator: Optional[ExecutionCoordinator] = None
        self._repository = ExecutionRepository(
            os.getenv("NIBLIT_EXECUTION_DB", "niblit_executions.db")
        )

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def execution_mode(self) -> str:
        return self._execution_mode

    async def initialise(self) -> None:
        """Bootstrap the selected backend adapter."""
        if self._backend == "freqtrade":
            adapter = FreqtradeAdapter(self._freqtrade_config)
            self._coordinator = ExecutionCoordinator(
                adapter=adapter,
                validator=DecisionValidator(),
                risk_engine=RiskEngine(),
                repository=self._repository,
                execution_mode=self._execution_mode,
                live_acknowledgement=os.getenv("NIBLIT_LIVE_TRADING_ACKNOWLEDGEMENT"),
            )
            self._adapter = adapter
        elif self._backend == "lean":
            raise NotImplementedError(
                "Lean backend requires LeanAlgoManager; use NIBLIT_TRADING_BACKEND=freqtrade"
            )
        else:
            raise ValueError(f"Unknown trading backend: {self._backend}")

    async def health(self) -> TradingRuntimeHealth:
        if self._adapter is None:
            return TradingRuntimeHealth(backend=self._backend, healthy=False, error="not_initialised")
        h = await self._adapter.health()
        return TradingRuntimeHealth(backend=self._backend, healthy=h.healthy, adapter_health=h)

    async def snapshot(self) -> TradingSnapshot:
        if self._adapter is None:
            return TradingSnapshot(backend=self._backend)
        balances = await self._adapter.get_balances()
        positions = await self._adapter.get_open_positions()
        total_equity = sum(b.total for b in balances)
        available = sum(b.free for b in balances)
        return TradingSnapshot(
            backend=self._backend,
            balances=list(balances),
            open_positions=list(positions),
            total_equity=total_equity,
            available_capital=available,
        )

    async def evaluate(self, proposal: TradingDecision) -> ValidationResult:
        if self._coordinator is None:
            raise RuntimeError("Trading runtime not initialised")
        return self._coordinator._validator.validate(
            decision=proposal,
            whitelist=await self._whitelist(),
            blacklist=await self._blacklist(),
            portfolio_value=sum(b.total for b in await self._adapter.get_balances()),
        )

    async def execute(self, proposal: TradingDecision) -> ExecutionResult:
        if self._coordinator is None or self._adapter is None:
            raise RuntimeError("Trading runtime not initialised")
        open_positions = await self._adapter.get_open_positions()
        portfolio_value = sum(b.total for b in await self._adapter.get_balances())
        return await self._coordinator.process_decision(
            decision=proposal,
            open_positions=open_positions,
            whitelist=await self._whitelist(),
            blacklist=await self._blacklist(),
            portfolio_value=portfolio_value,
        )

    async def shutdown(self) -> None:
        if self._adapter is not None:
            await self._adapter.close()
        self._repository.close()

    async def _whitelist(self) -> List[str]:
        if isinstance(self._adapter, FreqtradeAdapter):
            raw = await self._adapter.get_whitelist()
            if isinstance(raw, dict):
                return [str(p) for p in raw.get("whitelist", [])]
        return []

    async def _blacklist(self) -> List[str]:
        if isinstance(self._adapter, FreqtradeAdapter):
            raw = await self._adapter.get_blacklist()
            if isinstance(raw, dict):
                return [str(p) for p in raw.get("blacklist", [])]
        return []