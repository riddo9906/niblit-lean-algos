"""Freqtrade trading adapter implementing the TradingAdapter interface."""
from __future__ import annotations

import time
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

from .client import FreqtradeClient
from .config import FreqtradeAdapterConfig
from .errors import ExecutionDisabledError, FreqtradeAdapterError
from .mapper import map_balance, map_health, map_position, normalize_runtime_state

CAPABILITIES = {
    "health",
    "runtime_status",
    "balances",
    "open_positions",
    "closed_positions",
    "performance",
    "forceentry",
    "forceexit",
    "cancel_order",
    "whitelist",
    "blacklist",
    "locks",
}


class FreqtradeAdapter(TradingAdapter):
    """Transport and translation adapter for Freqtrade."""

    def __init__(
        self,
        config: Optional[FreqtradeAdapterConfig] = None,
        client: Optional[FreqtradeClient] = None,
    ):
        self._config = config or FreqtradeAdapterConfig.from_env()
        self._client = client or FreqtradeClient(self._config)

    async def close(self) -> None:
        await self._client.close()

    async def __aenter__(self) -> FreqtradeAdapter:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    # ── TradingAdapter interface ─────────────────────────────────────────────

    async def health(self) -> AdapterHealth:
        start = time.perf_counter()
        try:
            version_raw = await self._client.version()
            show = await self._client.show_config()
            latency_ms = (time.perf_counter() - start) * 1000.0
            version = version_raw.get("version") if isinstance(version_raw, dict) else str(version_raw)
            runtime_state = normalize_runtime_state(
                str(show.get("state", "unknown")) if isinstance(show, dict) else "unknown"
            )
            return map_health(
                backend="freqtrade",
                reachable=True,
                authenticated=True,
                runtime_state=runtime_state,
                version=version,
                latency_ms=latency_ms,
                capabilities=set(CAPABILITIES),
            )
        except FreqtradeAdapterError as exc:
            latency_ms = (time.perf_counter() - start) * 1000.0
            return map_health(
                backend="freqtrade",
                reachable=False,
                authenticated=False,
                runtime_state="unreachable",
                latency_ms=latency_ms,
                error=str(exc),
            )

    async def start(self) -> RuntimeStatus:
        await self._client.start()
        return RuntimeStatus(state="running", backend="freqtrade")

    async def stop(self) -> RuntimeStatus:
        await self._client.stop()
        return RuntimeStatus(state="stopped", backend="freqtrade")

    async def get_runtime_status(self) -> RuntimeStatus:
        show = await self._client.show_config()
        state = normalize_runtime_state(str(show.get("state", "unknown")) if isinstance(show, dict) else "unknown")
        return RuntimeStatus(state=state, backend="freqtrade")

    async def get_balances(self) -> Sequence[Balance]:
        raw = await self._client.balance()
        entries = raw.get("balances", []) if isinstance(raw, dict) else []
        return [map_balance(entry) for entry in entries if isinstance(entry, dict)]

    async def get_open_positions(self) -> Sequence[Position]:
        raw = await self._client.status()
        if isinstance(raw, dict):
            raw = raw.get("trades", [])
        return [map_position(trade, is_open=True) for trade in raw if isinstance(trade, dict)]

    async def get_closed_positions(self, limit: Optional[int] = None) -> Sequence[Position]:
        raw = await self._client.trades(limit=limit)
        if isinstance(raw, dict):
            raw = raw.get("trades", [])
        return [map_position(trade, is_open=False) for trade in raw if isinstance(trade, dict)]

    async def get_performance(self) -> PerformanceSummary:
        from datetime import datetime, timezone

        profit = await self._client.profit()
        performance = await self._client.performance()
        start_time = datetime.now(timezone.utc)
        end_time = datetime.now(timezone.utc)
        total_profit = 0.0
        total_loss = 0.0
        winning = 0
        losing = 0
        if isinstance(performance, list):
            for entry in performance:
                if not isinstance(entry, dict):
                    continue
                profit_val = float(entry.get("profit", 0.0) or 0.0)
                profit_pct = float(entry.get("profit_pct", 0.0) or 0.0)
                if profit_val > 0 or profit_pct > 0:
                    winning += 1
                    total_profit += profit_val
                else:
                    losing += 1
                    total_loss += profit_val
        total_trades = winning + losing
        return PerformanceSummary(
            start_time=start_time,
            end_time=end_time,
            total_trades=total_trades,
            winning_trades=winning,
            losing_trades=losing,
            total_profit=total_profit,
            total_loss=total_loss,
            max_drawdown=0.0,
            win_rate=winning / total_trades if total_trades else 0.0,
        )

    async def get_market_state(self, symbols: Optional[Sequence[str]] = None) -> MarketState:
        raise FreqtradeAdapterError(
            "Market state requires a data provider; not exposed by Freqtrade REST API directly"
        )

    async def execute_order(self, command: ExecutionCommand) -> ExecutionResult:
        if not self._config.allow_execution:
            raise ExecutionDisabledError(
                "Freqtrade execution is disabled (NIBLIT_FREQTRADE_ALLOW_EXECUTION=false)"
            )
        if command.action in ("enter_long", "enter_short"):
            raw = await self._client.forceenter(
                pair=command.symbol,
                side="long" if command.action == "enter_long" else "short",
            )
        elif command.action in ("exit_long", "exit_short"):
            trade_id = int(command.metadata.get("trade_id", -1))
            raw = await self._client.forceexit(trade_id)
        else:
            raise ExecutionDisabledError(f"Unsupported action for execution: {command.action}")
        return ExecutionResult(
            success=True,
            status="submitted",
            provider_trade_id=str(raw.get("trade_id", "")),
            raw=raw if isinstance(raw, dict) else {},
        )

    async def cancel_order(self, command: ExecutionCommand) -> ExecutionResult:
        if not self._config.allow_execution:
            raise ExecutionDisabledError(
                "Freqtrade execution is disabled (NIBLIT_FREQTRADE_ALLOW_EXECUTION=false)"
            )
        trade_id = int(command.metadata.get("trade_id", command.decision_id or -1))
        raw = await self._client.cancel_open_order(trade_id)
        return ExecutionResult(
            success=True,
            status="cancelled",
            provider_trade_id=str(trade_id),
            raw=raw if isinstance(raw, dict) else {},
        )

    # ── Adapter-specific helpers ─────────────────────────────────────────────

    async def get_whitelist(self) -> Dict[str, Any]:
        return await self._client.whitelist()

    async def get_blacklist(self) -> Dict[str, Any]:
        return await self._client.blacklist()

    async def get_locks(self) -> Dict[str, Any]:
        return await self._client.locks()

    async def ping(self) -> Dict[str, Any]:
        return await self._client.ping()