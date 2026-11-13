import asyncio, concurrent.futures, math, sys, threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from domain import TradingDecision, ExecutionResult
from persistence.execution_repository import ExecutionRepository
from services.execution_coordinator import ExecutionCoordinator
from risk.engine import RiskEngine

def decision(**kw):
    return TradingDecision(**(dict(decision_id='r3-one',symbol='BTC/USDT:USDT',action='enter_long',confidence=.75,rationale='fixture',stake_amount=10.,leverage=1.)|kw))

@pytest.mark.parametrize('field',['portfolio_value','realized_pnl_today','stake_amount','leverage','stop_loss','take_profit','limit_price'])
@pytest.mark.parametrize('value',[float('nan'),float('inf'),float('-inf')])
def test_invalid_risk_values(field,value):
    params=dict(portfolio_value=1000.,realized_pnl_today=0.,open_positions=[])
    kw={}
    if field in params:params[field]=value
    else:kw[field]=value
    assert not RiskEngine().assess(decision(**kw),**params).approved

def test_atomic_claim_between_connections_preserves_audit(tmp_path):
    path=str(tmp_path/'ledger.db')
    first,second=ExecutionRepository(path),ExecutionRepository(path)
    barrier=threading.Barrier(2)
    def claim(repo):
        barrier.wait();return repo.record_request('same',{'symbol':'BTC','action':'enter_long'},'fixture')
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        claims=list(pool.map(claim,[first,second]))
    assert sorted(claims)==[False,True]
    first.mark_ambiguous('same',{'evidence':'retained'})
    assert second.record_request('same',{'symbol':'OTHER'},'other') is False
    assert second.get_execution('same')['status']=='ambiguous'
    assert second.get_execution('same')['symbol']=='BTC'
    first.close();second.close()

def adapter():
    return SimpleNamespace(health=AsyncMock(return_value=SimpleNamespace(healthy=True,backend='paper')),execute_order=AsyncMock(return_value=ExecutionResult(success=True,status='filled')))

def test_real_money_blocked_even_with_legacy_acknowledgement(tmp_path):
    a=adapter();r=ExecutionRepository(str(tmp_path/'ledger.db'))
    c=ExecutionCoordinator(a,repository=r,execution_mode='live',live_acknowledgement='I_UNDERSTAND_LIVE_TRADING_RISK')
    result=asyncio.run(c.process_decision(decision(),portfolio_value=1000))
    assert result.status=='execution_blocked';a.execute_order.assert_not_awaited();r.close()

def test_uncertain_adapter_exception_is_ambiguous_and_not_retried(tmp_path):
    a=adapter();a.execute_order.side_effect=TimeoutError('receipt uncertain')
    r=ExecutionRepository(str(tmp_path/'ledger.db'));c=ExecutionCoordinator(a,repository=r,execution_mode='paper')
    result=asyncio.run(c.process_decision(decision(),portfolio_value=1000))
    assert result.status=='ambiguous'
    assert r.get_execution('r3-one')['status']=='ambiguous'
    assert asyncio.run(c.process_decision(decision(),portfolio_value=1000)).status=='duplicate'
    a.execute_order.assert_awaited_once();r.close()

def test_concurrent_coordinators_submit_once(tmp_path):
    path=str(tmp_path/'ledger.db');r1,r2=ExecutionRepository(path),ExecutionRepository(path)
    a=adapter()
    c1,c2=[ExecutionCoordinator(a,repository=r,execution_mode='paper') for r in (r1,r2)]
    async def run():return await asyncio.gather(c1.process_decision(decision(),portfolio_value=1000),c2.process_decision(decision(),portfolio_value=1000))
    results=asyncio.run(run())
    assert sum(r.status=='duplicate' for r in results)==1
    a.execute_order.assert_awaited_once();r1.close();r2.close()
