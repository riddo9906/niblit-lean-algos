"""Standalone launcher for the Niblit trading runtime."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

# Ensure flat imports work when run from the repo root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from manager import TradingRuntimeManager

log = logging.getLogger("niblit-trading-runtime")


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    backend = os.getenv("NIBLIT_TRADING_BACKEND", "paper")
    mode = os.getenv("NIBLIT_TRADING_MODE", "observe")
    api_port = int(os.getenv("NIBLIT_TRADING_API_PORT", "8100"))

    log.info("Starting Niblit Trading Runtime — backend=%s mode=%s", backend, mode)

    manager = TradingRuntimeManager.get_instance(backend=backend, execution_mode=mode)
    await manager.start()

    # Start optional REST API
    api_task: asyncio.Task | None = None
    try:
        from api.trading_api import create_app
        import uvicorn

        app = create_app(manager)
        config = uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="info")
        server = uvicorn.Server(config)

        async def _run_api():
            await server.serve()

        api_task = asyncio.create_task(_run_api())
        log.info("REST API started on 127.0.0.1:%d", api_port)
    except ImportError:
        log.info("FastAPI/uvicorn not installed — skipping REST API")

    # Wait for shutdown signal
    stop_event = asyncio.Event()

    def _signal_handler(*_args):
        log.info("Shutdown signal received")
        stop_event.set()

    # Windows doesn't support loop.add_signal_handler
    if sys.platform != "win32":
        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGINT, _signal_handler)
        loop.add_signal_handler(signal.SIGTERM, _signal_handler)
    else:
        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

    await stop_event.wait()

    log.info("Shutting down...")
    if api_task:
        api_task.cancel()
    await manager.stop()
    log.info("Shutdown complete")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))