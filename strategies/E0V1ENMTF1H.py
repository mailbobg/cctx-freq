"""E0V1ENMTF1H — 1h dip-buy with 1d regime and BTC-leader gates with signal-weighted sizing and a crash guard.

Paradigm: mean-reversion
Hypothesis: on 1h crypto majors, flushes that trigger BOTH a slow-RSI downturn
  and a fast-RSI washout (double-signal consensus) snap back harder than single
  triggers; sizing the consensus 1.5x and letting buy_new winners run beats
  equal-weight. A portfolio entry-rate cap plus a per-pair stop lock contains
  crash-leg damage (FTX Nov-2022 style: whole book filled in one leg).
Parent: E0V1ENWTA2H1 (fork: add 1d-regime + BTC-waterfall gates)
Created: 2026-10-05
Status: active
Uses MTF: yes (1d regime, 1h BTC cross-pair)

Port notes (from the 5m original):
- 24h change window 288 bars -> 24 bars; CooldownPeriod 96 -> 8 candles (8h);
  StoplossGuard 288/288 -> 24/24 (24h). Entry cap stays time-based (2/60min).
- CTI reimplemented with pandas (no pandas_ta dependency).
  Doubles (buy_1buy_new) hold +2.05%/trade even in the 2022 winter while pure
  buy_new bleeds -0.14% there (170 of 241 winter trades). So in a 1h
  medium-term downtrend (close < SMA200) only buy_1/doubles may enter;
  buy_new-only entries are allowed solely in non-bear candles.
- pair_basket is 3 of the 5 whitelist pairs: ETH is pruned (negative expectancy
  in EVERY regime: -0.03%/trade over 133 trades full-5y, -580 in winter) and BNB
  is excluded only because no 2024+ 1h data was available where this was
  validated; re-add when present.
- Single self-contained file: no local imports (the host harness treats every
  non-underscore .py as a strategy and requires class == filename).
"""

from datetime import datetime, timedelta
from functools import reduce
import warnings

import numpy as np
import talib.abstract as ta
from pandas import DataFrame

from freqtrade.persistence import Trade
from freqtrade.strategy import DecimalParameter, IntParameter, IStrategy, informative

warnings.simplefilter(action="ignore", category=RuntimeWarning)


def _cti(close, length: int = 20):
    """Correlation Trend Indicator: rolling correlation of price vs time index."""
    x = np.arange(length, dtype=float)

    def corr(y):
        if np.std(y) <= 0:
            return 0.0
        return float(np.corrcoef(x, y)[0, 1])

    return close.rolling(length).apply(corr, raw=True)


