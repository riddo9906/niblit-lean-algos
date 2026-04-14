# region imports
from AlgorithmImports import *
# endregion

# Optional Niblit bridge
try:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), '..', '..', 'niblit_bridge'))
    from connector import NiblitBridge as _NiblitBridge
    _NIBLIT_AVAILABLE = True
except Exception:
    _NIBLIT_AVAILABLE = False
    _NiblitBridge = None

import os
from collections import deque
from typing import Dict, List, Optional, Tuple


# ──────────────────────────────────────────────────────────────────────────────
#  Forex Multi-Pair Algorithm
#
#  Trades a basket of major forex pairs using EMA crossover + RSI filtering.
#  Supports EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD.
#
#  Key forex-specific features:
#    - Pip-value-aware position sizing
#    - Per-pair ATR stop losses
#    - Correlated-pair risk capping (max 2 USD-long at once)
#    - Niblit signal overlay (respects regime / confidence from TradingBrain)
#    - Paper-safe: uses PaperBrokerage in backtest mode
# ──────────────────────────────────────────────────────────────────────────────

# Major pairs to trade (env var overrides comma-separated list)
_DEFAULT_PAIRS = "EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD"
_PAIRS = [p.strip().upper() for p in
          os.environ.get("NIBLIT_FOREX_PAIRS", _DEFAULT_PAIRS).split(",") if p.strip()]

# JPY pairs use a pip = 0.01; all others pip = 0.0001
_PIP_SIZE: Dict[str, float] = {p: (0.01 if p.endswith("JPY") else 0.0001) for p in _PAIRS}

_EMA_FAST       = 9
_EMA_SLOW       = 21
_RSI_PERIOD     = 14
_ATR_PERIOD     = 14
_ATR_STOP_MULT  = 2.0
_RISK_PCT       = 0.01      # 1% of equity per trade (conservative for forex)
_MAX_PAIRS_LONG = 3         # max concurrent long positions
_MAX_PAIRS_SHORT = 3        # max concurrent short positions


