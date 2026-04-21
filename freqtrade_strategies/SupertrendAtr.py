"""
Supertrend ATR — Freqtrade strategy.

Entry (long):  Supertrend flips bullish (trend direction changes to +1).
Exit:          Supertrend flips bearish OR stoploss.
Niblit:        Blocks entry when AI confidence > 0.7 and contradicts direction.
Timeframe:     1h (crypto, Binance) — matching original LEAN hourly consolidation.
"""
from __future__ import annotations

import pandas as pd
import pandas_ta as ta  # type: ignore
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter

try:
    from .NiblitSignalMixin import NiblitSignalMixin
except ImportError:
    from NiblitSignalMixin import NiblitSignalMixin


class SupertrendAtr(NiblitSignalMixin, IStrategy):
    """Supertrend ATR trend-following strategy."""

    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = False

    minimal_roi = {"0": 0.99}  # rely on Supertrend exit signals, not time-based ROI
    stoploss = -0.05
    trailing_stop = False

    st_period     = IntParameter(7, 15,  default=10, space="buy", optimize=True)
    st_multiplier = DecimalParameter(2.0, 4.0, default=3.0, decimals=1, space="buy", optimize=True)

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        st = ta.supertrend(dataframe["high"], dataframe["low"], dataframe["close"],
                           length=self.st_period.value,
                           multiplier=self.st_multiplier.value)
        if st is not None:
            # pandas_ta supertrend returns columns: SUPERT_*, SUPERTd_*, SUPERTl_*, SUPERTs_*
            direction_col = [c for c in st.columns if c.startswith("SUPERTd")]
            if direction_col:
                dataframe["st_direction"] = st[direction_col[0]]

        dataframe["atr"] = ta.atr(dataframe["high"], dataframe["low"],
                                  dataframe["close"], length=14)
        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        if "st_direction" not in dataframe.columns:
            return dataframe

        # Long: direction flips to +1 (was -1)
        dataframe.loc[
            (dataframe["st_direction"] == 1) &
            (dataframe["st_direction"].shift(1) == -1) &
            (dataframe["volume"] > 0),
            "enter_long"
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        if "st_direction" not in dataframe.columns:
            return dataframe

        dataframe.loc[dataframe["st_direction"] == -1, "exit_long"]  = 1
        return dataframe

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float,
                            rate: float, time_in_force: str, current_time,
                            entry_tag, side: str, **kwargs) -> bool:
        conf = self.niblit_confidence()
        if conf > 0.7 and self.niblit_block_entry(pair, side == "long"):
            return False
        return True
