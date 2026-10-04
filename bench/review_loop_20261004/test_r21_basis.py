"""Native boundary, contract/calendar and one-veto causality checks."""
from decimal import Decimal
import pandas as pd
import pytest

from r21_basis import parse_native, front_symbol, feature, overlay


def candle(day='2024-01-05',scale=1000,close='100.1'):
    start=pd.Timestamp(day,tz='UTC').value//(10**9//scale)
    return ','.join([str(start),'100','101','99',close,'10',str(start+86400*scale-1),'1000','10','5','500','0']).encode()+b'\n'


@pytest.mark.parametrize('scale',[1000,1000000])
def test_native_epoch_decimal_and_interval(scale):
    rows=parse_native(candle(scale=scale))
    row=rows[pd.Timestamp('2024-01-05',tz='UTC')]
    assert row['close']=='100.1'
    assert row['interval_end']==pd.Timestamp('2024-01-06',tz='UTC')
    assert row['native_epoch_scale']==scale


@pytest.mark.parametrize('body',[candle(close='102'),candle().replace(b'100.1,10,',b'100.1,-10,'),candle()+candle(),candle().replace(b',5,500,0',b',5,500')])
def test_invalid_source_calendar_physical_or_shape(body):
    with pytest.raises(ValueError):parse_native(body)


def test_nearest_quarter_roll_is_calendar_not_surviving_universe():
    assert front_symbol('BTC',pd.Timestamp('2024-03-25',tz='UTC'))=='BTCUSDT_240329'
    assert front_symbol('ETH',pd.Timestamp('2024-04-01',tz='UTC'))=='ETHUSDT_240628'
    with pytest.raises(ValueError):front_symbol('SOL',pd.Timestamp('2024-04-01',tz='UTC'))


def f(spot='100',future='100',decision='2024-01-08',asset='BTC'):
    day=pd.Timestamp(decision,tz='UTC');friday=day-pd.Timedelta(days=3)
    s=parse_native(candle(str(friday.date()),close=spot));q=parse_native(candle(str(friday.date()),close=future))
    return feature(asset,day,s,q,{'spot_sha256':'a'*64,'future_sha256':'b'*64})


def test_equality_allows_and_native_precision_strict_discount():
    assert f()['allow'] is True
    sample=f('100.000000000000001','100.000000000000000')
    assert float(sample['spot_close_decimal'])==float(sample['future_close_decimal'])
    assert sample['allow'] is False
    assert sample['reason']=='discounted'
    assert sample['historical_publication_certified'] is False


def test_feature_clock_and_friday_missing_not_cash():
    sample=f()
    assert sample['available_at_assumed']==pd.Timestamp('2024-01-07',tz='UTC')
    assert sample['observation_day']==pd.Timestamp('2024-01-05',tz='UTC')
    with pytest.raises(ValueError):feature('BTC',sample['decision'],{}, {},{})
    with pytest.raises(ValueError):f(decision='2024-01-09')


def test_first_known_not_listed_exception_is_only_real_first_monday():
    sample=feature('ETH',pd.Timestamp('2021-02-01',tz='UTC'),None,None,{})
    assert sample['known'] is False and sample['allow'] is False
    assert sample['reason']=='confirmed_not_listed'
    with pytest.raises(ValueError):feature('ETH',pd.Timestamp('2021-02-08',tz='UTC'),None,None,{})


def test_per_asset_veto_after_covariance_without_redistribution():
    day=pd.Timestamp('2024-01-08',tz='UTC')
    base=pd.DataFrame({'BTC':[.1,.13],'ETH':[.07,.08]},index=pd.date_range('2024-01-07',periods=2,tz='UTC'))
    btc=f('100.1','100');eth=f(asset='ETH')
    weights,audit=overlay(base,{day:{'BTC':btc,'ETH':eth}})
    assert weights.loc[base.index[0],'BTC']==0
    assert weights.loc[base.index[0],'ETH']==.07
    assert weights.loc[base.index[1]].sum()==0
    assert len(audit)==2
    btc['available_at_assumed']=day+pd.Timedelta(seconds=1)
    with pytest.raises(ValueError):overlay(base,{day:{'BTC':btc,'ETH':eth}})


def test_overlay_rejects_foreign_contract_or_observation_identity():
    day=pd.Timestamp('2024-01-08',tz='UTC')
    base=pd.DataFrame({'BTC':[.1],'ETH':[.07]},index=pd.date_range('2024-01-07',periods=1,tz='UTC'))
    btc=f();eth=f(asset='ETH');eth['symbol']='BTCUSDT_240329'
    with pytest.raises(ValueError):overlay(base,{day:{'BTC':btc,'ETH':eth}})
