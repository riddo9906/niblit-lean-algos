# Freqtrade-Lean Bridge

Production-grade bridge between Niblit AI and Freqtrade execution engine.

## Architecture

```
Niblit AI → Lean Strategy Layer → Signal API → Freqtrade Adapter → Freqtrade → Exchange
```

## Components

- **bridge.py** - Main bridge orchestration
- **signal_server.py** - REST/WebSocket signal transport
- **strategy_adapter.py** - Freqtrade IStrategy adapter
- **risk_manager.py** - Risk management and position sizing
- **portfolio_manager.py** - Portfolio state tracking
- **execution_manager.py** - Order execution via Freqtrade
- **position_tracker.py** - Cross-system position synchronization
- **config.py** - Configuration management

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run in dry-run mode
python -m freqtrade_bridge

# Or via Docker Compose
docker-compose up
```

## Usage

### From Freqtrade Strategy

```python
from freqtrade_bridge.strategy_adapter import NiblitFreqtradeStrategy

# Use the mixin in your strategy
class MyStrategy(NiblitFreqtradeStrategy):
    pass
```

### From Niblit

```python
from freqtrade_bridge.bridge import get_bridge

bridge = get_bridge()

# Generate a signal
signal = bridge.generate_signal(
    symbol="BTC/USDT",
    side="buy",
    confidence=0.85,
    leverage=2.0,
    stop_loss=0.03,
    take_profit=0.08,
    reason="Momentum breakout"
)

# Check approval
if bridge.approve_trade(signal):
    print("Trade approved")
```

### REST API

```bash
# Get signals
curl http://localhost:8080/signals

# Submit a signal
curl -X POST http://localhost:8080/signals \
  -H "Content-Type: application/json" \
  -d '{"symbol": "ETH/USDT", "side": "buy", "confidence": 0.9}'

# Pause trading
curl -X POST http://localhost:8080/pause

# Emergency stop
curl -X POST http://localhost:8080/emergency-stop
```

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `NIBLIT_EXECUTION_MODE` | `dry_run` | Execution mode |
| `NIBLIT_SIGNAL_FILE` | `/tmp/niblit_signal.json` | Signal file path |
| `NIBLIT_CONFIDENCE_THRESHOLD` | `0.70` | Min confidence to trade |
| `NIBLIT_MAX_DRAWDOWN` | `0.10` | Max portfolio drawdown |
| `FREQTRADE_HOST` | `127.0.0.1` | Freqtrade API host |

## License

MIT