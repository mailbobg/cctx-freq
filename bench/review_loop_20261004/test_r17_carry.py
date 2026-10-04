from decimal import Decimal, localcontext

import numpy as np
import pandas as pd
import pytest

from r17_carry import FundingSeries, PairedLedger, funding_forecast, select_pairs


def funding(rate='0.0001', periods=40, hours=8):
    times = pd.date_range('2023-12-20', periods=periods, freq=f'{hours}h', tz='UTC')
    frame = pd.DataFrame({'rate_decimal':rate, 'interval_hours':hours, 'slot':times}, index=times)
    return FundingSeries(frame, times[0], times[-1]+pd.Timedelta(hours=hours))


def test_exact_sum_threshold_and_symbol_ties():
    decision = pd.Timestamp('2024-01-01T01:00:00Z')
    series = funding(rate='0.0003')
    score = funding_forecast(series, decision)
    assert score == Decimal('0.0063')  # 21 actual rates, not a floating mean.
    assert select_pairs({'BBB':score,'AAA':score,'CCC':Decimal('0.0058')}) == ('AAA','BBB')
    assert select_pairs({'AAA':Decimal('0.005800000000000000001')}) == ('AAA',)
    assert select_pairs({'AAA':Decimal('0.0058'),'BBB':Decimal('-0.01')}) == ()


def test_one_hour_availability_weekly_endpoints_and_future_rate_exclusion():
    series = funding(rate='0.0003')
    decision = pd.Timestamp('2024-01-01T01:00:00Z')
    frame = series.events.copy()
    lower = pd.Timestamp('2023-12-25T00:00:00Z')
    frame.loc[lower, 'rate_decimal'] = '10'  # Open left endpoint, excluded.
    frame.loc[pd.Timestamp('2024-01-01T08:00:00Z'), 'rate_decimal'] = '10'
    changed = FundingSeries(frame, series.coverage_start, series.coverage_end)
    assert funding_forecast(changed,decision) == Decimal('0.0063')
    with pytest.raises(ValueError, match='Monday01'):
        funding_forecast(series, decision-pd.Timedelta(hours=1))
    frame.loc[pd.Timestamp('2024-01-01T00:00:00Z'), 'rate_decimal'] = '0.0004'
    assert funding_forecast(FundingSeries(frame,series.coverage_start,series.coverage_end),decision) == Decimal('0.0064')


def test_variable_frequency_sums_cash_flows_instead_of_mean_event_rate():
    times = pd.date_range('2023-12-20', '2024-01-02', freq='4h', tz='UTC')
    frame = pd.DataFrame({'rate_decimal':'0.00015','interval_hours':4,'slot':times},index=times)
    series = FundingSeries(frame,times[0],times[-1]+pd.Timedelta(hours=4))
    assert funding_forecast(series,'2024-01-01T01:00:00Z') == Decimal('0.00630')
    # Actual declared switch 8h ->4h must follow the current settlement's interval.
    times = pd.DatetimeIndex(list(pd.date_range('2023-12-20','2023-12-29',freq='8h',tz='UTC'))+
                            list(pd.date_range('2023-12-29T04:00:00Z','2024-01-02T00:00:00Z',freq='4h')))
    hours = [8 if t <= pd.Timestamp('2023-12-29',tz='UTC') else 4 for t in times]
    frame = pd.DataFrame({'rate_decimal':'0.0001','interval_hours':hours,'slot':times},index=times)
    series = FundingSeries(frame,times[0],times[-1]+pd.Timedelta(hours=4))
    assert funding_forecast(series,'2024-01-01T01:00:00Z') == Decimal('0.003')


def test_funding_gaps_duplicates_bad_offsets_or_insufficient_coverage_fail():
    series=funding();frame=series.events.copy()
    with pytest.raises(ValueError,match='calendar'):
        FundingSeries(frame.drop(frame.index[15]),series.coverage_start,series.coverage_end)
    with pytest.raises(ValueError,match='calendar'):
        FundingSeries(pd.concat([frame,frame.iloc[[0]]]),series.coverage_start,series.coverage_end)
    frame.index = frame.index+pd.Timedelta(seconds=2)
    with pytest.raises(ValueError,match='offset'):
        FundingSeries(frame,series.coverage_start,series.coverage_end)
    with pytest.raises(ValueError,match='coverage'):
        funding_forecast(series,'2024-01-08T01:00:00Z')


