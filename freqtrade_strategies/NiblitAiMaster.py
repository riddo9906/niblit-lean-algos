"""
Niblit AI Master — Freqtrade flagship strategy.

Entry logic (all must align for a long):
  1. Long-term trend:   close > EMA200     (macro bull filter)
  2. Medium trend:      close > EMA50      (intermediate trend)
  3. Short-term signal: EMA9 > EMA21       (fast crossover)
  4. Momentum gate:     ADX > adx_threshold (trending, not choppy)
  5. RSI range:         rsi_buy_min < RSI < rsi_buy_max (not oversold/overbought)
  6. Volume:            volume > 0

Exit signals:
  - EMA9 crosses below EMA21 AND close drops below EMA50  (clear reversal)
  - RSI > rsi_overbought                                  (momentum exhaustion)

Custom exits (live + backtest):
  - Dangerous regime (volatile/crash/bear) → immediate exit
  - Profit protection: if up >1% and EMA9 turns bearish   → lock in gains

Niblit AI role (live only):
  - BUY regime with conf>min_conf: AI-vetoed entries are blocked and logged
  - ranging/sideways regime: position size halved
  - volatile/crash/bear: entry blocked, open positions force-exited

Timeframe: 1h (crypto, Binance).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Optional

import pandas as pd
import pandas_ta as ta  # type: ignore
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter

try:
    from .NiblitSignalMixin import NiblitSignalMixin
except ImportError:
    from NiblitSignalMixin import NiblitSignalMixin

logger = logging.getLogger(__name__)

_NIBLIT_WEIGHT   = 0.70
_INTERNAL_WEIGHT = 0.30
_DEFAULT_RISK    = 0.02
_RESULTS_FILE = os.environ.get(
    "NIBLIT_RESULTS_FILE",
    os.path.join(os.environ.get("TMPDIR", "/tmp"), "niblit_ft_results.json"),
)


class NiblitAiMaster(NiblitSignalMixin, IStrategy):
    """Niblit AI Master — Freqtrade edition."""

    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = False

    # Staged ROI: take quick profits early, hold winners progressively longer
    minimal_roi = {"0": 0.05, "30": 0.03, "60": 0.02, "120": 0.01}

    # Tighter hard stop — pairs with -3% losses dominated the bad exits
    stoploss = -0.015

    # Trail after gains appear so winners don't round-trip to losses
    trailing_stop = True
    trailing_stop_positive = 0.01          # activate trailing once up 1%
    trailing_stop_positive_offset = 0.02   # lock floor at entry+1% when at +2%
    trailing_only_offset_is_reached = True

    # EMA200 needs 200 candles; set a buffer so indicators are fully warmed
    startup_candle_count: int = 200

    # ── hyperopt parameters ───────────────────────────────────────────────

    # ADX strength gate — filter out choppy, range-bound markets
    adx_threshold = IntParameter(15, 35, default=20, space="buy", optimize=True)

    # RSI entry window for longs — avoid both oversold traps and overbought entries
    rsi_buy_min = IntParameter(30, 50, default=40, space="buy", optimize=True)
    rsi_buy_max = IntParameter(55, 75, default=65, space="buy", optimize=True)

    # RSI overbought exit level
    rsi_overbought = IntParameter(70, 85, default=75, space="sell", optimize=True)

    # ── lifecycle ─────────────────────────────────────────────────────────

    def bot_start(self, **kwargs) -> None:
        self._trade_count: int   = 0
        self._win_count:   int   = 0
        self._total_pnl:   float = 0.0

    # ── indicators ────────────────────────────────────────────────────────

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        # Short-term trend
        dataframe["ema9"]   = ta.ema(dataframe["close"], length=9)
        dataframe["ema21"]  = ta.ema(dataframe["close"], length=21)

        # Medium-term trend
        dataframe["ema50"]  = ta.ema(dataframe["close"], length=50)

        # Long-term macro trend filter
        dataframe["ema200"] = ta.ema(dataframe["close"], length=200)

        # Momentum / oscillator
        dataframe["rsi"]    = ta.rsi(dataframe["close"], length=14)

        # ADX — trend strength gate (avoids choppy, ranging markets)
        adx_result = ta.adx(dataframe["high"], dataframe["low"], dataframe["close"], length=14)
        if adx_result is not None and not adx_result.empty:
            adx_col = [c for c in adx_result.columns if c.upper().startswith("ADX_")]
            if adx_col:
                dataframe["adx"] = adx_result[adx_col[0]]
        if "adx" not in dataframe.columns:
            dataframe["adx"] = 0.0

        # Volatility — used in custom_exit profit protection
        dataframe["atr"]    = ta.atr(dataframe["high"], dataframe["low"],
                                     dataframe["close"], length=14)
        return dataframe

    # ── entry signals (fully vectorised) ─────────────────────────────────

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        # Long-term macro trend (EMA200 filter)
        macro_bull  = dataframe["close"] > dataframe["ema200"]
        # Medium trend alignment
        mid_bull    = dataframe["close"] > dataframe["ema50"]
        # Short-term EMA crossover
        ema_bull    = dataframe["ema9"]  > dataframe["ema21"]
        # Momentum gate — ADX must show a trending market
        adx_ok      = dataframe["adx"]   > self.adx_threshold.value
        # RSI in healthy buy zone — not a blind oversold bounce
        rsi_in_zone = (
            (dataframe["rsi"] > self.rsi_buy_min.value) &
            (dataframe["rsi"] < self.rsi_buy_max.value)
        )
        vol_ok      = dataframe["volume"] > 0

        dataframe.loc[
            macro_bull & mid_bull & ema_bull & adx_ok & rsi_in_zone & vol_ok,
            "enter_long"
        ] = 1

        # (Shorts disabled: can_short = False — column kept for interface compliance)
        return dataframe

    # ── exit signals (fully vectorised) ──────────────────────────────────

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        # Clear trend reversal: fast EMA crossed below slow AND price lost medium support
        ema_reversal  = (dataframe["ema9"] < dataframe["ema21"]) & \
                        (dataframe["close"] < dataframe["ema50"])
        # Momentum exhaustion: RSI overbought
        rsi_exhausted = dataframe["rsi"] > self.rsi_overbought.value

        dataframe.loc[ema_reversal | rsi_exhausted, "exit_long"] = 1

        return dataframe

    # ── live hooks ────────────────────────────────────────────────────────

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float,
                            rate: float, time_in_force: str, current_time,
                            entry_tag, side: str, **kwargs) -> bool:
        sig    = self.niblit_signal()
        conf   = self.niblit_confidence()
        regime = self.niblit_regime()
        is_long = side == "long"

        # Regime block — dangerous market conditions
        if regime in ("volatile", "crash", "bear"):
            logger.info("NiblitAiMaster: blocking entry — regime=%s pair=%s", regime, pair)
            return False

        # Niblit AI veto — only applied when a live signal is available
        if sig is not None:
            niblit_score = (1.0 if sig == "BUY" else -1.0 if sig == "SELL" else 0.0) * conf
            combined     = _NIBLIT_WEIGHT * niblit_score + _INTERNAL_WEIGHT * (1.0 if is_long else -1.0)
            threshold    = 0.20
            if is_long and combined < threshold:
                logger.info(
                    "NiblitAiMaster: AI veto LONG — pair=%s sig=%s conf=%.2f combined=%.2f",
                    pair, sig, conf, combined,
                )
                return False
            if not is_long and combined > -threshold:
                logger.info(
                    "NiblitAiMaster: AI veto SHORT — pair=%s sig=%s conf=%.2f combined=%.2f",
                    pair, sig, conf, combined,
                )
                return False
            logger.debug(
                "NiblitAiMaster: AI accept %s — pair=%s sig=%s conf=%.2f combined=%.2f",
                side, pair, sig, conf, combined,
            )

        return True

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float],
                            max_stake: float, leverage: float,
                            entry_tag: Optional[str], side: str, **kwargs) -> float:
        regime = self.niblit_regime()
        if regime in ("ranging", "sideways"):
            proposed_stake *= 0.5
        return max(proposed_stake, min_stake or 0)

    def custom_exit(self, pair: str, trade, current_time: datetime,
                    current_rate: float, current_profit: float,
                    **kwargs) -> Optional[str]:
        """Regime-based force-exit and profit-protection early exit."""
        is_long = not getattr(trade, "is_short", False)

        # Force-exit on dangerous regime (live only; no-op in backtesting)
        regime = self.niblit_regime()
        if is_long and regime in ("volatile", "crash", "bear"):
            logger.info("NiblitAiMaster: force-exit long — regime=%s pair=%s", regime, pair)
            return f"regime_{regime}"

        # Profit protection: if we're up and the fast EMA has turned against us, exit early
        # rather than waiting for the full trailing stop to fire.
        if is_long and current_profit > 0.01:
            try:
                dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
                if not dataframe.empty:
                    last = dataframe.iloc[-1]
                    ema9  = last.get("ema9",  float("nan"))
                    ema21 = last.get("ema21", float("nan"))
                    if ema9 < ema21:
                        logger.info(
                            "NiblitAiMaster: profit-protect exit — pair=%s profit=%.2f%%",
                            pair, current_profit * 100,
                        )
                        return "profit_protect"
            except (AttributeError, KeyError, IndexError, TypeError, ValueError):
                pass

        return None

    def confirm_trade_exit(self, pair: str, trade, order_type: str, amount: float,
                           rate: float, time_in_force: str, exit_reason: str,
                           current_time, **kwargs) -> bool:
        return True

    # ── results write-back ────────────────────────────────────────────────

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        """Periodically persist performance metrics so Niblit can read them."""
        try:
            results = {
                "source":      "freqtrade",
                "trade_count": getattr(self, "_trade_count", 0),
                "win_count":   getattr(self, "_win_count", 0),
                "total_pnl":   round(getattr(self, "_total_pnl", 0.0), 2),
                "timestamp":   current_time.isoformat() if hasattr(current_time, "isoformat") else str(current_time),
                "niblit_signal":   self.niblit_signal(),
                "niblit_regime":   self.niblit_regime(),
                "niblit_conf":     self.niblit_confidence(),
            }
            with open(_RESULTS_FILE, "w", encoding="utf-8") as fh:
                json.dump(results, fh, indent=2)
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Could not write results file: %s", exc)
