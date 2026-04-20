# niblit-lean-algos

**Freqtrade-native crypto trading strategies powered by Niblit AI, running on Binance.**

The repository hosts two generations of Niblit trading code side-by-side:

| Layer | Platform | Status |
|-------|----------|--------|
| `freqtrade_strategies/` | **Freqtrade + Binance** | ✅ Active |
| `algorithms/` | QuantConnect LEAN | 🗄️ Legacy (kept, not removed) |

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Repository Structure](#repository-structure)
3. [Quick Start — Freqtrade](#quick-start--freqtrade)
4. [Freqtrade Strategies](#freqtrade-strategies)
5. [Niblit AI Integration](#niblit-ai-integration)
6. [Risk Controls](#risk-controls)
7. [Environment Variables](#environment-variables)
8. [GitHub Actions Workflows](#github-actions-workflows)
9. [Legacy QuantConnect Section](#legacy-quantconnect-section)

---

## Project Overview

Niblit AI generates trading signals (BUY / SELL / HOLD) with a confidence score and market regime label. These signals are written to a JSON sidecar file. Freqtrade strategies read that file via the **`NiblitSignalMixin`** and use it to veto or weight entry/exit decisions.

- **Exchange**: Binance (spot and futures)
- **Quote currency**: USDT
- **Default pairs**: BTC/USDT, ETH/USDT, BNB/USDT, SOL/USDT, XRP/USDT
- **Timeframe**: 1h (all strategies)
- **Backtesting**: Works without a live Niblit instance — mixin gracefully no-ops

---

## Repository Structure

```
niblit-lean-algos/
├── freqtrade_strategies/          # ← NEW: Freqtrade strategies (primary)
│   ├── __init__.py
│   ├── NiblitSignalMixin.py       # AI signal bridge (shared mixin)
│   ├── EmaTripleCross.py          # EMA 9/21/50 triple-cross
│   ├── MacdMomentum.py            # MACD histogram + SMA200 filter
│   ├── RsiMeanReversion.py        # RSI oversold/overbought + EMA trend
│   ├── BollingerSqueeze.py        # Bollinger Band squeeze breakout
│   ├── SupertrendAtr.py           # Supertrend ATR flip
│   └── NiblitAiMaster.py          # Flagship: 70% AI + 30% internal signal
│
├── configs/                       # ← NEW: Freqtrade configuration files
│   ├── freqtrade_config_binance.json      # Live trading config
│   ├── freqtrade_config_dry_run.json      # Dry-run config (paper trading)
│   └── freqtrade_config_backtesting.json  # Backtesting config
│
├── scripts/
│   ├── ft_backtest.py             # ← NEW: Freqtrade backtest runner
│   ├── ft_hyperopt.py             # ← NEW: Freqtrade hyperopt runner
│   ├── ft_live.py                 # ← NEW: Freqtrade live/dry-run manager
│   ├── qc_client.py               # Legacy: QuantConnect REST client
│   ├── deploy_all_to_qc.py        # Legacy: QC deployment
│   └── ...
│
├── algorithms/                    # Legacy: 22 QuantConnect LEAN algorithms
│   ├── 01_ema_triple_cross/
│   ├── 02_macd_momentum/
│   └── ... (22 total)
│
├── niblit_bridge/                 # Niblit ↔ LEAN connector (legacy)
├── nibblebots/                    # AI bot helpers
├── .github/workflows/             # CI + Freqtrade automation
├── .env.example                   # Environment variable template
├── requirements.txt               # Python dependencies
└── lean.json                      # QuantConnect project config
```

---

## Quick Start — Freqtrade

### 1. Install Freqtrade

```bash
pip install freqtrade
# or full install:
git clone https://github.com/freqtrade/freqtrade
cd freqtrade && ./setup.sh -i
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env — fill in BINANCE_API_KEY / BINANCE_API_SECRET for live trading
```

### 3. Download historical data

```bash
freqtrade download-data \
  --exchange binance \
  --pairs BTC/USDT ETH/USDT SOL/USDT \
  --timeframe 1h \
  --days 365 \
  --data-dir user_data/data/binance \
  --config configs/freqtrade_config_backtesting.json
```

### 4. Backtest

```bash
# Backtest all 6 strategies (180 days)
python scripts/ft_backtest.py

# Backtest a single strategy
python scripts/ft_backtest.py --strategy EmaTripleCross --days 90

# Results saved to backtest_results/
```

### 5. Hyperopt

```bash
# Optimise EMA periods and MACD parameters
python scripts/ft_hyperopt.py --strategy EmaTripleCross --epochs 200

# Optimise all strategies
python scripts/ft_hyperopt.py --epochs 100
```

### 6. Dry-run (paper trading)

```bash
python scripts/ft_live.py start --strategy NiblitAiMaster --dry-run
```

### 7. Live trading

```bash
export BINANCE_API_KEY=your_key
export BINANCE_API_SECRET=your_secret
python scripts/ft_live.py start --strategy NiblitAiMaster
```

### 8. Monitor

```bash
python scripts/ft_live.py status
python scripts/ft_live.py trades --limit 20
python scripts/ft_live.py balance
```

---

## Freqtrade Strategies

| Strategy | Signal Logic | Stoploss | Short? | Hyperopt Params | Live-Ready |
|----------|-------------|----------|--------|-----------------|-----------|
| **EmaTripleCross** | EMA9 > EMA21 > EMA50 alignment | -3% | ✅ | EMA periods (fast/mid/slow) | ✅ |
| **MacdMomentum** | MACD histogram cross + SMA200 regime | -4% | ✅ | MACD fast/slow/signal | ✅ |
| **RsiMeanReversion** | RSI < 30/> 70 + EMA50/200 filter | -3% | ✅ | RSI period + thresholds | ✅ |
| **BollingerSqueeze** | BB squeeze release + momentum | -3% | ✅ | BB std, Keltner multiplier | ✅ |
| **SupertrendAtr** | Supertrend flip + ATR | -5% | ✅ | ST period + multiplier | ✅ |
| **NiblitAiMaster** | 70% Niblit AI + 30% EMA/RSI | -3% | ✅ | None (regime-driven) | ✅ |

All strategies use **`INTERFACE_VERSION = 3`**, `timeframe = "1h"`, and include `confirm_trade_entry()` for Niblit AI veto.

---

## Niblit AI Integration

### How `NiblitSignalMixin` works

The mixin reads a JSON signal file written by Niblit's TradingBrain:

```json
{
  "signal":     "BUY",
  "confidence": 0.82,
  "symbol":     "BTC/USDT",
  "timestamp":  1713100000,
  "regime":     "bullish",
  "risk_pct":   0.02
}
```

**In live / dry-run mode:**
- `confirm_trade_entry()` calls `niblit_block_entry(pair, is_long)` — returns `False` (block) when Niblit contradicts the direction with confidence ≥ `NIBLIT_MIN_CONF` (default 0.55)
- `NiblitAiMaster` additionally weights entries: combined score = 70% × Niblit + 30% × internal; threshold 0.20
- Regime `"volatile"` / `"crash"` / `"bear"` → all entries blocked, open longs force-exited
- Regime `"ranging"` / `"sideways"` → position size halved via `custom_stake_amount()`

**In backtesting mode:**
- Signal file is absent → `_niblit_read()` returns `None` → all helpers return neutral values
- Strategy trades purely on technical indicators — no dependency on live Niblit instance

**Signal file path** (set via env var):
```bash
export NIBLIT_SIGNAL_FILE=/path/to/niblit_lean_signal.json
```

**Results write-back**: `NiblitAiMaster.bot_loop_start()` writes `niblit_ft_results.json` so Niblit can observe Freqtrade's performance.

### MRO usage

```python
class MyStrategy(NiblitSignalMixin, IStrategy):
    def confirm_trade_entry(self, pair, order_type, amount, rate,
                            time_in_force, current_time, entry_tag, side, **kwargs):
        if self.niblit_block_entry(pair, side == "long"):
            return False
        return True
```

---

## Risk Controls

### Per-strategy stoplosses

| Strategy | Stoploss | Trailing |
|----------|----------|---------|
| EmaTripleCross | -3% | No |
| MacdMomentum | -4% | No |
| RsiMeanReversion | -3% | No |
| BollingerSqueeze | -3% | No |
| SupertrendAtr | -5% | No |
| NiblitAiMaster | -3% | No |

### Exchange-level protections (live config)

- **StoplossGuard**: Pauses trading for 4 candles after 2 stoploss hits in 24h
- **MaxDrawdown**: Halts trading for 8 candles if 15% drawdown across 3+ trades in 48h
- **LowProfitPairs**: Removes pairs with zero profit over 168 candles from rotation

### Position sizing

- Default: `stake_amount = "unlimited"` (equal-weight across `max_open_trades = 5`)
- `NiblitAiMaster`: halves stake in ranging/sideways regimes via `custom_stake_amount()`
- `tradable_balance_ratio = 0.99` — 1% cash buffer

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BINANCE_API_KEY` | — | Binance API key (live trading only) |
| `BINANCE_API_SECRET` | — | Binance API secret (live trading only) |
| `FT_API_HOST` | `127.0.0.1` | Freqtrade REST API host |
| `FT_API_PORT` | `8080` | Freqtrade REST API port |
| `FT_API_USER` | `freqtrader` | Freqtrade REST API username |
| `FT_API_PASS` | — | Freqtrade REST API password |
| `TELEGRAM_TOKEN` | — | Telegram bot token for notifications |
| `TELEGRAM_CHAT_ID` | — | Telegram chat ID |
| `NIBLIT_SIGNAL_FILE` | `/tmp/niblit_lean_signal.json` | Path to Niblit signal JSON |
| `NIBLIT_SIGNAL_MAX_AGE` | `300` | Max signal age in seconds before stale |
| `NIBLIT_MIN_CONF` | `0.55` | Min Niblit confidence to trigger veto |
| `NIBLIT_RESULTS_FILE` | `/tmp/niblit_ft_results.json` | Freqtrade → Niblit results path |

Copy `.env.example` to `.env` and fill in values before running locally.

---

## GitHub Actions Workflows

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `ci.yml` | Push / PR to main | Validates all Python syntax (LEAN + Freqtrade), JSON configs, lean.json |
| `freqtrade-smoke.yml` | Push / PR touching `freqtrade_strategies/` or `configs/` | Syntax check, import smoke test, quick 30-day backtest on BTC/USDT |
| `freqtrade-backtest.yml` | Manual (`workflow_dispatch`) | Full configurable backtest with artifact upload |
| `backtest-quantconnect.yml` | Manual | Legacy QC backtest runner |
| `deploy-quantconnect.yml` | Manual | Legacy QC deployment |
| `nibblebot-*.yml` | Scheduled / manual | AI bot research, improvements, architecture reviews |

### Running the backtest workflow manually

1. Go to **Actions → Freqtrade Backtest → Run workflow**
2. Fill in optional inputs: `strategy`, `days`, `pairs`
3. Download results artifact from the completed run

---

## Legacy QuantConnect Section

The `algorithms/` directory contains **22 QuantConnect LEAN algorithms** (equity + crypto, SPY-centric). They remain fully intact and deployable via QC cloud.

| # | Algorithm | Type |
|---|-----------|------|
| 01 | EMA Triple Cross | Technical |
| 02 | MACD Momentum | Technical |
| 03 | RSI Mean Reversion | Technical |
| 04 | Bollinger Squeeze | Technical |
| 05 | Supertrend ATR | Technical |
| 06 | Pairs Cointegration | Statistical arb |
| 07 | ML Random Forest | ML |
| 08 | LSTM Predictor | Deep learning |
| 09 | RL DQN | Reinforcement learning |
| 10 | RL PPO | Reinforcement learning |
| 11 | Regime HMM | Regime detection |
| 12 | Multi Factor | Factor model |
| 13 | Kalman Pairs | Kalman filter arb |
| 14 | Crypto Funding Arb | Crypto arb |
| 15 | Volatility Targeting | Risk management |
| 16 | Dual Momentum | Momentum |
| 17 | Gradient Boosting | ML |
| 18 | Transformer Attention | Deep learning |
| 19 | Sentiment Alpha | NLP |
| 20 | Niblit AI Master | AI flagship |
| 21 | Forex Multi-Pair | FX |
| 22 | Self-Aware Adaptive | Meta-learning |

**To deploy to QuantConnect:**
```bash
python scripts/deploy_all_to_qc.py
```

**Legacy environment variables** (see `.env.example` for full list):
- `QC_USER_ID` — QuantConnect user ID
- `QC_API_CRED` — QuantConnect API token
