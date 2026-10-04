import pandas as pd
import pytest

from r14_flow import flow_features, overlay
from r10_gold import simulate_spot


def frame(total='10.0', buy='5.0', n=38):
    index = pd.date_range('2024-01-01', periods=n, freq='D', tz='UTC')
    return pd.DataFrame({'quote_volume': total, 'buy_quote_volume': buy,
                         'available_at': index+pd.Timedelta(days=1)}, index=index)


def test_exact_neutral_and_tiny_positive_boundary():
    zero = flow_features(frame())
    positive = flow_features(frame(buy='5.0000000000000001'))
    assert not zero.positive.any()
    assert zero.known.iloc[-1] and zero.numerator_decimal.iloc[-1] == '0.0'
    assert positive.positive.iloc[-1]
    assert not positive.known.iloc[28]
    assert positive.known.iloc[29]


def test_sum_quote_not_mean_daily_share_or_base_volume():
    f = frame(total='1', buy='1', n=30)
    f.loc[f.index[-1], ['quote_volume', 'buy_quote_volume']] = ['100', '0']
    f['volume'], f['buy_volume'] = '1', '1'
    feature = flow_features(f)
    assert feature.known.iloc[-1] and not feature.positive.iloc[-1]
    assert feature.numerator_decimal.iloc[-1] == '-71'
    assert feature.denominator_decimal.iloc[-1] == '129'


def test_calendar_gap_is_unknown_not_shortened_sample_or_zero():
    f = frame(n=65).drop(index=pd.Timestamp('2024-01-20', tz='UTC'))
    feature = flow_features(f)
    assert not feature.loc['2024-01-30', 'known']
    assert not feature.loc['2024-02-17', 'known']
    assert feature.loc['2024-02-19', 'known']


def test_source_completion_time_and_decimal_type_cannot_be_guessed():
    f = frame(); f.loc[f.index[-1], 'available_at'] += pd.Timedelta(hours=1)
    with pytest.raises(ValueError, match='completion'): flow_features(f)
    f = frame(); f['quote_volume'] = 10.0
    with pytest.raises(ValueError, match='decimal'): flow_features(f)


def test_no_companion_rescale_and_incomplete_asset_fail_closed():
    btc = frame(buy='4'); eth = frame(buy='6')
    base = pd.DataFrame({'BTC': .13, 'ETH': .21}, index=btc.index)
    target, audit = overlay(base, {'BTC': btc, 'ETH': eth})
    assert target.BTC.eq(0).all()
    assert target.ETH.iloc[-1] == .21 and target.ETH.iloc[28] == 0
    sunday = pd.Timestamp('2024-02-04', tz='UTC')
    row = audit.loc[(audit.decision == sunday+pd.Timedelta(days=1)) & audit.asset.eq('ETH')].iloc[0]
    assert row.feature_day == sunday and row.available_at == row.decision
    assert row.target_weight == row.baseline_weight == .21


def test_future_bar_excluded_at_monday_open_and_full_exit_bypasses_band():
    days = pd.date_range('2023-01-01', '2024-02-06', tz='UTC')
    px = pd.DataFrame({c: 100. for c in ['BTC_open', 'BTC_close', 'ETH_open', 'ETH_close']}, index=days)
    btc, eth = frame(buy='6', n=68), frame(buy='6', n=68)
    for f in (btc, eth):
        f.index = pd.date_range('2023-12-01', periods=68, tz='UTC')
        f['available_at'] = f.index+pd.Timedelta(days=1)
    # Last completed Sunday switches off BTC. Current Monday data cannot restore it.
    btc.loc['2024-01-29':'2024-02-04', ['quote_volume', 'buy_quote_volume']] = ['1000', '0']
    base = pd.DataFrame({'BTC': .04, 'ETH': .21}, index=days)
    target, _ = overlay(base, {'BTC': btc, 'ETH': eth})
    assert target.loc['2024-02-04', 'BTC'] == 0
    curve = simulate_spot(px, target, '2024-01-29', '2024-02-06', .0012, terminal=False)
    assert curve.loc['2024-01-29', 'units_BTC'] > 0
    assert curve.loc['2024-02-05', 'units_BTC'] == 0
    changed = btc.copy(); changed.loc['2024-02-05', 'buy_quote_volume'] = '10'
    other, _ = overlay(base, {'BTC': changed, 'ETH': eth})
    pd.testing.assert_frame_equal(target.loc[:'2024-02-04'], other.loc[:'2024-02-04'])


def test_features_and_mask_full_prefix_equality():
    f = frame(buy='6', n=65)
    base = pd.DataFrame({'BTC': .13, 'ETH': .21}, index=f.index)
    feature = flow_features(f)
    target, audit = overlay(base, {'BTC': f, 'ETH': f})
    cutoff = f.index[48]
    pd.testing.assert_frame_equal(flow_features(f.loc[:cutoff]), feature.loc[:cutoff])
    short, short_audit = overlay(base.loc[:cutoff], {'BTC': f.loc[:cutoff], 'ETH': f.loc[:cutoff]})
    pd.testing.assert_frame_equal(short, target.loc[:cutoff])
    pd.testing.assert_frame_equal(short_audit, audit.loc[audit.feature_day <= cutoff].reset_index(drop=True))
