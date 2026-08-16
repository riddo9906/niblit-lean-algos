"""Packaging/import contract tests for the ``niblit_lean_algos`` namespace.

These tests pin down the runtime-import repair (Phase A/B): the editable package
must expose the ``niblit_lean_algos`` namespace while keeping the legacy
flat-layout imports working, with the wrapper delegating to the single
authoritative ``manager`` module (no duplicate class).
"""
from __future__ import annotations

import importlib

import pytest


def test_namespace_importable():
    """The package must be importable from any cwd (not just the repo root)."""
    mod = importlib.import_module("niblit_lean_algos")
    assert mod is not None
    assert hasattr(mod, "__file__")


def test_manager_namespace_path():
    """``niblit_lean_algos.manager`` re-exports the authoritative root module."""
    from niblit_lean_algos.manager import (
        BOOT_STAGES,
        VALID_STATES,
        VALID_TRANSITIONS,
        BootStage,
        LeanAlgoManager,
        TradingRuntimeHealth,
        TradingRuntimeManager,
        TradingRuntimeSnapshot,
        WorkerState,
        get_lean_algo_manager,
    )

    assert TradingRuntimeManager is not None
    assert LeanAlgoManager is not None
    assert callable(get_lean_algo_manager)


def test_api_namespace_path():
    """``niblit_lean_algos.api.trading_api`` re-exports ``create_app``."""
    from niblit_lean_algos.api.trading_api import DecisionRequest, create_app

    assert callable(create_app)
    assert DecisionRequest is not None


def test_legacy_root_imports():
    """Legacy flat imports keep working from the repo root."""
    manager = importlib.import_module("manager")
    assert hasattr(manager, "TradingRuntimeManager")

    adapters_freqtrade = importlib.import_module("adapters.freqtrade.adapter")
    assert hasattr(adapters_freqtrade, "FreqtradeAdapter")


def test_wrapper_is_authoritative_identity():
    """The wrapper must reference the SAME class as the root module (no dup)."""
    import manager as root_manager

    from niblit_lean_algos.manager import TradingRuntimeManager as WrappedManager

    assert WrappedManager is root_manager.TradingRuntimeManager


def test_lean_algo_manager_alias_is_subclass():
    """``LeanAlgoManager`` is the backward-compatible subclass alias."""
    import manager as root_manager

    assert issubclass(root_manager.LeanAlgoManager, root_manager.TradingRuntimeManager)


def test_get_lean_algo_manager_returns_instance():
    """``get_lean_algo_manager`` returns the singleton manager."""
    import manager as root_manager

    instance = root_manager.get_lean_algo_manager()
    assert root_manager.TradingRuntimeManager is instance.__class__ or isinstance(
        instance, root_manager.TradingRuntimeManager
    )
    # reset the singleton so repeated test runs stay isolated
    root_manager.TradingRuntimeManager.reset_instance()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
