"""Phase 3 — trading authority, safety gates, lifecycle, reconciliation, idempotency."""
from __future__ import annotations
import asyncio, sqlite3, sys, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock
import httpx
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from domain.execution import ExecutionCommand, ExecutionResult
from domain.trading_decision import TradingDecision, _safe_provenance_tag
from persistence.execution_repository import ExecutionRepository
from risk.engine import RiskEngine
from risk.policy import RiskPolicy

def _decision(decision_id='p3-001', **kw):
    base = dict(decision_id=decision_id, symbol='BTC/USDT:USDT', action='enter_long', confidence=0.75, rationale='phase-3 test', stake_amount=10.0, stake_currency='USDT', order_type='market', leverage=1)
    base.update(kw)
    return TradingDecision(**base)

class TestCanonicalContract(unittest.TestCase):
    def test_schema_version_and_fields(self):
        d = _decision()
        canon = d.to_canonical_dict()
        self.assertEqual(canon['schema_version'], 'niblit.trading.decision.v1')
        for f in ('decision_id','symbol','action','confidence','rationale','strategy','stake_amount','stake_currency','order_type','limit_price','stop_loss','take_profit','leverage','metadata','created_at'):
            self.assertIn(f, canon)
    def test_roundtrip(self):
        d = _decision()
        restored = TradingDecision.from_canonical_dict(d.to_canonical_dict())
        self.assertEqual(restored.decision_id, d.decision_id)
        self.assertEqual(restored.symbol, d.symbol)
        self.assertEqual(restored.action, d.action)
    def test_provenance_tag_deterministic_and_bounded(self):
        d = _decision(decision_id='abc-12345-long-decision-id-exceeding-length')
        tag = d.provider_entry_tag()
        self.assertTrue(tag.startswith('niblit:'))
        self.assertLessEqual(len(tag), 20)
        self.assertEqual(tag, _safe_provenance_tag(d.decision_id))
    def test_short_decision_preserved_in_tag(self):
        self.assertEqual(_safe_provenance_tag('p3-001'), 'niblit:p3-001')
class TestRepositoryLifecycle(unittest.TestCase):
    def _repo(self, tmp):
        return ExecutionRepository(str(Path(tmp)/'exec.db'))
    def test_migration_keeps_existing_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp)/'exec.db'
            conn = sqlite3.connect(str(db))
            conn.execute('''CREATE TABLE executions (decision_id TEXT PRIMARY KEY, command TEXT NOT NULL, backend TEXT NOT NULL, request_timestamp TEXT NOT NULL, response_timestamp TEXT, result TEXT, provider_trade_id TEXT, status TEXT NOT NULL)''')
            conn.execute('INSERT INTO executions VALUES (?,?,?,?,?,?,?,?)', ('legacy-1','{}','freqtrade','2024-01-01T00:00:00+00:00',None,None,'42','submitted'))
            conn.commit(); conn.close()
            repo = self._repo(tmp)
            self.assertTrue(repo.has_decision('legacy-1'))
            rec = repo.get_execution('legacy-1')
            self.assertEqual(rec['provider_trade_id'], '42')
            self.assertIn('schema_version', rec)
            self.assertIn('reconciliation_status', rec)
            self.assertIn('execution_mode', rec)
            repo.close()
    def test_submitting_distinct_from_submitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp)
            repo.record_request('p3-lc-1', {'symbol':'BTC/USDT:USDT','action':'enter_long'}, 'freqtrade', execution_mode='paper')
            repo.mark_submitting('p3-lc-1')
            self.assertEqual(repo.get_status('p3-lc-1'), 'submitting')
            repo.mark_submitted('p3-lc-1', {'status':'submitted'}, '77', provider_tag='niblit:p3-lc-1')
            self.assertEqual(repo.get_status('p3-lc-1'), 'submitted')
            self.assertEqual(repo.get_execution('p3-lc-1')['provider_tag'], 'niblit:p3-lc-1')
            repo.close()
    def test_ambiguous_state_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp)
            repo.record_request('p3-amb-1', {'symbol':'X'}, 'freqtrade', execution_mode='paper')
            repo.mark_ambiguous('p3-amb-1', {'status':'ambiguous'})
            self.assertEqual(repo.get_status('p3-amb-1'), 'ambiguous')
            repo.close()
    def test_query_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(tmp)
            repo.record_request('p3-q-1', {'symbol':'BTC/USDT:USDT','action':'enter_long'}, 'freqtrade', execution_mode='paper')
            repo.mark_submitted('p3-q-1', {'ok':1}, 't-101', provider_tag='niblit:p3-q-1')
            self.assertIsNotNone(repo.get_execution('p3-q-1'))
            self.assertEqual(len(repo.list_executions()), 1)
            self.assertEqual(len(repo.find_by_provider_trade_id('t-101')), 1)
            self.assertEqual(len(repo.list_executions(status='submitted')), 1)
            self.assertEqual(len(repo.list_executions(status='nope')), 0)
            repo.close()
    def test_persistent_idempotency_survives_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp)/'exec.db'
            repo = ExecutionRepository(str(db))
            repo.record_request('p3-persist-1', {'symbol':'BTC/USDT:USDT'}, 'freqtrade', execution_mode='paper')
            repo.mark_ambiguous('p3-persist-1')
            repo.close()
            repo2 = ExecutionRepository(str(db))
            self.assertTrue(repo2.has_decision('p3-persist-1'))
            self.assertEqual(repo2.get_status('p3-persist-1'), 'ambiguous')
            repo2.close()
