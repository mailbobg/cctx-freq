"""EmaCross - parametryzowana wersja EMAPriceCrossoverWithThreshold.

Oryginal: Paul Csapak, https://github.com/paulcpk/freqtrade-strategies-that-work (MIT)
Logika identyczna, roznica polega tylko na tym, ze trzy zaszyte na sztywno
liczby (okres EMA, prog wyjscia, stoploss) sa tu parametrami - zeby dalo sie
zmierzyc, czy wynik zalezy od szczesliwego trafienia w konkretne wartosci.

TEZA: bardzo dluga EMA (800 godzin = ok. 33 dni) dziala jak filtr rezimu.
Powyzej niej jestesmy w trendzie wzrostowym, ponizej - poza rynkiem.
Prog 1% ponizej EMA to bufor przed pila na samym przecieciu.
"""
from __future__ import annotations

import freqtrade.vendor.qtpylib.indicators as qtpylib
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import DecimalParameter, IntParameter, IStrategy


class EmaCross(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = False
    process_only_new_candles = True
    startup_candle_count = 1300

    minimal_roi = {"0": 10}
    stoploss = -0.15
    trailing_stop = True

    ema_period = IntParameter(300, 1300, default=800, space="buy", optimize=True)
    exit_threshold = DecimalParameter(0.3, 3.0, default=1.0, decimals=1,
                                      space="sell", optimize=True)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema"] = ta.EMA(dataframe, timeperiod=int(self.ema_period.value))
        dataframe["ema_exit"] = dataframe["ema"] * (100 - float(self.exit_threshold.value)) / 100
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (qtpylib.crossed_above(dataframe["close"], dataframe["ema"]))
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "ema_cross_up")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            qtpylib.crossed_below(dataframe["close"], dataframe["ema_exit"]),
            ["exit_long", "exit_tag"],
        ] = (1, "ema_cross_down")
        return dataframe
