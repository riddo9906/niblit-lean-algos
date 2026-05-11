"""
EMA Triple Cross — Freqtrade strategy.

Entry (long):  EMA9 > EMA21 > EMA50 — all three EMAs aligned bullish.
Exit:          EMA alignment turns bearish OR minimal_roi OR stoploss.
Niblit:        Blocks entry when AI signal contradicts direction.
Timeframe:     1h  (crypto, Binance).
"""
from __future__ import annotations

import pandas as pd
import pandas_ta as ta  # type: ignore
from freqtrade.strategy import IStrategy, IntParameter

try:
    from .NiblitSignalMixin import NiblitSignalMixin
except ImportError:
    from NiblitSignalMixin import NiblitSignalMixin


class EmaTripleCross(NiblitSignalMixin, IStrategy):
    """EMA 9/21/50 triple-crossover strategy for Binance spot/futures."""

    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = False

    minimal_roi = {"120": 0.03, "60": 0.05, "0": 0.10}
    stoploss = -0.03
    trailing_stop = False

    # EMA periods — hyperopted
    ema_fast = IntParameter(5, 15, default=9, space="buy", optimize=True)
    ema_mid  = IntParameter(15, 30, default=21, space="buy", optimize=True)
    ema_slow = IntParameter(40, 70, default=50, space="buy", optimize=True)

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        for length in set([self.ema_fast.value, self.ema_mid.value, self.ema_slow.value,
                           9, 21, 50]):
            dataframe[f"ema{length}"] = ta.ema(dataframe["close"], length=length)
        dataframe["atr"] = ta.atr(dataframe["high"], dataframe["low"],
                                  dataframe["close"], length=14)
        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        fast = f"ema{self.ema_fast.value}"
        mid  = f"ema{self.ema_mid.value}"
        slow = f"ema{self.ema_slow.value}"

        dataframe.loc[
            (dataframe[fast] > dataframe[mid]) &
            (dataframe[mid]  > dataframe[slow]) &
            (dataframe["volume"] > 0),
            "enter_long"
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        fast = f"ema{self.ema_fast.value}"
        mid  = f"ema{self.ema_mid.value}"
        slow = f"ema{self.ema_slow.value}"

        dataframe.loc[
            (dataframe[fast] < dataframe[mid]) &
            (dataframe[mid]  < dataframe[slow]),
            "exit_long"
        ] = 1

        return dataframe

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float,
                            rate: float, time_in_force: str, current_time,
                            entry_tag, side: str, **kwargs) -> bool:
        return self.niblit_allow_entry(pair, side == "long")
