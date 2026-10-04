"""E0V1ENWTA: E0V1EN + win-trade-attribution sizing/exits (experiment, not live).

Grounded in 2026-01~09 full-universe attribution (1412 trades):
  * buy_1buy_new (double-signal consensus): +0.719%/trade -> 2x stake.
  * buy_1: +0.244%/trade -> 1x stake.
  * buy_new: 70% winrate but +0.092%/trade, profits choked by the fastk
    profit-taker -> 0.5x stake AND relaxed fastk exit (91 vs 84).

Entries, stoploss, trailing, protections, ROI: identical to E0V1EN.
Tune window: 2026-01~09. Validate on user_data/data_holdout (2020~2023).
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from E0V1EN import E0V1EN  # noqa: E402
from freqtrade.persistence import Trade  # noqa: E402
from freqtrade.strategy import IntParameter  # noqa: E402
from phantom_fill import PhantomFillMixin  # noqa: E402


class E0V1ENWTA(PhantomFillMixin, E0V1EN):
    # tag -> stake multiplier (double-signal consensus gets paid most)
    stake_double = 2.0
    stake_buy1 = 1.0
    stake_buynew = 1.0  # v2: never under-invest (0.5x idled capital and
    # lagged compounding in strong years); edge tilt comes from 2x doubles only

    sell_fastx_buynew = IntParameter(85, 98, default=91, space="sell", optimize=True)

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float | None, max_stake: float,
                            leverage: float, entry_tag: str | None, side: str, **kwargs) -> float:
        tag = str(entry_tag or "")
        if "buy_1" in tag and "buy_new" in tag:
            mult = self.stake_double
        elif "buy_new" in tag:
            mult = self.stake_buynew
        else:
            mult = self.stake_buy1
        return self._cap(proposed_stake * mult, pair, current_time, min_stake)

    def custom_exit(self, pair: str, trade: 'Trade', current_time: 'datetime', current_rate: float,
                    current_profit: float, **kwargs):
        dataframe, _ = self.dp.get_analyzed_dataframe(pair=pair, timeframe=self.timeframe)
        current_candle = dataframe.iloc[-1].squeeze()

        if current_profit > 0 and str(trade.enter_tag) == "buy_new":
            if current_candle["fastk"] > self.sell_fastx_buynew.value:
                return "fastk_profit_sell_relaxed"

        if current_profit > -0.03:
            if current_candle["cci"] > 80:
                return "cci_loss_sell"

        if current_time - timedelta(hours=7) > trade.open_date_utc:
            if current_profit >= -0.05:
                return "time_loss_sell_7_5"

        if current_time - timedelta(hours=10) > trade.open_date_utc:
            if current_profit >= -0.1:
                return "time_loss_sell_10_10"

        return None
