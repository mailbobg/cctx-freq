"""Offline regression tests; no exchange calls or live databases."""
import sys
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'strategies'))
from RiskHoldC import RiskHoldC

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


class RiskRegression(unittest.TestCase):
    def test_mtm_uses_fill_notional_and_each_side_fee_once(self):
        from mtm import equity_curve
        days = pd.date_range('2026-01-01', periods=2, tz='UTC')
        orders = [dict(amount=1., safe_price=100., cost=100.1, ft_order_side='buy',
                       order_filled_timestamp=days[0].value // 10**6),
                  dict(amount=1., safe_price=110., cost=110.11, ft_order_side='sell',
                       order_filled_timestamp=days[1].value // 10**6)]
        trades = pd.DataFrame([dict(pair='BTC/USDT', fee_open=.001, fee_close=.002,
                                    is_short=False, leverage=1., orders=orders)])
        with patch('mtm.daily_closes', return_value=pd.DataFrame({'BTC/USDT': [100., 110.]}, index=days)):
            eq, _, _ = equity_curve(trades, 1000., days)
        self.assertAlmostEqual(eq.iloc[-1], 1009.68)

    def test_export_naive_boundaries_match_utc_trade_dates(self):
        from evaluate import daily_realized_pnl
        trades = pd.DataFrame({'close_date': pd.to_datetime(['2026-01-02'], utc=True), 'profit_abs': [5.]})
        self.assertEqual(daily_realized_pnl(trades, '2026-01-01', '2026-01-03').tolist(), [0., 5., 0.])

    def test_equity_backtest_uses_only_analyzed_prices(self):
        s = RiskHoldC({'stake_currency': 'USDT'})
        s.wallets = SimpleNamespace(get_total=lambda currency: 500.)
        s.dp = SimpleNamespace(runmode=SimpleNamespace(value='backtest'),
                               get_analyzed_dataframe=lambda *args: (pd.DataFrame({'close': [100.]}), None))
        t = SimpleNamespace(pair='BTC/USDT', amount=10.)
        with patch('RiskHoldC.Trade.get_trades_proxy', return_value=[t]):
            self.assertEqual(s._equity(), 1500.)

    def test_already_rebalanced_week_is_not_traded_again(self):
        s = RiskHoldC({})
        s._target = lambda pair: .5
        t = SimpleNamespace(pair='BTC/USDT', date_last_filled_utc=NOW)
        self.assertIsNone(s.adjust_trade_position(t, NOW, 100., .2, 10., 1000., 100., 100., .2, .2))

    def test_block_interval_keeps_zero_pnl_days(self):
        from block_bootstrap import block_interval
        self.assertEqual(block_interval([1.] * 30, 7), (30., 30.))

    def test_portfolio_weights_do_not_see_future_prices(self):
        from portfolio_risk import portfolio_weights
        import numpy as np
        dates = pd.date_range('2025-01-01', periods=80, tz='UTC')
        prices = pd.DataFrame({'BTC': np.exp(np.arange(80) * .01),
                               'ETH': np.exp(np.arange(80) * .005)}, index=dates)
        first = portfolio_weights(prices.iloc[:60])
        prices.iloc[60:] *= 10
        pd.testing.assert_frame_equal(first, portfolio_weights(prices).iloc[:60])

    def test_portfolio_cap_accounts_for_perfect_correlation(self):
        from portfolio_risk import cap_weights
        import numpy as np
        result = cap_weights(np.array([.5, .5]), np.array([[.36, .36], [.36, .36]]))
        self.assertAlmostEqual(result.sum(), 2 / 3)

    def test_portfolio_cap_never_increases_small_allocations(self):
        from portfolio_risk import cap_weights
        import numpy as np
        result = cap_weights(np.array([.1, 0]), np.eye(2) * .01)
        self.assertEqual(result.tolist(), [.1, 0.])

    def test_bounded_dip_exits_deep_loser_at_deadline(self):
        from E0V1ENBounded import E0V1ENBounded
        s = E0V1ENBounded({})
        t = SimpleNamespace(open_date_utc=NOW-timedelta(hours=24))
        self.assertEqual(s.custom_exit('BTC/USDT', t, NOW, 100., -.09), 'max_hold_24h')

    def test_x8_refuses_loss_adds_but_preserves_trims(self):
        from NostalgiaForInfinityX8Budget import NostalgiaForInfinityX8Budget
        from NostalgiaForInfinityX8PF import NostalgiaForInfinityX8PF
        s = object.__new__(NostalgiaForInfinityX8Budget)
        s.wallets = SimpleNamespace(get_total_stake_amount=lambda: 10000.)
        t = SimpleNamespace(stake_amount=1000.)
        args = (t, NOW, 100., -.06, 10., 1000., 100., 100., -.06, -.06)
        with patch.object(NostalgiaForInfinityX8PF, 'adjust_trade_position', return_value=(200., 'add')):
            self.assertIsNone(s.adjust_trade_position(*args))
        with patch.object(NostalgiaForInfinityX8PF, 'adjust_trade_position', return_value=(-200., 'trim')):
            self.assertEqual(s.adjust_trade_position(*args), (-200., 'trim'))

    def test_x8_caps_add_at_remaining_budget(self):
        from NostalgiaForInfinityX8Budget import NostalgiaForInfinityX8Budget
        from NostalgiaForInfinityX8PF import NostalgiaForInfinityX8PF
        s = object.__new__(NostalgiaForInfinityX8Budget)
        s.wallets = SimpleNamespace(get_total_stake_amount=lambda: 10000.)
        t = SimpleNamespace(stake_amount=1400.)
        args = (t, NOW, 100., -.01, 10., 1000., 100., 100., -.01, -.01)
        with patch.object(NostalgiaForInfinityX8PF, 'adjust_trade_position', return_value=(200., 'add')):
            self.assertEqual(s.adjust_trade_position(*args), (100., 'add'))

    def test_trim_sells_half_not_all_after_price_doubles(self):
        s = RiskHoldC({})
        s._target = lambda pair: 0.5
        s._equity = lambda *args: 1000.0
        t = SimpleNamespace(pair='BTC/USDT', amount=10., stake_amount=500.,
                            date_last_filled_utc=NOW-timedelta(days=7))
        stake, _ = s.adjust_trade_position(t, NOW, 100., 1., 10., 1000., 100., 100., 1., 1.)
        self.assertEqual(abs(stake) * t.amount / t.stake_amount, 5.)

    def test_trim_cost_conversion_also_works_below_entry_price(self):
        s = RiskHoldC({})
        s._target = lambda pair: .5
        s._equity = lambda *args: 400.
        t = SimpleNamespace(pair='BTC/USDT', amount=10., stake_amount=1000.,
                            date_last_filled_utc=NOW-timedelta(days=7))
        stake, _ = s.adjust_trade_position(t, NOW, 50., -.5, 10., 1000., 50., 50., -.5, -.5)
        self.assertEqual(abs(stake) * t.amount / t.stake_amount, 6.)

    def test_missing_price_skips_rebalance(self):
        s = RiskHoldC({})
        s._target = lambda pair: .5
        s._equity = lambda *args: None
        t = SimpleNamespace(pair='BTC/USDT', date_last_filled_utc=NOW-timedelta(days=7))
        self.assertIsNone(s.adjust_trade_position(t, NOW, 50., -.5, 10., 1000., 50., 50., -.5, -.5))

    def test_equity_uses_callback_quote_for_current_position(self):
        s = RiskHoldC({'stake_currency': 'USDT'})
        s.wallets = SimpleNamespace(get_total=lambda currency: 500.)
        # No data provider: a network lookup here would be a bug.
        t = SimpleNamespace(pair='BTC/USDT', amount=10.)
        with patch('RiskHoldC.Trade.get_trades_proxy', return_value=[t]):
            self.assertEqual(s._equity('BTC/USDT', 90.), 1400.)

    def test_equity_includes_unrealized_profit(self):
        s = RiskHoldC({'stake_currency': 'USDT', 'tradable_balance_ratio': 0.99})
        s.wallets = SimpleNamespace(get_total=lambda currency: 500., get_total_stake_amount=lambda: 990.)
        s.dp = SimpleNamespace(runmode=SimpleNamespace(value='dry_run'), ticker=lambda pair: {'bid': 100.})
        t = SimpleNamespace(pair='BTC/USDT', amount=10., stake_amount=500.)
        with patch('RiskHoldC.Trade.get_trades_proxy', return_value=[t]):
            self.assertEqual(s._equity(), 1485.)

    def test_missing_price_blocks_entry_instead_of_sizing_from_cost(self):
        s = RiskHoldC({'stake_currency': 'USDT'})
        s._target = lambda pair: 0.5
        s.wallets = SimpleNamespace(get_total=lambda currency: 500., get_total_stake_amount=lambda: 1000.)
        s.dp = SimpleNamespace(runmode=SimpleNamespace(value='dry_run'), ticker=lambda pair: {})
        t = SimpleNamespace(pair='ETH/USDT', amount=10., stake_amount=500.)
        with patch('RiskHoldC.Trade.get_trades_proxy', return_value=[t]):
            self.assertEqual(s.custom_stake_amount('BTC/USDT', NOW, 100., 500., 10., 1000., 1., None, 'long'), 0.)


if __name__ == '__main__':
    unittest.main()
