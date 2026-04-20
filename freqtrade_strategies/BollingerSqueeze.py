"""
Bollinger Squeeze — Freqtrade strategy.

Squeeze condition: Bollinger Bands (20, 2σ) are entirely inside the
Keltner Channel (20, 1.5 × ATR).

Entry: Squeeze releases AND momentum (close > BB midline → long;
       close < BB midline → short).
Exit:  Price touches opposite BB band OR minimal_roi OR stoploss.
Niblit: Blocks entry when AI direction contradicts breakout direction.
Timeframe: 1h (crypto, Binance).
"""
from __future__ import annotations

import pandas as pd
import pandas_ta as ta  # type: ignore
from freqtrade.strategy import IStrategy, DecimalParameter

try:
    from .NiblitSignalMixin import NiblitSignalMixin
except ImportError:
    from NiblitSignalMixin import NiblitSignalMixin


class BollingerSqueeze(NiblitSignalMixin, IStrategy):
    """Bollinger Band squeeze breakout strategy."""

    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = False

    minimal_roi = {"0": 0.10}
    stoploss = -0.03
    trailing_stop = False

    bb_std  = DecimalParameter(1.5, 2.5, default=2.0, decimals=1, space="buy", optimize=True)
    kc_mult = DecimalParameter(1.0, 2.0, default=1.5, decimals=1, space="buy", optimize=True)

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        # Bollinger Bands
        bbands = ta.bbands(dataframe["close"], length=20, std=self.bb_std.value)
        if bbands is not None:
            dataframe["bb_upper"]  = bbands.iloc[:, 0]
            dataframe["bb_mid"]    = bbands.iloc[:, 1]
            dataframe["bb_lower"]  = bbands.iloc[:, 2]

        # ATR for Keltner Channel
        dataframe["atr"]    = ta.atr(dataframe["high"], dataframe["low"],
                                     dataframe["close"], length=20)
        dataframe["sma20"]  = ta.sma(dataframe["close"], length=20)
        dataframe["kc_upper"] = dataframe["sma20"] + self.kc_mult.value * dataframe["atr"]
        dataframe["kc_lower"] = dataframe["sma20"] - self.kc_mult.value * dataframe["atr"]

        # Squeeze flag (1 = squeeze on, 0 = squeeze off)
        dataframe["squeeze"] = (
            (dataframe["bb_upper"] < dataframe["kc_upper"]) &
            (dataframe["bb_lower"] > dataframe["kc_lower"])
        ).astype(int)

        # Squeeze just released: previous bar in squeeze, current bar out of squeeze
        dataframe["squeeze_release"] = (
            (dataframe["squeeze"] == 0) & (dataframe["squeeze"].shift(1) == 1)
        ).astype(int)

        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe.loc[
            (dataframe["squeeze_release"] == 1) &
            (dataframe["close"] > dataframe["bb_mid"]) &
            (dataframe["volume"] > 0),
            "enter_long"
        ] = 1

        dataframe.loc[
            (dataframe["squeeze_release"] == 1) &
            (dataframe["close"] < dataframe["bb_mid"]) &
            (dataframe["volume"] > 0),
            "enter_short"
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe.loc[
            dataframe["close"] >= dataframe["bb_upper"],
            "exit_long"
        ] = 1

        dataframe.loc[
            dataframe["close"] <= dataframe["bb_lower"],
            "exit_short"
        ] = 1

        return dataframe

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float,
                            rate: float, time_in_force: str, current_time,
                            entry_tag, side: str, **kwargs) -> bool:
        if self.niblit_block_entry(pair, side == "long"):
            return False
        return True
