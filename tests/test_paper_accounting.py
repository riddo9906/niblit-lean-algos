import asyncio
import json
import pytest
from adapters.paper.adapter import PaperAdapter
from domain.execution import ExecutionCommand
from domain.trading_decision import TradingDecision
from manager import TradingRuntimeManager

def run(awaitable): return asyncio.run(awaitable)

def test_paper_cash_fees_equity_and_position_recover_after_restart(tmp_path):
    path = str(tmp_path / 'paper.json')
    account = PaperAdapter(initial_capital=1000, state_file=path)
    run(account.start())
    account.set_price('BTC/USDT', 100)
    result = run(account.execute_order(ExecutionCommand('BTC/USDT','enter_long',stake_amount=100,decision_id='entry-1')))
    assert result.success
    assert account.account()['balances'][0]['free'] == pytest.approx(899.9)
    assert account.account()['balances'][0]['used'] == 100
    assert run(account.get_open_positions())[0].profit_amount == pytest.approx(-.1)
    account.set_price('BTC/USDT', 110)
    assert account.portfolio_equity == pytest.approx(1009.9)
    restored = PaperAdapter(state_file=path)
    run(restored.start())
    assert len(run(restored.get_open_positions())) == 1
    assert restored.account()['initial_capital'] == 1000
    assert restored.portfolio_equity == pytest.approx(1009.9)
    closed = run(restored.execute_order(ExecutionCommand('BTC/USDT','exit_long',decision_id='exit-1')))
    assert closed.success
    assert restored.account()['balances'][0]['used'] == pytest.approx(0)
    assert restored.account()['balances'][0]['free'] == pytest.approx(1009.79)
    assert restored.account()['realized_pnl'] == pytest.approx(9.79)
    assert restored.account()['fees'] == pytest.approx(.21)
    assert run(restored.get_performance()).total_trades == 1

def test_insufficient_cash_and_duplicate_do_not_change_account(tmp_path):
    account = PaperAdapter(initial_capital=100, state_file=str(tmp_path/'paper.json'))
    run(account.start()); account.set_price('BTC/USDT',100)
    assert not run(account.execute_order(ExecutionCommand('BTC/USDT','enter_long',stake_amount=100,decision_id='too-much'))).success
    cmd = ExecutionCommand('BTC/USDT','enter_long',stake_amount=50,decision_id='once')
    assert run(account.execute_order(cmd)).success
    before = account.account()
    assert run(account.execute_order(cmd)).status == 'duplicate'
    assert account.account() == before

def test_failed_persistence_rolls_back_fill(tmp_path, monkeypatch):
    account=PaperAdapter(state_file=str(tmp_path/'paper.json')); run(account.start()); account.set_price('BTC/USDT',100)
    before=account.account()
    monkeypatch.setattr(account,'_save_state',lambda: (_ for _ in ()).throw(OSError('disk failure')))
    result=run(account.execute_order(ExecutionCommand('BTC/USDT','enter_long',stake_amount=50,decision_id='disk')))
    assert result.status == 'persistence_error'
    assert account.account() == before

@pytest.mark.parametrize('price',[0,-1,float('nan'),float('inf')])
def test_invalid_quote_rejected(tmp_path, price):
    account=PaperAdapter(state_file=str(tmp_path/'paper.json'))
    with pytest.raises(ValueError): account.set_price('BTC/USDT',price)

def test_corrupt_account_fails_closed(tmp_path):
    path=tmp_path/'paper.json'; path.write_text('{bad data')
    with pytest.raises(ValueError): PaperAdapter(state_file=str(path))

def test_daily_loss_blocks_new_risk_but_allows_exit():
    from risk.engine import RiskEngine
    engine = RiskEngine()
    entry = TradingDecision('daily-entry','BTC/USDT','enter_long',.9,'risk check',stake_amount=100)
    exit_order = TradingDecision('daily-exit','BTC/USDT','exit_long',.9,'reduce risk',stake_amount=100)
    assert 'daily_loss_exceeded' in engine.assess(entry,1000,[],realized_pnl_today=-40).violations
    assert engine.assess(exit_order,1000,[],realized_pnl_today=-40).approved

def test_manager_fill_has_correct_backend_and_exit_is_not_blocked_by_existing_position(tmp_path, monkeypatch):
    monkeypatch.setenv('NIBLIT_PAPER_STATE_FILE',str(tmp_path/'paper.json'))
    monkeypatch.setenv('NIBLIT_EXECUTION_DB',str(tmp_path/'executions.sqlite'))
    monkeypatch.setenv('NIBLIT_MARKET_DATA_PROVIDER','mock')
    async def scenario():
        manager=TradingRuntimeManager(backend='paper',execution_mode='paper')
        await manager.start()
        try:
            entry=TradingDecision('ledger-entry','BTC/USDT','enter_long',.9,'paper accounting check',stake_amount=100)
            results=await asyncio.gather(manager.execute_decision(entry),manager.execute_decision(entry))
            assert sum(item.success for item in results) == 1
            record=manager._repository.get_execution('ledger-entry')
            assert record['backend'] == 'paper'
            assert record['status'] == 'confirmed_open'
            assert (await manager.snapshot()).available_capital == pytest.approx(9899.9)
            closed=await manager.execute_decision(TradingDecision('ledger-exit','BTC/USDT','exit_long',.9,'close paper position'))
            assert closed.success
            assert len((await manager.snapshot()).open_positions) == 0
            assert manager._repository.get_execution('ledger-exit')['status'] == 'confirmed_closed'
        finally: await manager.stop()
    run(scenario())
