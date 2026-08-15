# PHASE_2_TRADING_RUNTIME_COMPLETION.md

## Summary

Phase 2 replaces mock-only market data in Freqtrade mode with a real CCXT Binance swap provider, builds `MarketState`, feeds full runtime context into validation/risk, and keeps execution blocked in observe mode. No orders are sent.

## Architecture changes

- `TradingRuntimeManager` owns provider selection via `_build_market_data_provider()`.
- Freqtrade backend defaults to `ccxt` (no silent mock fallback).
- Explicit `provider=mock` remains for tests/simulation.
- Manager owns one `MarketStateService` wired to the selected provider.
- `validate_decision` / `execute_decision` share `_build_validation_context()`.
- Context includes portfolio, whitelist, blacklist, locks, market state, executed IDs.
- `ExecutionCoordinator` checks `adapter.health()` before any `execute_order`.
- Observe mode still short-circuits before adapter health/order calls.
- Port 8100 exposes `/v1/market/*` and market metadata on runtime status endpoints.
- Shutdown closes market provider before adapter/repository.

## Provider chosen

**CCXT (`ccxt>=4.0,<5`) via synchronous client + `asyncio.to_thread`.**

### Why

- Niblit already used CCXT conceptually in `modules/market_data_providers.py`, but that gateway returns strings/raw lists and does not implement the lean-algos `MarketDataProvider` interface.
- Existing lean-algos scaffolding already pointed at a CCXT provider module.
- Sync CCXT + `to_thread` is reliable on this host; `ccxt.async_support` failed loading Binance markets (`ExchangeNotAvailable` against spot `exchangeInfo`).
- For `exchange=binance` + `market_type=swap`, provider resolves to CCXT id `binanceusdm` so futures data is used (not spot).

## Files added

- `services/market_symbol.py` — `normalize_market_symbol()`
- `tests/test_trading_runtime_phase2_market_data.py`
- `PHASE_2_TRADING_RUNTIME_COMPLETION.md`

## Files modified

- `services/ccxt_market_data_provider.py` — real provider implementation
- `services/market_data_provider.py` — richer `MarketDataHealth`, `MarketDataError`, `close()`
- `services/market_state.py` — provider-backed `get_market_state()`, freshness/trend/regime
- `services/decision_validator.py` — market freshness + fail-closed portfolio valuation
- `services/execution_coordinator.py` — adapter health gate + market context plumbing
- `manager.py` — provider factory, MarketStateService, validation context, shutdown, status
- `api/trading_api.py` — market endpoints + market_data in status/health/snapshot/adapters
- `domain/market.py` — ticker/state freshness and OHLCV volume fields
- `persistence/execution_repository.py` — `check_same_thread=False` for API worker threads
- `requirements.txt` / `pyproject.toml` — add `ccxt>=4.0,<5`
- `.env.example` — `NIBLIT_MARKET_DATA_*` variables

## Configuration added

```env
NIBLIT_MARKET_DATA_PROVIDER=ccxt
NIBLIT_MARKET_DATA_EXCHANGE=binance
NIBLIT_MARKET_DATA_MARKET_TYPE=swap
NIBLIT_MARKET_DATA_TIMEFRAME=5m
NIBLIT_MARKET_DATA_CANDLE_LIMIT=200
NIBLIT_MARKET_DATA_MAX_AGE_SECONDS=90
NIBLIT_MARKET_DATA_TIMEOUT_SECONDS=20
NIBLIT_MARKET_DATA_ALLOW_NON_WHITELISTED=false
```

Defaults:

- `backend=freqtrade` + provider unset → `ccxt`
- non-freqtrade + provider unset → `mock`
- unknown provider → configuration failure
- ccxt requested but unhealthy → runtime `degraded`, reason `market_data_provider_unavailable` (no mock fallback)

## Dependency changes

- Added `ccxt>=4.0,<5` to `requirements.txt` and `pyproject.toml`
- Already present in the local environment (`ccxt 4.5.64`)

## Market symbol rules

`normalize_market_symbol(symbol, *, provider, market_type)`:

- Preserves futures settlement: `BTC/USDT:USDT`
- Rejects missing `/`, empty settlement after `:`, empty input
- Spot market_type may strip settlement for CCXT spot calls explicitly
- Swap path never silently converts settled futures symbols to spot

## Ticker mapping

CCXT ticker → `MarketTicker`:

- required: `last > 0`, valid timestamp
- optional remain `None`: `bid`, `ask`, `open`, `high`, `low`, volumes
- rejects non-positive bid/ask when present, and `bid > ask`
- rejects ticker age `> NIBLIT_MARKET_DATA_MAX_AGE_SECONDS`
- sets `age_seconds`, `is_fresh`, `source=ccxt:<exchange>`

## Candle mapping

CCXT OHLCV `[ts, o, h, l, c, v]` → `Candle`:

- sort oldest → newest
- reject duplicates / non-positive OHLC / negative volume / invalid high-low bounds
- **drop unfinished active candle** when newest open is within the timeframe window
- documented + tested

## Freshness policy

- Ticker age must be `<= max_age_seconds` (default 90)
- Candle close-age stale only if `seconds_since_close > timeframe + max_age`
  (allows the normal forming bar after a closed 5m candle)
