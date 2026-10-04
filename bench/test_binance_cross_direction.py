"""Cross collateral must use the actual direction of every other position."""
from types import SimpleNamespace

import pytest

from freqtrade.enums import MarginMode, RunMode, TradingMode
from freqtrade.exchange.binance import Binance


def exchange(mode):
    instance = object.__new__(Binance)
    instance.close = lambda: None
    instance._config = {"runmode": mode}
    instance.margin_mode = MarginMode.CROSS
    instance.trading_mode = TradingMode.FUTURES
    instance.get_maintenance_ratio_and_amt = lambda pair, stake: (.005, 0.)
    instance.fetch_funding_rates = lambda pairs: {"ETH/USDT:USDT": {"markPrice": 120.}}
    return instance


@pytest.mark.parametrize("mode", [RunMode.DRY_RUN, RunMode.LIVE])
@pytest.mark.parametrize("own_short,other_short,expected", [
    (True, True, 179.4 / 1.005),
    (True, False, 219.4 / 1.005),
    (False, True, 20.6 / .995),
    (False, False, -19.4 / .995),
])
def test_other_short_loss_is_not_credited_as_cross_collateral(mode, own_short, other_short, expected):
    ex = exchange(mode)
    other = SimpleNamespace(pair="ETH/USDT:USDT", amount=1., open_rate=100.,
                            stake_amount=50., is_short=other_short)
    actual = ex.dry_run_liquidation_price("BTC/USDT:USDT", 100., own_short, 1., 50., 2., 100., [other])
    assert actual == pytest.approx(expected)


def test_historical_open_price_fallback_is_unchanged_and_never_fetches():
    ex = exchange(RunMode.BACKTEST)
    def forbidden(pairs):
        raise AssertionError("Historical calculation must not fetch today's prices")
    ex.fetch_funding_rates = forbidden
    other = SimpleNamespace(pair="ETH/USDT:USDT", amount=1., open_rate=100.,
                            stake_amount=50., is_short=True)
    assert ex.dry_run_liquidation_price("BTC/USDT:USDT", 100., True, 1., 50., 2., 100., [other]) == pytest.approx(199.5 / 1.005)