class TestTypedForceEntryRequest(unittest.TestCase):
    def test_payload_maps_fields(self):
        from adapters.freqtrade.client import build_force_entry_request
        req = build_force_entry_request(pair='BTC/USDT:USDT', side='long', stake_amount=10.0, order_type='limit', limit_price=50000.0, leverage=1, entry_tag='niblit:p3-001')
        payload = req.as_payload()
        self.assertEqual(payload['pair'], 'BTC/USDT:USDT')
        self.assertEqual(payload['side'], 'long')
        self.assertEqual(payload['stakeamount'], 10.0)
        self.assertEqual(payload['ordertype'], 'limit')
        self.assertEqual(payload['price'], 50000.0)
        self.assertEqual(payload['leverage'], 1)
        self.assertEqual(payload['entry_tag'], 'niblit:p3-001')
    def test_unsupported_order_type_omitted(self):
        from adapters.freqtrade.client import build_force_entry_request
        req = build_force_entry_request(pair='P', side='short', order_type='fancy')
        self.assertNotIn('ordertype', req.as_payload())
class _FreqtradeClientHarness:
    def __init__(self, handler):
        from adapters.freqtrade.client import FreqtradeClient
        from adapters.freqtrade.config import FreqtradeAdapterConfig
        config = FreqtradeAdapterConfig(base_url='http://127.0.0.1:8080', api_token='test', max_retries=1, retry_backoff_seconds=0.01, timeout_seconds=2)
        self.client = FreqtradeClient(config=config, transport=httpx.MockTransport(handler))
def _show_config(dry_run=True, force_entry=True):
    body = {'runmode': 'dry_run' if dry_run else 'live', 'dry_run': dry_run, 'force_entry_enable': force_entry, 'state': 'running'}
    return httpx.Response(200, json=body)
