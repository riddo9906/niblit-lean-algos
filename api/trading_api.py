"""FastAPI REST API for the Niblit trading runtime."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from domain.trading_decision import TradingDecision
from manager import TradingRuntimeManager
from services.market_data_provider import MarketDataError
from services.market_symbol import MarketSymbolError

log = logging.getLogger("TradingAPI")


class DecisionRequest(BaseModel):
    decision_id: str
    symbol: str
    action: str
    confidence: float
    rationale: str
    strategy: str | None = None
    stake_amount: float | None = None
    stake_currency: str | None = None
    order_type: str | None = None
    limit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    leverage: float | None = None
    metadata: Dict[str, Any] = {}


def _serialize_ticker(ticker: Any) -> Dict[str, Any]:
    return {
        "symbol": ticker.symbol,
        "last": ticker.last,
        "bid": ticker.bid,
        "ask": ticker.ask,
        "open": ticker.open,
        "high": ticker.high,
        "low": ticker.low,
        "volume": ticker.volume,
        "base_volume": ticker.base_volume,
        "quote_volume": ticker.quote_volume,
        "timestamp": ticker.timestamp.isoformat() if ticker.timestamp else None,
        "source": ticker.source,
        "age_seconds": ticker.age_seconds,
        "is_fresh": ticker.is_fresh,
    }


def _serialize_candle(candle: Any) -> Dict[str, Any]:
    return {
        "symbol": candle.symbol,
        "timeframe": candle.timeframe,
        "timestamp": candle.timestamp.isoformat() if candle.timestamp else None,
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
    }


def _serialize_market_state(state: Any) -> Dict[str, Any]:
    return {
        "symbol": state.symbol,
        "timestamp": state.timestamp.isoformat() if state.timestamp else None,
        "ticker": _serialize_ticker(state.ticker) if state.ticker else None,
        "candle_count": len(state.recent_candles or []),
        "volatility": state.volatility,
        "trend": state.trend,
        "market_regime": state.market_regime,
        "data_freshness": state.data_freshness,
        "age_seconds": state.age_seconds,
        "is_fresh": state.is_fresh,
        "source": state.source,
    }


def _market_http_error(exc: Exception) -> HTTPException:
    msg = str(exc)
    lower = msg.lower()
    if isinstance(exc, MarketSymbolError) or "invalid_symbol" in lower or "malformed symbol" in lower:
        return HTTPException(status_code=400, detail={"error": "invalid_symbol", "message": msg})
    if isinstance(exc, PermissionError):
        if "whitelist" in lower:
            return HTTPException(status_code=404, detail={"error": "symbol_not_in_whitelist", "message": msg})
        if "blacklist" in lower:
            return HTTPException(status_code=404, detail={"error": "symbol_blacklisted", "message": msg})
        if "locked" in lower:
            return HTTPException(status_code=409, detail={"error": "pair_locked", "message": msg})
        return HTTPException(status_code=403, detail={"error": "forbidden", "message": msg})
    if isinstance(exc, MarketDataError) or "stale_market_data" in lower:
        if "stale" in lower:
            return HTTPException(status_code=503, detail={"error": "stale_market_data", "message": msg})
        return HTTPException(status_code=503, detail={"error": "market_provider_unavailable", "message": msg})
    if "unavailable" in lower or "not started" in lower:
        return HTTPException(status_code=503, detail={"error": "market_provider_unavailable", "message": msg})
    log.exception("Unexpected market API error")
    return HTTPException(status_code=500, detail={"error": "internal_error", "message": "unexpected internal failure"})


def create_app(manager: TradingRuntimeManager) -> FastAPI:
    """Create the FastAPI app wired to the given manager."""
    app = FastAPI(title="Niblit Trading Runtime", version="1.0.0")

    # Non-secret runtime identity for development diagnostics.
    # Correlates the Core and API manager instances.
    manager_instance_id = str(id(manager))

    def _market_block() -> Dict[str, Any]:
        cfg = getattr(manager, "_market_data_config", {}) or {}
        return {
            "provider": cfg.get("provider"),
            "exchange": cfg.get("exchange"),
            "market_type": cfg.get("market_type"),
            "healthy": None,
            "timeframe": cfg.get("timeframe"),
        }

    @app.get("/health")
    async def health() -> Dict[str, Any]:
        h = await manager.health()
        return {
            "backend": h.backend,
            "healthy": h.healthy,
            "state": h.state,
            "error": h.error,
            "manager_instance_id": manager_instance_id,
            "market_data": h.market_data or _market_block(),
            "mode": manager.execution_mode,
            "execution_enabled": manager.execution_mode not in ("observe",),
        }

    @app.get("/v1/runtime/status")
    async def runtime_status() -> Dict[str, Any]:
        h = await manager.health()
        market = h.market_data or _market_block()
        return {
            "backend": manager.backend,
            "state": manager.state,
            "mode": manager.execution_mode,
            "execution_enabled": manager.execution_mode not in ("observe",),
            "manager_instance_id": manager_instance_id,
            "market_data": market,
            "market_provider": (market or {}).get("provider"),
            "market_data_healthy": (market or {}).get("healthy"),
        }

    @app.get("/v1/runtime/snapshot")
    async def runtime_snapshot() -> Dict[str, Any]:
        snap = await manager.snapshot()
        return {
            "backend": snap.backend,
            "state": snap.state,
            "total_equity": snap.total_equity,
            "available_capital": snap.available_capital,
            "open_positions": len(snap.open_positions),
            "boot_stages": snap.boot_stages,
            "market_data": snap.market_data or _market_block(),
        }

    @app.get("/v1/adapters")
    async def adapters() -> Dict[str, Any]:
        h = await manager.health()
        return {
            "available": ["paper", "freqtrade", "lean"],
            "selected": manager.backend,
            "market_data": h.market_data or _market_block(),
        }

    @app.get("/v1/market/health")
    async def market_health() -> Dict[str, Any]:
        provider = manager.market_data_provider
        if provider is None:
            raise HTTPException(
                status_code=503,
                detail={"error": "market_provider_unavailable", "message": "no market provider"},
            )
        try:
            health = await provider.health()
        except Exception as exc:
            raise _market_http_error(exc) from exc
        cfg = getattr(manager, "_market_data_config", {}) or {}
        return {
            "healthy": health.healthy,
            "provider": health.provider or cfg.get("provider"),
            "exchange": health.exchange or cfg.get("exchange"),
            "market_type": health.market_type or cfg.get("market_type"),
            "latency_ms": health.latency_ms,
            "error": health.error,
            "timeframe": health.timeframe or cfg.get("timeframe"),
            "source": health.source,
        }

    @app.get("/v1/market/ticker")
    async def market_ticker(
        symbol: str = Query(..., description="Trading pair, e.g. BTC/USDT:USDT"),
    ) -> Dict[str, Any]:
        provider = manager.market_data_provider
        if provider is None:
            raise HTTPException(
                status_code=503,
                detail={"error": "market_provider_unavailable", "message": "no market provider"},
            )
        try:
            # Enforce whitelist via manager path when Freqtrade backend is active.
            if manager.backend == "freqtrade":
                state = await manager.get_market_state(symbol)
                return {"ticker": _serialize_ticker(state.ticker)}
            ticker = await provider.get_ticker(symbol)
            return {"ticker": _serialize_ticker(ticker)}
        except Exception as exc:
            raise _market_http_error(exc) from exc

    @app.get("/v1/market/candles")
    async def market_candles(
        symbol: str = Query(..., description="Trading pair, e.g. BTC/USDT:USDT"),
        timeframe: str = Query("5m"),
        limit: int = Query(200, ge=1, le=1000),
    ) -> Dict[str, Any]:
        provider = manager.market_data_provider
        if provider is None:
            raise HTTPException(
                status_code=503,
                detail={"error": "market_provider_unavailable", "message": "no market provider"},
            )
        if not timeframe or not timeframe[-1].isalpha():
            raise HTTPException(
                status_code=400,
                detail={"error": "invalid_timeframe", "message": f"invalid timeframe: {timeframe}"},
            )
        try:
            if manager.backend == "freqtrade":
                # Whitelist / lock checks
                await manager.get_market_state(symbol, timeframe=timeframe)
            candles = await provider.get_candles(symbol, timeframe=timeframe, limit=limit)
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "count": len(candles),
                "candles": [_serialize_candle(c) for c in candles],
            }
        except Exception as exc:
            raise _market_http_error(exc) from exc

    @app.get("/v1/market/state")
    async def market_state(
        symbol: str = Query(..., description="Trading pair, e.g. BTC/USDT:USDT"),
        timeframe: Optional[str] = Query(None),
    ) -> Dict[str, Any]:
        try:
            state = await manager.get_market_state(symbol, timeframe=timeframe)
            if state.is_fresh is False:
                raise HTTPException(
                    status_code=503,
                    detail={"error": "stale_market_data", "message": f"stale market state for {symbol}"},
                )
            return {"state": _serialize_market_state(state)}
        except HTTPException:
            raise
        except Exception as exc:
            raise _market_http_error(exc) from exc

    @app.get("/v1/portfolio")
    async def portfolio() -> Dict[str, Any]:
        snap = await manager.snapshot()
        return {
            "backend": snap.backend,
            "total_equity": snap.total_equity,
            "available_capital": snap.available_capital,
            "balances": [
                {"currency": b.currency, "free": b.free, "used": b.used, "total": b.total}
                for b in snap.balances
            ],
        }

    @app.get("/v1/positions/open")
    async def open_positions() -> Dict[str, Any]:
        snap = await manager.snapshot()
        return {
            "positions": [
                {
                    "symbol": p.symbol,
                    "side": p.side,
                    "entry_price": p.entry_price,
                    "profit": p.profit_amount,
                }
                for p in snap.open_positions
            ]
        }

    @app.get("/v1/positions/closed")
    async def closed_positions() -> Dict[str, Any]:
        if manager._adapter is None:
            return {"positions": []}
        positions = await manager._adapter.get_closed_positions()
        return {
            "positions": [
                {
                    "symbol": p.symbol,
                    "side": p.side,
                    "exit_price": p.exit_price,
                    "profit": p.profit_amount,
                }
                for p in positions
            ]
        }

    @app.get("/v1/performance")
    async def performance() -> Dict[str, Any]:
        if manager._adapter is None:
            return {}
        perf = await manager._adapter.get_performance()
        return {
            "total_trades": perf.total_trades,
            "winning_trades": perf.winning_trades,
            "losing_trades": perf.losing_trades,
            "win_rate": perf.win_rate,
            "total_profit": perf.total_profit,
            "total_loss": perf.total_loss,
        }

    @app.post("/v1/decisions/validate")
    async def validate_decision(req: DecisionRequest) -> Dict[str, Any]:
        decision = TradingDecision(**req.model_dump())
        result = await manager.validate_decision(decision)
        return {"valid": result.valid, "errors": result.errors, "warnings": result.warnings}

    @app.post("/v1/decisions/execute")
    async def execute_decision(req: DecisionRequest) -> Dict[str, Any]:
        decision = TradingDecision(**req.model_dump())
        result = await manager.execute_decision(decision)
        return {
            "success": result.success,
            "status": result.status,
            "error": result.error,
            "trade_id": result.provider_trade_id,
        }

    @app.post("/v1/runtime/start")
    async def runtime_start() -> Dict[str, Any]:
        await manager.start()
        return {"state": manager.state}

    @app.post("/v1/runtime/stop")
    async def runtime_stop() -> Dict[str, Any]:
        await manager.stop()
        return {"state": manager.state}

    @app.post("/v1/runtime/restart")
    async def runtime_restart() -> Dict[str, Any]:
        await manager.restart()
        return {"state": manager.state}

    return app
