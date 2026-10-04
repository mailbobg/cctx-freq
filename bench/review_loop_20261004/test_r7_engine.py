"""Price neutrality must not silently pool spot gains into futures margin."""
import numpy as np
import pandas as pd
import pytest

from r7_dated import CarryLedger, cycles, simulate


def test_spot_purchase_debits_cash_and_future_short_adds_no_proceeds():
    book = CarryLedger(1000.)
    book.open([1.,0.], [100.,100.], [110.,110.], .001)
    assert book.spot_cash == pytest.approx(399.9)
    assert book.future.balance == pytest.approx(499.89)
    assert book.equity([150.,100.], [160.,110.]) == pytest.approx(999.79)
    assert book.future.equity([160.,110.]) == pytest.approx(449.89)
    assert book.future.quantity.tolist() == [-1.,0.]


def test_basis_convergence_and_final_fees_reconcile_realized_total():
    book = CarryLedger(1000.)
    book.open([1.,0.], [100.,100.], [110.,110.], .001)
    book.close([150.,100.], [150.,110.], .001)
    assert book.equity([150.,100.], [150.,110.]) == pytest.approx(1009.49)
    assert book.price_pnl == pytest.approx(10.)
    assert book.fees == pytest.approx(.51)
    assert book.spot_quantity.tolist() == [0.,0.]
    assert book.future.quantity.tolist() == [0.,0.]


def test_price_hedged_total_equity_does_not_prevent_futures_insolvency():
    book = CarryLedger(1000.)
    book.open([4.,0.], [100.,100.], [100.,100.], 0.)
    assert book.equity([300.,100.], [300.,100.]) == 1000.
    assert book.future.equity([300.,100.]) == -300.
    with pytest.raises(ValueError):
        book.repool()


def test_quarter_schedule_does_not_slide_entry_to_a_later_profitable_day():
    schedule = cycles('2021-02-01','2021-07-01')
    assert schedule[0] == (pd.Timestamp('2021-02-01',tz='UTC'),pd.Timestamp('2021-03-26 08:00',tz='UTC'))
    assert schedule[1] == (pd.Timestamp('2021-03-29',tz='UTC'),pd.Timestamp('2021-06-25 08:00',tz='UTC'))
    assert schedule[2] == (pd.Timestamp('2021-06-28',tz='UTC'),pd.Timestamp('2021-09-24 08:00',tz='UTC'))


def inputs():
    dates = pd.date_range('2023-12-31','2024-03-28',tz='UTC')
    spot = {a: pd.DataFrame(100., index=dates,columns=['open','high','low','close']) for a in ('BTCUSDT','ETHUSDT')}
    dated = {a+'_240329': pd.DataFrame(110.,index=dates,columns=['open','high','low','close']) for a in spot}
    return spot,dated


def test_same_day_future_signal_cannot_change_monday_entry():
    spot,dated=inputs()
    dates=next(iter(spot.values())).index
    a,events=simulate(spot,dated,'2024-01-01','2024-01-05',.0012,terminal=False)
    for frame in dated.values():
        frame.loc['2024-01-01':'2024-01-05','close']=99.
    b,changed=simulate(spot,dated,'2024-01-01','2024-01-05',.0012,terminal=False)
    assert events[0]['entered'] == changed[0]['entered']
    assert events[0]['quantities'] == pytest.approx(changed[0]['quantities'])
    assert events[0]['entered'] == ['enter','enter']
    full,_=simulate(spot,dated,'2024-01-01','2024-01-12',.0012,terminal=False)
    pd.testing.assert_frame_equal(b,full.loc[b.index])
    assert a.gross.iloc[0] <= .99 + 1e-8


def test_missing_active_contract_day_is_an_error_not_zero_pnl():
    spot,dated=inputs()
    dated['BTCUSDT_240329']=dated['BTCUSDT_240329'].drop(pd.Timestamp('2024-01-03',tz='UTC'))
    with pytest.raises(ValueError,match='active'):
        simulate(spot,dated,'2024-01-01','2024-01-05',.0012,terminal=False)


def test_confirmed_initial_unlisted_assets_remain_cash_for_the_cycle():
    dates=pd.date_range('2021-01-31','2021-02-12',tz='UTC')
    spot={a:pd.DataFrame(100.,index=dates,columns=['open','high','low','close']) for a in ('BTCUSDT','ETHUSDT')}
    curve,events=simulate(spot,{},'2021-02-01','2021-02-12',.0012,terminal=False)
    assert curve.equity.eq(10000.).all()
    assert events[0]['entered']==['confirmed_not_listed_at_decision']*2


def test_margin_concern_does_not_disappear_when_total_price_pnl_is_hedged():
    spot,dated=inputs()
    for frame in dated.values():
        frame.loc['2024-01-03','high']=300.
    curve,_=simulate(spot,dated,'2024-01-01','2024-01-05',.0012,terminal=False)
    assert curve.loc['2024-01-03','unresolved_margin_concern']
    assert curve.loc['2024-01-03','future_adverse_equity'] < 0
    assert curve.loc['2024-01-03','equity'] > 9900