class TestRemoteExecutionGates(unittest.TestCase):
    def test_paper_remote_dry_run_true_can_continue(self):
        from adapters.freqtrade.adapter import FreqtradeAdapter
        adapter = FreqtradeAdapter(client=_FreqtradeClientHarness(lambda req: _show_config(dry_run=True, force_entry=True)).client)
        cmd = ExecutionCommand(symbol='BTC/USDT:USDT', action='enter_long', stake_amount=10.0, decision_id='p3-g-1')
        ok, err = asyncio.run(adapter.verify_execution_gate(cmd))
        self.assertTrue(ok); self.assertIsNone(err)
    def test_remote_dry_run_false_blocks(self):
        from adapters.freqtrade.adapter import FreqtradeAdapter
        adapter = FreqtradeAdapter(client=_FreqtradeClientHarness(lambda req: _show_config(dry_run=False, force_entry=True)).client)
        cmd = ExecutionCommand(symbol='BTC/USDT:USDT', action='enter_long', stake_amount=10.0, decision_id='p3-g-2')
        ok, err = asyncio.run(adapter.verify_execution_gate(cmd))
        self.assertFalse(ok); self.assertIn('not_dry_run', err)
    def test_remote_config_unavailable_blocks(self):
        from adapters.freqtrade.adapter import FreqtradeAdapter
        def boom(req):
            raise httpx.ConnectError('boom')
        adapter = FreqtradeAdapter(client=_FreqtradeClientHarness(boom).client)
        cmd = ExecutionCommand(symbol='BTC/USDT:USDT', action='enter_long', stake_amount=10.0, decision_id='p3-g-3')
        ok, err = asyncio.run(adapter.verify_execution_gate(cmd))
        self.assertFalse(ok); self.assertIn('unavailable', err)
    def test_force_entry_disabled_blocks(self):
        from adapters.freqtrade.adapter import FreqtradeAdapter
        adapter = FreqtradeAdapter(client=_FreqtradeClientHarness(lambda req: _show_config(dry_run=True, force_entry=False)).client)
        cmd = ExecutionCommand(symbol='BTC/USDT:USDT', action='enter_long', stake_amount=10.0, decision_id='p3-g-4')
        ok, err = asyncio.run(adapter.verify_execution_gate(cmd))
        self.assertFalse(ok); self.assertIn('force_entry_disabled', err)
    def test_adapter_unhealthy_blocks(self):
        from adapters.freqtrade.adapter import FreqtradeAdapter
        from adapters.freqtrade.client import FreqtradeClient
        from adapters.freqtrade.config import FreqtradeAdapterConfig
        client = FreqtradeClient(config=FreqtradeAdapterConfig(base_url='http://127.0.0.1:8080'))
        client._client = AsyncMock(); client._client.request.side_effect = httpx.ConnectError('down')
        adapter = FreqtradeAdapter(client=client)
        cmd = ExecutionCommand(symbol='BTC/USDT:USDT', action='enter_long', stake_amount=10.0, decision_id='p3-g-5')
        ok, err = asyncio.run(adapter.verify_execution_gate(cmd))
        self.assertFalse(ok)
class TestNoRetryExecution(unittest.TestCase):
    def test_execution_timeout_is_ambiguous(self):
        from adapters.freqtrade.client import FreqtradeClient
        from adapters.freqtrade.config import FreqtradeAdapterConfig
        from adapters.freqtrade.errors import AmbiguousExecutionError
        client = FreqtradeClient(config=FreqtradeAdapterConfig(base_url='http://127.0.0.1:8080'))
        client._client = AsyncMock(); client._client.request.side_effect = httpx.ReadTimeout('read timeout')
        with self.assertRaises(AmbiguousExecutionError):
            asyncio.run(client.forceenter('BTC/USDT:USDT', 'long'))
        self.assertEqual(client._client.request.call_count, 1)
class TestCoordinatorAmbiguous(unittest.TestCase):
    def test_ambiguous_result_no_retry_and_persisted(self):
        from services.execution_coordinator import ExecutionCoordinator
        with tempfile.TemporaryDirectory() as tmp:
            repo = ExecutionRepository(str(Path(tmp)/'exec.db'))
            adapter = MagicMock()
            adapter.execute_order = AsyncMock(return_value=ExecutionResult(success=False, status='ambiguous', error='timeout'))
            coordinator = ExecutionCoordinator(adapter=adapter, repository=repo, execution_mode='paper')
            result = asyncio.run(coordinator.process_decision(_decision('p3-amb-2'), portfolio_value=1000.0))
            self.assertEqual(result.status, 'ambiguous')
            self.assertEqual(adapter.execute_order.call_count, 1)
            self.assertEqual(repo.get_status('p3-amb-2'), 'ambiguous')
            repo.close()
    def test_duplicate_after_execution_no_provider_call(self):
        from services.execution_coordinator import ExecutionCoordinator
        with tempfile.TemporaryDirectory() as tmp:
            repo = ExecutionRepository(str(Path(tmp)/'exec.db'))
            adapter = MagicMock()
            adapter.execute_order = AsyncMock(return_value=ExecutionResult(success=True, status='submitted', provider_trade_id='88'))
            coordinator = ExecutionCoordinator(adapter=adapter, repository=repo, execution_mode='paper')
            d = _decision('p3-dup-1')
            asyncio.run(coordinator.process_decision(d, portfolio_value=1000.0))
            first = adapter.execute_order.call_count
            second = asyncio.run(coordinator.process_decision(d, portfolio_value=1000.0))
            self.assertEqual(second.status, 'duplicate')
            self.assertEqual(adapter.execute_order.call_count, first)
            self.assertTrue(second.raw.get('duplicate'))
            repo.close()
