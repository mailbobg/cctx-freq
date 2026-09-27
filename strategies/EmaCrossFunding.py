"""EmaCross + filtr funding rate. Test hipotezy: czy dane spoza swiec pomagaja?

Funding rate mowi, jak bardzo tlum jest przelewarowany. Hipoteza: unikaj
wejscia, gdy rynek jest rozgrzany (funding wysoko) - wtedy rosnie ryzyko
gwaltownej korekty przez kaskade likwidacji.

UWAGA: wczesniejszy test pokazal, ze kierunek tego sygnalu odwrocil sie
w 2023. Dlatego filtr jest celowo laczony JEDNOSTRONNIE (tylko unikanie
skrajnego przegrzania), a nie jako pelny sygnal kierunkowy.
"""
from __future__ import annotations

from pathlib import Path

import freqtrade.vendor.qtpylib.indicators as qtpylib
import pandas as pd
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.strategy import DecimalParameter, IntParameter, IStrategy

FUNDING = Path(__file__).resolve().parents[1] / "data/binance/futures/BTC_USDT_USDT-1h-funding_rate.feather"  # our Binance BTC perp funding


def _load_funding() -> pd.Series:
    df = pd.read_feather(FUNDING)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return df.set_index("date").sort_index()["funding_rate"]


class EmaCrossFunding(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    can_short = False
    process_only_new_candles = True
    startup_candle_count = 1300

    minimal_roi = {"0": 10}
    stoploss = -0.15
    trailing_stop = True

    ema_period = IntParameter(300, 1300, default=600, space="buy", optimize=True)
    exit_threshold = DecimalParameter(0.3, 3.0, default=2.0, decimals=1,
                                      space="sell", optimize=True)
    # prog percentyla fundingu, powyzej ktorego NIE wchodzimy
    funding_max_pct = IntParameter(50, 100, default=90, space="buy", optimize=True)

    _funding = None

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["ema"] = ta.EMA(dataframe, timeperiod=int(self.ema_period.value))
        dataframe["ema_exit"] = dataframe["ema"] * (100 - float(self.exit_threshold.value)) / 100

        if EmaCrossFunding._funding is None:
            EmaCrossFunding._funding = _load_funding()
        fr = EmaCrossFunding._funding

        idx = pd.to_datetime(dataframe["date"], utc=True)
        # ffill = ostatnia OPUBLIKOWANA wartosc, nigdy przyszla
        f = fr.reindex(idx, method="ffill")
        dataframe["funding_3d"] = f.rolling(72, min_periods=24).mean().to_numpy()
        # percentyl liczony na oknie kroczacym 180 dni - bez zagladania w przyszlosc
        dataframe["funding_pct"] = (
            pd.Series(dataframe["funding_3d"].to_numpy())
            .rolling(24 * 180, min_periods=24 * 30)
            .rank(pct=True) * 100
        ).to_numpy()
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (qtpylib.crossed_above(dataframe["close"], dataframe["ema"]))
            & (dataframe["volume"] > 0)
            & ((dataframe["funding_pct"] < float(self.funding_max_pct.value))
               | dataframe["funding_pct"].isna()),
            ["enter_long", "enter_tag"],
        ] = (1, "ema_cross_funding_ok")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            qtpylib.crossed_below(dataframe["close"], dataframe["ema_exit"]),
            ["exit_long", "exit_tag"],
        ] = (1, "ema_cross_down")
        return dataframe
