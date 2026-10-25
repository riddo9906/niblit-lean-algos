"""Local Freqtrade strategy service over recorded candles; never places orders."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from datetime import timedelta

import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

STRATEGIES = {
    "EmaTripleCross": "EmaTripleCross", "MacdMomentum": "MacdMomentum",
    "RsiMeanReversion": "RsiMeanReversion", "BollingerSqueeze": "BollingerSqueeze",
    "SupertrendAtr": "SupertrendAtr",
}

class ReplaySession:
    def __init__(self, data_dir: Path, state_file: Path):
        self.root = data_dir.resolve()
        self.state_file = state_file
        self.lock = threading.RLock()
        saved = json.loads(state_file.read_text()) if state_file.exists() else {}
        self.pending=saved.get('pending',False)
        self.journal=saved.get('journal',[])
        self.load(saved.get("dataset", "BTC_USDT_USDT-1h-futures.feather"),
                  saved.get("strategy", "EmaTripleCross"), saved.get("cursor", 199), saved.get("data_sha256"), resume=True)

    def load(self, dataset, strategy, cursor=199, expected_hash=None, resume=False):
        import importlib
        if strategy not in STRATEGIES or Path(dataset).name != dataset:
            raise ValueError("Choose an available dataset and strategy")
        path = (self.root / dataset).resolve(strict=True)
        if not path.is_relative_to(self.root) or path.suffix != '.feather':
            raise ValueError("Dataset must be a recorded feather file inside the configured data directory")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected_hash and expected_hash != digest:
            raise ValueError("Recorded data changed; select a new replay session explicitly")
        frame = pd.read_feather(path)
        required = ['date','open','high','low','close','volume']
        if not all(key in frame for key in required) or len(frame) < 210:
            raise ValueError("Dataset needs OHLCV columns and at least 210 candles")
        frame['date'] = pd.to_datetime(frame['date'], utc=True)
        if not frame.date.is_monotonic_increasing or frame.date.duplicated().any():
            raise ValueError("Recorded candles must have unique increasing timestamps")
        for key in required[1:]:
            if not frame[key].map(lambda value: math.isfinite(float(value)) and (value >= 0 if key == 'volume' else value > 0)).all():
                raise ValueError("Invalid recorded OHLCV value")
        symbol = dataset.split('-')[0].split('_')
        pair = f'{symbol[0]}/{symbol[1]}' + (f':{symbol[2]}' if len(symbol) == 3 else '')
        timeframe = dataset.split('-')[1]
        if timeframe != '1h': raise ValueError('This replay profile requires recorded 1h candles')
        cls = getattr(importlib.import_module(f'freqtrade_strategies.{STRATEGIES[strategy]}'), strategy)
        instance = cls({'stake_currency':'USDT'})
        if instance.timeframe != timeframe: raise ValueError('Strategy timeframe does not match dataset')
        frame = instance.populate_indicators(frame.copy(), {'pair':pair})
        frame = instance.populate_entry_trend(frame, {'pair':pair})
        frame = instance.populate_exit_trend(frame, {'pair':pair})
        with self.lock:
            self.frame, self.dataset, self.strategy, self.symbol = frame, dataset, strategy, pair
            self.data_sha256, self.timeframe = digest, timeframe
            self.cursor = max(199, min(int(cursor), len(frame)-1))
            if not resume: self.pending=False
            self._save()
            return self.snapshot()

    def _save(self):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        data = {key:getattr(self,key) for key in ('dataset','strategy','cursor','data_sha256')}
        data.update(pending=self.pending,journal=self.journal[-2000:])
        name = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.state_file.parent, delete=False) as stream:
                name=stream.name; json.dump(data,stream); stream.flush(); os.fsync(stream.fileno())
            for attempt in range(6):
                try:
                    os.replace(name,self.state_file)
                    break
                except PermissionError:
                    if attempt == 5:
                        raise
                    time.sleep(.01 * (attempt + 1))
        finally:
            if name and os.path.exists(name): os.unlink(name)

    def snapshot(self, limit=200):
        with self.lock:
            row = self.frame.iloc[self.cursor]
            candles=[]
            for _, item in self.frame.iloc[max(0,self.cursor-limit+1):self.cursor+1].iterrows():
                candles.append({'timestamp':item.date.isoformat(), **{key:float(item[key]) for key in ('open','high','low','close','volume')}})
            signal = lambda name: bool(pd.notna(row.get(name)) and row.get(name) == 1)
            return {'schema_version':'niblit.strategy.observation.v1', 'source':'historical_replay',
                'clock_kind':'historical_candle_close', 'as_of':(row.date+timedelta(hours=1)).isoformat(),
                'dataset':self.dataset,'data_sha256':self.data_sha256,'strategy':self.strategy,
                'symbol':self.symbol,'timeframe':self.timeframe,'cursor':self.cursor,'total_candles':len(self.frame),
                'last':float(row.close),'candles':candles,'enter_long':signal('enter_long'),
                'pending':self.pending,'last_outcome':self.journal[-1] if self.journal else None,
                'exit_long':signal('exit_long'),'done':self.cursor>=len(self.frame)-1,
                'observation_id':f'{self.data_sha256[:16]}:{self.strategy}:{self.cursor}',
                'data_start':self.frame.iloc[0].date.isoformat(),'data_end':self.frame.iloc[-1].date.isoformat()}

    def step(self):
        with self.lock:
            if self.pending: return self.snapshot()
            if self.cursor >= len(self.frame)-1: return self.snapshot()
            before=self.cursor
            self.cursor += 1
            self.pending=True
            try: self._save()
            except Exception:
                self.cursor=before; self.pending=False; raise
            return self.snapshot()

    def acknowledge(self, observation_id, outcome):
        with self.lock:
            if observation_id != self.snapshot()['observation_id']: raise ValueError('Replay observation changed')
            if not self.pending: return {'acknowledged':True,'duplicate':True}
            self.journal.append({'observation_id':observation_id,'as_of':self.snapshot()['as_of'],'outcome':outcome})
            self.pending=False
            try: self._save()
            except Exception:
                self.journal.pop(); self.pending=True; raise
            return {'acknowledged':True,'cursor':self.cursor}

class LoadRequest(BaseModel):
    dataset: str = 'BTC_USDT_USDT-1h-futures.feather'
    strategy: str = 'EmaTripleCross'
    cursor: int = 199

class AcknowledgeRequest(BaseModel):
    observation_id: str
    outcome: dict

def create_app(session: ReplaySession):
    app=FastAPI(title='Niblit Freqtrade Offline Strategy Worker')
    @app.get('/health')
    def health():
        import freqtrade
        return {'service':'niblit-freqtrade-strategy','healthy':True,'mode':'historical_replay',
                'freqtrade_version':freqtrade.__version__,'strategy':session.strategy,'cursor':session.cursor}
    @app.get('/v1/strategies')
    def strategies():
        return {'strategies':list(STRATEGIES),'datasets':sorted(p.name for p in session.root.glob('*-1h-futures.feather'))}
    @app.get('/v1/replay/current')
    def current(): return session.snapshot()
    @app.post('/v1/replay/step')
    def step(): return session.step()
    @app.post('/v1/replay/ack')
    def acknowledge(request:AcknowledgeRequest):
        try: return session.acknowledge(request.observation_id,request.outcome)
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc
    @app.get('/v1/replay/history')
    def history(): return {'events':session.journal[-100:]}
    @app.post('/v1/replay/load')
    def load(request:LoadRequest):
        try: return session.load(request.dataset,request.strategy,request.cursor)
        except (ValueError, OSError) as exc: raise HTTPException(400,str(exc)) from exc
    return app

def main():
    import uvicorn
    frozen = bool(getattr(sys,'frozen',False))
    root = Path(sys.executable).parent/'data' if frozen else Path(__file__).resolve().parents[2]/'freqtrade/user_data/data/binance/futures'
    data=Path(os.getenv('NIBLIT_REPLAY_DATA_DIR',str(root)))
    state=Path(os.getenv('NIBLIT_REPLAY_STATE_FILE',str(Path.home()/'.niblit/paper/replay.json')))
    app=create_app(ReplaySession(data,state))
    uvicorn.run(app,host='127.0.0.1',port=int(os.getenv('NIBLIT_STRATEGY_API_PORT','8150')))

if __name__=='__main__': main()
