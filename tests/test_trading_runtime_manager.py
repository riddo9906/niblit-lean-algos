"""Tests for the TradingRuntimeManager, adapters, and lifecycle."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import pytest


@pytest.fixture(autouse=True)
def isolated_account(tmp_path, monkeypatch):
    monkeypatch.setenv('NIBLIT_PAPER_STATE_FILE', str(tmp_path / 'account.json'))
    monkeypatch.setenv('NIBLIT_TRADING_CONTROL_FILE', str(tmp_path / 'control.json'))
    monkeypatch.setenv('NIBLIT_EXECUTION_DB', str(tmp_path / 'executions.sqlite'))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from domain.trading_decision import TradingDecision
from manager import TradingRuntimeManager, LeanAlgoManager, get_lean_algo_manager


class TestManagerLifecycle(unittest.TestCase):
    """Phase 18: Manager lifecycle tests."""

    def test_construction(self):
        TradingRuntimeManager.reset_instance()
        mgr = TradingRuntimeManager(backend="paper", execution_mode="observe")
        self.assertEqual(mgr.state, "created")
        self.assertEqual(mgr.backend, "paper")

    def test_successful_startup(self):
        TradingRuntimeManager.reset_instance()
        mgr = TradingRuntimeManager(backend="paper", execution_mode="observe")
        asyncio.run(mgr.start())
        self.assertIn(mgr.state, ("ready", "degraded"))
        asyncio.run(mgr.stop())

    def test_idempotent_repeated_startup(self):
        TradingRuntimeManager.reset_instance()
        mgr = TradingRuntimeManager(backend="paper", execution_mode="observe")
        asyncio.run(mgr.start())
        asyncio.run(mgr.start())  # should not raise
        self.assertIn(mgr.state, ("ready", "degraded"))
        asyncio.run(mgr.stop())

    def test_successful_shutdown(self):
        TradingRuntimeManager.reset_instance()
        mgr = TradingRuntimeManager(backend="paper", execution_mode="observe")
        asyncio.run(mgr.start())
        asyncio.run(mgr.stop())
        self.assertEqual(mgr.state, "stopped")

    def test_idempotent_repeated_shutdown(self):
        TradingRuntimeManager.reset_instance()
        mgr = TradingRuntimeManager(backend="paper", execution_mode="observe")
        asyncio.run(mgr.start())
        asyncio.run(mgr.stop())
        asyncio.run(mgr.stop())  # should not raise
        self.assertEqual(mgr.state, "stopped")

    def test_restart(self):
        TradingRuntimeManager.reset_instance()
        mgr = TradingRuntimeManager(backend="paper", execution_mode="observe")
        asyncio.run(mgr.start())
        asyncio.run(mgr.restart())
        self.assertIn(mgr.state, ("ready", "degraded"))
        asyncio.run(mgr.stop())


class TestBackendSelection(unittest.TestCase):
    """Phase 18: Backend selection tests."""

    def test_paper_selection(self):
        TradingRuntimeManager.reset_instance()
        mgr = TradingRuntimeManager(backend="paper", execution_mode="observe")
        asyncio.run(mgr.start())
        self.assertEqual(mgr.backend, "paper")
        asyncio.run(mgr.stop())

    def test_lean_selection(self):
        TradingRuntimeManager.reset_instance()
        mgr = TradingRuntimeManager(backend="lean", execution_mode="observe")
        asyncio.run(mgr.start())
        self.assertEqual(mgr.backend, "lean")
        asyncio.run(mgr.stop())

    def test_unsupported_backend(self):
        TradingRuntimeManager.reset_instance()
        mgr = TradingRuntimeManager(backend="invalid", execution_mode="observe")
        asyncio.run(mgr.start())
        self.assertEqual(mgr.state, "failed")


class TestOwnership(unittest.TestCase):
    """Phase 18: Ownership tests."""

    def test_one_manager_instance(self):
        TradingRuntimeManager.reset_instance()
        a = TradingRuntimeManager.get_instance(backend="paper")
        b = TradingRuntimeManager.get_instance()
        self.assertIs(a, b)
        TradingRuntimeManager.reset_instance()

    def test_lean_algo_manager_alias(self):
        TradingRuntimeManager.reset_instance()
        mgr = get_lean_algo_manager(backend="paper")
        self.assertIsInstance(mgr, LeanAlgoManager)
        self.assertIsInstance(mgr, TradingRuntimeManager)
        TradingRuntimeManager.reset_instance()


class TestPaperAdapter(unittest.TestCase):
    """Phase 18: Paper adapter tests."""

    def test_entry_and_balance(self):
        from adapters.paper.adapter import PaperAdapter

        with tempfile.TemporaryDirectory() as tmp:
            adapter = PaperAdapter(initial_capital=10000.0, state_file=str(Path(tmp) / "paper.json"))
            asyncio.run(adapter.start())
            balances = asyncio.run(adapter.get_balances())
            self.assertEqual(len(balances), 1)
            self.assertEqual(balances[0].total, 10000.0)

            adapter.set_price("BTC/USDT", 50000.0)
            from domain.execution import ExecutionCommand

            result = asyncio.run(adapter.execute_order(
                ExecutionCommand(symbol="BTC/USDT", action="enter_long", stake_amount=100.0)
            ))
            self.assertTrue(result.success)
            self.assertEqual(result.status, "filled")

            positions = asyncio.run(adapter.get_open_positions())
            self.assertEqual(len(positions), 1)
            asyncio.run(adapter.stop())

    def test_exit_and_realized_pnl(self):
        from adapters.paper.adapter import PaperAdapter
        from domain.execution import ExecutionCommand

        with tempfile.TemporaryDirectory() as tmp:
            adapter = PaperAdapter(initial_capital=10000.0, state_file=str(Path(tmp) / "paper.json"))
            asyncio.run(adapter.start())
            adapter.set_price("BTC/USDT", 50000.0)
            asyncio.run(adapter.execute_order(
                ExecutionCommand(symbol="BTC/USDT", action="enter_long", stake_amount=100.0)
            ))
            adapter.set_price("BTC/USDT", 51000.0)
            result = asyncio.run(adapter.execute_order(
                ExecutionCommand(symbol="BTC/USDT", action="exit_long")
            ))
            self.assertTrue(result.success)
            perf = asyncio.run(adapter.get_performance())
            self.assertEqual(perf.total_trades, 1)
            self.assertEqual(perf.winning_trades, 1)
            asyncio.run(adapter.stop())


class TestLeanAdapter(unittest.TestCase):
    """Phase 18: Lean adapter tests."""

    def test_health_mapping(self):
        from adapters.lean.adapter import LeanAdapter

        adapter = LeanAdapter()
        asyncio.run(adapter.start())
        h = asyncio.run(adapter.health())
        self.assertTrue(h.healthy)
        self.assertEqual(h.backend, "lean")
        asyncio.run(adapter.stop())

    def test_unsupported_execution(self):
        from adapters.lean.adapter import LeanAdapter
        from domain.execution import ExecutionCommand

        adapter = LeanAdapter()
        result = asyncio.run(adapter.execute_order(
            ExecutionCommand(symbol="SPY", action="enter_long", stake_amount=100.0)
        ))
        self.assertFalse(result.success)
        self.assertEqual(result.status, "unsupported")


class TestMarketDataProvider(unittest.TestCase):
    """Phase 18: Market data provider tests."""

    def test_mock_ticker(self):
        from services.market_data_provider import MockMarketDataProvider

        provider = MockMarketDataProvider()
        ticker = asyncio.run(provider.get_ticker("BTC/USDT"))
        self.assertEqual(ticker.symbol, "BTC/USDT")
        self.assertGreater(ticker.last, 0)

    def test_mock_candles(self):
        from services.market_data_provider import MockMarketDataProvider

        provider = MockMarketDataProvider()
        candles = asyncio.run(provider.get_candles("BTC/USDT", "1h", 10))
        self.assertEqual(len(candles), 10)


class TestManagerDecisionFlow(unittest.TestCase):
    """Phase 18: Decision flow through manager tests."""

    def test_observe_mode_blocks_execution(self):
        TradingRuntimeManager.reset_instance()
        mgr = TradingRuntimeManager(backend="paper", execution_mode="observe")
        asyncio.run(mgr.start())
        decision = TradingDecision(
            decision_id="mgr-001",
            symbol="BTC/USDT",
            action="enter_long",
            confidence=0.8,
            rationale="test",
            stake_amount=50.0,
        )
        result = asyncio.run(mgr.execute_decision(decision))
        self.assertFalse(result.success)
        self.assertEqual(result.status, "execution_blocked")
        asyncio.run(mgr.stop())

    def test_paper_mode_executes(self):
        import time as _time
        TradingRuntimeManager.reset_instance()
        tmp = tempfile.mkdtemp()
        os.environ["NIBLIT_EXECUTION_DB"] = str(Path(tmp) / "test_exec.db")
        try:
            mgr = TradingRuntimeManager(backend="paper", execution_mode="paper")
            asyncio.run(mgr.start())
            # Set price on the paper adapter
            if hasattr(mgr._adapter, "set_price"):
                mgr._adapter.set_price("BTC/USDT", 50000.0)
            decision = TradingDecision(
                decision_id=f"mgr-paper-{_time.time_ns()}",
                symbol="BTC/USDT",
                action="enter_long",
                confidence=0.8,
                rationale="test",
                stake_amount=50.0,
            )
            result = asyncio.run(mgr.execute_decision(decision))
            self.assertTrue(result.success, f"Expected success but got: status={result.status} error={result.error}")
            asyncio.run(mgr.stop())
        finally:
            del os.environ["NIBLIT_EXECUTION_DB"]
            TradingRuntimeManager.reset_instance()


if __name__ == "__main__":
    unittest.main()
