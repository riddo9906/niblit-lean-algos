"""Prepare private desktop trading profiles without starting an engine."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import sys


def prepare_profile(destination: Path, mode: str, root: Path):
    if mode not in {'dry-run', 'live'}:
        raise ValueError('Choose dry-run or live')
    if destination.exists():
        raise FileExistsError('Profile already exists; it was not overwritten')
    filename = 'freqtrade_config_dry_run.json' if mode == 'dry-run' else 'freqtrade_config_binance.json'
    profiles = root / 'profiles' if (root / 'profiles').is_dir() else root / 'configs'
    strategies = root / 'strategies' if (root / 'strategies').is_dir() else root / 'freqtrade_strategies'
    config = json.loads((profiles / filename).read_text(encoding='utf-8'))
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = destination.parent / ('dry-run' if mode == 'dry-run' else 'live')
    data.mkdir(exist_ok=True)
    config.pop('protections', None)  # NiblitAiMaster owns StoplossGuard
    config.update(dry_run=mode == 'dry-run', initial_state='stopped',
                  strategy='NiblitAiMaster', strategy_path=str(strategies),
                  user_data_dir=str(data), trading_mode='spot', margin_mode='',
                  db_url='sqlite:///' + (data / 'trades.sqlite').as_posix(),
                  force_entry_enable=False)
    config['exchange'].update(key='', secret='', password='')
    config['api_server'].update(enabled=True, listen_ip_address='127.0.0.1',
        listen_port=18080, username='niblit-trader', password=secrets.token_urlsafe(32),
        jwt_secret_key=secrets.token_hex(32), ws_token=secrets.token_urlsafe(32))
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(config, stream, indent=2)
    destination.chmod(0o600)
    return {'profile': str(destination), 'mode': mode, 'initial_state': 'stopped',
            'credentials_configured': False, 'engine_started': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['dry-run', 'live'], default='dry-run')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    root = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
    report = prepare_profile(args.output.resolve(), args.mode, root)
    print(json.dumps(report, indent=2))
