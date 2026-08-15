"""Market data provider interface and implementations."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from domain import Candle, MarketTicker


@dataclass
class MarketDataHealth:
    healthy: bool
    source: str = ""
    provider: Optional[str] = None
    exchange: Optional[str] = None
    market_type: Optional[str] = None
    latency_ms: Optional[float] = None
    error: Optional[str] = None
    last_update: Optional[float] = None
    timeframe: Optional[str] = None


class MarketDataError(ValueError):
    """Typed market-data validation / connectivity error."""


class MarketDataProvider(ABC):
    """Abstract market data provider."""

    @abstractmethod
    async def health(self) -> MarketDataHealth:
        ...

    @abstractmethod
    async def get_ticker(self, symbol: str) -> MarketTicker:
        ...

    @abstractmethod
    async def get_candles(self, symbol: str, timeframe: str, limit: int) -> List[Candle]:
        ...

    async def close(self) -> None:
        """Release provider resources. Default is a no-op."""
        return None


class MockMarketDataProvider(MarketDataProvider):
    """Deterministic mock provider for testing."""

    def __init__(self):
        self._prices: Dict[str, float] = {
            "BTC/USDT": 50000.0,
            "ETH/USDT": 3000.0,
            "BTC/USDT:USDT": 50000.0,
            "ETH/USDT:USDT": 3000.0,
        }
        self._closed = False

    async def health(self) -> MarketDataHealth:
        return MarketDataHealth(
            healthy=not self._closed,
            source="mock",
            provider="mock",
            exchange="mock",
            market_type="spot",
            latency_ms=0.0,
            last_update=time.time(),
            timeframe="5m",
        )

    async def get_ticker(self, symbol: str) -> MarketTicker:
        price = self._prices.get(symbol, 100.0)
        now = datetime.now(timezone.utc)
        return MarketTicker(
            symbol=symbol,
            bid=price * 0.999,
            ask=price * 1.001,
            last=price,
            open=price,
            high=price * 1.01,
            low=price * 0.99,
            volume=1000.0,
            base_volume=1000.0,
            quote_volume=price * 1000.0,
            timestamp=now,
            source="mock",
            age_seconds=0.0,
            is_fresh=True,
        )

    async def get_candles(self, symbol: str, timeframe: str, limit: int) -> List[Candle]:
        price = self._prices.get(symbol, 100.0)
        candles = []
        now = datetime.now(timezone.utc)
        for i in range(limit):
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=now,
                    open=price,
                    high=price * 1.01,
                    low=price * 0.99,
                    close=price,
                    volume=100.0,
                )
            )
        return candles

    def set_price(self, symbol: str, price: float) -> None:
        self._prices[symbol] = price

    async def close(self) -> None:
        self._closed = True