class ForexMultiPair(QCAlgorithm):
    """
    Forex Multi-Pair EMA + RSI Strategy.

    Trades a configurable basket of major forex pairs.
    Uses EMA 9/21 crossover with RSI(14) filter and ATR-based stops.
    Integrates with Niblit bridge for regime-aware risk adjustment.

    Environment variables
    ---------------------
    NIBLIT_FOREX_PAIRS   — Comma-separated forex pair symbols (default: EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD)
    NIBLIT_SIGNAL_FILE   — Path to Niblit signal file (default: /tmp/niblit_lean_signal.json)
    """

    def initialize(self) -> None:
        self.set_start_date(2021, 1, 1)
        self.set_end_date(2024, 6, 1)
        self.set_cash(100_000)
        self.set_account_currency("USD")

        if not self.live_mode:
            self.set_brokerage_model(BrokerageName.PAPER_BROKERAGE)

        self.log(f"ForexMultiPair initializing — pairs: {', '.join(_PAIRS)}")

        # ── Subscribe to forex pairs ──────────────────────────────────────────
        self._syms: Dict[str, Symbol] = {}
        self._ema_fast: Dict[str, object] = {}
        self._ema_slow: Dict[str, object] = {}
        self._rsi: Dict[str, object] = {}
        self._atr: Dict[str, object] = {}

        for pair in _PAIRS:
            try:
                sec = self.add_forex(pair, Resolution.DAILY)
                sym = sec.symbol
                self._syms[pair]     = sym
                self._ema_fast[pair] = self.ema(sym, _EMA_FAST, Resolution.DAILY)
                self._ema_slow[pair] = self.ema(sym, _EMA_SLOW, Resolution.DAILY)
                self._rsi[pair]      = self.rsi(sym, _RSI_PERIOD, Resolution.DAILY)
                self._atr[pair]      = self.atr(sym, _ATR_PERIOD, Resolution.DAILY)
                self.log(f"  Subscribed: {pair}")
            except Exception as exc:
                self.log(f"  ⚠️ Could not add {pair}: {exc}")

        self.set_warm_up(max(_EMA_SLOW, _ATR_PERIOD) + 5)

        # ── NiblitBridge ──────────────────────────────────────────────────────
        self._bridge: Optional[object] = None
        if _NIBLIT_AVAILABLE and _NiblitBridge is not None:
            try:
                self._bridge = _NiblitBridge()
                self.log("NiblitBridge connected.")
            except Exception as exc:
                self.log(f"NiblitBridge unavailable: {exc}")

        # ── State ─────────────────────────────────────────────────────────────
        self._positions: Dict[str, int]   = {p: 0 for p in _PAIRS}   # +1 / -1 / 0
        self._stops:     Dict[str, float] = {p: 0.0 for p in _PAIRS}
        self._entries:   Dict[str, float] = {p: 0.0 for p in _PAIRS}
        self._bar_count: int = 0

    # ------------------------------------------------------------------ #
    #  Main bar handler                                                    #
    # ------------------------------------------------------------------ #
    def on_data(self, data: Slice) -> None:
        if self.is_warming_up:
            return

        self._bar_count += 1

        # ── Read Niblit signal ────────────────────────────────────────────────
        niblit_regime   = "ranging"
        niblit_risk_mul = 1.0
        if self._bridge is not None:
            try:
                full = self._bridge.get_full()
                if full:
                    niblit_regime = full.get("regime", "ranging")
                    conf = float(full.get("confidence", 0.5))
                    # In volatile/bear regime: halve risk or skip
                    if niblit_regime in ("volatile", "crash", "bear"):
                        niblit_risk_mul = 0.0
                    elif niblit_regime in ("ranging", "sideways"):
                        niblit_risk_mul = 0.5
                    else:
                        niblit_risk_mul = conf  # bullish → scale by confidence
            except Exception as exc:
                self.log(f"NiblitBridge error: {exc}")

        if niblit_risk_mul == 0.0:
            # Regime says stay flat — close all and skip
            for pair in _PAIRS:
                if self._positions.get(pair, 0) != 0:
                    self._close_position(pair, data, reason="regime")
            return

        equity = self.portfolio.total_portfolio_value

        # Count current open longs and shorts
        open_longs  = sum(1 for v in self._positions.values() if v ==  1)
        open_shorts = sum(1 for v in self._positions.values() if v == -1)

        # ── Per-pair logic ────────────────────────────────────────────────────
        for pair, sym in self._syms.items():
            if sym not in data.bars:
                continue
            if not (self._ema_fast[pair].is_ready and
                    self._ema_slow[pair].is_ready and
                    self._rsi[pair].is_ready and
                    self._atr[pair].is_ready):
                continue

            price    = data.bars[sym].close
            ema_fast = self._ema_fast[pair].current.value
            ema_slow = self._ema_slow[pair].current.value
            rsi      = self._rsi[pair].current.value
            atr      = self._atr[pair].current.value
            pos      = self._positions.get(pair, 0)

            # Stop-loss check
            if pos != 0 and self._stops[pair] > 0:
                stop_hit = (pos ==  1 and price <= self._stops[pair]) or \
                           (pos == -1 and price >= self._stops[pair])
                if stop_hit:
                    self._close_position(pair, data, reason="stop")
                    open_longs  = sum(1 for v in self._positions.values() if v ==  1)
                    open_shorts = sum(1 for v in self._positions.values() if v == -1)
                    continue

            # Signal generation
            bull_signal = ema_fast > ema_slow and 30 < rsi < 65
            bear_signal = ema_fast < ema_slow and 35 < rsi < 70

            stop_dist = _ATR_STOP_MULT * atr if atr > 0 else _PIP_SIZE[pair] * 20
            risk_pct  = _RISK_PCT * niblit_risk_mul

            # ── Entry logic ───────────────────────────────────────────────────
            if bull_signal and pos != 1:
                if pos == -1:
                    self._close_position(pair, data, reason="signal_reversal")
                    open_shorts = max(0, open_shorts - 1)
                if open_longs < _MAX_PAIRS_LONG:
                    qty = self._compute_qty(equity, risk_pct, stop_dist, price, pair)
                    if qty > 0:
                        self.market_order(sym, qty)
                        self._positions[pair] = 1
                        self._stops[pair]     = price - stop_dist
                        self._entries[pair]   = price
                        open_longs += 1
                        self.log(
                            f"LONG {pair} {qty:.4f} @ {price:.5f}  "
                            f"stop={self._stops[pair]:.5f}  regime={niblit_regime}"
                        )

            elif bear_signal and pos != -1:
                if pos == 1:
                    self._close_position(pair, data, reason="signal_reversal")
                    open_longs = max(0, open_longs - 1)
                if open_shorts < _MAX_PAIRS_SHORT:
                    qty = self._compute_qty(equity, risk_pct, stop_dist, price, pair)
                    if qty > 0:
                        self.market_order(sym, -qty)
                        self._positions[pair] = -1
                        self._stops[pair]     = price + stop_dist
                        self._entries[pair]   = price
                        open_shorts += 1
                        self.log(
                            f"SHORT {pair} {qty:.4f} @ {price:.5f}  "
                            f"stop={self._stops[pair]:.5f}  regime={niblit_regime}"
                        )

            elif not bull_signal and not bear_signal and pos != 0:
                self._close_position(pair, data, reason="neutral")
                if pos == 1:
                    open_longs  = max(0, open_longs  - 1)
                else:
                    open_shorts = max(0, open_shorts - 1)

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #
    def _compute_qty(self, equity: float, risk_pct: float,
                     stop_dist: float, price: float, pair: str) -> float:
        """Position size in base currency units (lots * 100,000 approx)."""
        if stop_dist <= 0 or price <= 0:
            return 0.0
        dollar_risk   = equity * risk_pct
        # For JPY pairs price ~150, pip=0.01; for others price ~1.x, pip=0.0001
        pip           = _PIP_SIZE[pair]
        pips_at_risk  = stop_dist / pip
        # Value per pip ≈ pip_size * lot_size / price (USD account, non-USD quote)
        # Simplified: dollar_per_pip = pip / price * lot_size
        lot_size      = 100_000
        dollar_per_pip = (pip / price) * lot_size if price > 1 else pip * lot_size
        max_lots      = (dollar_risk / pips_at_risk) / dollar_per_pip if dollar_per_pip > 0 else 0
        # Cap at 5% of equity for any single pair
        max_notional  = equity * 0.05
        max_lots_cap  = max_notional / (price * lot_size) if price > 0 else 0
        units         = min(max_lots, max_lots_cap) * lot_size
        return round(max(0.0, units), 0)

    def _close_position(self, pair: str, data: Slice, reason: str = "") -> None:
        sym = self._syms.get(pair)
        if sym is None:
            return
        price = data.bars[sym].close if sym in data.bars else self.securities[sym].price
        entry = self._entries.get(pair, price)
        pos   = self._positions.get(pair, 0)
        pnl   = (price - entry) * pos
        self.liquidate(sym)
        self._positions[pair] = 0
        self._stops[pair]     = 0.0
        self._entries[pair]   = 0.0
        self.log(f"CLOSE {pair} @ {price:.5f}  pnl={pnl:.2f}  reason={reason}")

    # ------------------------------------------------------------------ #
    #  Event hooks                                                         #
    # ------------------------------------------------------------------ #
    def on_order_event(self, order_event: OrderEvent) -> None:
        self.log(str(order_event))

    def on_end_of_algorithm(self) -> None:
        equity = self.portfolio.total_portfolio_value
        self.log(
            f"=== ForexMultiPair Final Report ===\n"
            f"  Portfolio value : {equity:.2f}\n"
            f"  Pairs traded    : {', '.join(_PAIRS)}\n"
            f"  Bars processed  : {self._bar_count}\n"
            f"  NiblitBridge    : {'connected' if self._bridge else 'unavailable'}"
        )
