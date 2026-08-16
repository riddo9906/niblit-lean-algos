"""Compatibility wrapper for the authoritative trading runtime manager.

``niblit_lean_algos.manager`` re-exports the authoritative ``TradingRuntimeManager``
implementation that lives at the repository root (``manager.py``). The root module
remains the single source of truth so existing tests and scripts are unaffected.
"""

from __future__ import annotations

from manager import (  # noqa: F401
    TradingRuntimeManager,
    LeanAlgoManager,
    get_lean_algo_manager,
    BootStage,
    WorkerState,
    TradingRuntimeSnapshot,
    TradingRuntimeHealth,
    VALID_STATES,
    VALID_TRANSITIONS,
    BOOT_STAGES,
)

__all__ = [
    "TradingRuntimeManager",
    "LeanAlgoManager",
    "get_lean_algo_manager",
    "BootStage",
    "WorkerState",
    "TradingRuntimeSnapshot",
    "TradingRuntimeHealth",
    "VALID_STATES",
    "VALID_TRANSITIONS",
    "BOOT_STAGES",
]