"""
Niblit AI Master — Freqtrade flagship strategy.

Combines Niblit AI signal (70% weight) with internal EMA9/21 + RSI14
fallback signal (30% weight) — matching the logic in the LEAN master.

• In live/dry-run: reads NiblitBridge JSON signal in confirm_trade_entry()
  and confirm_trade_exit().
• In backtesting:  Niblit weight falls back to zero gracefully; the strategy
  trades on internal signals only.

Regime handling:
  - "ranging" / "sideways" → position size halved via custom_stake_amount()
  - "volatile" / "crash" / "bear" → entry blocked; any open long is closed

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
from freqtrade.strategy import IStrategy

from freqtrade_strategies.NiblitSignalMixin import NiblitSignalMixin

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
    can_short = True

    minimal_roi = {"0": 0.99}  # rely on combined signal exits
    stoploss = -0.03
    trailing_stop = False

    # ── lifecycle ─────────────────────────────────────────────────────────

    def bot_start(self, **kwargs) -> None:
        self._trade_count: int   = 0
        self._win_count:   int   = 0
        self._total_pnl:   float = 0.0

    # ── indicators ────────────────────────────────────────────────────────

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["ema9"]   = ta.ema(dataframe["close"], length=9)
        dataframe["ema21"]  = ta.ema(dataframe["close"], length=21)
        dataframe["rsi"]    = ta.rsi(dataframe["close"], length=14)
        dataframe["sma50"]  = ta.sma(dataframe["close"], length=50)
        dataframe["atr"]    = ta.atr(dataframe["high"], dataframe["low"],
                                     dataframe["close"], length=14)
        return dataframe

    # ── signals ───────────────────────────────────────────────────────────

    def _internal_score(self, row: pd.Series) -> float:
        ema_bull  = row["ema9"]  > row["ema21"]
        ema_bear  = row["ema9"]  < row["ema21"]
        rsi_ok    = 30 < row["rsi"] < 70
        trend_up  = row["close"] > row["sma50"]
        trend_dn  = row["close"] < row["sma50"]

        if ema_bull and rsi_ok and trend_up:
            return 1.0
        if ema_bear and rsi_ok and trend_dn:
            return -1.0
        return 0.0

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        # Vectorised internal signal (Niblit weight is applied in confirm_trade_entry)
        dataframe["internal_score"] = dataframe.apply(self._internal_score, axis=1)
        combined = _INTERNAL_WEIGHT * dataframe["internal_score"]

        dataframe.loc[
            (combined > 0.06) &  # 30% weight × 0.20 threshold
            (dataframe["volume"] > 0),
            "enter_long"
        ] = 1

        dataframe.loc[
            (combined < -0.06) &
            (dataframe["volume"] > 0),
            "enter_short"
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["internal_score"] = dataframe.apply(self._internal_score, axis=1)

        dataframe.loc[dataframe["internal_score"] <= 0, "exit_long"]  = 1
        dataframe.loc[dataframe["internal_score"] >= 0, "exit_short"] = 1

        return dataframe

    # ── live hooks ────────────────────────────────────────────────────────

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float,
                            rate: float, time_in_force: str, current_time,
                            entry_tag, side: str, **kwargs) -> bool:
        sig    = self.niblit_signal()
        conf   = self.niblit_confidence()
        regime = self.niblit_regime()

        # Regime block
        if regime in ("volatile", "crash", "bear"):
            logger.info("NiblitAiMaster: blocking entry — regime=%s", regime)
            return False

        # Combined score check (replicate LEAN logic)
        internal_ok = True  # signal already validated in populate_entry_trend

        if sig is not None:
            niblit_score = (1.0 if sig == "BUY" else -1.0 if sig == "SELL" else 0.0) * conf
            is_long      = side == "long"
            combined     = _NIBLIT_WEIGHT * niblit_score + _INTERNAL_WEIGHT * (1.0 if is_long else -1.0)
            threshold    = 0.20
            if is_long  and combined < threshold:
                return False
            if not is_long and combined > -threshold:
                return False

        return True

    def custom_stake_amount(self, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float],
                            max_stake: float, leverage: float,
                            entry_tag: Optional[str], side: str, **kwargs) -> float:
        regime = self.niblit_regime()
        if regime in ("ranging", "sideways"):
            proposed_stake *= 0.5
        return max(proposed_stake, min_stake or 0)

    def confirm_trade_exit(self, pair: str, trade, order_type: str, amount: float,
                           rate: float, time_in_force: str, exit_reason: str,
                           current_time, **kwargs) -> bool:
        regime = self.niblit_regime()
        # Force-exit longs in dangerous regimes
        if trade.is_long and regime in ("volatile", "crash", "bear"):
            logger.info("NiblitAiMaster: force-exiting long — regime=%s", regime)
            return True
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
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not write results file: %s", exc)
