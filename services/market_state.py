"""Market state service with provider-backed market data."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from domain import Candle, MarketState, MarketTicker

from .market_data_provider import MarketDataError, MarketDataProvider

log = logging.getLogger("MarketStateService")


class MarketStateService:
    """Fetches, calculates, and caches market state for trading decisions.

    Callers use :meth:`get_market_state` — ticker/candles are fetched from the
    configured provider. Manual :meth:`refresh` remains available for tests.
    """

    def __init__(
        self,
        provider: Optional[MarketDataProvider] = None,
        max_staleness_seconds: float = 90.0,
        timeframe: str = "5m",
        candle_limit: int = 200,
        source: Optional[str] = None,
    ):
        self._provider = provider
        self._cache: Dict[str, MarketState] = {}
        self._max_staleness_seconds = max_staleness_seconds
        self._timeframe = timeframe
        self._candle_limit = candle_limit
        self._source = source
        self._last_refresh: Dict[str, float] = {}

    def set_provider(self, provider: MarketDataProvider) -> None:
        self._provider = provider

    def set_data_source(self, source: str) -> None:
        """Register the active market data source label."""
        self._source = source

    async def get_market_state(
        self,
        symbol: str,
        *,
        timeframe: Optional[str] = None,
        force_refresh: bool = False,
    ) -> MarketState:
        """Fetch or return cached market state for a symbol.

        Flow: ticker → candles → freshness → trend/volatility/regime → cache.
        """
        tf = timeframe or self._timeframe
        cache_key = f"{symbol}:{tf}"
        if not force_refresh and self.is_fresh(cache_key):
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        if self._provider is None:
            raise MarketDataError("market_data_provider_unavailable")

        ticker = await self._provider.get_ticker(symbol)
        candles = await self._provider.get_candles(
            symbol, timeframe=tf, limit=self._candle_limit
        )
        state = await self.refresh(symbol, ticker=ticker, candles=candles, timeframe=tf)
        log.info(
            "[Trading] Market state refreshed: %s age=%.1fs trend=%s regime=%s",
            symbol,
            state.age_seconds or -1.0,
            state.trend,
            state.market_regime,
        )
        return state

    async def refresh(
        self,
        symbol: str,
        ticker: MarketTicker,
        candles: Optional[Sequence[Candle]] = None,
        timeframe: Optional[str] = None,
    ) -> MarketState:
        """Update the cached market state for a symbol."""
        now = datetime.now(timezone.utc)
        candle_list = list(candles) if candles else []
        volatility = self._compute_volatility(candle_list)
        trend = self._compute_trend(candle_list)
        age = now.timestamp() - ticker.timestamp.timestamp()
        is_fresh = age <= self._max_staleness_seconds
        tf = timeframe or self._timeframe
        cache_key = f"{symbol}:{tf}"

        # Candle freshness uses close time (open + timeframe). A closed bar is
        # expected to age through the next forming candle; mark stale only when
        # more than one full timeframe + grace has elapsed since close.
        if candle_list:
            tf_seconds = self._timeframe_to_seconds(tf)
            last_close_ts = candle_list[-1].timestamp.timestamp() + tf_seconds
            seconds_since_close = now.timestamp() - last_close_ts
            if seconds_since_close > (tf_seconds + self._max_staleness_seconds):
                is_fresh = False
                age = max(age, seconds_since_close)

        state = MarketState(
            symbol=symbol,
            timestamp=now,
            ticker=ticker,
            recent_candles=candle_list,
            volatility=volatility,
            trend=trend,
            market_regime=self._classify_regime(volatility),
            data_freshness=age,
            age_seconds=age,
            is_fresh=is_fresh,
            source=self._source or (ticker.source if ticker else None),
        )
        self._cache[cache_key] = state
        self._last_refresh[cache_key] = now.timestamp()
        # Also index by bare symbol for callers that omit timeframe.
        self._cache[symbol] = state
        self._last_refresh[symbol] = now.timestamp()
        return state

    def is_fresh(self, symbol: str) -> bool:
        """Return True if cached market data for a symbol is fresh enough."""
        state = self._cache.get(symbol)
        if state is not None and state.is_fresh is not None:
            if not state.is_fresh:
                return False
            if state.age_seconds is not None:
                return state.age_seconds <= self._max_staleness_seconds
        last = self._last_refresh.get(symbol)
        if last is None:
            return False
        return (time.time() - last) <= self._max_staleness_seconds

    def reject_if_stale(self, symbol: str) -> Optional[str]:
        """Return an error code if the cached data is stale, else None."""
        if not self.is_fresh(symbol):
            return "stale_market_data"
        return None

    @staticmethod
    def _timeframe_to_seconds(timeframe: str) -> int:
        unit = timeframe[-1]
        value = int(timeframe[:-1])
        if unit == "m":
            return value * 60
        if unit == "h":
            return value * 3600
        if unit == "d":
            return value * 86400
        if unit == "s":
            return value
        return 300

    @staticmethod
    def _compute_volatility(candles: Optional[List[Candle]]) -> Optional[float]:
        if not candles or len(candles) < 2:
            return None
        closes = [c.close for c in candles]
        returns = [
            (closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(1, len(closes))
            if closes[i - 1] > 0
        ]
        if not returns:
            return None
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        return round(variance ** 0.5, 6)

    @staticmethod
    def _compute_trend(candles: Optional[List[Candle]]) -> Optional[str]:
        """Minimal deterministic trend: bullish / bearish / sideways."""
        if not candles or len(candles) < 2:
            return None
        closes = [c.close for c in candles]
        first = closes[0]
        last = closes[-1]
        if last > first * 1.0005:
            return "bullish"
        if last < first * 0.9995:
            return "bearish"
        return "sideways"

    @staticmethod
    def _classify_regime(volatility: Optional[float]) -> Optional[str]:
        if volatility is None:
            return None
        if volatility < 0.01:
            return "low_volatility"
        if volatility < 0.03:
            return "normal"
        return "high_volatility"
