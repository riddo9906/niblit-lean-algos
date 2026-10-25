"""Universal trading runtime manager with staged boot and state machine."""
from __future__ import annotations

import asyncio
import logging
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
from urllib.request import urlopen
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Type

from adapters.base import TradingAdapter
from adapters.freqtrade.adapter import FreqtradeAdapter
from adapters.freqtrade.config import FreqtradeAdapterConfig
from adapters.lean.adapter import LeanAdapter
from adapters.paper.adapter import PaperAdapter
from domain import (
    AdapterHealth,
    ExecutionResult,
    MarketState,
    TradingDecision,
    ValidationResult,
)
from persistence.execution_repository import ExecutionRepository
from risk.engine import RiskEngine
from services.ccxt_market_data_provider import CCXTMarketDataProvider
from services.decision_validator import DecisionValidator
from services.execution_coordinator import ExecutionCoordinator
from services.market_data_provider import MarketDataProvider, MockMarketDataProvider
from services.market_state import MarketStateService
from services.market_symbol import MarketSymbolError, normalize_market_symbol

log = logging.getLogger("TradingRuntimeManager")

VALID_STATES = {
    "created", "configuring", "starting", "ready", "degraded",
    "stopping", "stopped", "failed",
}

VALID_TRANSITIONS = {
    "created": {"configuring", "starting"},
    "configuring": {"starting", "failed"},
    "starting": {"ready", "degraded", "failed"},
    "ready": {"stopping", "degraded"},
    "degraded": {"stopping", "ready"},
    "stopping": {"stopped"},
    "stopped": {"starting"},
    "failed": {"starting"},
}

BOOT_STAGES = [
    "configuration", "storage", "adapter_registry", "selected_backend",
    "market_data", "portfolio", "risk", "execution", "health", "ready",
]


@dataclass
class BootStage:
    name: str
    status: str = "pending"
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: float = 0.0
    message: str = ""
    error: Optional[str] = None
    required: bool = True


@dataclass
class WorkerState:
    name: str
    state: str = "stopped"
    thread_id: Optional[int] = None
    last_heartbeat: Optional[float] = None
    last_success: Optional[float] = None
    last_error: Optional[str] = None
    restart_count: int = 0


@dataclass
class TradingRuntimeSnapshot:
    backend: str
    state: str
    balances: List[object] = field(default_factory=list)
    open_positions: List[object] = field(default_factory=list)
    total_equity: float = 0.0
    available_capital: float = 0.0
    boot_stages: List[Dict[str, Any]] = field(default_factory=list)
    market_data: Optional[Dict[str, Any]] = None


