"""
MACD Momentum — Freqtrade strategy.

Entry (long):  MACD histogram crosses from negative to positive AND
               price > SMA200 (bull-regime filter).
Exit:          Histogram crosses back negative OR minimal_roi OR stoploss.
Niblit:        Blocks entry when AI signal contradicts direction.
Timeframe:     1h (crypto, Binance).
"""
from __future__ import annotations

import pandas as pd
import pandas_ta as ta  # type: ignore
from freqtrade.strategy import IStrategy, IntParameter

try:
    from .NiblitSignalMixin import NiblitSignalMixin
except ImportError:
    from NiblitSignalMixin import NiblitSignalMixin


class MacdMomentum(NiblitSignalMixin, IStrategy):
    """MACD histogram crossover with SMA-200 regime filter."""

    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = False

    minimal_roi = {"120": 0.04, "60": 0.06, "0": 0.12}
    stoploss = -0.04
    trailing_stop = False

    macd_fast   = IntParameter(8, 16,  default=12, space="buy", optimize=True)
    macd_slow   = IntParameter(20, 32, default=26, space="buy", optimize=True)
    macd_signal = IntParameter(6, 12,  default=9,  space="buy", optimize=True)

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        macd = ta.macd(dataframe["close"],
                       fast=self.macd_fast.value,
                       slow=self.macd_slow.value,
                       signal=self.macd_signal.value)
        if macd is not None:
            dataframe["macd"]         = macd.iloc[:, 0]
            dataframe["macd_hist"]    = macd.iloc[:, 1]
            dataframe["macd_signal"]  = macd.iloc[:, 2]
        dataframe["sma200"] = ta.sma(dataframe["close"], length=200)
        dataframe["atr"]    = ta.atr(dataframe["high"], dataframe["low"],
                                     dataframe["close"], length=14)
        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe.loc[
            (dataframe["macd_hist"] > 0) &
            (dataframe["macd_hist"].shift(1) <= 0) &
            (dataframe["close"] > dataframe["sma200"]) &
            (dataframe["volume"] > 0),
            "enter_long"
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe.loc[
            (dataframe["macd_hist"] < 0) &
            (dataframe["macd_hist"].shift(1) >= 0),
            "exit_long"
        ] = 1

        return dataframe

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float,
                            rate: float, time_in_force: str, current_time,
                            entry_tag, side: str, **kwargs) -> bool:
        if self.niblit_block_entry(pair, side == "long"):
            return False
        return True
