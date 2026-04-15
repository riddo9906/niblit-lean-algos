# Niblit LEAN Algorithms

A comprehensive collection of AI-powered trading algorithms for
[QuantConnect LEAN](https://www.lean.io/), built to integrate cleanly
with [Niblit](https://github.com/riddo9906/Niblit) — the autonomous
AI agent — without any naming collisions or circular dependencies.

## Repository Structure

```
niblit-lean-algos/
├── niblit_bridge/
│   ├── __init__.py
│   └── connector.py        ← Niblit ↔ LEAN signal bridge (file-based)
├── algorithms/
│   ├── 01_ema_triple_cross/main.py
│   ├── 02_macd_momentum/main.py
│   ├── 03_rsi_mean_reversion/main.py
│   ├── 04_bollinger_squeeze/main.py
│   ├── 05_supertrend_atr/main.py
│   ├── 06_pairs_cointegration/main.py
│   ├── 07_ml_random_forest/main.py
│   ├── 08_lstm_predictor/main.py
│   ├── 09_rl_dqn/main.py
│   ├── 10_rl_ppo/main.py
│   ├── 11_regime_hmm/main.py
│   ├── 12_multi_factor/main.py
│   ├── 13_kalman_pairs/main.py
│   ├── 14_crypto_funding_arb/main.py
│   ├── 15_volatility_targeting/main.py
│   ├── 16_dual_momentum/main.py
│   ├── 17_gradient_boosting/main.py
│   ├── 18_transformer_attention/main.py
│   ├── 19_sentiment_alpha/main.py
│   ├── 20_niblit_ai_master/main.py
│   ├── 21_forex_multi_pair/main.py
│   └── 22_self_aware_adaptive/main.py
├── scripts/
│   ├── qc_client.py            ← Shared QC REST API client (HMAC-SHA256, stdlib only)
│   ├── deploy_all_to_qc.py     ← Create new projects + upload algorithms
│   ├── update_project.py       ← Push code changes to existing QC projects
│   ├── download_qc_projects.py ← Download all cloud projects locally
│   ├── backtest_all.py         ← Run backtests for deployed algorithms
│   ├── list_projects.py        ← List deployed projects + backtest/live status
│   ├── live_trading.py         ← Start / stop / liquidate / monitor live algos
│   └── sync_signal.py          ← Inject Niblit signals for local LEAN testing
├── lean.json               ← LEAN workspace config
├── requirements.txt
└── README.md
```

## QuantConnect REST API Client

`scripts/qc_client.py` is a **stdlib-only** (no third-party packages) QC REST
API v2 client shared by all deployment scripts.  It implements the required
HMAC-SHA256 authentication:

```python
timestamp = str(int(time.time()))
hash_hex  = sha256(f"{timestamp}:{api_token}").hexdigest()
header    = "Basic " + base64(f"{user_id}:{hash_hex}")
# + Timestamp: <epoch> header on every request
```

You can also use it directly in your own scripts:

```python
from scripts.qc_client import QCClient

client = QCClient()   # reads QC_USER_ID + QC_API_CRED from environment

# Projects
projects  = client.list_projects()
project   = client.create_project("my-algo")
pid       = project["projectId"]

# Files
client.create_file(pid, "main.py", source_code)
client.update_file(pid, "main.py", updated_source)
client.upsert_file(pid, "main.py", code)   # create-or-update

# Compile & Backtest
compile_id = client.compile(pid)["compileId"]
bt_id      = client.create_backtest(pid, compile_id)["backtestId"]
result     = client.read_backtest(pid, bt_id)

# Live trading
client.create_live(pid, compile_id, brokerage="PaperBrokerage")
client.read_live_portfolio(pid)
client.stop_live(pid)
client.liquidate_live(pid)
```

## How Niblit Integration Works

**No Niblit Python modules are imported inside LEAN algorithms.** Instead,
a lightweight file-based signal bridge is used:

```
Niblit TradingBrain
   │  writes /tmp/niblit_lean_signal.json
   ▼
NiblitBridge.get_signal()   ← called from on_data() in each LEAN algo
   │  reads JSON → "BUY" | "SELL" | "HOLD"
   ▼
LEAN QCAlgorithm (position management)
```

The `modules/lean_algo_manager.py` module in Niblit orchestrates:
1. Pushing algorithm files to QC cloud via the REST API
2. Writing Niblit's trading brain signal to the signal file
3. Reading back live P&L and feeding it to Niblit's knowledge base

## Quick Start

### 1. Set credentials

```bash
export QC_USER_ID=<your_quantconnect_user_id>
export QC_API_CRED=<your_quantconnect_api_token>
```

### 2. Deploy all algorithms to QuantConnect Cloud

```bash
cd niblit-lean-algos
python scripts/deploy_all_to_qc.py          # create projects + upload all algorithms
python scripts/deploy_all_to_qc.py --algo 01 # deploy a single algorithm by prefix
python scripts/deploy_all_to_qc.py --backtest # also launch an initial backtest
python scripts/deploy_all_to_qc.py --dry-run  # preview without making changes
```

### 3. Update algorithm files (after code changes)

```bash
python scripts/update_project.py            # push updates to all deployed projects
python scripts/update_project.py --algo 20  # update a single algorithm
python scripts/update_project.py --backtest # also recompile + launch a new backtest
```

### 4. Run backtests

```bash
python scripts/backtest_all.py              # backtest all deployed algorithms
python scripts/backtest_all.py --algo 01    # backtest a specific algorithm
python scripts/backtest_all.py --wait       # poll until each backtest completes
```

### 5. Manage live trading

```bash
# Start live paper trading for the AI Master algorithm:
python scripts/live_trading.py start --algo 20

# Start live trading on a real brokerage:
python scripts/live_trading.py start --project-id <id> --brokerage InteractiveBrokersBrokerage

# Check live status of all deployed algorithms:
python scripts/live_trading.py status

# Show live portfolio (holdings + cash):
python scripts/live_trading.py portfolio --project-id <id>

# Show recent order history:
python scripts/live_trading.py orders --project-id <id>

# Tail the live log:
python scripts/live_trading.py log --project-id <id> --follow

# Stop gracefully (keep positions open):
python scripts/live_trading.py stop --project-id <id>

# Liquidate all positions and stop:
python scripts/live_trading.py liquidate --project-id <id>
```

### 6. Download cloud projects locally

```bash
python scripts/download_qc_projects.py         # download all projects
python scripts/download_qc_projects.py --project-id <id>  # one project
```

### 7. List projects and their status

```bash
python scripts/list_projects.py                # list all deployed projects
python scripts/list_projects.py --backtests    # also show backtest history
python scripts/list_projects.py --live         # show live algorithm status
python scripts/list_projects.py --json         # output raw JSON
```

### 8. Enable Niblit AI Master algorithm (live practice)

The `20_niblit_ai_master` algorithm reads Niblit's trading brain signals
in real-time. Start Niblit first, then start live practice:

```
trading start                                          ← start Niblit's trading brain
python scripts/live_trading.py start --algo 20         ← start the master algorithm
```

## Algorithms

| # | Algorithm | Style | Key Indicators | Live Ready |
|---|-----------|-------|----------------|------------|
| 01 | EMA Triple Crossover | Trend | EMA 9/21/50 | ✅ |
| 02 | MACD Momentum | Trend/Momentum | MACD, ATR | ✅ |
| 03 | RSI Mean Reversion | Mean Reversion | RSI, EMA | ✅ |
| 04 | Bollinger Squeeze | Breakout | BB, KeltnerCh | ✅ |
| 05 | Supertrend ATR | Trend | ATR Supertrend | ✅ |
| 06 | Pairs Cointegration | Stat Arb | Spread z-score | ✅ |
| 07 | ML Random Forest | AI/ML | Pure-Python RF | ✅ |
| 08 | LSTM Predictor | AI/ML | Rolling LSTM | ✅ |
| 09 | RL DQN Trader | AI/RL | Q-learning | ✅ |
| 10 | RL PPO Trader | AI/RL | Actor-Critic | ✅ |
| 11 | Regime HMM | AI/ML | HMM regimes | ✅ |
| 12 | Multi-Factor | Factor | MOM/QUA/VOL | ✅ |
| 13 | Kalman Pairs | Stat Arb | Kalman spread | ✅ |
| 14 | Crypto Funding Arb | Arb | Funding rate | ✅ |
| 15 | Volatility Targeting | Risk | ATR vol target | ✅ |
| 16 | Dual Momentum | Momentum | ABS+REL mom | ✅ |
| 17 | Gradient Boosting | AI/ML | Pure-Python GBM | ✅ |
| 18 | Transformer Attention | AI/ML | Attention weights | ✅ |
| 19 | Sentiment Alpha | NLP/AI | News sentiment | ✅ |
| 20 | **Niblit AI Master** | **AI Agent** | **All signals** | ✅ |
| 21 | **Forex Multi-Pair** | **Forex/Trend** | **EMA+RSI multi-pair** | ✅ |
| 22 | **Self-Aware Adaptive** | **AI/Self-Learn** | **Adaptive strategy** | ✅ |

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `QC_USER_ID` | QuantConnect user ID | For deployment |
| `QC_API_CRED` | QuantConnect API token | For deployment |
| `NIBLIT_SIGNAL_FILE` | Path to Niblit signal JSON | For AI Master |
| `NIBLIT_ALGO_MODE` | `paper` or `live` | Optional |
| `NIBLIT_RESULTS_FILE` | Path for AI Master results JSON (default: `/tmp/niblit_lean_results.json`) | For AI Master |
| `NIBLIT_FOREX_PAIRS` | Comma-separated forex pairs (default: `EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD`) | For Forex Multi-Pair |
| `NIBLIT_SA_SYMBOL` | Symbol for Self-Aware algo (default: `SPY`) | For Self-Aware Adaptive |
| `NIBLIT_SA_META_FILE` | Path for self-assessment JSON (default: `/tmp/niblit_self_aware_state.json`) | For Self-Aware Adaptive |

## Notes

- All algorithms are **paper-trading safe by default** (brokerage set to
  `PaperBrokerage` when `self.live_mode` is False).
- ML/AI algorithms use **pure Python** implementations (no PyTorch/sklearn)
  so they deploy on QuantConnect Cloud without custom package requirements.
- The Niblit AI Master algorithm is the **only** algorithm that reads
  external Niblit signals; all others are self-contained.
