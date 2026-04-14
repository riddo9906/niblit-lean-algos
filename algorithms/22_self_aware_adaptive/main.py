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

import json
import math
import os
from collections import deque
from typing import Any, Dict, List, Optional, Tuple


# ──────────────────────────────────────────────────────────────────────────────
#  Self-Aware Adaptive Algorithm
#
#  This algorithm continuously monitors its own performance and adapts:
#
#  Self-Awareness layers:
#    1. Performance Tracking  — rolling win-rate, Sharpe, max-drawdown
#    2. Strategy Selection    — chooses between trend / mean-reversion /
#                               volatility-breakout based on regime + own history
#    3. Risk Adaptation       — doubles down when win-rate > 60%, cuts risk
#                               when drawdown > 8% or win-rate < 40%
#    4. Regime Awareness      — classifies market as trending / ranging /
#                               volatile using ATR percentile + ADX
#    5. Meta-Performance Log  — writes self-assessment JSON every 20 bars
#                               so Niblit can read back the algo's "mind state"
#    6. Niblit Signal Overlay — TradingBrain signal adjusts final score
#
#  Supported symbols (via NIBLIT_SA_SYMBOL env var):
#    Equities  : SPY, QQQ, AAPL, NVDA, …
#    Crypto    : BTCUSD, ETHUSD
#    Forex     : EURUSD, GBPUSD (single-pair mode)
# ──────────────────────────────────────────────────────────────────────────────

# Rolling window sizes
_PERF_WINDOW     = 20    # bars for rolling performance metrics
_ATR_HIST_LEN    = 50    # bars for ATR percentile (regime detection)
_MIN_TRADES      = 5     # minimum trades before adapting risk

# Risk bounds
_MIN_RISK        = 0.005  # 0.5% floor
_BASE_RISK       = 0.02   # 2% baseline
_MAX_RISK        = 0.04   # 4% ceiling
_MAX_DRAWDOWN_THRESH = 0.08  # 8% drawdown → halve risk

# Strategy thresholds
_TREND_ADX_MIN   = 25.0  # ADX >= 25 → trending
_VOL_ATR_PCT     = 80    # ATR at 80th percentile → volatile

# Meta-log path (matches LeanAlgoManager results path convention)
_META_LOG_FILE   = os.environ.get(
    "NIBLIT_SA_META_FILE",
    os.path.join(os.environ.get("TMPDIR", "/tmp"), "niblit_self_aware_state.json"),
)

# Universe size cap when running multi-symbol (extend list as needed)
_SYMBOL_ENV = os.environ.get("NIBLIT_SA_SYMBOL", "SPY").upper()


def _is_crypto(sym: str) -> bool:
    return sym in ("BTCUSD", "BTCUSDT", "ETHUSD", "ETHUSDT")


def _is_forex(sym: str) -> bool:
    return len(sym) == 6 and sym.isalpha() and sym[:3] != sym[3:]


# ── Pure-Python rolling stats (no scipy/numpy) ───────────────────────────────

