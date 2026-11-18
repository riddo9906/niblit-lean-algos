"""Offline replay driver over the EXISTING PaperAdapter and execution authority.

No service bootstrap, broker factory, network access or model generation. This
driver is an integration client, not an alternate trading engine. Inputs and
source files are pinned by the Core deployment manifest before invocation.
"""
from __future__ import annotations
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time


def guard(root):
    def audit(event, args):
        if event in {'socket.connect', 'socket.connect_ex', 'socket.bind', 'subprocess.Popen', 'os.system'}:
            raise PermissionError('Offline paper worker cannot access network or launch processes')
        targets = []
        if event == 'open' and not isinstance(args[0], int):
            mode, flags = args[1:3]
            if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)):
                targets = [args[0]]
        elif event in {'os.mkdir', 'os.remove', 'os.unlink', 'os.rmdir'}:
            targets = [args[0]]
        elif event in {'os.rename', 'os.replace'}:
            targets = args[:2]
        if any(not Path(p).resolve().is_relative_to(root) for p in targets):
            raise PermissionError('Paper write outside isolated run denied')
    sys.addaudithook(audit)


async def replay(config):
    root = Path(config['run_root']).resolve()
    guard(root)
    # Flat Lean domain imports stay in this dedicated, network-denied process.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.path.insert(0, config['core_root'])
    from adapters.paper.adapter import PaperAdapter
    from domain import TradingDecision, MarketState, MarketTicker
    from services.execution_coordinator import ExecutionCoordinator
    from services.decision_validator import DecisionValidator
    from persistence.execution_repository import ExecutionRepository
    from risk.policy import RiskPolicy
    from risk.engine import RiskEngine
    from modules.strategy_manager import assess_observation
    from modules.position_exit_policy import assess_exit

    dataset_bytes = Path(config['dataset']).read_bytes()
    if hashlib.sha256(dataset_bytes).hexdigest() != config['dataset_sha256']:
        raise ValueError('Replay dataset changed')
    dataset = json.loads(dataset_bytes)
    started = time.monotonic()
    receipt = {'run_id': config['run_id'], 'mode': 'paper_replay', 'status': 'running',
        'provenance': dataset['provenance'], 'dataset_sha256': config['dataset_sha256'],
        'assumptions': {'currency': 'USDT', 'capital_per_scenario': 10000,
            'stake': 100, 'fee_rate_per_fill': .001, 'slippage': 0,
            'quote': 'Exact synthetic completed-bar close; zero spread; no liquidity model',
            'confidence': '1 denotes deterministic rule admission, NOT predictive probability',
            'scope': 'Synthetic integration coverage; not a backtest or evidence of profitability'},
        'scenarios': [], 'broker_calls': 0}

    def checkpoint():
        (root / 'receipt.json').write_text(json.dumps(receipt, default=str, allow_nan=False), encoding='utf-8')

    def cancelled():
        marker = root / 'cancel'
        heartbeat = root / 'heartbeat'
        lease_lost = config.get('lease_required') and (not heartbeat.exists() or time.time()-heartbeat.stat().st_mtime > 2)
        stopped = marker.exists() or lease_lost or time.monotonic() - started > 60
        if stopped and 'cancellation_acknowledged_at' not in receipt:
            receipt['cancellation_acknowledged_at'] = time.time()
            receipt['cancel_requested_at'] = marker.stat().st_mtime if marker.exists() else None
            receipt['cancel_reason'] = ('controller_lease_lost' if lease_lost else
                'scoped_cancel_or_revocation' if marker.exists() else 'deadline')
        return stopped

    for spec in dataset['scenarios']:
        if cancelled():
            receipt['status'] = 'cancelled'; break
        name = spec['name']
        account = PaperAdapter(state_file=str(root / (name + '-account.json')))
        await account.start()
        # Explicit defaults, never load ambient live/demo execution settings.
        policy = RiskPolicy(allowed_pairs=['BTC/USDT'], maximum_open_positions=1)
        repository = ExecutionRepository(str(root / (name + '-executions.sqlite3')))
        coordinator = ExecutionCoordinator(account, validator=DecisionValidator(policy),
            risk_engine=RiskEngine(policy), repository=repository, execution_mode='paper')
        episode = {'name': name, 'timeline': [], 'orders': [], 'status': 'observing',
            'risk_fixture': spec.get('realized_pnl_today', 0)}
        receipt['scenarios'].append(episode)
        context = None
        history = list(spec['warmup'])
        try:
            for step in range(1 + len(spec['updates'])):
                if step:
                    history.append(spec['updates'][step-1])
                # Bounded pacing makes actual in-flight cooperative cancellation observable.
                await asyncio.sleep(.15)
                if cancelled():
                    receipt['status'] = 'cancelled'; episode['status'] = 'cancelled'; break
                bar = history[-1]
                stamp = bar['timestamp'] + 300
                observation = {'symbol': 'BTC/USDT', 'venue': 'isolated_paper',
                    'timeframe': '5m', 'mode': 'replay', 'account_scope': config['run_id'],
                    'source': 'synthetic_fixture_v1', 'candles': history[-500:],
                    'as_of': stamp, 'received_at': stamp, 'fresh': True}
                assessment = assess_observation(observation)
                price = bar['close']
                account.set_price('BTC/USDT', price)
                positions = await account.get_open_positions()
                event = {'step': step, 'replay_timestamp': stamp, 'price': price,
                    'signal': assessment['signal'], 'side': assessment['side'],
                    'strategy': assessment.get('strategy'), 'signals': assessment['candidates'],
                    'selection_reason': assessment.get('selection_reason', assessment.get('abstain_reason')),
                    'positions_before': [p.position_id for p in positions]}
                episode['timeline'].append(event)
                decision = None
                if positions:
                    p = positions[0]
                    exit_check = assess_exit({'type': 0 if p.side == 'long' else 1,
                        'price_open': p.entry_price}, history, {'bid': price, 'ask': price,
                        'fresh': True, 'timestamp': stamp}, context)
                    event['exit_check'] = exit_check
                    if exit_check['action'] == 'close' and exit_check['execution_eligible']:
                        decision = TradingDecision(f"{config['run_id']}:{name}:exit", 'BTC/USDT',
                            'exit_long' if p.side == 'long' else 'exit_short', 1., exit_check['reason'],
                            strategy=p.strategy, stake_amount=p.stake_amount)
                elif not episode['orders'] and assessment['signal'] in ('BUY', 'SELL'):
                    side = assessment['side']
                    decision = TradingDecision(f"{config['run_id']}:{name}:entry", 'BTC/USDT',
                        'enter_long' if side == 'buy' else 'enter_short', 1., assessment['selection_reason'],
                        strategy=assessment['strategy'], stake_amount=100, order_type='market', leverage=1.)
                if decision:
                    decision.metadata = {'strategy': decision.strategy,
                        'market_timestamp': datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                        'simulation': True, 'dataset_sha256': config['dataset_sha256']}
                    clock = datetime.fromtimestamp(stamp, timezone.utc)
                    market = MarketState('BTC/USDT', clock,
                        MarketTicker('BTC/USDT', price, bid=price, ask=price,
                            timestamp=clock, source='synthetic_replay', is_fresh=True),
                        is_fresh=True, source='synthetic_replay', age_seconds=0)
                    # Cancellation is checked at the last boundary before authority invocation.
                    if cancelled():
                        receipt['status'] = 'cancelled'; episode['status'] = 'cancelled'; break
                    risk = coordinator._risk_engine.assess(decision, account.portfolio_equity,
                        positions, realized_pnl_today=spec.get('realized_pnl_today', 0))
                    event['execution_started_at'] = time.time()
                    result = await coordinator.process_decision(decision, positions,
                        portfolio_value=account.portfolio_equity, market_state=market,
                        require_market_state=True, realized_pnl_today=spec.get('realized_pnl_today', 0))
                    event['risk'] = asdict(risk)
                    order = {'decision': decision.to_canonical_dict(), 'result': asdict(result),
                        'execution_record': repository.get_execution(decision.decision_id)}
                    episode['orders'].append(order)
                    event['order'] = {'id': decision.decision_id, 'status': result.status}
                    if result.success and decision.action.startswith('enter_'):
                        evidence = assessment['evidence']
                        context = {'strategy': decision.strategy,
                            'initial_stop': price * (.98 if assessment['side'] == 'buy' else 1.02),
                            'breakout_level': evidence['prior20_high'] if assessment['side'] == 'buy' else evidence['prior20_low']}
                        duplicate = await coordinator.process_decision(decision,
                            await account.get_open_positions(), portfolio_value=account.portfolio_equity,
                            market_state=market, require_market_state=True)
                        order['duplicate_probe'] = asdict(duplicate)
                    elif result.success:
                        episode['status'] = 'closed'
                    else:
                        episode['status'] = result.status
                event['account'] = account.account()
                checkpoint()
                if episode['status'] == 'closed': break
            if episode['status'] == 'observing':
                episode['status'] = 'abstained' if not episode['orders'] else 'open_at_replay_end'
        finally:
            await account.stop()
            episode['account'] = account.account()
            restored = PaperAdapter(state_file=str(root / (name + '-account.json')))
            episode['account_reload_equal'] = restored.account() == account.account()
            closed = await account.get_closed_positions()
            episode['actuals'] = []
            for p in closed:
                sign = 1 if p.side == 'long' else -1
                gross = sign * (p.exit_price - p.entry_price) * p.quantity
                fees = p.entry_price * p.quantity * .001 + p.exit_price * p.quantity * .001
                net = gross - fees
                if abs(net - p.profit_amount) > 1e-9:
                    raise ValueError('Independent fill accounting disagrees')
                episode['actuals'].append({'position_id': p.position_id, 'side': p.side,
                    'entry': p.entry_price, 'exit': p.exit_price, 'quantity': p.quantity,
                    'gross': gross, 'fees': fees, 'net': net, 'verified': True})
            checkpoint()
            repository.close()
        if receipt['status'] == 'cancelled': break
    if receipt.get('cancel_reason') == 'controller_lease_lost':receipt['status']='interrupted'
    if receipt['status'] == 'running': receipt['status'] = 'completed'
    receipt['duration_seconds'] = time.monotonic() - started
    checkpoint()
    return receipt


if __name__ == '__main__':
    config = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    asyncio.run(replay(config))