@pytest.mark.parametrize('bad',['NaN','Infinity',0.001,None])
def test_funding_decimal_input_rejects_unknown_or_nonfinite(bad):
    with pytest.raises(ValueError,match='decimal'):
        funding(rate=bad)


def test_funding_full_prefix_equivalence():
    series=funding(periods=70)
    decision=pd.Timestamp('2024-01-01T01:00:00Z')
    prefix=series.events.loc[series.events.slot<=decision-pd.Timedelta(hours=1)]
    cut=FundingSeries(prefix,series.coverage_start,decision)
    assert funding_forecast(series,decision)==funding_forecast(cut,decision)


def test_exact_ranking_does_not_round_under_callers_decimal_context():
    with localcontext() as context:
        context.prec=6
        values={'AAA':Decimal('0.006000000000000000000000000001'),
                'BBB':Decimal('0.006000000000000000000000000002'),
                'CCC':Decimal('0.006000000000000000000000000003')}
        assert select_pairs(values)==('CCC','BBB')


def test_matched_native_hedge_and_actual_mark_funding_hand_oracle():
    book=PairedLedger(10000,1)
    book.open([10],[100],[101],[101],.001)
    assert book.equity([100],[101])==pytest.approx(9997.99)
    assert book.spot_cash==pytest.approx(3999.) and book.future.balance==pytest.approx(4998.99)
    assert np.array_equal(book.spot_quantity,-book.future.quantity)
    assert book.fund([101],[.01])==pytest.approx(10.1)
    # Same100 movement in both prices cancels, despite each sleeve's large P&L.
    assert book.equity([200],[201])==pytest.approx(10008.09)
    book.close([200],[201],.001)
    assert book.equity([200],[201])==pytest.approx(10004.08)
    assert book.fees==pytest.approx(6.02) and book.price_pnl==pytest.approx(0.)
    assert book.funding==pytest.approx(10.1)
    assert book.reconciliation()==pytest.approx(0.)


def test_basis_loss_does_not_cancel_and_negative_funding_is_payment():
    book=PairedLedger(10000,1);book.open([10],[100],[100],[100],0.)
    assert book.fund([120],[-.01])==pytest.approx(-12.)
    assert book.equity([110],[120])==pytest.approx(9888.)
    book.close([110],[120],0.)
    assert book.price_pnl==pytest.approx(-100.) and book.reconciliation()==pytest.approx(0.)


def test_separate_margin_can_fail_despite_positive_total_hedged_equity():
    book=PairedLedger(10000,1);book.open([40],[100],[100],[100],0.)
    assert book.equity([230],[230])==pytest.approx(10000.)
    assert book.future.equity([230])==-200.
    assert book.margin_concern([230])
    with pytest.raises(ValueError,match='held'):
        book.repool()


def test_paired_entry_checks_cash_native_quantity_and_postfill_marked_gross():
    book=PairedLedger(10000,2)
    with pytest.raises(ValueError,match='cash'):
        book.open([30,30],[100,100],[100,100],[100,100],.001)
    with pytest.raises(ValueError,match='quantity'):
        book.open([-1,0],[100,100],[100,100],[100,100],.001)
    # The contract trade price and risk mark are separate. Reserve can be insufficient.
    with pytest.raises(ValueError,match='marked'):
        book.open([49,0],[100,100],[100,100],[110,100],.001)
    assert book.spot_cash==5000 and book.future.balance==5000


def test_four_costs_every_week_and_realized_cash_only_repool():
    book=PairedLedger(10000,1);book.open([10],[100],[100],[100],.001)
    book.close([100],[100],.001);book.repool()
    assert book.spot_cash==book.future.balance==4998
    book.open([10],[100],[100],[100],.001);book.close([100],[100],.001)
    assert book.equity([100],[100])==9992 and book.fees==8
    assert book.reconciliation()==0


def test_initial_cash_funding_unknown_held_mark_cannot_be_imputed():
    book=PairedLedger(10000,2)
    assert book.fund([np.nan,100],[.1,.1])==0
    book.open([1,0],[100,100],[100,100],[100,100],0)
    with pytest.raises(ValueError,match='mark'):
        book.fund([np.nan,100],[.1,.1])