def _mean(vals: List[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _std(vals: List[float]) -> float:
    if len(vals) < 2:
        return 0.0
    m = _mean(vals)
    return math.sqrt(sum((v - m) ** 2 for v in vals) / (len(vals) - 1))


def _percentile_rank(val: float, series: List[float]) -> float:
    """Return the percentile rank of val within series (0–100)."""
    if not series:
        return 50.0
    below = sum(1 for v in series if v < val)
    return 100.0 * below / len(series)


class SelfAwareAdaptive(QCAlgorithm):
    """
    Self-Aware Adaptive Algorithm.

    Continuously monitors its own performance and adapts risk, strategy
    selection, and position sizing in real time.  The algorithm writes
    a JSON self-assessment file every 20 bars so Niblit can observe
    the algorithm's "mind state" and incorporate it into learning.

    Environment variables
    ---------------------
    NIBLIT_SA_SYMBOL    — Asset symbol (default: SPY)
    NIBLIT_SA_META_FILE — Path for self-assessment JSON output
    NIBLIT_SIGNAL_FILE  — Niblit bridge signal file
    """

    # ------------------------------------------------------------------ #
    #  Initialization                                                      #
    # ------------------------------------------------------------------ #
    def initialize(self) -> None:
        self.set_start_date(2020, 1, 1)
        self.set_end_date(2024, 6, 1)
        self.set_cash(100_000)

        if not self.live_mode:
            self.set_brokerage_model(BrokerageName.PAPER_BROKERAGE)

        self._ticker = _SYMBOL_ENV
        self.log(f"SelfAwareAdaptive initializing — symbol={self._ticker}")

        # ── Asset subscription ─────────────────────────────────────────────
        if _is_crypto(self._ticker):
            self._sym = self.add_crypto(
                self._ticker.replace("USDT", "USD"), Resolution.DAILY).symbol
            self._is_crypto = True
            self._is_forex  = False
        elif _is_forex(self._ticker):
            self._sym = self.add_forex(self._ticker, Resolution.DAILY).symbol
            self._is_crypto = False
            self._is_forex  = True
        else:
            self._sym = self.add_equity(self._ticker, Resolution.DAILY).symbol
            self._is_crypto = False
            self._is_forex  = False

        # ── Indicators ────────────────────────────────────────────────────
        self._ema_fast = self.ema(self._sym, 9,  Resolution.DAILY)
        self._ema_slow = self.ema(self._sym, 21, Resolution.DAILY)
        self._ema_200  = self.ema(self._sym, 200, Resolution.DAILY)
        self._rsi      = self.rsi(self._sym, 14, Resolution.DAILY)
        self._atr      = self.atr(self._sym, 14, Resolution.DAILY)
        self._adx      = self.adx(self._sym, 14, Resolution.DAILY)
        self._bb       = self.bb(self._sym, 20, 2.0, MovingAverageType.SIMPLE, Resolution.DAILY)

        self.set_warm_up(210)

        # ── NiblitBridge ──────────────────────────────────────────────────
        self._bridge: Optional[object] = None
        if _NIBLIT_AVAILABLE and _NiblitBridge is not None:
            try:
                self._bridge = _NiblitBridge()
                self.log("NiblitBridge connected.")
            except Exception as exc:
                self.log(f"NiblitBridge unavailable: {exc}")

        # ── Performance tracking ──────────────────────────────────────────
        self._trade_returns:   deque = deque(maxlen=_PERF_WINDOW)
        self._equity_curve:    deque = deque(maxlen=_ATR_HIST_LEN)
        self._atr_history:     deque = deque(maxlen=_ATR_HIST_LEN)
        self._total_trades:    int   = 0
        self._win_count:       int   = 0
        self._peak_equity:     float = 100_000.0
        self._max_drawdown:    float = 0.0
        self._current_risk:    float = _BASE_RISK

        # ── Position state ────────────────────────────────────────────────
        self._position:    int   = 0    # +1 long, -1 short, 0 flat
        self._entry_price: float = 0.0
        self._stop_price:  float = 0.0
        self._bar_count:   int   = 0

        # ── Strategy memory ───────────────────────────────────────────────
        # Track how each strategy has performed: {strategy: [pnl, ...]}
        self._strategy_pnl: Dict[str, deque] = {
            "trend":           deque(maxlen=_PERF_WINDOW),
            "mean_reversion":  deque(maxlen=_PERF_WINDOW),
            "volatility":      deque(maxlen=_PERF_WINDOW),
        }
        self._last_strategy: str = "trend"
        self._active_strategy: str = "trend"

    # ------------------------------------------------------------------ #
    #  Main bar handler                                                    #
    # ------------------------------------------------------------------ #
    def on_data(self, data: Slice) -> None:
        if self.is_warming_up:
            return
        if self._sym not in data.bars:
            return
        if not (self._ema_fast.is_ready and self._ema_slow.is_ready and
                self._rsi.is_ready and self._atr.is_ready and self._adx.is_ready):
            return

        self._bar_count += 1
        price = data.bars[self._sym].close
        equity = self.portfolio.total_portfolio_value

        # Track equity curve and update drawdown
        self._equity_curve.append(equity)
        if equity > self._peak_equity:
            self._peak_equity = equity
        drawdown = (self._peak_equity - equity) / self._peak_equity if self._peak_equity > 0 else 0.0
        if drawdown > self._max_drawdown:
            self._max_drawdown = drawdown

        # Collect ATR history for regime detection
        atr = self._atr.current.value
        if atr > 0:
            self._atr_history.append(atr)

        # ── Step 1: Regime detection ──────────────────────────────────────
        regime = self._detect_regime(price, atr)

        # ── Step 2: Strategy selection (self-aware) ───────────────────────
        self._active_strategy = self._select_strategy(regime)

        # ── Step 3: Risk adaptation (self-aware) ──────────────────────────
        self._adapt_risk(drawdown)

        # ── Step 4: NiblitBridge signal ───────────────────────────────────
        niblit_score    = 0.0
        niblit_regime   = "unknown"
        niblit_risk_mul = 1.0
        if self._bridge is not None:
            try:
                sig = self._bridge.get_full()
                if sig:
                    action = (sig.get("signal") or "HOLD").upper()
                    conf   = float(sig.get("confidence", 0.5))
                    niblit_regime   = sig.get("regime", "unknown")
                    niblit_score    = (1.0 if action == "BUY" else -1.0 if action == "SELL" else 0.0) * conf
                    if niblit_regime in ("volatile", "crash"):
                        niblit_risk_mul = 0.3
                    elif niblit_regime in ("ranging", "sideways"):
                        niblit_risk_mul = 0.6
                    else:
                        niblit_risk_mul = 1.0
            except Exception as exc:
                self.log(f"NiblitBridge error: {exc}")

        # ── Step 5: Strategy signal ───────────────────────────────────────
        strategy_score = self._compute_strategy_score(price, regime)

        # Combine: 60% strategy, 40% Niblit
        combined = 0.60 * strategy_score + 0.40 * niblit_score
        risk_pct = max(_MIN_RISK, min(_MAX_RISK, self._current_risk * niblit_risk_mul))

        # ── Step 6: Stop-loss check ───────────────────────────────────────
        if self._position != 0 and self._stop_price > 0:
            stop_hit = (self._position ==  1 and price <= self._stop_price) or \
                       (self._position == -1 and price >= self._stop_price)
            if stop_hit:
                pnl_pct = (price - self._entry_price) / self._entry_price * self._position
                self._record_trade(pnl_pct, self._last_strategy)
                self.liquidate(self._sym)
                self._position    = 0
                self._stop_price  = 0.0
                self._entry_price = 0.0
                self.log(f"Stop hit @ {price:.4f}  pnl_pct={pnl_pct:.4f}")
                self._maybe_write_meta()
                return

        # ── Step 7: Trade execution ───────────────────────────────────────
        stop_dist = 2.0 * atr if atr > 0 else price * 0.02
        want_long  = combined > 0.15
        want_short = combined < -0.15 and not self._is_crypto  # no short on crypto

        if want_long and self._position != 1:
            if self._position == -1:
                pnl_pct = (self._entry_price - price) / self._entry_price
                self._record_trade(pnl_pct, self._last_strategy)
                self.liquidate(self._sym)
                self._entry_price = 0.0

            qty = self._compute_qty(equity, risk_pct, stop_dist, price)
            if qty > 0:
                self.market_order(self._sym, qty)
                self._position     = 1
                self._stop_price   = price - stop_dist
                self._entry_price  = price
                self._last_strategy = self._active_strategy
                self.log(
                    f"LONG {qty} @ {price:.4f}  stop={self._stop_price:.4f}  "
                    f"strategy={self._active_strategy}  regime={regime}  "
                    f"risk={risk_pct:.4f}  combined={combined:.3f}"
                )

        elif want_short and self._position != -1:
            if self._position == 1:
                pnl_pct = (price - self._entry_price) / self._entry_price
                self._record_trade(pnl_pct, self._last_strategy)
                self.liquidate(self._sym)
                self._entry_price = 0.0

            qty = self._compute_qty(equity, risk_pct, stop_dist, price)
            if qty > 0:
                self.market_order(self._sym, -qty)
                self._position     = -1
                self._stop_price   = price + stop_dist
                self._entry_price  = price
                self._last_strategy = self._active_strategy
                self.log(
                    f"SHORT {qty} @ {price:.4f}  stop={self._stop_price:.4f}  "
                    f"strategy={self._active_strategy}  regime={regime}  "
                    f"risk={risk_pct:.4f}  combined={combined:.3f}"
                )

        elif not want_long and not want_short and self._position != 0:
            pnl_pct = (price - self._entry_price) / self._entry_price * self._position
            self._record_trade(pnl_pct, self._last_strategy)
            self.liquidate(self._sym)
            self._position    = 0
            self._stop_price  = 0.0
            self._entry_price = 0.0
            self.log(f"Flat signal @ {price:.4f}  pnl_pct={pnl_pct:.4f}")

        # ── Periodic meta-log ─────────────────────────────────────────────
        if self._bar_count % 20 == 0:
            self._maybe_write_meta()

    # ------------------------------------------------------------------ #
    #  Self-awareness: regime detection                                    #
    # ------------------------------------------------------------------ #
    def _detect_regime(self, price: float, atr: float) -> str:
        """Classify the current market regime.

        Returns: "trending", "ranging", or "volatile"
        """
        adx_val = self._adx.current.value if self._adx.is_ready else 0.0
        atr_rank = _percentile_rank(atr, list(self._atr_history))

        if atr_rank >= _VOL_ATR_PCT:
            return "volatile"
        if adx_val >= _TREND_ADX_MIN:
            return "trending"
        return "ranging"

    # ------------------------------------------------------------------ #
    #  Self-awareness: strategy selection                                  #
    # ------------------------------------------------------------------ #
    def _select_strategy(self, regime: str) -> str:
        """Choose the best strategy based on regime AND own past performance."""
        # Default: regime-based selection
        if regime == "trending":
            regime_choice = "trend"
        elif regime == "ranging":
            regime_choice = "mean_reversion"
        else:
            regime_choice = "volatility"

        # Self-aware override: if recent performance of regime_choice is poor
        # but another strategy has been doing better, switch.
        if self._total_trades >= _MIN_TRADES:
            best_strategy = regime_choice
            best_score    = self._strategy_score(regime_choice)
            for strat in ("trend", "mean_reversion", "volatility"):
                s = self._strategy_score(strat)
                if s > best_score + 0.05:   # 5% better win-rate required to switch
                    best_strategy = strat
                    best_score    = s
            return best_strategy

        return regime_choice

    def _strategy_score(self, strategy: str) -> float:
        """Win-rate (0–1) for a given strategy over the rolling window."""
        pnls = list(self._strategy_pnl.get(strategy, []))
        if not pnls:
            return 0.5
        wins = sum(1 for p in pnls if p > 0)
        return wins / len(pnls)

    # ------------------------------------------------------------------ #
    #  Self-awareness: risk adaptation                                     #
    # ------------------------------------------------------------------ #
    def _adapt_risk(self, drawdown: float) -> None:
        """Adjust self._current_risk based on own rolling performance."""
        if drawdown > _MAX_DRAWDOWN_THRESH:
            # Significant drawdown → cut risk
            self._current_risk = max(_MIN_RISK, self._current_risk * 0.7)
            return

        if self._total_trades < _MIN_TRADES:
            self._current_risk = _BASE_RISK
            return

        win_rate = self._rolling_win_rate()
        if win_rate > 0.60:
            # Performing well → cautiously increase risk
            self._current_risk = min(_MAX_RISK, self._current_risk * 1.05)
        elif win_rate < 0.40:
            # Underperforming → reduce risk
            self._current_risk = max(_MIN_RISK, self._current_risk * 0.90)

    def _rolling_win_rate(self) -> float:
        pnls = list(self._trade_returns)
        if not pnls:
            return 0.5
        return sum(1 for p in pnls if p > 0) / len(pnls)

    def _rolling_sharpe(self) -> float:
        pnls = list(self._trade_returns)
        if len(pnls) < 3:
            return 0.0
        m = _mean(pnls)
        s = _std(pnls)
        return (m / s) * math.sqrt(252) if s > 0 else 0.0

    # ------------------------------------------------------------------ #
    #  Strategy signal computation                                         #
    # ------------------------------------------------------------------ #
    def _compute_strategy_score(self, price: float, regime: str) -> float:
        """Return a score in [-1, 1] based on the active strategy."""
        strat = self._active_strategy

        ema_fast = self._ema_fast.current.value
        ema_slow = self._ema_slow.current.value
        rsi      = self._rsi.current.value

        if strat == "trend":
            # EMA crossover + trend filter via EMA200
            ema200 = self._ema_200.current.value if self._ema_200.is_ready else ema_slow
            if ema_fast > ema_slow and price > ema200 and rsi < 70:
                return  0.8
            if ema_fast < ema_slow and price < ema200 and rsi > 30:
                return -0.8
            return 0.0

        if strat == "mean_reversion":
            # Bollinger Band bounce
            if not self._bb.is_ready:
                return 0.0
            bb_upper = self._bb.upper_band.current.value
            bb_lower = self._bb.lower_band.current.value
            bb_mid   = self._bb.middle_band.current.value
            if price < bb_lower and rsi < 35:
                return  0.7   # oversold → buy
            if price > bb_upper and rsi > 65:
                return -0.7   # overbought → sell
            if abs(price - bb_mid) < (bb_upper - bb_lower) * 0.1:
                return 0.0    # near mid → neutral
            return 0.0

        if strat == "volatility":
            # Volatility breakout: price breaks above/below recent high/low
            if not self._bb.is_ready:
                return 0.0
            bb_upper = self._bb.upper_band.current.value
            bb_lower = self._bb.lower_band.current.value
            if price > bb_upper and ema_fast > ema_slow:
                return  0.6
            if price < bb_lower and ema_fast < ema_slow:
                return -0.6
            return 0.0

        return 0.0

    # ------------------------------------------------------------------ #
    #  Position sizing                                                     #
    # ------------------------------------------------------------------ #
    def _compute_qty(self, equity: float, risk_pct: float,
                     stop_dist: float, price: float):
        if stop_dist <= 0 or price <= 0:
            return 0
        dollar_risk = equity * risk_pct
        max_risk    = dollar_risk / stop_dist
        max_cap     = (equity * 0.30) / price
        qty_raw = min(max_risk, max_cap)
        if self._is_crypto:
            return round(qty_raw, 6)
        return int(qty_raw)

    # ------------------------------------------------------------------ #
    #  Trade record                                                        #
    # ------------------------------------------------------------------ #
    def _record_trade(self, pnl_pct: float, strategy: str) -> None:
        self._total_trades += 1
        self._trade_returns.append(pnl_pct)
        self._strategy_pnl.setdefault(strategy, deque(maxlen=_PERF_WINDOW)).append(pnl_pct)
        if pnl_pct > 0:
            self._win_count += 1

    # ------------------------------------------------------------------ #
    #  Meta-log: self-assessment JSON write                                #
    # ------------------------------------------------------------------ #
    def _maybe_write_meta(self) -> None:
        """Write self-assessment JSON so Niblit can observe the algorithm's state."""
        equity   = self.portfolio.total_portfolio_value
        win_rate = self._rolling_win_rate()
        sharpe   = self._rolling_sharpe()

        strategy_scores = {
            s: round(self._strategy_score(s), 4)
            for s in ("trend", "mean_reversion", "volatility")
        }

        state: Dict[str, Any] = {
            "algorithm":       "SelfAwareAdaptive",
            "symbol":          self._ticker,
            "bar_count":       self._bar_count,
            "equity":          round(equity, 2),
            "total_trades":    self._total_trades,
            "win_rate":        round(win_rate, 4),
            "rolling_sharpe":  round(sharpe, 4),
            "max_drawdown":    round(self._max_drawdown, 4),
            "current_risk":    round(self._current_risk, 6),
            "active_strategy": self._active_strategy,
            "strategy_scores": strategy_scores,
            "position":        self._position,
            "niblit_available": _NIBLIT_AVAILABLE,
            "self_assessment": self._build_self_assessment(win_rate, sharpe),
        }

        try:
            with open(_META_LOG_FILE, "w") as fh:
                json.dump(state, fh, indent=2)
        except Exception as exc:
            self.log(f"Meta-log write failed: {exc}")

    def _build_self_assessment(self, win_rate: float, sharpe: float) -> str:
        """Generate a human-readable self-assessment string."""
        parts = []
        if win_rate > 0.60:
            parts.append("performing well (win-rate > 60%)")
        elif win_rate < 0.40:
            parts.append("underperforming (win-rate < 40%) — reduced risk")
        else:
            parts.append("neutral performance")

        if self._max_drawdown > _MAX_DRAWDOWN_THRESH:
            parts.append(f"drawdown warning ({self._max_drawdown*100:.1f}%)")
        if sharpe > 1.0:
            parts.append("good risk-adjusted returns")
        elif sharpe < 0:
            parts.append("negative Sharpe — re-evaluating strategy")

        parts.append(f"using '{self._active_strategy}' strategy")
        return "; ".join(parts) if parts else "no assessment yet"

    # ------------------------------------------------------------------ #
    #  Event hooks                                                         #
    # ------------------------------------------------------------------ #
    def on_order_event(self, order_event: OrderEvent) -> None:
        self.log(str(order_event))

    def on_end_of_algorithm(self) -> None:
        if self._position != 0:
            price = self.securities[self._sym].price
            pnl_pct = (price - self._entry_price) / self._entry_price * self._position
            self._record_trade(pnl_pct, self._last_strategy)

        equity   = self.portfolio.total_portfolio_value
        win_rate = self._rolling_win_rate()
        sharpe   = self._rolling_sharpe()

        self.log(
            f"=== SelfAwareAdaptive Final Report ===\n"
            f"  Symbol          : {self._ticker}\n"
            f"  Portfolio value : {equity:.2f}\n"
            f"  Total trades    : {self._total_trades}\n"
            f"  Win rate        : {win_rate*100:.1f}%\n"
            f"  Rolling Sharpe  : {sharpe:.3f}\n"
            f"  Max drawdown    : {self._max_drawdown*100:.1f}%\n"
            f"  Active strategy : {self._active_strategy}\n"
            f"  Final risk      : {self._current_risk:.4f}\n"
            f"  Self-assessment : {self._build_self_assessment(win_rate, sharpe)}\n"
            f"  NiblitBridge    : {'connected' if self._bridge else 'unavailable'}"
        )

        # Final meta-log flush
        self._maybe_write_meta()
