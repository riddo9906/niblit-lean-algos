# Niblit Freqtrade Adapter

## Overview

The Freqtrade adapter allows Niblit to inspect Freqtrade runtime state, read balances, open/closed trades, performance, and execute approved trading decisions through the Freqtrade REST API.

## Architecture

```
Niblit Core
    │
    ▼
Trading Runtime API
    │
    ▼
Decision Orchestrator
    │
    ├── Market State Service
    ├── Portfolio State Service
    ├── Risk Engine
    ├── Decision Validator
    └── Execution Coordinator
             │
             ▼
      Trading Adapter Interface
             │
       ┌─────┴─────┐
       ▼           ▼
 Lean Adapter   Freqtrade Adapter
                     │
                     ▼
              Freqtrade REST API
                     │
                     ▼
                  Exchange
```

## Configuration

### Environment Variables

```bash
# Backend selection
NIBLIT_TRADING_BACKEND=freqtrade
NIBLIT_TRADING_MODE=observe

# Freqtrade connection
NIBLIT_FREQTRADE_BASE_URL=http://127.0.0.1:8080
NIBLIT_FREQTRADE_USERNAME=
NIBLIT_FREQTRADE_PASSWORD=
NIBLIT_FREQTRADE_TOKEN=
NIBLIT_FREQTRADE_TIMEOUT_SECONDS=30
NIBLIT_FREQTRADE_DRY_RUN=true
NIBLIT_FREQTRADE_VERIFY_SSL=true
NIBLIT_FREQTRADE_MAX_RETRIES=3
NIBLIT_FREQTRADE_RETRY_BACKOFF_SECONDS=1
NIBLIT_FREQTRADE_ALLOW_EXECUTION=false

# Risk policy
NIBLIT_MAX_STAKE_PERCENTAGE=0.10
NIBLIT_MAX_OPEN_POSITIONS=5
NIBLIT_MAX_DAILY_LOSS=0.03
NIBLIT_MAX_LEVERAGE=1.0
NIBLIT_MIN_CONFIDENCE=0.50

# Live trading (requires explicit acknowledgement)
NIBLIT_LIVE_TRADING_ACKNOWLEDGEMENT=I_UNDERSTAND_LIVE_TRADING_RISK
```

## Starting Freqtrade in Dry-Run Mode

```bash
# Install Freqtrade
pip install freqtrade

# Create a config
freqtrade new-config --config config.json

# Run in dry-run mode with REST API enabled
freqtrade trade \
  --config config.json \
  --strategy SampleStrategy \
  --dry-run \
  --datadir user_data/data \
  --logfile user_data/logs/freqtrade.log \
  --db-url sqlite:///user_data/tradesv3.dry_run.sqlite
```

Enable the REST API in your Freqtrade config:

```json
{
  "api_server": {
    "enabled": true,
    "listen_ip_address": "127.0.0.1",
    "listen_port": 8080,
    "username": "freqtrader",
    "password": "your_password",
    "verbosity": "error"
  }
}
```

## CLI Commands

```bash
# Health check
python -m cli.trading_cli health

# List adapters
python -m cli.trading_cli adapters

# Freqtrade commands
python -m cli.trading_cli freqtrade ping
python -m cli.trading_cli freqtrade status
python -m cli.trading_cli freqtrade balance
python -m cli.trading_cli freqtrade trades

# Portfolio and performance
python -m cli.trading_cli portfolio
python -m cli.trading_cli performance

# Validate a decision
python -m cli.trading_cli validate-decision examples/trading_decision.json

# Execute a decision
python -m cli.trading_cli execute-decision examples/trading_decision.json
```

## Execution Modes

| Mode | Description |
|------|-------------|
| `observe` | Read-only. No execution endpoint may be invoked. |
| `paper` | Requires Freqtrade dry-run mode. Execution allowed only against verified dry-run instance. |
| `live` | Requires `NIBLIT_FREQTRADE_ALLOW_EXECUTION=true`, `NIBLIT_TRADING_MODE=live`, Freqtrade reports `dry_run=false`, and explicit live-trading acknowledgement. |

## Safety Controls

- **Observe mode** blocks all execution
- **Paper mode** requires Freqtrade dry-run
- **Live mode** requires every live safety flag
- Invalid acknowledgement blocks execution
- Excessive stake is blocked or reduced
- Maximum open trades is enforced
- Blocked symbols are rejected
- Stale decisions are rejected
- Duplicate decision IDs are rejected
- Insufficient funds are rejected
- Pair locks are respected

## Freqtrade Endpoints Implemented

| Endpoint | Method |
|----------|--------|
| `/api/v1/ping` | GET |
| `/api/v1/version` | GET |
| `/api/v1/show_config` | GET |
| `/api/v1/status` | GET |
| `/api/v1/balance` | GET |
| `/api/v1/profit` | GET |
| `/api/v1/performance` | GET |
| `/api/v1/count` | GET |
| `/api/v1/trades` | GET |
| `/api/v1/trade/{id}` | GET |
| `/api/v1/whitelist` | GET |
| `/api/v1/blacklist` | GET |
| `/api/v1/locks` | GET |
| `/api/v1/logs` | GET |
| `/api/v1/start` | POST |
| `/api/v1/stop` | POST |
| `/api/v1/reload_config` | POST |
| `/api/v1/forceenter` | POST |
| `/api/v1/forceexit` | POST |
| `/api/v1/trades/{id}` | DELETE |
| `/api/v1/trades/{id}/open-order` | DELETE |

## Tests

```bash
cd niblit-lean-algos
python -m pytest tests/test_freqtrade_adapter.py -v
```

## Lean vs Freqtrade

| Feature | Lean | Freqtrade |
|---------|------|-----------|
| Backend | QuantConnect cloud | Local REST API |
| Execution | Cloud algorithms | Local exchange |
| Configuration | `lean.json` | Environment variables |
| Dry-run | Backtesting | Built-in dry-run mode |
| Adapter | `LeanAdapter` (planned) | `FreqtradeAdapter` |