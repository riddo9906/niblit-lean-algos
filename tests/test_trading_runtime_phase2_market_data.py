"""Phase 2 tests — real market data wiring + observe-mode pipeline."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from domain import Candle, MarketState, MarketTicker, TradingDecision
from domain.execution import ExecutionResult
from manager import TradingRuntimeManager
from services.ccxt_market_data_provider import CCXTMarketDataProvider
from services.decision_validator import DecisionValidator
from services.execution_coordinator import ExecutionCoordinator
from services.market_data_provider import MarketDataError, MockMarketDataProvider
from services.market_state import MarketStateService
from services.market_symbol import MarketSymbolError, normalize_market_symbol


def _fresh_ticker(symbol: str = "BTC/USDT:USDT", last: float = 50000.0, **kwargs: Any) -> MarketTicker:
    now = datetime.now(timezone.utc)
    return MarketTicker(
        symbol=symbol,
        last=last,
        bid=kwargs.get("bid", last * 0.999),
        ask=kwargs.get("ask", last * 1.001),
        open=kwargs.get("open", last),
        high=kwargs.get("high", last * 1.01),
        low=kwargs.get("low", last * 0.99),
        volume=kwargs.get("volume", 100.0),
        base_volume=kwargs.get("base_volume", 100.0),
        quote_volume=kwargs.get("quote_volume", last * 100.0),
        timestamp=kwargs.get("timestamp", now),
        source=kwargs.get("source", "test"),
        age_seconds=kwargs.get("age_seconds", 0.0),
        is_fresh=kwargs.get("is_fresh", True),
    )


def _candles(
    symbol: str = "BTC/USDT:USDT",
    n: int = 20,
    start_price: float = 100.0,
    end_price: float = 110.0,
    timeframe: str = "5m",
) -> List[Candle]:
    now = datetime.now(timezone.utc)
    out: List[Candle] = []
    for i in range(n):
        # Linear price path for deterministic trend
        price = start_price + (end_price - start_price) * (i / max(n - 1, 1))
        ts = now - timedelta(minutes=5 * (n - i))
        out.append(
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=ts,
                open=price,
                high=price * 1.01,
                low=price * 0.99,
                close=price,
                volume=10.0,
            )
        )
    return out


class TestProviderConfiguration(unittest.TestCase):
    def setUp(self) -> None:
        TradingRuntimeManager.reset_instance()
        self._env_backup = dict(os.environ)

    def tearDown(self) -> None:
        TradingRuntimeManager.reset_instance()
        os.environ.clear()
        os.environ.update(self._env_backup)

    def test_default_freqtrade_provider_is_ccxt(self):
        os.environ.pop("NIBLIT_MARKET_DATA_PROVIDER", None)
        mgr = TradingRuntimeManager(backend="freqtrade", execution_mode="observe")
        self.assertEqual(mgr._resolve_market_data_provider_name(), "ccxt")

    def test_explicit_mock_provider(self):
        os.environ["NIBLIT_MARKET_DATA_PROVIDER"] = "mock"
        mgr = TradingRuntimeManager(backend="freqtrade", execution_mode="observe")
        provider = mgr._build_market_data_provider()
        self.assertIsInstance(provider, MockMarketDataProvider)

    def test_unknown_provider_rejected(self):
        os.environ["NIBLIT_MARKET_DATA_PROVIDER"] = "not-a-provider"
        mgr = TradingRuntimeManager(backend="freqtrade", execution_mode="observe")
        with self.assertRaises(ValueError):
            mgr._build_market_data_provider()

    def test_ccxt_unavailable_causes_degraded_runtime_no_silent_mock(self):
        os.environ["NIBLIT_MARKET_DATA_PROVIDER"] = "ccxt"
        os.environ["NIBLIT_EXECUTION_DB"] = str(Path(tempfile.mkdtemp()) / "exec.db")
        mgr = TradingRuntimeManager(backend="freqtrade", execution_mode="observe")

        fake_adapter = MagicMock()
        fake_adapter.health = AsyncMock(return_value=MagicMock(healthy=True))
        fake_adapter.close = AsyncMock()
        fake_adapter.get_balances = AsyncMock(return_value=[])
        fake_adapter.get_open_positions = AsyncMock(return_value=[])
        mgr._adapter_registry["freqtrade"] = MagicMock(return_value=fake_adapter)

        async def _fail_health(*_a, **_k):
            from services.market_data_provider import MarketDataHealth

            return MarketDataHealth(
                healthy=False,
                source="ccxt",
                provider="ccxt",
                error="boom",
            )

        with patch.object(CCXTMarketDataProvider, "health", new=AsyncMock(side_effect=_fail_health)):
            asyncio.run(mgr.start())
        self.assertEqual(mgr.state, "degraded")
        self.assertEqual(mgr._market_data_degraded_reason, "market_data_provider_unavailable")
        self.assertFalse(isinstance(mgr.market_data_provider, MockMarketDataProvider))
        asyncio.run(mgr.stop())


class TestSymbolNormalization(unittest.TestCase):
    def test_valid_swap_symbol(self):
        self.assertEqual(
            normalize_market_symbol("BTC/USDT:USDT", provider="ccxt", market_type="swap"),
            "BTC/USDT:USDT",
        )
        self.assertEqual(
            normalize_market_symbol("ETH/USDT:USDT", provider="ccxt", market_type="swap"),
            "ETH/USDT:USDT",
        )

    def test_valid_spot_symbol(self):
        self.assertEqual(
            normalize_market_symbol("BTC/USDT", provider="ccxt", market_type="spot"),
            "BTC/USDT",
        )

    def test_spot_conversion_of_settled_only_when_spot(self):
        self.assertEqual(
            normalize_market_symbol("BTC/USDT:USDT", provider="ccxt", market_type="spot"),
            "BTC/USDT",
        )

    def test_missing_slash(self):
        with self.assertRaises(MarketSymbolError):
            normalize_market_symbol("BTCUSDT", provider="ccxt", market_type="swap")

    def test_missing_settlement_currency(self):
        with self.assertRaises(MarketSymbolError):
            normalize_market_symbol("BTC/USDT:", provider="ccxt", market_type="swap")

    def test_malformed_pair(self):
        with self.assertRaises(MarketSymbolError):
            normalize_market_symbol("", provider="ccxt", market_type="swap")

    def test_futures_settlement_preserved(self):
        # Must not silently become BTC/USDT for swap
        self.assertEqual(
            normalize_market_symbol("sol/usdt:usdt", provider="ccxt", market_type="swap"),
            "SOL/USDT:USDT",
        )


class TestTickerMapping(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = CCXTMarketDataProvider(max_age_seconds=90)

    def test_normal_ticker(self):
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        raw = {
            "last": 50000.0,
            "bid": 49990.0,
            "ask": 50010.0,
            "open": 49000.0,
            "high": 51000.0,
            "low": 48000.0,
            "baseVolume": 12.5,
            "quoteVolume": 625000.0,
            "timestamp": now_ms,
        }
        ticker = self.provider._map_ticker(raw, "BTC/USDT:USDT")
        self.assertEqual(ticker.last, 50000.0)
        self.assertEqual(ticker.bid, 49990.0)
        self.assertEqual(ticker.base_volume, 12.5)
        self.assertTrue(ticker.is_fresh)

    def test_missing_optional_bid(self):
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        raw = {"last": 100.0, "ask": 101.0, "timestamp": now_ms, "baseVolume": 1.0}
        ticker = self.provider._map_ticker(raw, "BTC/USDT:USDT")
        self.assertIsNone(ticker.bid)
        self.assertEqual(ticker.ask, 101.0)

    def test_missing_required_last(self):
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self.assertRaises(MarketDataError):
            self.provider._map_ticker({"timestamp": now_ms}, "BTC/USDT:USDT")

    def test_negative_price(self):
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self.assertRaises(MarketDataError):
            self.provider._map_ticker({"last": -1, "timestamp": now_ms}, "BTC/USDT:USDT")

    def test_bid_gt_ask(self):
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self.assertRaises(MarketDataError):
            self.provider._map_ticker(
                {"last": 100, "bid": 102, "ask": 101, "timestamp": now_ms},
                "BTC/USDT:USDT",
            )

    def test_fresh_timestamp(self):
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        ticker = self.provider._map_ticker({"last": 100, "timestamp": now_ms}, "BTC/USDT:USDT")
        self.assertTrue(ticker.is_fresh)

    def test_stale_timestamp(self):
        old = datetime.now(timezone.utc) - timedelta(seconds=200)
        old_ms = int(old.timestamp() * 1000)
        with self.assertRaises(MarketDataError):
            self.provider._map_ticker({"last": 100, "timestamp": old_ms}, "BTC/USDT:USDT")


class TestCandleMapping(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = CCXTMarketDataProvider()

    def _row(self, ts: int, o: float = 1, h: float = 2, l: float = 0.5, c: float = 1.5, v: float = 10):
        return [ts, o, h, l, c, v]

    def test_valid_ohlcv(self):
        base = int((datetime.now(timezone.utc) - timedelta(minutes=30)).timestamp() * 1000)
        rows = [self._row(base + i * 300_000) for i in range(5)]
        candles = self.provider._map_candles(rows, "BTC/USDT:USDT", "5m")
        self.assertEqual(len(candles), 5)
        self.assertLess(candles[0].timestamp, candles[-1].timestamp)

    def test_duplicate_timestamp(self):
        ts = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self.assertRaises(MarketDataError):
            self.provider._map_candles([self._row(ts), self._row(ts)], "BTC/USDT:USDT", "5m")

    def test_out_of_order_candles_are_sorted(self):
        base = int((datetime.now(timezone.utc) - timedelta(minutes=20)).timestamp() * 1000)
        rows = [self._row(base + 600_000), self._row(base), self._row(base + 300_000)]
        candles = self.provider._map_candles(rows, "BTC/USDT:USDT", "5m")
        stamps = [c.timestamp for c in candles]
        self.assertEqual(stamps, sorted(stamps))

    def test_invalid_high_low(self):
        ts = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self.assertRaises(MarketDataError):
            self.provider._map_candles([self._row(ts, o=10, h=9, l=8, c=9)], "BTC/USDT:USDT", "5m")

    def test_negative_close(self):
        ts = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self.assertRaises(MarketDataError):
            self.provider._map_candles([self._row(ts, o=1, h=2, l=0.5, c=-1)], "BTC/USDT:USDT", "5m")

    def test_unfinished_candle_dropped(self):
        now = datetime.now(timezone.utc)
        # Last candle opened 30s ago → unfinished for 5m
        rows = [
            [
                int((now - timedelta(minutes=10)).timestamp() * 1000),
                1, 2, 0.5, 1.5, 10,
            ],
            [
                int((now - timedelta(seconds=30)).timestamp() * 1000),
                1, 2, 0.5, 1.5, 10,
            ],
        ]
        candles = self.provider._map_candles(rows, "BTC/USDT:USDT", "5m")
        dropped = self.provider._drop_active_candle(candles, "5m")
        self.assertEqual(len(dropped), 1)


class TestMarketStateCalculations(unittest.TestCase):
    def test_bullish_bearish_sideways(self):
        svc = MarketStateService(max_staleness_seconds=90)
        bullish = svc._compute_trend(_candles(start_price=100, end_price=110))
        bearish = svc._compute_trend(_candles(start_price=110, end_price=100))
        sideways = svc._compute_trend(_candles(start_price=100, end_price=100.01))
        self.assertEqual(bullish, "bullish")
        self.assertEqual(bearish, "bearish")
        self.assertEqual(sideways, "sideways")

    def test_volatility_regimes(self):
        svc = MarketStateService()
        self.assertEqual(svc._classify_regime(0.005), "low_volatility")
        self.assertEqual(svc._classify_regime(0.02), "normal")
        self.assertEqual(svc._classify_regime(0.05), "high_volatility")

    def test_fresh_stale_and_cache(self):
        provider = MockMarketDataProvider()
        svc = MarketStateService(provider=provider, max_staleness_seconds=90, source="mock")

        async def _run():
            state = await svc.get_market_state("BTC/USDT:USDT")
            self.assertTrue(state.is_fresh)
            cached = await svc.get_market_state("BTC/USDT:USDT")
            self.assertIs(state, cached)
            # Force stale
            state.is_fresh = False
            state.age_seconds = 999
            svc._cache["BTC/USDT:USDT"] = state
            svc._last_refresh["BTC/USDT:USDT"] = 0
            self.assertEqual(svc.reject_if_stale("BTC/USDT:USDT"), "stale_market_data")
            refreshed = await svc.get_market_state("BTC/USDT:USDT", force_refresh=True)
            self.assertTrue(refreshed.is_fresh)

        asyncio.run(_run())

    def test_timeframe_aware_candle_freshness(self):
        """Just-closed candles are fresh; clearly old candles are stale per TF."""
        max_age = 90
        cases = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "1h": 3600,
        }
        now = datetime.now(timezone.utc)
        for tf, tf_seconds in cases.items():
            svc = MarketStateService(max_staleness_seconds=max_age, timeframe=tf)
            # Just closed: open = now - timeframe → close ≈ now
            just_closed = Candle(
                symbol="BTC/USDT:USDT",
                timeframe=tf,
                timestamp=now - timedelta(seconds=tf_seconds),
                open=100,
                high=101,
                low=99,
                close=100.5,
                volume=1.0,
            )
            ticker = _fresh_ticker(timestamp=now, age_seconds=0.0, is_fresh=True)
            fresh_state = asyncio.run(
                svc.refresh("BTC/USDT:USDT", ticker=ticker, candles=[just_closed], timeframe=tf)
            )
            self.assertTrue(fresh_state.is_fresh, f"{tf} just-closed should be fresh")

            # Clearly old: closed more than timeframe + grace ago
            old_open = now - timedelta(seconds=(2 * tf_seconds + max_age + 30))
            old_candle = Candle(
                symbol="BTC/USDT:USDT",
                timeframe=tf,
                timestamp=old_open,
                open=100,
                high=101,
                low=99,
                close=100.5,
                volume=1.0,
            )
            stale_state = asyncio.run(
                svc.refresh("BTC/USDT:USDT", ticker=ticker, candles=[old_candle], timeframe=tf)
            )
            self.assertFalse(stale_state.is_fresh, f"{tf} old candle should be stale")


class TestFreqtradeContextAndValidation(unittest.TestCase):
    def test_whitelist_blacklist_locks_portfolio_passed(self):
        validator = DecisionValidator()
        decision = TradingDecision(
            decision_id="p2-1",
            symbol="BTC/USDT:USDT",
            action="enter_long",
            confidence=0.8,
            rationale="test",
            stake_amount=10.0,
        )
        market = MarketState(
            symbol="BTC/USDT:USDT",
            timestamp=datetime.now(timezone.utc),
            ticker=_fresh_ticker(),
            age_seconds=5.0,
            is_fresh=True,
        )
        ok = validator.validate(
            decision,
            whitelist=["BTC/USDT:USDT"],
            blacklist=[],
            pair_locks=[],
            portfolio_value=1000.0,
            market_state=market,
            require_market_state=True,
        )
        self.assertTrue(ok.valid)

        bad_wl = validator.validate(
            decision,
            whitelist=["ETH/USDT:USDT"],
            portfolio_value=1000.0,
            market_state=market,
            require_market_state=True,
        )
        self.assertIn("symbol_not_in_whitelist", bad_wl.errors)

        bad_bl = validator.validate(
            decision,
            blacklist=["BTC/USDT:USDT"],
            portfolio_value=1000.0,
            market_state=market,
            require_market_state=True,
        )
        self.assertIn("symbol_blacklisted", bad_bl.errors)

        locked = validator.validate(
            decision,
            pair_locks=["BTC/USDT:USDT"],
            portfolio_value=1000.0,
            market_state=market,
            require_market_state=True,
        )
        self.assertIn("pair_locked", locked.errors)

    def test_fresh_accepted_stale_rejected(self):
        validator = DecisionValidator()
        decision = TradingDecision(
            decision_id="p2-2",
            symbol="BTC/USDT:USDT",
            action="enter_long",
            confidence=0.8,
            rationale="test",
            stake_amount=10.0,
        )
        fresh = MarketState(
            symbol="BTC/USDT:USDT",
            timestamp=datetime.now(timezone.utc),
            ticker=_fresh_ticker(),
            is_fresh=True,
            age_seconds=10,
        )
        stale = MarketState(
            symbol="BTC/USDT:USDT",
            timestamp=datetime.now(timezone.utc),
            ticker=_fresh_ticker(),
            is_fresh=False,
            age_seconds=120,
        )
        self.assertTrue(
            validator.validate(
                decision,
                portfolio_value=1000.0,
                market_state=fresh,
                require_market_state=True,
            ).valid
        )
        result = validator.validate(
            decision,
            portfolio_value=1000.0,
            market_state=stale,
            require_market_state=True,
        )
        self.assertIn("stale_market_data", result.errors)

    def test_insufficient_funds_and_duplicate(self):
        validator = DecisionValidator()
        decision = TradingDecision(
            decision_id="p2-3",
            symbol="BTC/USDT:USDT",
            action="enter_long",
            confidence=0.8,
            rationale="test",
            stake_amount=5000.0,
        )
        market = MarketState(
            symbol="BTC/USDT:USDT",
            timestamp=datetime.now(timezone.utc),
            ticker=_fresh_ticker(),
            is_fresh=True,
        )
        funds = validator.validate(
            decision,
            portfolio_value=100.0,
            market_state=market,
            require_market_state=True,
        )
        self.assertIn("insufficient_funds", funds.errors)
        dup = validator.validate(
            decision,
            portfolio_value=10000.0,
            market_state=market,
            require_market_state=True,
            already_executed_ids={"p2-3"},
        )
        self.assertIn("decision_already_executed", dup.errors)

    def test_lock_parsing_expired_and_different_pair(self):
        mgr = TradingRuntimeManager(backend="paper", execution_mode="observe")
        now = datetime.now(timezone.utc)
        raw = {
            "locks": [
                {"pair": "BTC/USDT:USDT", "lock_end_time": (now + timedelta(hours=1)).isoformat()},
                {"pair": "ETH/USDT:USDT", "lock_end_time": (now - timedelta(hours=1)).isoformat()},
                {"pair": "SOL/USDT:USDT", "active": False},
            ]
        }
        active = mgr._parse_active_locks(raw)
        self.assertIn("BTC/USDT:USDT", active)
        self.assertNotIn("ETH/USDT:USDT", active)
        self.assertNotIn("SOL/USDT:USDT", active)


class TestCoordinatorSafety(unittest.TestCase):
    def test_observe_mode_blocks_and_does_not_call_execute(self):
        mock_adapter = MagicMock()
        mock_adapter.execute_order = AsyncMock()
        mock_adapter.health = AsyncMock(return_value=MagicMock(healthy=True))
        coordinator = ExecutionCoordinator(adapter=mock_adapter, execution_mode="observe")
        decision = TradingDecision(
            decision_id="p2-obs-1",
            symbol="BTC/USDT:USDT",
            action="enter_long",
            confidence=0.8,
            rationale="test",
            stake_amount=10.0,
        )
        market = MarketState(
            symbol="BTC/USDT:USDT",
            timestamp=datetime.now(timezone.utc),
            ticker=_fresh_ticker(),
            is_fresh=True,
        )
        result = asyncio.run(
            coordinator.process_decision(
                decision,
                portfolio_value=1000.0,
                market_state=market,
                require_market_state=True,
                whitelist=["BTC/USDT:USDT"],
            )
        )
        self.assertFalse(result.success)
        self.assertEqual(result.status, "execution_blocked")
        mock_adapter.execute_order.assert_not_called()
        mock_adapter.health.assert_not_called()

    def test_adapter_unhealthy_blocks_order(self):
        mock_adapter = MagicMock()
        mock_adapter.execute_order = AsyncMock()
        mock_adapter.health = AsyncMock(return_value=MagicMock(healthy=False, error="down"))
        coordinator = ExecutionCoordinator(
            adapter=mock_adapter,
            execution_mode="paper",
        )
        decision = TradingDecision(
            decision_id="p2-health-1",
            symbol="BTC/USDT:USDT",
            action="enter_long",
            confidence=0.8,
            rationale="test",
            stake_amount=10.0,
        )
        market = MarketState(
            symbol="BTC/USDT:USDT",
            timestamp=datetime.now(timezone.utc),
            ticker=_fresh_ticker(),
            is_fresh=True,
        )
        result = asyncio.run(
            coordinator.process_decision(
                decision,
                portfolio_value=1000.0,
                market_state=market,
                require_market_state=True,
                whitelist=["BTC/USDT:USDT"],
            )
        )
        self.assertFalse(result.success)
        self.assertEqual(result.status, "adapter_unavailable")
        mock_adapter.execute_order.assert_not_called()


class TestMarketAPI(unittest.TestCase):
    def setUp(self) -> None:
        TradingRuntimeManager.reset_instance()
        os.environ["NIBLIT_MARKET_DATA_PROVIDER"] = "mock"
        os.environ["NIBLIT_EXECUTION_DB"] = str(Path(tempfile.mkdtemp()) / "api.db")
        self.mgr = TradingRuntimeManager(backend="paper", execution_mode="observe")
        asyncio.run(self.mgr.start())
        from api.trading_api import create_app
        from fastapi.testclient import TestClient

        self.app = create_app(self.mgr)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        asyncio.run(self.mgr.stop())
        TradingRuntimeManager.reset_instance()

    def test_market_health(self):
        resp = self.client.get("/v1/market/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["healthy"])
        self.assertEqual(body["provider"], "mock")

    def test_ticker_candles_state(self):
        t = self.client.get("/v1/market/ticker", params={"symbol": "BTC/USDT:USDT"})
        self.assertEqual(t.status_code, 200)
        self.assertGreater(t.json()["ticker"]["last"], 0)

        c = self.client.get(
            "/v1/market/candles",
            params={"symbol": "BTC/USDT:USDT", "timeframe": "5m", "limit": 10},
        )
        self.assertEqual(c.status_code, 200)
        self.assertEqual(c.json()["count"], 10)

        s = self.client.get("/v1/market/state", params={"symbol": "BTC/USDT:USDT"})
        self.assertEqual(s.status_code, 200)
        self.assertIn("trend", s.json()["state"])

    def test_invalid_symbol(self):
        resp = self.client.get("/v1/market/state", params={"symbol": "BTCUSDT"})
        self.assertEqual(resp.status_code, 400)

    def test_provider_unavailable(self):
        self.mgr._market_data = None
        resp = self.client.get("/v1/market/health")
        self.assertEqual(resp.status_code, 503)

    def test_runtime_status_exposes_market_provider(self):
        resp = self.client.get("/v1/runtime/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["market_provider"], "mock")
        self.assertIn("market_data", body)


class TestShutdownClosesProvider(unittest.TestCase):
    def test_shutdown_closes_market_provider(self):
        TradingRuntimeManager.reset_instance()
        os.environ["NIBLIT_MARKET_DATA_PROVIDER"] = "mock"
        os.environ["NIBLIT_EXECUTION_DB"] = str(Path(tempfile.mkdtemp()) / "shutdown.db")
        mgr = TradingRuntimeManager(backend="paper", execution_mode="observe")
        asyncio.run(mgr.start())
        provider = mgr.market_data_provider
        assert provider is not None
        close = AsyncMock(wraps=provider.close)
        provider.close = close  # type: ignore[method-assign]
        asyncio.run(mgr.stop())
        close.assert_awaited()
        TradingRuntimeManager.reset_instance()


class TestExecutionRepositoryConcurrency(unittest.TestCase):
    def test_concurrent_reads_and_writes_do_not_corrupt(self):
        import concurrent.futures
        from persistence.execution_repository import ExecutionRepository

        db = Path(tempfile.mkdtemp()) / "concurrent.db"
        repo = ExecutionRepository(str(db))

        def worker(i: int) -> bool:
            did = f"conc-{i}"
            repo.record_request(did, {"i": i}, "test")
            repo.record_result(did, {"ok": True}, None, "done")
            return repo.has_decision(did) and repo.get_status(did) == "done"

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(worker, range(80)))
        self.assertTrue(all(results))
        self.assertEqual(sum(1 for i in range(80) if repo.has_decision(f"conc-{i}")), 80)
        repo.close()


@unittest.skipUnless(
    os.getenv("NIBLIT_RUN_MARKET_DATA_INTEGRATION_TESTS") == "1"
    or os.getenv("NIBLIT_RUN_FREQTRADE_INTEGRATION_TESTS") == "1",
    "set NIBLIT_RUN_MARKET_DATA_INTEGRATION_TESTS=1 to run",
)
class TestLiveMarketDataIntegration(unittest.TestCase):
    """Optional live probe: CCXT + optional Freqtrade observe pipeline."""

    def test_ccxt_ticker_candles_market_state_and_observe_block(self):
        provider = CCXTMarketDataProvider(
            exchange=os.getenv("NIBLIT_MARKET_DATA_EXCHANGE", "binance"),
            market_type=os.getenv("NIBLIT_MARKET_DATA_MARKET_TYPE", "swap"),
            max_age_seconds=int(os.getenv("NIBLIT_MARKET_DATA_MAX_AGE_SECONDS", "90")),
        )

        async def _run():
            health = await provider.health()
            self.assertTrue(health.healthy, health.error)
            ticker = await provider.get_ticker("BTC/USDT:USDT")
            self.assertGreater(ticker.last, 0)
            candles = await provider.get_candles("BTC/USDT:USDT", timeframe="5m", limit=200)
            self.assertGreater(len(candles), 50)
            svc = MarketStateService(provider=provider, max_staleness_seconds=90, source="ccxt")
            state = await svc.get_market_state("BTC/USDT:USDT")
            self.assertTrue(state.is_fresh)
            self.assertIn(state.trend, ("bullish", "bearish", "sideways", None))

            mock_adapter = MagicMock()
            mock_adapter.execute_order = AsyncMock()
            mock_adapter.health = AsyncMock(return_value=MagicMock(healthy=True))
            coordinator = ExecutionCoordinator(adapter=mock_adapter, execution_mode="observe")
            decision = TradingDecision(
                decision_id=f"phase2-observe-btc-{int(datetime.now(timezone.utc).timestamp())}",
                symbol="BTC/USDT:USDT",
                action="enter_long",
                confidence=0.75,
                rationale="phase2 observe fixture",
                stake_amount=10.0,
                leverage=1.0,
            )
            result = await coordinator.process_decision(
                decision,
                portfolio_value=10_000.0,
                whitelist=["BTC/USDT:USDT"],
                market_state=state,
                require_market_state=True,
            )
            self.assertEqual(result.status, "execution_blocked")
            mock_adapter.execute_order.assert_not_called()
            await provider.close()

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
