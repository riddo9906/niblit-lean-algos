"""Windows Freqtrade dry-run launcher with a separate Niblit adapter service.

No exchange credentials, live-mode switch, or historical replay fallback.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.request import Request, ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'build' / 'windows-freqtrade-dry-run'
API_PORT = 18080
RUNTIME_PORT = 8100


def child_environment(config, output):
    # Freqtrade environment overrides take precedence over the JSON file.
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(('FREQTRADE__', 'NIBLIT_', 'BINANCE_'))}
    env.update({
        'PYTHON_DOTENV_DISABLED': '1',
        'NIBLIT_TRADING_BACKEND': 'freqtrade',
        'NIBLIT_TRADING_MODE': 'observe',
        'NIBLIT_TRADING_ALLOW_EXECUTION': 'false',
        'NIBLIT_FREQTRADE_ALLOW_EXECUTION': 'false',
        'NIBLIT_FREQTRADE_DRY_RUN': 'true',
        'NIBLIT_FREQTRADE_BASE_URL': f'http://127.0.0.1:{API_PORT}',
        'NIBLIT_FREQTRADE_USERNAME': config['api_server']['username'],
        'NIBLIT_FREQTRADE_PASSWORD': config['api_server']['password'],
        'NIBLIT_TRADING_API_PORT': str(RUNTIME_PORT),
        'NIBLIT_EXECUTION_DB': str(output / 'niblit-executions.sqlite'),
        'NIBLIT_MARKET_DATA_PROVIDER': 'ccxt',
        'NIBLIT_MARKET_DATA_EXCHANGE': 'binance',
        'NIBLIT_MARKET_DATA_MARKET_TYPE': 'spot',
        'NIBLIT_MARKET_DATA_TIMEFRAME': '1h',
        'NIBLIT_SIGNAL_FILE': str(output / 'niblit-signal.json'),
        'NIBLIT_TRACE_FILE': str(output / 'execution-trace.jsonl'),
        'NIBLIT_RESULTS_FILE': str(output / 'strategy-results.json'),
        'NIBLIT_REFLECTION_FILE': str(output / 'reflections.jsonl'),
        'NIBLIT_EPISODES_FILE': str(output / 'episodes.jsonl'),
    })
    return env


def prepare(output=OUTPUT):
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    userdir = output / 'user_data'
    userdir.mkdir(exist_ok=True)
    config_path = output / 'config.json'
    previous = json.loads(config_path.read_text()) if config_path.exists() else {}
    config = json.loads((ROOT / 'configs/freqtrade_config_dry_run.json').read_text())
    config.pop('protections', None)  # retained in NiblitAiMaster.protections
    config.update(dry_run=True, trading_mode='spot', margin_mode='',
                  strategy='NiblitAiMaster', initial_state='running',
                  force_entry_enable=False, bot_name='niblit-live-data-dry-run',
                  db_url='sqlite:///' + (output / 'trades.dryrun.sqlite').as_posix())
    config['exchange'].update(key='', secret='', password='')
    api = config['api_server']
    old_api = previous.get('api_server', {})
    api.update(listen_ip_address='127.0.0.1', listen_port=API_PORT,
               username='niblit-dry-run', password=old_api.get('password') or secrets.token_urlsafe(32),
               jwt_secret_key=old_api.get('jwt_secret_key') or secrets.token_hex(32),
               ws_token=old_api.get('ws_token') or secrets.token_urlsafe(32))
    config_path.write_text(json.dumps(config, indent=2), encoding='utf-8')
    # POSIX permission enforcement where available; Windows inherits workspace ACLs.
    config_path.chmod(0o600)
    return config, config_path, userdir


def command(python, config_path, userdir):
    return [str(python), '-m', 'freqtrade', 'trade', '--dry-run',
            '--config', str(config_path), '--userdir', str(userdir),
            '--strategy', 'NiblitAiMaster', '--strategy-path', str(ROOT / 'freqtrade_strategies')]


def request_json(url, config=None):
    headers = {}
    if config:
        api = config['api_server']
        token = base64.b64encode(f"{api['username']}:{api['password']}".encode()).decode()
        headers['Authorization'] = 'Basic ' + token
    # Loopback must not use a system HTTP proxy.
    with build_opener(ProxyHandler({})).open(Request(url, headers=headers), timeout=8) as response:
        return json.load(response)


def status(config):
    base = f'http://127.0.0.1:{API_PORT}/api/v1'
    remote = request_json(base + '/show_config', config)
    if remote.get('dry_run') is not True:
        raise RuntimeError('The connected Freqtrade instance is not verified dry-run')
    runtime = request_json(f'http://127.0.0.1:{RUNTIME_PORT}/health')
    if runtime.get('backend') != 'freqtrade' or runtime.get('mode') != 'observe':
        raise RuntimeError('Unexpected Niblit runtime identity or mode')
    candles = request_json(base + '/pair_candles?pair=BTC%2FUSDT&timeframe=1h&limit=2', config)
    return {'dry_run': True, 'strategy': remote.get('strategy'),
            'freqtrade_state': remote.get('state'), 'runtime': runtime,
            'market_sample': {key: candles.get(key) for key in
                              ('pair', 'timeframe', 'length', 'last_analyzed', 'data_stop')},
            'open_trades': request_json(base + '/status', config),
            'balance': request_json(base + '/balance', config)}


def preflight(python, config_path, userdir, env):
    probe = '''import sys
from freqtrade.configuration import Configuration
from freqtrade.resolvers import StrategyResolver
from freqtrade.enums import RunMode
from freqtrade.configuration.config_validation import validate_config_consistency
config = Configuration({'config': [sys.argv[1]], 'user_data_dir': sys.argv[2],
    'strategy': 'NiblitAiMaster', 'strategy_path': sys.argv[3]}, RunMode.DRY_RUN).get_config()
assert config['dry_run'] is True
strategy = StrategyResolver.load_strategy(config)
validate_config_consistency(config)
assert type(strategy).__name__ == 'NiblitAiMaster'
assert strategy.protections[0]['method'] == 'StoplossGuard'
import manager
print('PASS: real Freqtrade strategy and Niblit runtime imports; dry-run config validated')
'''
    subprocess.run([str(python), '-c', probe, str(config_path), str(userdir),
                    str(ROOT / 'freqtrade_strategies')], env=env, cwd=ROOT, check=True)


def run(python, config, config_path, userdir, output, verify_only=False):
    env = child_environment(config, output)
    preflight(python, config_path, userdir, env)
    for port in (API_PORT, RUNTIME_PORT):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))  # never replace an existing service
    processes, logs = [], []
    report = {'ready': False, 'dry_run': True, 'market_data': 'live_exchange'}
    try:
        for name, argv in (
            ('freqtrade', command(python, config_path, userdir)),
            ('niblit-adapter', [str(python), str(ROOT / 'main.py')]),
        ):
            stream = (output / f'{name}.log').open('w', encoding='utf-8')
            logs.append(stream)
            processes.append(subprocess.Popen(argv, cwd=output, env=env,
                             stdout=stream, stderr=subprocess.STDOUT,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)))
        print('Starting live-data dry-run. Logs:', output, flush=True)
        deadline = time.monotonic() + 180
        last_error = 'waiting for startup'
        while time.monotonic() < deadline:
            if any(p.poll() is not None for p in processes):
                raise RuntimeError('A service exited. Review freqtrade.log and niblit-adapter.log')
            try:
                snap = status(config)
                if (snap['runtime'].get('healthy') is True
                        and snap['runtime'].get('state') == 'ready'
                        and snap['freqtrade_state'] == 'running'
                        and (snap['market_sample'].get('length') or 0) > 0
                        and snap['runtime'].get('market_data', {}).get('healthy') is True):
                    report.update(ready=True, snapshot=snap)
                    break
                last_error = 'Services responding but not yet healthy/running with live market data'
            except (OSError, ValueError, RuntimeError) as exc:
                last_error = str(exc)
            time.sleep(2)
        if not report['ready']:
            raise RuntimeError('Readiness timed out: ' + last_error)
        print('READY: Freqtrade simulated trading + live market data + Niblit adapter.', flush=True)
        print('API: http://127.0.0.1:18080 | Niblit trading: http://127.0.0.1:8100', flush=True)
        if not verify_only:
            print('Keep this window open. Ctrl+C stops only these two child services.', flush=True)
            while all(p.poll() is None for p in processes):
                time.sleep(2)
            raise RuntimeError('A trading service exited; see logs')
    except KeyboardInterrupt:
        print('Stopping the dry-run services.')
    except Exception as exc:
        report['ready'] = False
        report['error'] = str(exc)
        raise
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        for stream in logs:
            stream.close()
        (output / 'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'check', 'start', 'verify', 'status'], nargs='?', default='start')
    parser.add_argument('--python', type=Path)
    args = parser.parse_args()
    python = args.python or ROOT.parent / 'freqtrade/.venv/Scripts/python.exe'
    if not python.is_file():
        parser.error('Freqtrade Python not found. Supply --python pointing to its Windows environment.')
    config, path, userdir = prepare()
    if args.action == 'prepare':
        print('Prepared dry-run files:', OUTPUT)
    elif args.action == 'check':
        preflight(python, path, userdir, child_environment(config, OUTPUT))
    elif args.action == 'status':
        print(json.dumps(status(config), indent=2))
    else:
        run(python, config, path, userdir, OUTPUT, verify_only=args.action == 'verify')


if __name__ == '__main__':
    main()
