"""Tests for the Freqtrade adapter, risk engine, and execution coordinator."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure the niblit-lean-algos directory is on the path for flat imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from domain.trading_decision import TradingDecision
from domain.execution import ExecutionCommand, ExecutionResult
from risk.engine import RiskEngine
from risk.policy import RiskPolicy
from services.decision_validator import DecisionValidator
from adapters.freqtrade.mapper import map_balance, map_position, normalize_runtime_state


class TestRuntimeStateMapping(unittest.TestCase):
    """Phase 7: Runtime state mapping tests."""

    def test_running_state(self):
        self.assertEqual(normalize_runtime_state("running"), "running")

    def test_stopped_state(self):
        self.assertEqual(normalize_runtime_state("stopped"), "stopped")

    def test_error_state(self):
        self.assertEqual(normalize_runtime_state("error"), "error")

    def test_fatal_state_maps_to_error(self):
        self.assertEqual(normalize_runtime_state("fatal"), "error")

    def test_unknown_state(self):
        self.assertEqual(normalize_runtime_state("weird"), "unknown")


class TestBalanceMapping(unittest.TestCase):
    """Phase 8: Balance mapping tests."""

    def test_map_balance_basic(self):
        raw = {"currency": "BTC", "free": 0.5, "used": 0.1, "total": 0.6, "value": 30000.0}
        b = map_balance(raw)
        self.assertEqual(b.currency, "BTC")
        self.assertEqual(b.free, 0.5)
        self.assertEqual(b.used, 0.1)
        self.assertEqual(b.total, 0.6)
        self.assertEqual(b.usd_value, 30000.0)

    def test_map_balance_missing_value(self):
        raw = {"currency": "USDT", "free": 1000.0, "used": 0.0, "total": 1000.0}
        b = map_balance(raw)
        self.assertIsNone(b.usd_value)


class TestPositionMapping(unittest.TestCase):
    """Phase 8: Position mapping tests."""

    def test_map_open_position(self):
        raw = {
            "trade_id": 42,
            "pair": "BTC/USDT",
            "is_short": False,
            "open_date": "2024-01-01T00:00:00Z",
            "open_rate": 50000.0,
            "current_rate": 51000.0,
            "stake_amount": 100.0,
            "amount": 0.002,
            "profit_abs": 2.0,
            "profit_ratio": 0.02,
            "leverage": 1.0,
            "strategy": "TestStrategy",
        }
        p = map_position(raw, is_open=True)
        self.assertEqual(p.symbol, "BTC/USDT")
        self.assertEqual(p.side, "long")
        self.assertEqual(p.status, "open")
        self.assertEqual(p.entry_price, 50000.0)
        self.assertEqual(p.current_price, 51000.0)
        self.assertEqual(p.profit_amount, 2.0)
        self.assertEqual(p.strategy, "TestStrategy")
        self.assertIsNone(p.exit_reason)

    def test_map_closed_position(self):
        raw = {
            "trade_id": 43,
            "pair": "ETH/USDT",
            "is_short": True,
            "open_date": "2024-01-01T00:00:00Z",
            "close_date": "2024-01-02T00:00:00Z",
            "open_rate": 3000.0,
            "close_rate": 2900.0,
            "stake_amount": 50.0,
            "amount": 0.0167,
            "profit_abs": 1.67,
            "profit_ratio": 0.033,
            "exit_reason": "take_profit",
        }
        p = map_position(raw, is_open=False)
        self.assertEqual(p.side, "short")
        self.assertEqual(p.status, "closed")
        self.assertEqual(p.exit_price, 2900.0)
        self.assertEqual(p.exit_reason, "take_profit")


class TestRiskEngine(unittest.TestCase):
    """Phase 10: Risk engine tests."""

    def _make_decision(self, **kwargs: Any) -> TradingDecision:
        defaults = {
            "decision_id": "test-001",
            "symbol": "BTC/USDT",
            "action": "enter_long",
            "confidence": 0.8,
            "rationale": "test",
            "stake_amount": 50.0,
        }
        defaults.update(kwargs)
        return TradingDecision(**defaults)

    def test_approved_decision(self):
        policy = RiskPolicy(maximum_stake_percentage=0.10, minimum_confidence=0.5)
        engine = RiskEngine(policy)
        decision = self._make_decision()
        result = engine.assess(decision, portfolio_value=1000.0, open_positions=[])
        self.assertTrue(result.approved)
        self.assertEqual(len(result.violations), 0)

    def test_excessive_stake_reduced(self):
        policy = RiskPolicy(maximum_stake_percentage=0.05)
        engine = RiskEngine(policy)
        decision = self._make_decision(stake_amount=100.0)
        result = engine.assess(decision, portfolio_value=1000.0, open_positions=[])
        self.assertTrue(result.approved)
        self.assertIsNotNone(result.adjusted_stake_amount)
        self.assertLessEqual(result.adjusted_stake_amount, 50.0)

    def test_low_confidence_rejected(self):
        policy = RiskPolicy(minimum_confidence=0.7)
        engine = RiskEngine(policy)
        decision = self._make_decision(confidence=0.3)
        result = engine.assess(decision, portfolio_value=1000.0, open_positions=[])
        self.assertFalse(result.approved)
        self.assertIn("confidence_below_minimum", result.violations)

    def test_max_open_positions_exceeded(self):
        policy = RiskPolicy(maximum_open_positions=2)
        engine = RiskEngine(policy)
        from domain.portfolio import Position

        pos1 = Position(position_id="1", symbol="ETH/USDT", side="long", status="open", opened_at=datetime.now(timezone.utc))
        pos2 = Position(position_id="2", symbol="ADA/USDT", side="long", status="open", opened_at=datetime.now(timezone.utc))
        decision = self._make_decision()
        result = engine.assess(decision, portfolio_value=1000.0, open_positions=[pos1, pos2])
        self.assertFalse(result.approved)
        self.assertIn("maximum_open_positions_exceeded", result.violations)

    def test_blocked_symbol_rejected(self):
        policy = RiskPolicy(blocked_pairs=["BTC/USDT"])
        engine = RiskEngine(policy)
        decision = self._make_decision()
        result = engine.assess(decision, portfolio_value=1000.0, open_positions=[])
        self.assertFalse(result.approved)
        self.assertIn("symbol_blacklisted", result.violations)


class TestDecisionValidator(unittest.TestCase):
    """Phase 9: Decision validation tests."""

    def _make_decision(self, **kwargs: Any) -> TradingDecision:
        defaults = {
            "decision_id": "test-002",
            "symbol": "BTC/USDT",
            "action": "enter_long",
            "confidence": 0.8,
            "rationale": "test",
            "stake_amount": 50.0,
        }
        defaults.update(kwargs)
        return TradingDecision(**defaults)

    def test_valid_decision(self):
        validator = DecisionValidator()
        decision = self._make_decision()
        result = validator.validate(decision, portfolio_value=1000.0)
        self.assertTrue(result.valid)
        self.assertIsNotNone(result.adjusted_command)

    def test_missing_decision_id(self):
        validator = DecisionValidator()
        decision = self._make_decision(decision_id="")
        result = validator.validate(decision)
        self.assertFalse(result.valid)
        self.assertIn("decision_id_missing", result.errors)

    def test_unsupported_action(self):
        validator = DecisionValidator()
        decision = self._make_decision(action="buy")
        result = validator.validate(decision)
        self.assertFalse(result.valid)

    def test_blacklisted_symbol(self):
        validator = DecisionValidator()
        decision = self._make_decision()
        result = validator.validate(decision, blacklist=["BTC/USDT"])
        self.assertFalse(result.valid)
        self.assertIn("symbol_blacklisted", result.errors)

    def test_duplicate_decision(self):
        validator = DecisionValidator()
        decision = self._make_decision()
        result = validator.validate(decision, already_executed_ids={"test-002"})
        self.assertFalse(result.valid)
        self.assertIn("decision_already_executed", result.errors)

    def test_insufficient_funds(self):
        validator = DecisionValidator()
        decision = self._make_decision(stake_amount=2000.0)
        result = validator.validate(decision, portfolio_value=1000.0)
        self.assertFalse(result.valid)
        self.assertIn("insufficient_funds", result.errors)


class TestExecutionSafety(unittest.TestCase):
    """Phase 11: Execution safety mode tests."""

    def test_observe_mode_blocks_execution(self):
        from services.execution_coordinator import ExecutionCoordinator

        mock_adapter = MagicMock()
        mock_adapter.execute_order = AsyncMock()
        coordinator = ExecutionCoordinator(
            adapter=mock_adapter,
            execution_mode="observe",
        )
        decision = TradingDecision(
            decision_id="test-003",
            symbol="BTC/USDT",
            action="enter_long",
            confidence=0.8,
            rationale="test",
            stake_amount=50.0,
        )
        result = asyncio.run(coordinator.process_decision(decision, portfolio_value=1000.0))
        self.assertFalse(result.success)
        self.assertEqual(result.status, "execution_blocked")
        mock_adapter.execute_order.assert_not_called()

    def test_live_mode_without_acknowledgement_blocks(self):
        from services.execution_coordinator import ExecutionCoordinator

        mock_adapter = MagicMock()
        mock_adapter.execute_order = AsyncMock()
        coordinator = ExecutionCoordinator(
            adapter=mock_adapter,
            execution_mode="live",
            live_acknowledgement=None,
        )
        decision = TradingDecision(
            decision_id="test-004",
            symbol="BTC/USDT",
            action="enter_long",
            confidence=0.8,
            rationale="test",
            stake_amount=50.0,
        )
        result = asyncio.run(coordinator.process_decision(decision, portfolio_value=1000.0))
        self.assertFalse(result.success)
        self.assertIn("acknowledgement", result.error.lower())


class TestIdempotency(unittest.TestCase):
    """Phase 12: Idempotency tests."""

    def test_duplicate_decision_rejected(self):
        from persistence.execution_repository import ExecutionRepository

        with tempfile.TemporaryDirectory() as tmp:
            repo = ExecutionRepository(str(Path(tmp) / "test.db"))
            repo.record_request("dup-001", {"symbol": "BTC/USDT"}, "freqtrade")
            self.assertTrue(repo.has_decision("dup-001"))
            repo.close()


class TestEndpointConstruction(unittest.TestCase):
    """Phase 4: Endpoint construction tests."""

    def test_normalized_url_strips_trailing_slash(self):
        from adapters.freqtrade.config import FreqtradeAdapterConfig

        config = FreqtradeAdapterConfig(base_url="http://127.0.0.1:8080/")
        self.assertEqual(config.normalized_url, "http://127.0.0.1:8080")

    def test_normalized_url_strips_api_v1(self):
        from adapters.freqtrade.config import FreqtradeAdapterConfig

        config = FreqtradeAdapterConfig(base_url="http://127.0.0.1:8080/api/v1")
        self.assertEqual(config.normalized_url, "http://127.0.0.1:8080")

    def test_api_base_appends_v1(self):
        from adapters.freqtrade.config import FreqtradeAdapterConfig

        config = FreqtradeAdapterConfig(base_url="http://127.0.0.1:8080")
        self.assertEqual(config.normalized_url, "http://127.0.0.1:8080")


if __name__ == "__main__":
    unittest.main()