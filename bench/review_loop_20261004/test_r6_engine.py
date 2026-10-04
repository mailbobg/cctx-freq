"""Signed perpetual accounting and historical timing must hold before results."""
import numpy as np
import pandas as pd
import pytest

from r6_relative import PerpLedger, bounded_target, funding_marks, weights, simulate


def test_opening_short_does_not_credit_sale_proceeds():
    book = PerpLedger(1000.)
    book.trade_to([2., -3.], [100., 100.], .001)
    assert book.balance == pytest.approx(999.5)
    assert book.equity([100., 100.]) == pytest.approx(999.5)
    assert book.equity([110., 110.]) == pytest.approx(989.5)


def test_reductions_additions_and_flips_realize_only_closed_quantity():
    book = PerpLedger(1000.)
    book.trade_to([0., -4.], [100., 100.], 0.)
    book.trade_to([0., -2.], [100., 120.], 0.)
    assert book.balance == pytest.approx(960.)
    assert book.entry[1] == 100.
    book.trade_to([0., -4.], [100., 120.], 0.)
    assert book.entry[1] == 110.
    book.trade_to([0., 1.], [100., 130.], 0.)
    assert book.balance == pytest.approx(880.)
    assert book.entry[1] == 130.
    book.trade_to([0., 0.], [100., 140.], 0.)
    assert book.balance == pytest.approx(890.)


def test_positive_funding_pays_long_and_credits_short():
    book = PerpLedger(1000.)
    book.trade_to([2., -3.], [100., 100.], 0.)
    assert book.fund([100., 100.], [.01, .02]) == pytest.approx(4.)
    assert book.balance == pytest.approx(1004.)


def test_budget_accounts_for_fees_and_trade_mark_basis_jointly():
    book = PerpLedger(1000.)
    requested = np.array([5., -5.])
    trade, mark = np.array([100., 100.]), np.array([101., 99.])
    target = bounded_target(book, requested, trade, mark, .01)
    assert target[0] / requested[0] == pytest.approx(target[1] / requested[1])
    book.trade_to(target, trade, .01)
    assert np.abs(book.quantity) @ mark <= .99 * book.equity(mark) + 1e-8
    assert np.abs(book.quantity) @ mark > .98999 * book.equity(mark)


def test_missing_funding_marks_are_explicit_directional_sensitivities():
    associated = np.array([np.nan, 105.])
    # Only the missing mark is replaced. A receiving short uses LOW in adverse mode.
    assert funding_marks(associated, [1., -1.], [.01, .01], [100., 100.],
                         [110., 110.], [90., 90.], "adverse").tolist() == [110., 105.]
    assert funding_marks([np.nan, np.nan], [1., -1.], [.01, .01], [100., 100.],
                         [110., 110.], [90., 90.], "adverse").tolist() == [110., 90.]
    assert funding_marks([np.nan, np.nan], [1., -1.], [-.01, -.01], [100., 100.],
                         [110., 110.], [90., 90.], "favorable").tolist() == [110., 90.]


def inputs():
    dates = pd.date_range("2020-01-01", "2021-02-15", tz="UTC")
    t = np.arange(len(dates))
    close = pd.DataFrame({"BTCUSDT": 100 * np.exp(.002*t + .01*np.sin(t/3)),
                          "ETHUSDT": 100 * np.exp(.001*t + .01*np.sin(t/5))}, index=dates)
    trade = {name: pd.DataFrame({"open": close[name], "close": close[name]}, index=dates)
             for name in close}
    hours = pd.date_range("2021-02-01", "2021-02-16", freq="1h", inclusive="left", tz="UTC")
    mark = {name: pd.DataFrame(100., index=hours, columns=["open", "high", "low", "close"])
            for name in close}
    slots = hours[hours.hour % 8 == 0]
    fund = {name: pd.DataFrame({"slot": slots, "rate": .001, "mark": 100.}, index=slots)
            for name in close}
    return close, trade, mark, fund


def test_signal_and_execution_prefix_use_no_future_rows():
    close, trade, mark, fund = inputs()
    full = weights(close, "relative30")
    pd.testing.assert_frame_equal(full.loc[:"2021-02-07"], weights(close.loc[:"2021-02-07"], "relative30"))
    run = simulate(trade, mark, fund, full, "2021-02-01", "2021-02-15", .0012, terminal=False)
    part = simulate(trade, mark, fund, full, "2021-02-01", "2021-02-07", .0012, terminal=False)
    pd.testing.assert_frame_equal(run[0].loc[:"2021-02-07"], part[0])
    pd.testing.assert_frame_equal(run[1].loc[:"2021-02-07"], part[1])


def test_monday_zero_funding_uses_old_quantity_and_later_slots_use_new():
    close, trade, mark, fund = inputs()
    for frame in trade.values():
        frame.loc[:, :] = 100.
    prescribed = pd.DataFrame(np.tile([.4, -.2], (len(close), 1)), index=close.index, columns=close.columns)
    daily, hours = simulate(trade, mark, fund, prescribed, "2021-02-01", "2021-02-07", .001, terminal=True)
    assert hours.iloc[0].funding == 0.
    assert hours.loc["2021-02-01 08:00:00+00:00"].funding == pytest.approx(-1.98)
    assert hours.loc["2021-02-01 16:00:00+00:00"].funding == pytest.approx(-1.98)
    assert daily.equity.iloc[-1] == pytest.approx(10000 - 11.88 - 20 * 1.98)
    assert hours.gross.max() <= .99 + 1e-6


def test_missing_daily_close_blocks_thirty_complete_returns():
    close, _, _, _ = inputs()
    gap = close.index[-38]
    close.loc[gap, "ETHUSDT"] = np.nan
    result = weights(close, "relative30")
    assert result.loc[gap:gap + pd.Timedelta(days=30)].eq(0.).all().all()
    assert result.loc[gap + pd.Timedelta(days=31)].abs().sum() > 0
