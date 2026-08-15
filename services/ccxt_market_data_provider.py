"""CCXT-based market data provider for real exchange data."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from domain import Candle, MarketTicker

from .market_data_provider import MarketDataError, MarketDataHealth, MarketDataProvider
from .market_symbol import normalize_market_symbol

log = logging.getLogger("CCXTMarketDataProvider")


def resolve_ccxt_exchange_id(exchange: str, market_type: str) -> str:
    """Map logical exchange + market type onto a CCXT exchange id.

    Binance USDT-M futures must use ``binanceusdm``. Using plain ``binance``
    with defaultType=swap still loads spot ``exchangeInfo`` first in many
    CCXT versions and is less reliable for swap-only observe mode.
    """
    name = (exchange or "binance").strip().lower()
    mtype = (market_type or "swap").strip().lower()
    if name == "binance" and mtype in ("swap", "future", "futures", "linear"):
        return "binanceusdm"
    if name == "binance" and mtype in ("delivery", "inverse"):
        return "binancecoinm"
    return name


class CCXTMarketDataProvider(MarketDataProvider):
    """Real market data provider backed by CCXT.

    Network calls are isolated with ``asyncio.to_thread`` so Niblit's event
    loop is never blocked. ``ccxt.async_support`` is intentionally avoided as
    the primary path because Binance market loading is more reliable via the
    synchronous client in this runtime.
    """

    def __init__(
        self,
        exchange: str = "binance",
        market_type: str = "swap",
        timeout_seconds: int = 20,
        max_age_seconds: int = 90,
        timeframe: str = "5m",
    ):
        self._exchange_name = exchange
        self._market_type = market_type
        self._ccxt_id = resolve_ccxt_exchange_id(exchange, market_type)
        self._timeout_seconds = timeout_seconds
        self._max_age_seconds = max_age_seconds
        self._timeframe = timeframe
        self._exchange: Optional[Any] = None
        self._markets_loaded = False
        self._closed = False

    @property
    def exchange_name(self) -> str:
        return self._exchange_name

    @property
    def market_type(self) -> str:
        return self._market_type

    @property
    def timeframe(self) -> str:
        return self._timeframe

    @property
    def max_age_seconds(self) -> int:
        return self._max_age_seconds

    def _create_exchange(self) -> Any:
        try:
            import ccxt
        except ImportError as exc:
            raise MarketDataError(
                "ccxt is not installed. Run: pip install 'ccxt>=4.0,<5'"
            ) from exc

        exch_class = getattr(ccxt, self._ccxt_id, None)
        if exch_class is None:
            raise MarketDataError(f"Unknown CCXT exchange: {self._ccxt_id}")

        options: Dict[str, Any] = {"defaultType": self._market_type}
        return exch_class(
            {
                "enableRateLimit": True,
                "timeout": self._timeout_seconds * 1000,
                "options": options,
            }
        )

    def _get_exchange_sync(self) -> Any:
        if self._closed:
            raise MarketDataError("market_data_provider_closed")
        if self._exchange is None:
            self._exchange = self._create_exchange()
        return self._exchange

    def _load_markets_sync(self) -> None:
        exch = self._get_exchange_sync()
        if self._markets_loaded:
            return
        exch.load_markets()
        self._markets_loaded = True

    async def health(self) -> MarketDataHealth:
        """Check provider connectivity with a real lightweight market call."""
        source = f"ccxt:{self._exchange_name}:{self._market_type}"
        start = time.perf_counter()

        def _probe() -> Dict[str, Any]:
            exch = self._get_exchange_sync()
            self._load_markets_sync()
            markets = getattr(exch, "markets", {}) or {}
            if markets and self._market_type in ("swap", "future", "futures"):
                has_swap = any(
                    bool(m.get("swap") or m.get("future") or m.get("type") in ("swap", "future"))
                    for m in markets.values()
                    if isinstance(m, dict)
                )
                if not has_swap:
                    raise MarketDataError("configured market type not supported")
            probe_symbol = normalize_market_symbol(
                "BTC/USDT:USDT",
                provider="ccxt",
                market_type=self._market_type,
            )
            ticker = exch.fetch_ticker(probe_symbol)
            if not ticker or ticker.get("last") is None:
                raise MarketDataError("No ticker data returned")
            return ticker

        try:
            await asyncio.to_thread(_probe)
            latency_ms = (time.perf_counter() - start) * 1000.0
            return MarketDataHealth(
                healthy=True,
                source=source,
                provider="ccxt",
                exchange=self._exchange_name,
                market_type=self._market_type,
                latency_ms=latency_ms,
                last_update=time.time(),
                timeframe=self._timeframe,
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000.0
            return MarketDataHealth(
                healthy=False,
                source=source,
                provider="ccxt",
                exchange=self._exchange_name,
                market_type=self._market_type,
                latency_ms=latency_ms,
                error=str(exc),
                last_update=time.time(),
                timeframe=self._timeframe,
            )

    async def get_ticker(self, symbol: str) -> MarketTicker:
        normalized = normalize_market_symbol(
            symbol, provider="ccxt", market_type=self._market_type
        )

        def _fetch() -> Dict[str, Any]:
            exch = self._get_exchange_sync()
            self._load_markets_sync()
            raw = exch.fetch_ticker(normalized)
            if raw is None:
                raise MarketDataError(f"Empty ticker for {symbol}")
            return raw

        try:
            raw = await asyncio.to_thread(_fetch)
        except MarketDataError:
            raise
        except Exception as exc:
            raise MarketDataError(f"CCXT ticker fetch failed for {symbol}: {exc}") from exc
        return self._map_ticker(raw=raw, symbol=symbol)

    def _map_ticker(self, raw: Dict[str, Any], symbol: str) -> MarketTicker:
        last = raw.get("last")
        if last is None:
            raise MarketDataError(f"No 'last' price in ticker for {symbol}")
        last_f = float(last)
        if last_f <= 0:
            raise MarketDataError(f"Non-positive last price for {symbol}: {last_f}")

        bid = raw.get("bid")
        ask = raw.get("ask")
        bid_f: Optional[float] = None
        ask_f: Optional[float] = None
        if bid is not None:
            bid_f = float(bid)
            if bid_f <= 0:
                raise MarketDataError(f"Non-positive bid for {symbol}: {bid_f}")
        if ask is not None:
            ask_f = float(ask)
            if ask_f <= 0:
                raise MarketDataError(f"Non-positive ask for {symbol}: {ask_f}")
        if bid_f is not None and ask_f is not None and bid_f > ask_f:
            raise MarketDataError(f"Bid {bid_f} exceeds ask {ask_f} for {symbol}")

        open_p = raw.get("open")
        high = raw.get("high")
        low = raw.get("low")
        open_f = float(open_p) if open_p is not None else None
        high_f = float(high) if high is not None else None
        low_f = float(low) if low is not None else None

        base_volume = raw.get("baseVolume")
        quote_volume = raw.get("quoteVolume")
        base_f = float(base_volume) if base_volume is not None else None
        quote_f = float(quote_volume) if quote_volume is not None else None

        ts = raw.get("timestamp")
        if ts is None:
            raise MarketDataError(f"Missing ticker timestamp for {symbol}")
        timestamp = datetime.fromtimestamp(float(ts) / 1000.0, tz=timezone.utc)
        age = (datetime.now(timezone.utc) - timestamp).total_seconds()
        if age > self._max_age_seconds:
            raise MarketDataError(
                f"stale_market_data: ticker age {age:.1f}s exceeds {self._max_age_seconds}s"
            )

        return MarketTicker(
            symbol=symbol,
            last=last_f,
            bid=bid_f,
            ask=ask_f,
            open=open_f,
            high=high_f,
            low=low_f,
            volume=base_f,
            base_volume=base_f,
            quote_volume=quote_f,
            timestamp=timestamp,
            source=f"ccxt:{self._exchange_name}",
            age_seconds=age,
            is_fresh=True,
        )

    async def get_candles(
        self,
        symbol: str,
        timeframe: str = "5m",
        limit: int = 200,
    ) -> List[Candle]:
        """Fetch OHLCV candles. Drops the unfinished active candle when present."""
        normalized = normalize_market_symbol(
            symbol, provider="ccxt", market_type=self._market_type
        )

        def _fetch() -> List[List[float]]:
            exch = self._get_exchange_sync()
            self._load_markets_sync()
            ohlcv = exch.fetch_ohlcv(normalized, timeframe=timeframe, limit=limit)
            if not ohlcv:
                raise MarketDataError(f"No candles returned for {symbol}")
            return ohlcv

        try:
            ohlcv = await asyncio.to_thread(_fetch)
        except MarketDataError:
            raise
        except Exception as exc:
            raise MarketDataError(f"CCXT candle fetch failed for {symbol}: {exc}") from exc
        candles = self._map_candles(ohlcv=ohlcv, symbol=symbol, timeframe=timeframe)
        return self._drop_active_candle(candles, timeframe)

    def _map_candles(
        self,
        ohlcv: List[List[float]],
        symbol: str,
        timeframe: str,
    ) -> List[Candle]:
        candles: List[Candle] = []
        last_ts: Optional[int] = None
        seen: set[int] = set()

        # Sort oldest → newest by timestamp first.
        rows = sorted(ohlcv, key=lambda r: int(r[0]))

        for row in rows:
            if len(row) < 6:
                raise MarketDataError(f"Malformed OHLCV row: {row}")
            ts = int(row[0])
            open_p = float(row[1])
            high = float(row[2])
            low = float(row[3])
            close = float(row[4])
            volume = float(row[5])

            if ts in seen:
                raise MarketDataError(f"Duplicate candle timestamp: {ts}")
            seen.add(ts)
            if last_ts is not None and ts < last_ts:
                raise MarketDataError(f"Candles not chronologically ordered at {ts}")
            last_ts = ts

            if open_p <= 0 or high <= 0 or low <= 0 or close <= 0:
                raise MarketDataError(f"Non-positive OHLC value in row: {row}")
            if volume < 0:
                raise MarketDataError(f"Negative volume in row: {row}")
            if high < open_p or high < close or high < low:
                raise MarketDataError(f"Invalid high in row: {row}")
            if low > open_p or low > close:
                raise MarketDataError(f"Invalid low in row: {row}")

            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    timestamp=datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc),
                    open=open_p,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                )
            )

        if not candles:
            raise MarketDataError(f"No valid candles for {symbol}")
        return candles

    def _drop_active_candle(self, candles: List[Candle], timeframe: str) -> List[Candle]:
        """Drop the incomplete active candle for safer decision inputs.

        Preferred Phase 2 behavior: if the newest candle opened within the
        current timeframe window, treat it as unfinished and exclude it.
        """
        if len(candles) <= 1:
            return candles
        timeframe_seconds = self._timeframe_to_seconds(timeframe)
        now = time.time()
        last_ts = candles[-1].timestamp.timestamp()
        if now - last_ts < timeframe_seconds:
            return candles[:-1]
        return candles

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

    async def close(self) -> None:
        self._closed = True
        exch = self._exchange
        self._exchange = None
        self._markets_loaded = False
        if exch is None:
            return

        def _close() -> None:
            close_fn = getattr(exch, "close", None)
            if close_fn is None:
                return
            close_fn()

        try:
            await asyncio.to_thread(_close)
        except Exception as exc:
            log.debug("[Trading] CCXT exchange close error: %s", exc)