@dataclass
class TradingRuntimeHealth:
    backend: str
    healthy: bool
    state: str
    adapter_health: Optional[AdapterHealth] = None
    market_data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class TradingRuntimeManager:
    """Universal lifecycle manager for paper, freqtrade, and lean backends.

    Owns exactly one TradingRuntime (via ExecutionCoordinator).
    Created once during Niblit bootstrap.
    """

    _instance: Optional["TradingRuntimeManager"] = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls, **kwargs: Any) -> "TradingRuntimeManager":
        """Return the singleton manager instance."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(**kwargs)
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (for testing)."""
        with cls._lock:
            cls._instance = None

    def __init__(
        self,
        backend: Optional[str] = None,
        execution_mode: Optional[str] = None,
    ):
        self._backend = backend or os.getenv("NIBLIT_TRADING_BACKEND", "paper")
        self._execution_mode = execution_mode or os.getenv("NIBLIT_TRADING_MODE", "observe")
        self._control_file = Path(os.getenv('NIBLIT_TRADING_CONTROL_FILE',
            str(Path(os.getenv('NIBLIT_PAPER_STATE_FILE','paper_state.json')).with_suffix('.control.json'))))
        if self._backend == 'paper' and self._control_file.exists():
            saved = json.loads(self._control_file.read_text())
            if saved.get('mode') not in ('paper','observe'): raise ValueError('Invalid persisted paper control mode')
            self._execution_mode = saved['mode']
        self._state = "created"
        self._execution_lock = asyncio.Lock()
        self._strategy_process = None
        self._strategy_log = None
        self._started_at: Optional[datetime] = None
        self._stopped_at: Optional[datetime] = None
        self._last_transition_at: datetime = datetime.now(timezone.utc)
        self._transition_reason: str = ""
        self._adapter: Optional[TradingAdapter] = None
        self._coordinator: Optional[ExecutionCoordinator] = None
        self._repository: Optional[ExecutionRepository] = None
        self._validator: Optional[DecisionValidator] = None
        self._risk_engine: Optional[RiskEngine] = None
        self._market_data: Optional[MarketDataProvider] = None
        self._market_state_service: Optional[MarketStateService] = None
        self._market_data_config: Dict[str, Any] = {}
        self._market_data_degraded_reason: Optional[str] = None
        self._boot_stages: Dict[str, BootStage] = {name: BootStage(name=name) for name in BOOT_STAGES}
        self._workers: Dict[str, WorkerState] = {}
        self._stop_event = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._adapter_registry: Dict[str, Type[TradingAdapter]] = {
            "paper": PaperAdapter,
            "freqtrade": FreqtradeAdapter,
            "lean": LeanAdapter,
        }
        log.info(
            "[TradingRuntimeManager] Created — backend=%s mode=%s id=%s",
            self._backend,
            self._execution_mode,
            id(self),
        )

    @property
    def state(self) -> str:
        return self._state

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def execution_mode(self) -> str:
        return self._execution_mode

    @property
    def started_at(self) -> Optional[datetime]:
        return self._started_at

    @property
    def market_data_provider(self) -> Optional[MarketDataProvider]:
        return self._market_data

    @property
    def market_state_service(self) -> Optional[MarketStateService]:
        return self._market_state_service

    def _transition(self, new_state: str, reason: str = "") -> None:
        if new_state not in VALID_STATES:
            raise ValueError(f"Invalid state: {new_state}")
        if new_state not in VALID_TRANSITIONS.get(self._state, set()):
            log.warning("[TradingRuntimeManager] Invalid transition %s→%s", self._state, new_state)
        self._state = new_state
        self._last_transition_at = datetime.now(timezone.utc)
        self._transition_reason = reason
        log.info("[TradingRuntimeManager] State: %s (%s)", new_state, reason)

    def _run_stage(self, stage_name: str, fn: Any, required: bool = True) -> bool:
        stage = self._boot_stages[stage_name]
        stage.required = required
        stage.started_at = datetime.now(timezone.utc)
        stage.status = "starting"
        start = time.perf_counter()
        try:
            fn()
            stage.completed_at = datetime.now(timezone.utc)
            stage.duration_ms = (time.perf_counter() - start) * 1000
            stage.status = "completed"
            return True
        except Exception as exc:
            stage.completed_at = datetime.now(timezone.utc)
            stage.duration_ms = (time.perf_counter() - start) * 1000
            stage.error = str(exc)
            stage.status = "failed" if required else "degraded"
            log.error("[TradingRuntimeManager] Stage %s failed: %s", stage_name, exc)
            return not required

    def _resolve_market_data_provider_name(self) -> str:
        explicit = os.getenv("NIBLIT_MARKET_DATA_PROVIDER", "").strip().lower()
        if explicit:
            return explicit
        if self._backend == "freqtrade":
            return "ccxt"
        return "mock"

    def _build_market_data_provider(self) -> MarketDataProvider:
        """Construct the configured market-data provider.

        Selection:
          backend=freqtrade + provider unspecified → ccxt
          provider=ccxt → CCXTMarketDataProvider
          provider=mock → MockMarketDataProvider
          unknown → explicit configuration failure
        Never silently falls back to mock when ccxt was requested.
        """
        provider_name = self._resolve_market_data_provider_name()
        exchange = os.getenv("NIBLIT_MARKET_DATA_EXCHANGE", "binance")
        market_type = os.getenv("NIBLIT_MARKET_DATA_MARKET_TYPE", "swap")
        timeframe = os.getenv("NIBLIT_MARKET_DATA_TIMEFRAME", "5m")
        candle_limit = int(os.getenv("NIBLIT_MARKET_DATA_CANDLE_LIMIT", "200"))
        max_age = int(os.getenv("NIBLIT_MARKET_DATA_MAX_AGE_SECONDS", "90"))
        timeout = int(os.getenv("NIBLIT_MARKET_DATA_TIMEOUT_SECONDS", "20"))

        self._market_data_config = {
            "provider": provider_name,
            "exchange": exchange,
            "market_type": market_type,
            "timeframe": timeframe,
            "candle_limit": candle_limit,
            "max_age_seconds": max_age,
            "timeout_seconds": timeout,
            "allow_non_whitelisted": os.getenv(
                "NIBLIT_MARKET_DATA_ALLOW_NON_WHITELISTED", "false"
            ).lower()
            in ("1", "true", "yes"),
        }

        log.info("[Trading] Market provider selected: %s", provider_name)
        log.info("[Trading] Exchange: %s", exchange)
        log.info("[Trading] Market type: %s", market_type)

        if provider_name == "mock":
            return MockMarketDataProvider()
        if provider_name == 'replay':
            if self._backend != 'paper': raise ValueError('Historical replay is restricted to the paper backend')
            from services.replay_market_data_provider import ReplayMarketDataProvider
            self._market_data_config['timeframe'] = '1h'
            self._market_data_config['exchange'] = 'recorded_binance'
            return ReplayMarketDataProvider()
        if provider_name == "ccxt":
            return CCXTMarketDataProvider(
                exchange=exchange,
                market_type=market_type,
                timeout_seconds=timeout,
                max_age_seconds=max_age,
                timeframe=timeframe,
            )
        raise ValueError(f"Unknown market data provider: {provider_name}")

    def _market_data_status_dict(self, healthy: Optional[bool] = None, error: Optional[str] = None) -> Dict[str, Any]:
        cfg = self._market_data_config or {}
        return {
            "provider": cfg.get("provider"),
            "exchange": cfg.get("exchange"),
            "market_type": cfg.get("market_type"),
            "healthy": healthy,
            "timeframe": cfg.get("timeframe"),
            "error": error or self._market_data_degraded_reason,
        }

    async def start(self) -> None:
        """Execute staged startup."""
        if self._state in ("ready", "degraded"):
            log.info("[TradingRuntimeManager] Already started")
            return
        self._transition("configuring", "startup")
        self._started_at = datetime.now(timezone.utc)
        self._stop_event.clear()
        self._market_data_degraded_reason = None
        self._transition("starting", "boot_stages")

        # Stage: configuration
        self._run_stage("configuration", lambda: None)

        # Stage: storage
        def _init_storage():
            self._repository = ExecutionRepository(os.getenv("NIBLIT_EXECUTION_DB", "niblit_executions.db"))
        self._run_stage("storage", _init_storage)

        # Stage: adapter_registry
        def _init_registry():
            pass  # already set in __init__
        self._run_stage("adapter_registry", _init_registry)

        # Stage: selected_backend
        def _init_backend():
            if self._backend not in self._adapter_registry:
                raise ValueError(f"Unknown backend: {self._backend}")
            adapter_cls = self._adapter_registry[self._backend]
            if self._backend == "freqtrade":
                self._adapter = adapter_cls(FreqtradeAdapterConfig.from_env())
            elif self._backend == "lean":
                self._adapter = adapter_cls()
            else:
                self._adapter = adapter_cls()
        if not self._run_stage("selected_backend", _init_backend):
            self._transition("failed", "backend_init_failed")
            return

        if self._backend == "paper":
            await self._adapter.start()

        if self._resolve_market_data_provider_name() == 'replay':
            try: await self._ensure_strategy_worker()
            except Exception as exc:
                self._transition('failed',str(exc))
                raise

        # Stage: market_data
        market_required = self._backend == "freqtrade" or self._resolve_market_data_provider_name() in ("ccxt", "replay")

        def _init_market_data():
            self._market_data = self._build_market_data_provider()
            cfg = self._market_data_config
            self._market_state_service = MarketStateService(
                provider=self._market_data,
                max_staleness_seconds=float(cfg.get("max_age_seconds", 90)),
                timeframe=str(cfg.get("timeframe", "5m")),
                candle_limit=int(cfg.get("candle_limit", 200)),
                source=f"{cfg.get('provider')}:{cfg.get('exchange')}:{cfg.get('market_type')}",
            )

        if not self._run_stage("market_data", _init_market_data, required=market_required):
            if market_required:
                self._market_data_degraded_reason = "market_data_provider_unavailable"
                self._transition("degraded", "market_data_provider_unavailable")
                # Continue boot so observe/status APIs can still report degradation.
            else:
                self._market_data = MockMarketDataProvider()
                self._market_state_service = MarketStateService(
                    provider=self._market_data,
                    source="mock",
                )

        # Stage: portfolio
        def _init_portfolio():
            pass
        self._run_stage("portfolio", _init_portfolio, required=False)

        # Stage: risk
        def _init_risk():
            self._risk_engine = RiskEngine()
            self._validator = DecisionValidator()
        self._run_stage("risk", _init_risk)

        # Stage: execution
        def _init_execution():
            self._coordinator = ExecutionCoordinator(
                adapter=self._adapter,
                validator=self._validator,
                risk_engine=self._risk_engine,
                repository=self._repository,
                execution_mode=self._execution_mode,
                live_acknowledgement=os.getenv("NIBLIT_LIVE_TRADING_ACKNOWLEDGEMENT"),
            )
        if not self._run_stage("execution", _init_execution):
            self._transition("failed", "execution_init_failed")
            return

        # Stage: health — probe market provider when configured
        async def _probe_market_health() -> None:
            if self._market_data is None:
                if market_required:
                    raise RuntimeError("market_data_provider_unavailable")
                return
            health = await self._market_data.health()
            if health.healthy:
                log.info("[Trading] Market provider health: ready")
            else:
                log.warning("[Trading] Market provider health: %s", health.error)
                if market_required:
                    self._market_data_degraded_reason = (
                        health.error or "market_data_provider_unavailable"
                    )
                    raise RuntimeError(self._market_data_degraded_reason)

        try:
            stage = self._boot_stages["health"]
            stage.required = market_required
            stage.started_at = datetime.now(timezone.utc)
            stage.status = "starting"
            start = time.perf_counter()
            await _probe_market_health()
            stage.completed_at = datetime.now(timezone.utc)
            stage.duration_ms = (time.perf_counter() - start) * 1000
            stage.status = "completed"
        except Exception as exc:
            stage = self._boot_stages["health"]
            stage.completed_at = datetime.now(timezone.utc)
            stage.error = str(exc)
            stage.status = "failed" if market_required else "degraded"
            if market_required:
                self._market_data_degraded_reason = "market_data_provider_unavailable"
                log.error("[TradingRuntimeManager] Stage health failed: %s", exc)

        # Stage: ready
        self._run_stage("ready", lambda: None)
        all_required_ok = all(
            self._boot_stages[s].status == "completed"
            for s in BOOT_STAGES
            if self._boot_stages[s].required
        )
        if self._market_data_degraded_reason:
            self._transition("degraded", self._market_data_degraded_reason)
        else:
            self._transition("ready" if all_required_ok else "degraded", "boot_complete")
        self._print_summary()

    def _print_summary(self) -> None:
        print("\nTrading Runtime READY\n")
        for name in BOOT_STAGES:
            stage = self._boot_stages[name]
            print(f"  [{name}] {stage.status}")

    async def stop(self) -> None:
        if self._strategy_process is not None:
            if self._strategy_process.poll() is None:
                self._strategy_process.terminate()
                try: await asyncio.to_thread(self._strategy_process.wait,10)
                except subprocess.TimeoutExpired: self._strategy_process.kill()
            self._strategy_process=None
        if self._strategy_log is not None:
            self._strategy_log.close(); self._strategy_log=None
        """Graceful shutdown."""
        if self._state in ("stopped", "failed"):
            return
        self._transition("stopping", "shutdown")
        self._stop_event.set()
        if self._market_data is not None:
            try:
                await self._market_data.close()
            except Exception as exc:
                log.debug("[TradingRuntimeManager] Market provider close error: %s", exc)
        if self._adapter:
            try:
                await self._adapter.close()
            except Exception as exc:
                log.debug("[TradingRuntimeManager] Adapter close error: %s", exc)
        if self._repository:
            self._repository.close()
        self._stopped_at = datetime.now(timezone.utc)
        self._transition("stopped", "shutdown_complete")

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def health(self) -> TradingRuntimeHealth:
        market_info = self._market_data_status_dict()
        if self._market_data is not None:
            try:
                mh = await self._market_data.health()
                market_info = self._market_data_status_dict(
                    healthy=mh.healthy,
                    error=mh.error,
                )
                market_info["latency_ms"] = mh.latency_ms
            except Exception as exc:
                market_info = self._market_data_status_dict(healthy=False, error=str(exc))

        if self._adapter is None:
            return TradingRuntimeHealth(
                backend=self._backend,
                healthy=False,
                state=self._state,
                market_data=market_info,
                error="not_initialised",
            )
        try:
            h = await self._adapter.health()
            overall = bool(h.healthy) and self._state in ("ready", "degraded")
            if (self._backend == "freqtrade" or self._resolve_market_data_provider_name() in ('replay', 'ccxt')) and not market_info.get("healthy"):
                overall = False
            return TradingRuntimeHealth(
                backend=self._backend,
                healthy=overall and self._state == "ready",
                state=self._state,
                adapter_health=h,
                market_data=market_info,
            )
        except Exception as exc:
            return TradingRuntimeHealth(
                backend=self._backend,
                healthy=False,
                state=self._state,
                market_data=market_info,
                error=str(exc),
            )

    async def snapshot(self) -> TradingRuntimeSnapshot:
        market_info = self._market_data_status_dict()
        if self._market_data is not None:
            try:
                mh = await self._market_data.health()
                market_info = self._market_data_status_dict(healthy=mh.healthy, error=mh.error)
            except Exception as exc:
                market_info = self._market_data_status_dict(healthy=False, error=str(exc))

        if self._adapter is None:
            return TradingRuntimeSnapshot(
                backend=self._backend,
                state=self._state,
                market_data=market_info,
            )
        balances = await self._adapter.get_balances()
        positions = await self._adapter.get_open_positions()
        total = sum(b.total for b in balances)
        if self._backend == "paper":
            total = self._adapter.portfolio_equity
        available = sum(b.free for b in balances)
        stages = [
            {"name": s.name, "status": s.status, "duration_ms": s.duration_ms}
            for s in self._boot_stages.values()
        ]
        return TradingRuntimeSnapshot(
            backend=self._backend,
            state=self._state,
            balances=list(balances),
            open_positions=list(positions),
            total_equity=total,
            available_capital=available,
            boot_stages=stages,
            market_data=market_info,
        )

    def _parse_pair_list(self, raw: Any, *keys: str) -> List[str]:
        if raw is None:
            return []
        if isinstance(raw, list):
            return [str(x) for x in raw]
        if isinstance(raw, dict):
            for key in keys:
                value = raw.get(key)
                if isinstance(value, list):
                    return [str(x) for x in value]
            # Some payloads nest under "data"
            data = raw.get("data")
            if isinstance(data, list):
                return [str(x) for x in data]
        return []

    def _parse_active_locks(self, raw: Any) -> List[str]:
        """Normalize Freqtrade lock payloads into locked pair symbols."""
        locks: List[Any]
        if raw is None:
            return []
        if isinstance(raw, list):
            locks = raw
        elif isinstance(raw, dict):
            locks = raw.get("locks") or raw.get("data") or []
        else:
            return []

        now = datetime.now(timezone.utc)
        active: List[str] = []
        for entry in locks:
            if isinstance(entry, str):
                active.append(entry)
                continue
            if not isinstance(entry, dict):
                continue
            pair = entry.get("pair") or entry.get("symbol")
            if not pair:
                continue
            if entry.get("active") is False:
                continue
            lock_end = entry.get("lock_end_time") or entry.get("until") or entry.get("lock_end")
            if lock_end:
                try:
                    if isinstance(lock_end, (int, float)):
                        end_dt = datetime.fromtimestamp(float(lock_end), tz=timezone.utc)
                    else:
                        end_dt = datetime.fromisoformat(str(lock_end).replace("Z", "+00:00"))
                    if end_dt <= now:
                        continue  # expired
                except Exception:
                    pass
            active.append(str(pair))
        return active

    async def _get_pair_lists(self) -> Dict[str, List[str]]:
        whitelist: List[str] = []
        blacklist: List[str] = []
        locks: List[str] = []
        if isinstance(self._adapter, FreqtradeAdapter):
            try:
                wl_raw = await self._adapter.get_whitelist()
                whitelist = self._parse_pair_list(wl_raw, "whitelist", "pairlist", "pairs")
                log.info("[Trading] Freqtrade whitelist loaded: %s pairs", len(whitelist))
            except Exception as exc:
                log.warning("[Trading] Failed to load whitelist: %s", exc)
            try:
                bl_raw = await self._adapter.get_blacklist()
                blacklist = self._parse_pair_list(bl_raw, "blacklist", "pairs")
            except Exception as exc:
                log.warning("[Trading] Failed to load blacklist: %s", exc)
            try:
                locks_raw = await self._adapter.get_locks()
                locks = self._parse_active_locks(locks_raw)
            except Exception as exc:
                log.warning("[Trading] Failed to load locks: %s", exc)
        return {"whitelist": whitelist, "blacklist": blacklist, "pair_locks": locks}

    async def get_market_state(
        self,
        symbol: str,
        *,
        timeframe: Optional[str] = None,
    ) -> MarketState:
        if self._market_state_service is None:
            raise RuntimeError("market_data_provider_unavailable")
        allow_non = bool(self._market_data_config.get("allow_non_whitelisted"))
        if not allow_non and isinstance(self._adapter, FreqtradeAdapter):
            pairs = await self._get_pair_lists()
            if pairs["whitelist"] and symbol not in pairs["whitelist"]:
                raise PermissionError(f"symbol_not_in_whitelist:{symbol}")
            if pairs["blacklist"] and symbol in pairs["blacklist"]:
                raise PermissionError(f"symbol_blacklisted:{symbol}")
            if pairs["pair_locks"] and symbol in pairs["pair_locks"]:
                raise PermissionError(f"pair_locked:{symbol}")
        market_type = str(self._market_data_config.get("market_type", "swap"))
        provider_name = str(self._market_data_config.get("provider", "ccxt"))
        normalize_market_symbol(symbol, provider=provider_name, market_type=market_type)
        return await self._market_state_service.get_market_state(symbol, timeframe=timeframe)

    async def _build_validation_context(self, decision: TradingDecision) -> Dict[str, Any]:
        """Authoritative context shared by validate_decision and execute_decision."""
        if self._adapter is None:
            raise RuntimeError("Manager not started")

        open_positions = list(await self._adapter.get_open_positions())
        balances = list(await self._adapter.get_balances())
        if decision.action.startswith("exit_") and decision.stake_amount is None:
            matching = next((p for p in open_positions if p.symbol == decision.symbol
                             and p.side == ("long" if decision.action == "exit_long" else "short")), None)
            if matching is not None:
                decision.stake_amount = matching.stake_amount
        portfolio_value = sum(b.total for b in balances)

        pairs = await self._get_pair_lists()
        allow_non = bool(self._market_data_config.get("allow_non_whitelisted"))
        whitelist = [] if allow_non else pairs["whitelist"]
        blacklist = pairs["blacklist"]
        pair_locks = pairs["pair_locks"]

        already_executed_ids: set[str] = set()
        if self._repository is not None and self._repository.has_decision(decision.decision_id):
            already_executed_ids.add(decision.decision_id)

        market_state: Optional[MarketState] = None
        market_error: Optional[str] = None
        if decision.action != "hold":
            try:
                market_type = str(self._market_data_config.get("market_type", "swap"))
                provider_name = str(self._market_data_config.get("provider", "mock"))
                normalize_market_symbol(
                    decision.symbol, provider=provider_name, market_type=market_type
                )
                if self._market_state_service is None:
                    raise RuntimeError("market_data_provider_unavailable")
                market_state = await self._market_state_service.get_market_state(decision.symbol)
                if market_state.is_fresh is False:
                    market_error = "stale_market_data"
            except MarketSymbolError as exc:
                market_error = f"invalid_symbol:{exc}"
            except Exception as exc:
                market_error = str(exc) or "market_data_unavailable"
                log.warning("[Trading] Market state unavailable for validation: %s", exc)

        return {
            "open_positions": open_positions,
            "balances": balances,
            "portfolio_value": portfolio_value,
            "whitelist": whitelist,
            "blacklist": blacklist,
            "pair_locks": pair_locks,
            "already_executed_ids": already_executed_ids,
            "market_state": market_state,
            "market_error": market_error,
            "require_market_state": True,
        }

    async def validate_decision(self, decision: TradingDecision) -> ValidationResult:
        if self._validator is None or self._adapter is None:
            raise RuntimeError("Manager not started")
        ctx = await self._build_validation_context(decision)
        if ctx["market_error"] and decision.action != "hold":
            # Surface typed market failures before broader validation.
            errors = []
            if "stale_market_data" in ctx["market_error"]:
                errors.append("stale_market_data")
            elif "symbol_not_in_whitelist" in ctx["market_error"]:
                errors.append("symbol_not_in_whitelist")
            elif "invalid_symbol" in ctx["market_error"]:
                errors.append("invalid_symbol")
            else:
                errors.append("market_data_unavailable")
            return ValidationResult(valid=False, errors=errors)

        result = self._validator.validate(
            decision=decision,
            open_positions=ctx["open_positions"],
            whitelist=ctx["whitelist"],
            blacklist=ctx["blacklist"],
            pair_locks=ctx["pair_locks"],
            portfolio_value=ctx["portfolio_value"],
            already_executed_ids=ctx["already_executed_ids"],
            market_state=ctx["market_state"],
            require_market_state=ctx["require_market_state"],
        )
        log.info(
            "[Trading] Validation: %s decision_id=%s errors=%s",
            "approved" if result.valid else "rejected",
            decision.decision_id,
            result.errors,
        )
        return result

    async def execute_decision(self, decision: TradingDecision) -> ExecutionResult:
        async with self._execution_lock:
            return await self._execute_decision(decision)

    async def set_paper_enabled(self, enabled: bool) -> dict:
        if self._backend != 'paper': raise PermissionError('Paper control cannot operate a live or external backend')
        async with self._execution_lock:
            if enabled and self._resolve_market_data_provider_name() not in ('replay','ccxt'):
                raise ValueError('Paper activation requires recorded replay data or a real market feed; mock quotes are not an operator trading feed')
            if enabled and (self._state != 'ready' or self._market_data is None or not (await self._market_data.health()).healthy):
                raise ValueError('The configured paper market feed must be healthy before activation')
            old=self._execution_mode
            mode='paper' if enabled else 'observe'
            self._control_file.parent.mkdir(parents=True,exist_ok=True)
            filename=None
            try:
                with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=self._control_file.parent,delete=False) as stream:
                    filename=stream.name; json.dump({'backend':'paper','mode':mode},stream); stream.flush(); os.fsync(stream.fileno())
                os.replace(filename,self._control_file)
                self._execution_mode=mode
                if self._coordinator: self._coordinator._execution_mode=mode
            except Exception:
                self._execution_mode=old
                raise
            finally:
                if filename and os.path.exists(filename): os.unlink(filename)
            return {'backend':'paper','mode':mode,'execution_enabled':enabled}

    async def paper_account(self) -> dict:
        if self._backend != 'paper' or self._adapter is None:
            raise PermissionError('This operation requires the paper backend')
        market=None
        if self._resolve_market_data_provider_name() == 'replay':
            observation=await asyncio.to_thread(self._market_data._read)
            self._adapter.set_price(observation['symbol'],observation['last'])
            market={key:observation[key] for key in ('source','clock_kind','as_of','dataset','data_sha256','strategy','cursor','total_candles','symbol','last','done')}
        return {'backend':'paper','mode':self._execution_mode,'state':self._state,
                'market':market,'account':self._adapter.account()}

    async def _ensure_strategy_worker(self):
        url=f"http://127.0.0.1:{int(os.getenv('NIBLIT_STRATEGY_API_PORT','8150'))}/health"
        def ready():
            try:
                with urlopen(url,timeout=1) as response: health=json.load(response)
                return health.get('service')=='niblit-freqtrade-strategy' and health.get('healthy') is True
            except Exception: return False
        if await asyncio.to_thread(ready): return
        root=Path(sys.executable).parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
        if getattr(sys,'frozen',False):
            command=[str(root.parent/'freqtrade/niblit-freqtrade-worker.exe')]
        else:
            interpreter=Path(os.getenv('NIBLIT_FREQTRADE_PYTHON',str(root.parent/'freqtrade/.venv/Scripts/python.exe')))
            command=[str(interpreter),'-m','freqtrade_bridge.offline_worker']
        if not Path(command[0]).is_file(): raise RuntimeError('The Freqtrade strategy runtime is missing')
        log_path=Path(os.getenv('NIBLIT_PAPER_STATE_FILE','paper_state.json')).parent/'strategy-worker.log'
        log_path.parent.mkdir(parents=True,exist_ok=True)
        self._strategy_log=log_path.open('a',encoding='utf-8')
        environment=dict(os.environ)
        environment['PYTHONPATH']=str(root)+os.pathsep+environment.get('PYTHONPATH','')
        self._strategy_process=subprocess.Popen(command,cwd=str(root),env=environment,
            stdout=self._strategy_log,stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        deadline=time.monotonic()+30
        while time.monotonic()<deadline and self._strategy_process.poll() is None:
            if await asyncio.to_thread(ready): return
            await asyncio.sleep(.25)
        raise RuntimeError('Freqtrade strategy runtime failed to become ready; inspect strategy-worker.log')

    async def _execute_decision(self, decision: TradingDecision) -> ExecutionResult:
        if self._coordinator is None or self._adapter is None:
            raise RuntimeError("Manager not started")
        ctx = await self._build_validation_context(decision)
        if ctx["market_error"] and decision.action != "hold":
            errors = []
            if "stale_market_data" in ctx["market_error"]:
                errors.append("stale_market_data")
            else:
                errors.append("market_data_unavailable")
            return ExecutionResult(
                success=False,
                status="validation_failed",
                error="; ".join(errors),
            )
        if self._backend == "paper" and ctx["market_state"] is not None:
            expected=decision.metadata.get('market_timestamp')
            actual=ctx['market_state'].ticker.timestamp.isoformat()
            if expected and expected != actual:
                return ExecutionResult(success=False,status='validation_failed',error='Replay quote changed before execution')
            decision.metadata={**decision.metadata,'market_timestamp':actual,'market_source':ctx['market_state'].source}
            self._adapter.set_price(decision.symbol, float(ctx["market_state"].ticker.last))
        realized_today = 0.0
        if self._backend == 'paper' and ctx['market_state'] is not None:
            trading_date = ctx['market_state'].ticker.timestamp.date()
            account = self._adapter.account()
            for position in account.get('positions', []):
                closed = position.get('closed_at')
                if closed and datetime.fromisoformat(str(closed)).date() == trading_date:
                    realized_today += float(position.get('profit_amount') or 0)
        return await self._coordinator.process_decision(
            decision=decision,
            open_positions=ctx["open_positions"],
            whitelist=ctx["whitelist"],
            blacklist=ctx["blacklist"],
            pair_locks=ctx["pair_locks"],
            portfolio_value=ctx["portfolio_value"],
            market_state=ctx["market_state"],
            already_executed_ids=ctx["already_executed_ids"],
            require_market_state=ctx["require_market_state"],
            realized_pnl_today=realized_today,
        )

    def get_boot_stages(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": s.name,
                "status": s.status,
                "duration_ms": s.duration_ms,
                "error": s.error,
            }
            for s in self._boot_stages.values()
        ]


class LeanAlgoManager(TradingRuntimeManager):
    """Backward-compatible runtime manager name.

    Existing imports of LeanAlgoManager continue to work.
    """

    pass


def get_lean_algo_manager(**kwargs: Any) -> LeanAlgoManager:
    """Return the singleton LeanAlgoManager (backward-compatible)."""
    return LeanAlgoManager.get_instance(**kwargs)
