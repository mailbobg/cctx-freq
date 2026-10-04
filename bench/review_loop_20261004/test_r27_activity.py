"""Native activity aggregates, source holes and causal identities."""
import pandas as pd
import pytest

from r22_sources import PANEL
from r27_activity import feature, overlay


MONDAY=pd.Timestamp('2021-02-01',tz='UTC')


def inputs(positive=4):
    series={symbol:{} for symbol in PANEL}
    for index,symbol in enumerate(PANEL):
        for day in pd.date_range('2020-12-01','2021-01-29',tz='UTC'):
            volume='1.0000000000000000'
            if index<positive and day>=pd.Timestamp('2020-12-31',tz='UTC'):volume='1.0000000000000001'
            series[symbol][day]=dict(volume=volume,quote_volume='1.0',trades=1,
                source_symbol=symbol,interval_end=day+pd.Timedelta(days=1),source_sha256='a'*64)
    return series


def test_exact_quantity_sum_avoids_float_equality_and_quote_value_confounding():
    series=inputs();f=feature(MONDAY,series)
    assert f['positive_count']==4 and f['allow']
    vote=f['native_votes'][0]
    assert len(vote['native_daily_volumes'])==60
    assert vote['current_volume_sum_decimal']=='30.0000000000000030'
    assert vote['prior_volume_sum_decimal']=='30.0000000000000000'
    for rows in series.values():
        for row in rows.values():row['quote_volume']='999999999.0'
    assert feature(MONDAY,series)==f
    assert f['available_at_assumed']==pd.Timestamp('2021-01-31',tz='UTC')


def test_equality_is_known_negative_not_missing():
    f=feature(MONDAY,inputs(0))
    assert f['known'] and not f['allow'] and f['positive_count']==0
    assert all(not v['positive'] for v in f['native_votes'])


def test_native_prefix_ignores_actual_future_rows():
    short=inputs();full=inputs()
    for symbol,rows in full.items():
        rows[pd.Timestamp('2021-02-02',tz='UTC')]=dict(volume='999999',quote_volume='999999',trades=1,
            source_symbol=symbol,interval_end=pd.Timestamp('2021-02-03',tz='UTC'),source_sha256='b'*64)
    assert feature(MONDAY,full)==feature(MONDAY,short)


@pytest.mark.parametrize('damage',[
    lambda s:s.pop(PANEL[0]),
    lambda s:s[PANEL[0]].pop(pd.Timestamp('2020-12-02',tz='UTC')),
    lambda s:s[PANEL[0]][pd.Timestamp('2021-01-29',tz='UTC')].update(source_symbol='BTCUSDT'),
    lambda s:s[PANEL[0]][pd.Timestamp('2021-01-29',tz='UTC')].update(interval_end=MONDAY),
    lambda s:s[PANEL[0]][pd.Timestamp('2021-01-29',tz='UTC')].update(source_sha256=''),
    lambda s:s[PANEL[0]][pd.Timestamp('2021-01-29',tz='UTC')].update(volume='-1'),
    lambda s:s[PANEL[0]][pd.Timestamp('2021-01-29',tz='UTC')].update(trades=0),
])
def test_unknown_foreign_future_or_inactive_source_never_becomes_a_vote(damage):
    series=inputs();damage(series)
    with pytest.raises(ValueError):feature(MONDAY,series)


def test_post_covariance_targets_and_declared_sum_or_count_corruption():
    sunday=MONDAY-pd.Timedelta(days=1);base=pd.DataFrame({'BTC':[.08],'ETH':[.03]},index=pd.DatetimeIndex([sunday]))
    f=feature(MONDAY,inputs());out,_=overlay(base,{MONDAY:f});pd.testing.assert_frame_equal(out,base)
    no=feature(MONDAY,inputs(3));out,_=overlay(base,{MONDAY:no});assert (out==0).all().all()
    with pytest.raises(ValueError):overlay(base,{MONDAY:dict(f,positive_count=5)})
    changed=feature(MONDAY,inputs());changed['native_votes'][0]['current_volume_sum_decimal']='31.0'
    with pytest.raises(ValueError):overlay(base,{MONDAY:changed})
    with pytest.raises(ValueError):overlay(base,{})
