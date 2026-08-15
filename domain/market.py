"""Market domain models."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class MarketTicker:
    symbol: str
    last: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    volume: Optional[float] = None
    base_volume: Optional[float] = None
    quote_volume: Optional[float] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: Optional[str] = None
    age_seconds: Optional[float] = None
    is_fresh: Optional[bool] = None


@dataclass
class Candle:
    symbol: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    indicators: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketState:
    symbol: str
    timestamp: datetime
    ticker: MarketTicker
    recent_candles: List[Candle] = field(default_factory=list)
    volatility: Optional[float] = None
    trend: Optional[str] = None
    market_regime: Optional[str] = None
    data_freshness: Optional[float] = None
    age_seconds: Optional[float] = None
    is_fresh: Optional[bool] = None
    source: Optional[str] = None