class E0V1ENMTF1H(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "1h"
    startup_candle_count = 400
    process_only_new_candles = True

    minimal_roi = {"0": 1}
    stoploss = -0.25
    trailing_stop = True
    trailing_stop_positive = 0.002
    trailing_stop_positive_offset = 0.03
    trailing_only_offset_is_reached = True

    order_types = {
        "entry": "market",
        "exit": "market",
        "emergency_exit": "market",
        "force_entry": "market",
        "force_exit": "market",
        "stoploss": "market",
        "stoploss_on_exchange": False,
    }

    # ---- Auto-Quant harness affordances ----
    pair_basket = ["BTC/USDT", "SOL/USDT", "AVAX/USDT"]
    test_timeranges = [
        ("bull_2021", "20210101-20211231"),
        ("winter_2022", "20220101-20221231"),
        ("recovery_23_25", "20230101-20251231"),
        ("full_5y", "20210101-20251231"),
    ]

    # ---- entry parameters (same defaults as the 5m lineage) ----
    is_optimize_32 = False
    buy_rsi_fast_32 = IntParameter(20, 70, default=40, space="buy", optimize=is_optimize_32)
    buy_rsi_32 = IntParameter(15, 50, default=42, space="buy", optimize=is_optimize_32)
    buy_sma15_32 = DecimalParameter(0.900, 1, default=0.973, decimals=3, space="buy", optimize=is_optimize_32)
    buy_cti_32 = DecimalParameter(-1, 1, default=0.69, decimals=2, space="buy", optimize=is_optimize_32)
    buy_24h_min_pct = DecimalParameter(-30.0, 0.0, default=-15.0, decimals=1, space="buy", optimize=True)
    buy_24h_max_pct = DecimalParameter(0.0, 200.0, default=50.0, decimals=1, space="buy", optimize=True)
    buy_24h_min_pct1 = DecimalParameter(-30.0, 0.0, default=-15.0, decimals=1, space="buy", optimize=True)
    buy_24h_max_pct1 = DecimalParameter(0.0, 200.0, default=50.0, decimals=1, space="buy", optimize=True)

    sell_fastx_buynew = IntParameter(85, 98, default=91, space="sell", optimize=True)

    # ---- sizing / guard ----
    stake_double = 1.5
    max_entries = 2
    window_minutes = 60
    max_volume_share = 0.10
    min_notional = 10.0

    @property
    def protections(self):
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 8},
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 24,
                "trade_limit": 1,
                "stop_duration_candles": 24,
                "only_per_pair": True,
            },
        ]

    # ---- sizing helpers (phantom-fill cap, inlined: no local imports) ----
    def _fillable(self, pair: str, current_time: datetime) -> float:
        df, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if df is None or df.empty:
            return 0.0
        closed = df[df["date"] < current_time]
        if closed.empty:
            return 0.0
        row = closed.iloc[-1]
        return float(row["volume"] * row["close"]) * self.max_volume_share

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: float | None, max_stake: float,
                            leverage: float, entry_tag: str | None, side: str, **kwargs) -> float:
        tag = str(entry_tag or "")
        mult = self.stake_double if ("buy_1" in tag and "buy_new" in tag) else 1.0
        stake = proposed_stake * mult
        cap = self._fillable(pair, current_time)
        capped = min(stake, cap)
        floor = max(self.min_notional, min_stake or 0.0)
        return capped if capped >= floor else 0.0

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time: datetime, entry_tag: str | None,
                            side: str, **kwargs) -> bool:
        since = current_time - timedelta(minutes=self.window_minutes)
        recent = sum(1 for t in Trade.get_trades_proxy() if t.open_date_utc > since)
        if recent >= self.max_entries:
            return False
        return True

    # ---- signals ----
    @informative("1d")
    def populate_indicators_1d(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # merged as sma50_1d: medium trend of the same pair on daily
        dataframe["sma50"] = ta.SMA(dataframe, timeperiod=50)
        return dataframe

    @informative("1h", "BTC/USDT")
    def populate_indicators_btc(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # merged as btc_usdt_chg_168_1h: BTC trailing-week change, the leader
        dataframe["chg_168"] = dataframe["close"].pct_change(periods=168) * 100
        return dataframe

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["sma_15"] = ta.SMA(dataframe, timeperiod=15)
        dataframe["cti"] = _cti(dataframe["close"], length=20)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["rsi_fast"] = ta.RSI(dataframe, timeperiod=4)
        dataframe["rsi_slow"] = ta.RSI(dataframe, timeperiod=20)
        dataframe["24h_change_pct"] = dataframe["close"].pct_change(periods=24) * 100
        stoch_fast = ta.STOCHF(dataframe, 5, 3, 0, 3, 0)
        dataframe["fastk"] = stoch_fast["fastk"]
        dataframe["cci"] = ta.CCI(dataframe, timeperiod=20)
        dataframe["sma_200"] = ta.SMA(dataframe, timeperiod=200)
        # NOTE (2026-10-05): tried restricting buy_new only in waterfall
        # candles (below-trend AND -5%/week) instead of all below-trend
        # candles. Winter fell +14.4% -> +12.6%: re-admitted crab-bear
        # buy_new also bleeds. Strict below-trend gate kept.
        dataframe["bear"] = dataframe["close"] < dataframe["sma_200"]
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        conditions = []
        dataframe.loc[:, "enter_tag"] = ""
        buy_1 = (
            (dataframe["rsi_slow"] < dataframe["rsi_slow"].shift(1))
            & (dataframe["rsi_fast"] < self.buy_rsi_fast_32.value)
            & (dataframe["rsi"] > self.buy_rsi_32.value)
            & (dataframe["close"] < dataframe["sma_15"] * self.buy_sma15_32.value)
            & (dataframe["cti"] < self.buy_cti_32.value)
            & (dataframe["24h_change_pct"] > self.buy_24h_min_pct1.value)
            & (dataframe["24h_change_pct"] < self.buy_24h_max_pct1.value)
        )
        buy_new = (
            (dataframe["rsi_slow"] < dataframe["rsi_slow"].shift(1))
            & (dataframe["rsi_fast"] < 34)
            & (dataframe["rsi"] > 28)
            & (dataframe["close"] < dataframe["sma_15"] * 0.96)
            & (dataframe["cti"] < self.buy_cti_32.value)
            & (dataframe["24h_change_pct"] > self.buy_24h_min_pct.value)
            & (dataframe["24h_change_pct"] < self.buy_24h_max_pct.value)
        )
        conditions.append(buy_1)
        dataframe.loc[buy_1, "enter_tag"] += "buy_1"
        # bear regime: only buy_1 (and its doubles) may enter; buy_new-only
        # entries bleed in grind-down markets (winter 2022: -0.14%/trade).
        # MTF layer: buy_new-only additionally needs the daily trend up AND
        # no BTC waterfall (leader crash = alt liquidity vacuum).
        bull_1d = (dataframe["close"] > dataframe["sma50_1d"]).fillna(True)
        btc_ok = (dataframe["btc_usdt_chg_168_1h"] > -15.0).fillna(True)
        buy_new_ok = buy_new & (~dataframe["bear"].fillna(False) | buy_1) & bull_1d & btc_ok
        conditions.append(buy_new_ok)
        dataframe.loc[buy_new_ok, "enter_tag"] += "buy_new"
        if conditions:
            dataframe.loc[reduce(lambda x, y: x | y, conditions), "enter_long"] = 1
        return dataframe

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

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[:, ["exit_long", "exit_tag"]] = (0, "long_out")
        return dataframe