class TestExecutionReconciler(unittest.TestCase):
    def _provider(self, trade, search_result=None):
        p = MagicMock()
        p.get_trade = AsyncMock(return_value=trade)
        p.search_by_tag = AsyncMock(return_value=search_result)
        p.explicit_absence = AsyncMock(return_value=False)
        return p
    def _reconcile(self, record_kwargs, provider):
        from services.execution_reconciler import ExecutionReconciler
        with tempfile.TemporaryDirectory() as tmp:
            repo = ExecutionRepository(str(Path(tmp)/'exec.db'))
            repo.record_request(record_kwargs['decision_id'], {'symbol':'BTC/USDT:USDT','action':'enter_long'}, 'freqtrade', execution_mode='paper', provider_tag=record_kwargs.get('provider_tag'))
            if record_kwargs.get('provider_trade_id'):
                repo.mark_status(record_kwargs['decision_id'], 'submitted', provider_trade_id=record_kwargs['provider_trade_id'])
            reconciler = ExecutionReconciler(repository=repo, provider=lambda: provider)
            result = asyncio.run(reconciler.reconcile(record_kwargs['decision_id']))
            repo.close()
            return result
    def test_provider_trade_open_confirmed_open(self):
        provider = self._provider(trade={'trade_id': 1, 'is_open': True, 'pair': 'BTC/USDT:USDT'})
        result = self._reconcile({'decision_id': 'p3-rec-1', 'provider_trade_id': '1'}, provider)
        self.assertEqual(result['reconciliation_state'], 'confirmed_open')
        provider.get_trade.assert_awaited_once()
    def test_provider_trade_closed_confirmed_closed(self):
        provider = self._provider(trade={'trade_id': 2, 'is_open': False, 'pair': 'BTC/USDT:USDT'})
        result = self._reconcile({'decision_id': 'p3-rec-2', 'provider_trade_id': '2'}, provider)
        self.assertEqual(result['reconciliation_state'], 'confirmed_closed')
    def test_tag_search_confirmed_open(self):
        provider = self._provider(trade=None, search_result={'trade_id': 9, 'is_open': True, 'enter_tag': 'niblit:p3-rec-3'})
        result = self._reconcile({'decision_id': 'p3-rec-3', 'provider_tag': 'niblit:p3-rec-3'}, provider)
        self.assertEqual(result['reconciliation_state'], 'confirmed_open')
    def test_no_evidence_ambiguous(self):
        provider = self._provider(trade=None, search_result=None)
        result = self._reconcile({'decision_id': 'p3-rec-4'}, provider)
        self.assertEqual(result['reconciliation_state'], 'ambiguous')
    def test_not_found_with_reliable_evidence(self):
        provider = self._provider(trade=None, search_result=None)
        provider.explicit_absence = AsyncMock(return_value=True)
        result = self._reconcile({'decision_id': 'p3-rec-5', 'provider_trade_id': '999'}, provider)
        self.assertEqual(result['reconciliation_state'], 'not_found')
class TestMaximumTotalExposure(unittest.TestCase):
    def test_exposure_exceeds_limit_rejected(self):
        from domain import Position
        policy = RiskPolicy(maximum_total_exposure=0.50, maximum_stake_percentage=1.0)
        engine = RiskEngine(policy=policy)
        pos = Position(position_id='1', symbol='ETH/USDT:USDT', side='long', status='open', opened_at=datetime.now(timezone.utc), stake_amount=400.0)
        d = _decision('p3-exp-1', stake_amount=200.0)
        assessment = engine.assess(decision=d, portfolio_value=1000.0, open_positions=[pos])
        self.assertFalse(assessment.approved)
        self.assertIn('maximum_total_exposure_exceeded', assessment.violations)
    def test_exposure_within_limit_approved(self):
        from domain import Position
        policy = RiskPolicy(maximum_total_exposure=0.80, maximum_stake_percentage=1.0)
        engine = RiskEngine(policy=policy)
        pos = Position(position_id='1', symbol='ETH/USDT:USDT', side='long', status='open', opened_at=datetime.now(timezone.utc), stake_amount=100.0)
        d = _decision('p3-exp-2', stake_amount=200.0)
        assessment = engine.assess(decision=d, portfolio_value=1000.0, open_positions=[pos])
        self.assertTrue(assessment.approved)
if __name__ == '__main__':
    unittest.main()