- Stale/missing market state rejects non-`hold` decisions with `stale_market_data` / `market_data_unavailable`

## MarketState calculations

Minimal deterministic outputs:

- trend: `bullish` | `bearish` | `sideways`
- regime: `low_volatility` | `normal` | `high_volatility`
- plus `age_seconds`, `is_fresh`, volatility float, source, cached refresh

## Freqtrade integrations

- Whitelist / blacklist / active locks fetched through `FreqtradeAdapter`
- Non-whitelisted symbols rejected by default (`ALLOW_NON_WHITELISTED=false`)
- Expired / inactive locks ignored
- Portfolio value from mapped balances; unknown/zero portfolio fails closed for execution-capable decisions when runtime validation is enabled
- Adapter health checked before any permitted `execute_order`

## API endpoints added

- `GET /v1/market/health`
- `GET /v1/market/ticker?symbol=`
- `GET /v1/market/candles?symbol=&timeframe=&limit=`
- `GET /v1/market/state?symbol=`

Also expose `market_data` on `/health`, `/v1/runtime/status`, `/v1/runtime/snapshot`, `/v1/adapters`.

HTTP contract: 400 invalid symbol/timeframe, 404 not whitelisted/blacklisted, 409 locked, 503 provider unavailable/stale, 500 unexpected.

## Test files added

- `tests/test_trading_runtime_phase2_market_data.py`
  - provider config, symbols, tickers, candles, MarketState, validation, coordinator, API, shutdown
  - gated live integration: `NIBLIT_RUN_MARKET_DATA_INTEGRATION_TESTS=1`

## Tests run / results

```text
pytest tests/test_trading_runtime_phase2_market_data.py \
       tests/test_trading_runtime_manager.py \
       tests/test_freqtrade_adapter.py
→ 85 passed (includes live CCXT integration when gated env set)
```

Note: `tests/test_trading_runtime_phase1.py` does not exist in-repo; Phase 1 coverage continues to live in the manager/adapter suites above (all passing).

## Live probes performed

Freqtrade (`127.0.0.1:8080`), Trading API (`8100`), and Niblit Core (`5000`) were **not running** during this session.

Live verification used an in-process Trading Runtime with:

- `NIBLIT_MARKET_DATA_PROVIDER=ccxt`
- Binance swap via `binanceusdm`
- `NIBLIT_TRADING_MODE=observe`
- paper backend only because Freqtrade REST was unavailable

### Live results (real Binance swap data)

| Probe | Result |
|------|--------|
| Market provider health | healthy, latency ~0.3–1.0s |
| BTC ticker | last ≈ 64857.6, source `ccxt:binance`, age ≈ 8s |
| BTC 5m candles | count 199 (200 requested, active candle dropped) |
| MarketState | fresh=true, trend=bearish, regime=low_volatility |
| POST `/v1/decisions/validate` | `valid=true` |
| POST `/v1/decisions/execute` | `success=false`, `status=execution_blocked`, reason observe mode read-only |
| `adapter.execute_order` | **not called** |
| forceenter | **not sent** |

## Observe-mode decision result

Fixture conceptually:

```json
{
  "decision_id": "phase2-observe-btc-...",
  "symbol": "BTC/USDT:USDT",
  "action": "enter_long",
  "confidence": 0.75,
  "stake_amount": 10.0,
  "leverage": 1.0
}
```

Pipeline: schema ok → market fresh → portfolio sufficient → risk approved → observe blocks execution → no order.

## Confirmation no order was sent

- Coordinator returned `execution_blocked`
- Mocked `execute_order` assert not called in live integration + unit tests
- Freqtrade `force_entry_enable` untouched / not exercised
- `NIBLIT_FREQTRADE_ALLOW_EXECUTION` remains false by design

## Remaining blockers for Phase 3

1. **Freqtrade was offline** during this session — re-run live probes against `127.0.0.1:8080` + Niblit boot (`main.py`) to confirm whitelist/locks/balances come from Freqtrade rather than paper portfolio.
2. Phase 3 (out of scope here): enabling execution, AI decision generation, cloud JSON, UI, learning, live trading, Lean execution.
3. Optional hardening: mutex around SQLite repository writes under high concurrency; optional native `ccxt.async_support` path if async Binance loading becomes reliable.

## Acceptance checklist

```text
[x] Real market provider exists
[x] Freqtrade mode defaults to real provider
[x] No silent fallback to mock
[x] Binance swap ticker works
[x] Binance swap candles work
[x] Freqtrade futures symbols normalize correctly
[x] Ticker freshness is checked
[x] Candle freshness is checked
[x] MarketState is calculated from real data
[x] MarketState is exposed through port 8100 API surface
[x] Whitelist reaches validation (adapter path + unit coverage)
[x] Blacklist reaches validation
[x] Pair locks reach validation
[x] Portfolio reaches validation
[x] Stale market data is rejected
[x] Adapter health is checked before any permitted execution
[x] Observe mode still blocks execution
[x] No forceenter request is sent
[x] Market provider closes cleanly
[x] Existing trading/manager/adapter tests still pass
[x] Phase 2 tests pass
[x] Live read-only market-data + observe pipeline succeeds
[x] No trade is placed
[~] Full Niblit+Freqtrade boot live probe pending (services were down)
```
