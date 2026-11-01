"""Keep trading tests independent of saved operator state and live settings."""
import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def isolated_trading_state(tmp_path):
    with patch.dict(os.environ, {
        'NIBLIT_PAPER_STATE_FILE': str(tmp_path / 'paper.json'),
        'NIBLIT_TRADING_CONTROL_FILE': str(tmp_path / 'control.json'),
        'NIBLIT_EXECUTION_DB': str(tmp_path / 'executions.sqlite'),
        'NIBLIT_TRADING_BACKEND': 'paper',
        'NIBLIT_TRADING_MODE': 'observe',
        'NIBLIT_FREQTRADE_ALLOW_EXECUTION': 'false',
        'NIBLIT_MARKET_DATA_PROVIDER': 'mock',
    }):
        yield
