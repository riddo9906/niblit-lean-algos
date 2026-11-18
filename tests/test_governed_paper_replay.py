"""Real offline integration: no mocked strategy votes, fills or risk decisions."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest

LEAN = Path(__file__).resolve().parents[1]
CORE = Path(os.getenv('NIBLIT_CORE_SOURCE', str(LEAN.parent / 'Niblit')))
WORKER = LEAN / 'scripts/paper_acceptance.py'
DATA = LEAN / 'tests/fixtures/governed_paper_v1.json'


def launch(tmp_path,lease=False):
    run = tmp_path / 'run'
    run.mkdir()
    config = dict(run_root=str(run),run_id='paper-source-test1',core_root=str(CORE),
        dataset=str(DATA),dataset_sha256=hashlib.sha256(DATA.read_bytes()).hexdigest(),lease_required=lease)
    if lease:(run/'heartbeat').write_text('test owned controller')
    request = run / 'request.json'
    request.write_text(json.dumps(config))
    log = (run / 'worker.log').open('w')
    process = subprocess.Popen([sys.executable,'-I','-B',str(WORKER),str(request)],stdout=log,stderr=log)
    return run, process, log


def test_real_rules_risk_fills_exits_and_accounting(tmp_path):
    run, process, log = launch(tmp_path)
    try:
        assert process.wait(timeout=30) == 0, (run/'worker.log').read_text()
    finally:
        if process.poll() is None:process.kill();process.wait()
        log.close()
    receipt=json.loads((run/'receipt.json').read_text())
    assert receipt['status']=='completed'
    scenarios={e['name']:e for e in receipt['scenarios']}
    for name in ('long_favorable','long_adverse','short_favorable','short_adverse'):
        e=scenarios[name]
        assert e['status']=='closed' and e['account_reload_equal']
        assert len(e['orders'])==2 and all(o['result']['status']=='filled' for o in e['orders'])
        assert e['orders'][0]['duplicate_probe']['status']=='duplicate'
        assert len(e['timeline'])>=3
        assert e['orders'][-1]['execution_record']['status']=='confirmed_closed'
        assert e['actuals'][0]['verified']
        assert (e['actuals'][0]['net']>0)==('favorable' in name)
        assert e['account']['realized_pnl']==pytest.approx(e['actuals'][0]['net'])
    assert scenarios['disagreement']['status']=='abstained'
    votes={c['side'] for c in scenarios['disagreement']['timeline'][0]['signals']}
    assert {'buy','sell'}<=votes
    assert scenarios['insufficient']['orders']==[]
    assert scenarios['risk_blocked']['orders'][0]['result']['status']=='risk_rejected'
    assert scenarios['risk_blocked']['account']['executions']==[]
    assert receipt['broker_calls']==0


def test_real_inflight_cancellation_stops_subsequent_fills(tmp_path):
    run,process,log=launch(tmp_path)
    try:
        deadline=time.monotonic()+15
        account=run/'long_favorable-account.json'
        while time.monotonic()<deadline:
            if account.exists() and json.loads(account.read_text()).get('executions'):break
            time.sleep(.02)
        assert account.exists()
        (run/'cancel').write_text('Authorized test cancellation')
        assert process.wait(timeout=10)==0
        receipt=json.loads((run/'receipt.json').read_text())
        assert receipt['status']=='cancelled'
        assert len(receipt['scenarios'])==1
        assert len(receipt['scenarios'][0]['account']['executions'])==1
        assert receipt['scenarios'][0]['status']=='cancelled'
    finally:
        if process.poll() is None:process.kill();process.wait()
        log.close()


def test_offline_guard_denies_network_and_outside_writes(tmp_path):
    script=f'''import importlib.util,pathlib,socket
spec=importlib.util.spec_from_file_location('driver',{str(WORKER)!r})
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
m.guard(pathlib.Path({str(tmp_path)!r}))
try:socket.create_connection(('127.0.0.1',8100),timeout=.1)
except PermissionError:pass
else:raise AssertionError('Network allowed')
try:pathlib.Path({str(tmp_path.parent/'forbidden.txt')!r}).write_text('bad')
except PermissionError:pass
else:raise AssertionError('Outside write allowed')
'''
    result=subprocess.run([sys.executable,'-I','-B','-c',script],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr


def test_lost_controller_lease_stops_without_automatic_reentry(tmp_path):
    run,process,log=launch(tmp_path,lease=True)
    try:
        assert process.wait(timeout=15)==0
        receipt=json.loads((run/'receipt.json').read_text())
        assert receipt['status']=='interrupted' and receipt['cancel_reason']=='controller_lease_lost'
        ack=receipt['cancellation_acknowledged_at']
        calls=[e for s in receipt['scenarios'] for e in s['timeline'] if e.get('execution_started_at')]
        assert calls and all(e['execution_started_at']<ack for e in calls)
        counts={p.name:len(json.loads(p.read_text())['executions']) for p in run.glob('*-account.json')}
        time.sleep(.2)
        assert counts=={p.name:len(json.loads(p.read_text())['executions']) for p in run.glob('*-account.json')}
    finally:
        if process.poll() is None:process.kill();process.wait()
        log.close()
