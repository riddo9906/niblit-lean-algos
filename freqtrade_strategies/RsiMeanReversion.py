"""
RSI Mean Reversion — Freqtrade strategy.

Entry (long):  RSI < 30 AND EMA50 > EMA200 (uptrend context).
Entry (short): RSI > 70 AND EMA50 < EMA200 (downtrend context).
Exit:          RSI crosses 50 OR minimal_roi OR stoploss.
Niblit:        Veto blocks entry when AI says strongly opposite direction.
Timeframe:     1h (crypto, Binance).
"""
from __future__ import annotations

import pandas as pd
import pandas_ta as ta  # type: ignore
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter

from freqtrade_strategies.NiblitSignalMixin import NiblitSignalMixin


class RsiMeanReversion(NiblitSignalMixin, IStrategy):
    """RSI oversold/overbought mean-reversion with EMA trend filter."""

    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = True

    minimal_roi = {"0": 0.08}
    stoploss = -0.03
    trailing_stop = False

    rsi_period     = IntParameter(10, 20,  default=14,  space="buy", optimize=True)
    rsi_oversold   = DecimalParameter(20, 35, default=30, decimals=0, space="buy", optimize=True)
    rsi_overbought = DecimalParameter(65, 80, default=70, decimals=0, space="sell", optimize=True)
    rsi_exit       = DecimalParameter(45, 55, default=50, decimals=0, space="sell", optimize=True)

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["rsi"]    = ta.rsi(dataframe["close"], length=self.rsi_period.value)
        dataframe["ema50"]  = ta.ema(dataframe["close"], length=50)
        dataframe["ema200"] = ta.ema(dataframe["close"], length=200)
        dataframe["atr"]    = ta.atr(dataframe["high"], dataframe["low"],
                                     dataframe["close"], length=14)
        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe.loc[
            (dataframe["rsi"] < self.rsi_oversold.value) &
            (dataframe["ema50"] > dataframe["ema200"]) &
            (dataframe["volume"] > 0),
            "enter_long"
        ] = 1

        dataframe.loc[
            (dataframe["rsi"] > self.rsi_overbought.value) &
            (dataframe["ema50"] < dataframe["ema200"]) &
            (dataframe["volume"] > 0),
            "enter_short"
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe.loc[
            (dataframe["rsi"] >= self.rsi_exit.value),
            "exit_long"
        ] = 1

        dataframe.loc[
            (dataframe["rsi"] <= self.rsi_exit.value),
            "exit_short"
        ] = 1

        return dataframe

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float,
                            rate: float, time_in_force: str, current_time,
                            entry_tag, side: str, **kwargs) -> bool:
        if self.niblit_block_entry(pair, side == "long"):
            return False
        return True
