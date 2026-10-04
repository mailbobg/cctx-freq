"""Counterpart interval/unit precision; no source-clock or alpha fitting."""
from decimal import Decimal

import pandas as pd
import pytest

from r24_reconcile import parse_5m,ratio_comparison,oi_volume_comparison


def candle(day='2026-06-24 00:00:00',volume='10.000',buy='6.000',quote='1000.00',buy_quote='600.00'):
    t=pd.Timestamp(day,tz='UTC').value//10**6
    return ','.join([str(t),'100','101','99','100',volume,str(t+300000-1),quote,'10',buy,buy_quote,'0']).encode()+b'\n'


def test_native_complete_window_and_decimal_fields():
    rows=parse_5m(candle(),'2026-06-24');r=next(iter(rows.values()))
    assert r['volume']==Decimal('10.000') and r['taker_buy_volume']==Decimal('6.000')
    assert r['end']==pd.Timestamp('2026-06-24 00:05:00',tz='UTC')


@pytest.mark.parametrize('body',[candle(buy='11'),candle(volume='-1'),candle().replace(b',600.00,0',b',1001.00,0'),candle()+candle(),candle().replace(b',10,6.000,',b',0,6.000,')])
def test_invalid_or_duplicate_native_volume_halts(body):
    with pytest.raises(ValueError):parse_5m(body,'2026-06-24')


def test_ratio_uses_both_fixed_windows_without_shifting_original_metric():
    prices={**parse_5m(candle('2026-06-23 23:55:00',buy='4'),'2026-06-23'),**parse_5m(candle(),'2026-06-24')}
    metric=dict(create_time='2026-06-24 00:00:00',sum_taker_long_short_vol_ratio='1.50000000')
    c=ratio_comparison(metric,prices)
    assert c['source_label_unchanged']==metric['create_time']
    assert c['preceding']['base']['ratio_decimal']!=c['following']['base']['ratio_decimal']
    assert c['following']['base']['within_reported_half_ulp'] is True
    assert c['preceding']['base']['within_reported_half_ulp'] is False
    assert c['measurement_clock_certified'] is False


def test_zero_sell_and_missing_interval_are_explicit_not_zero_ratio():
    prices=parse_5m(candle(buy='10',buy_quote='1000'),'2026-06-24')
    c=ratio_comparison(dict(create_time='2026-06-24 00:00:00',sum_taker_long_short_vol_ratio='1'),prices)
    assert c['preceding']['state']=='missing_native_interval'
    assert c['following']['base']['state']=='zero_sell_volume'
    assert c['following']['base']['ratio_decimal'] is None


def test_inventory_bound_uses_quantities_not_price_moving_usd_value():
    prices=parse_5m(candle(),'2026-06-24')
    old=dict(create_time='2026-06-24 00:00:00',sum_open_interest='100',sum_open_interest_value='10000')
    new=dict(create_time='2026-06-24 00:05:00',sum_open_interest='100',sum_open_interest_value='11000')
    c=oi_volume_comparison(old,new,prices)
    assert c['absolute_native_oi_change']=='0'
    assert c['label_time_window']['exceeds_native_volume'] is False
    new['sum_open_interest']='111';c=oi_volume_comparison(old,new,prices)
    assert c['label_time_window']['exceeds_native_volume'] is True
    assert c['actual_inventory_unit_and_clock_certified'] is False
