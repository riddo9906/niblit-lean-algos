"""Signal server for REST/WebSocket signal transport."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from aiohttp import web

from .signal import Signal
from .config import BridgeConfig

log = logging.getLogger(__name__)


class SignalServer:
    """Expose signals via REST and WebSocket.

    Endpoints:
    - GET /signals - List recent signals
    - GET /signals/{pair} - Get signal for specific pair
    - POST /signals - Submit a new signal
    - GET /positions - Current positions
    - GET /portfolio - Portfolio summary
    - POST /pause - Pause trading
    - POST /resume - Resume trading
    - POST /emergency-stop - Emergency halt
    """

    def __init__(self, config: Optional[BridgeConfig] = None):
        self._config = config or BridgeConfig()
        self._signals: List[Signal] = []
        self._paused: bool = False
        self._app: Optional[web.Application] = None

    async def get_signals(self) -> List[Dict[str, Any]]:
        """Return all recent signals."""
        return [s.to_dict() for s in self._signals[-100:]]

    async def get_signal(self, pair: str) -> Optional[Dict[str, Any]]:
        """Return signal for a specific pair."""
        for sig in reversed(self._signals):
            if sig.pair == pair:
                return sig.to_dict()
        return None

    async def post_signal(self, data: Dict[str, Any]) -> bool:
        """Accept and store a new signal."""
        signal = Signal.from_dict(data)
        if signal:
            self._signals.append(signal)
            log.info("Signal received: %s %s (confidence=%.2f)", signal.side.value, signal.pair, signal.confidence)
            return True
        return False

    async def pause_trading(self) -> None:
        """Pause trading."""
        self._paused = True
        log.warning("Trading paused via API")

    async def resume_trading(self) -> None:
        """Resume trading."""
        self._paused = False
        log.info("Trading resumed via API")

    async def emergency_stop(self) -> None:
        """Trigger emergency stop."""
        self._paused = True
        log.critical("EMERGENCY STOP triggered via API")

    def is_paused(self) -> bool:
        """Check if trading is paused."""
        return self._paused

    def create_app(self) -> web.Application:
        """Create aiohttp application."""
        app = web.Application()
        app.router.add_get("/signals", self._handle_get_signals)
        app.router.add_get("/signals/{pair}", self._handle_get_signal)
        app.router.add_post("/signals", self._handle_post_signal)
        app.router.add_post("/pause", self._handle_pause)
        app.router.add_post("/resume", self._handle_resume)
        app.router.add.post("/emergency-stop", self._handle_emergency_stop)
        self._app = app
        return app

    async def _handle_get_signals(self, request: web.Request) -> web.Response:
        signals = await self.get_signals()
        return web.json_response({"signals": signals})

    async def _handle_get_signal(self, request: web.Request) -> web.Response:
        pair = request.match_info.get("pair", "")
        signal = await self.get_signal(pair)
        if signal:
            return web.json_response(signal)
        return web.json_response({"error": "No signal found"}, status=404)

    async def _handle_post_signal(self, request: web.Request) -> web.Response:
        data = await request.json()
        success = await self.post_signal(data)
        status = 201 if success else 400
        return web.json_response({"status": "created" if success else "failed"}, status=status)

    async def _handle_pause(self, request: web.Request) -> web.Response:
        await self.pause_trading()
        return web.json_response({"status": "paused"})

    async def _handle_resume(self, request: web.Request) -> web.Response:
        await self.resume_trading()
        return web.json_response({"status": "resumed"})

    async def _handle_emergency_stop(self, request: web.Request) -> web.Response:
        await self.emergency_stop()
        return web.json_response({"status": "emergency_stopped"})

    async def start(self, host: str = "127.0.0.1", port: int = 8080) -> None:
        """Start the signal server."""
        app = self.create_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        log.info("Signal server started on %s:%d", host, port)

    def status(self) -> Dict[str, Any]:
        return {
            "paused": self._paused,
            "signal_count": len(self._signals),
            "last_signals": [s.pair for s in self._signals[-10:]],
        }