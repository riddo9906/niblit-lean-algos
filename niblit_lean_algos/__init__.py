"""Niblit Lean Algorithms — universal trading runtime.

Provides:
  - TradingRuntimeManager (paper, freqtrade, lean backends)
  - LeanAlgoManager (backward-compatible alias)
  - get_lean_algo_manager()

This namespace package makes ``niblit_lean_algos.manager`` importable from anywhere
(including the Niblit Core venv) while re-exporting the authoritative implementation
that lives at the repository root.
"""

from __future__ import annotations

from niblit_lean_algos.manager import (  # noqa: F401
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