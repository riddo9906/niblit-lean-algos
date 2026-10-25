"""Historical-clock market data from the real Freqtrade strategy worker."""
import asyncio
from datetime import datetime
import json
import os
import httpx
from domain import Candle, MarketTicker
from .market_data_provider import MarketDataProvider, MarketDataHealth, MarketDataError

class ReplayMarketDataProvider(MarketDataProvider):
    always_refresh = True
    def __init__(self):
        self.url=f"http://127.0.0.1:{int(os.getenv('NIBLIT_STRATEGY_API_PORT','8150'))}"
        self.current=None
        self._client=httpx.Client(base_url=self.url,trust_env=False,timeout=10)
    def _read(self):
        response=self._client.get('/v1/replay/current')
        response.raise_for_status()
        result=response.json()
        if result.get('source')!='historical_replay': raise MarketDataError('Replay worker identity mismatch')
        self.current=result
        return result
    async def close(self):
        await asyncio.to_thread(self._client.close)
    def clock_now(self):
        if self.current is None: raise MarketDataError('Replay clock is unavailable')
        return datetime.fromisoformat(self.current['as_of'])
    async def health(self):
        try:
            await asyncio.to_thread(self._read)
            return MarketDataHealth(healthy=True,source='historical_replay',provider='replay',exchange='recorded_binance',market_type='swap',timeframe=self.current['timeframe'])
        except Exception as exc:
            return MarketDataHealth(healthy=False,source='historical_replay',provider='replay',error=str(exc))
    async def get_ticker(self,symbol):
        current=await asyncio.to_thread(self._read)
        if symbol!=current['symbol']: raise MarketDataError('Replay symbol does not match selected dataset')
        price=current['last']
        return MarketTicker(symbol=symbol,last=price,bid=price,ask=price,timestamp=self.clock_now(),source='historical_replay',age_seconds=0,is_fresh=True)
    async def get_candles(self,symbol,timeframe,limit):
        current=self.current or await asyncio.to_thread(self._read)
        if symbol!=current['symbol'] or timeframe!=current['timeframe']: raise MarketDataError('Replay dataset/timeframe mismatch')
        return [Candle(symbol=symbol,timeframe=timeframe,timestamp=datetime.fromisoformat(item['timestamp']),
                       **{key:item[key] for key in ('open','high','low','close','volume')}) for item in current['candles'][-limit:]]
