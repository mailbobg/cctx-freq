from decimal import Decimal
import pandas as pd
import pytest

from r19_term import parse_archive, parse_counterpart, decision_feature, overlay


def raw(vintage='2024-01-06', values='13.35,15.51', day='2024-01-05'):
    stamp = vintage.replace('-', '')
    return f'observation_date,VIXCLS_{stamp},VXVCLS_{stamp}\n{day},{values}\n'.encode()


def feature(body=None, monday='2024-01-08', vintage='2024-01-06'):
    frame = parse_archive(raw() if body is None else body, vintage)
    return decision_feature(pd.Timestamp(monday, tz='UTC'), frame, vintage, 'a'*64,
                            pd.Timestamp('2026-10-04T13:00:00Z'))


def test_exact_vintage_series_and_native_boundary():
    f = feature(raw(values='15.51000000000000001,15.51'))
    assert f['known'] and not f['allow']
    assert Decimal(f['vix_decimal']) > Decimal(f['vix3m_decimal'])
    assert float(f['vix_decimal']) == float(f['vix3m_decimal'])
    assert feature(raw(values='15.51,15.51'))['allow']
    assert feature()['allow']


@pytest.mark.parametrize('body', [raw().replace(b'20240106,VXVCLS_20240106', b'20240107,VXVCLS_20240107'),
    raw().replace(b'VXVCLS', b'VIXCLS'), raw().replace(b'VIXCLS_20240106', b'VXVCLS_20240106')])
def test_header_identity_cannot_be_current_or_different_vintage(body):
    with pytest.raises(ValueError, match='identity'): parse_archive(body, '2024-01-06')


@pytest.mark.parametrize('body', [raw()+b'2024-01-05,13,15\n', raw(day='2024-01-08'),
    raw(day='2024-01-06'), raw(day='2024-1-5'), raw()+b'2024-01-04,13,15\n'])
def test_future_duplicate_weekend_unordered_and_noniso_rows(body):
    with pytest.raises(ValueError): parse_archive(body, '2024-01-06')


@pytest.mark.parametrize('values', ['NaN,15', '0,15', '-1,15', 'Infinity,15', '13,15,17'])
def test_invalid_native_values_are_errors_not_cash_signal(values):
    with pytest.raises(ValueError): parse_archive(raw(values=values), '2024-01-06')


def test_holiday_missing_friday_and_partial_pair_do_not_resurrect_thursday():
    holiday = raw(vintage='2024-03-30', day='2024-03-28', values='13,15') + b'2024-03-29,,\n'
    for body in (holiday, holiday.rsplit(b'2024-03-29', 1)[0]):
        f = feature(body, monday='2024-04-01', vintage='2024-03-30')
        assert not f['known'] and not f['allow'] and f['reason'] == 'missing_friday'
    assert not feature(raw(values='13.35,'))['known']
    assert not feature(raw(values=',15.51'))['known']


def test_date_only_archive_clock_and_dst_completion_are_separate():
    winter = feature()
    summer = feature(raw(vintage='2024-07-06', day='2024-07-05'), '2024-07-08', '2024-07-06')
    assert winter['available_at'] == pd.Timestamp('2024-01-08T00:00:00Z')
    assert winter['observation_completion_bound'] == pd.Timestamp('2024-01-05T21:15:00Z')
    assert summer['observation_completion_bound'] == pd.Timestamp('2024-07-05T20:15:00Z')
    assert winter['local_receipt'] > winter['decision']
    assert winter['historical_evidence_type'] == 'provider_archived_vintage'
    # A later/current vintage is not usable just because its observation date is old.
    with pytest.raises(ValueError, match='Saturday'): feature(raw(vintage='2024-01-13'), vintage='2024-01-13')
    with pytest.raises(ValueError, match='Monday'): feature(monday='2024-01-07')


def test_unknown_warmup_zero_gate_and_no_companion_or_aggregate_rescale():
    index = pd.date_range('2024-01-01', '2024-01-15', tz='UTC')
    base = pd.DataFrame({'BTC': .04, 'ETH': .21}, index=index)
    a = feature(); b = feature(raw(vintage='2024-01-13', day='2024-01-12', values='18,17'), '2024-01-15', '2024-01-13')
    target, audit = overlay(base, {a['decision']: a, b['decision']: b})
    assert target.loc['2024-01-07'].to_list() == [.04, .21]
    assert target.loc['2024-01-14'].eq(0).all()
    assert target.loc['2024-01-01'].eq(0).all()
    assert audit[audit.decision.eq(a['decision'])].iloc[0].target_ETH == .21
    # A future week's inverted values cannot change the earlier target.
    short, _ = overlay(base.loc[:'2024-01-07'], {a['decision']: a})
    pd.testing.assert_frame_equal(short, target.loc[short.index])


def test_current_counterpart_schema_calendar_and_physical_prices():
    body = b'DATE,OPEN,HIGH,LOW,CLOSE\n01/05/2024,13.20,13.50,13.10,13.35\n'
    frame = parse_counterpart(body)
    assert frame.loc[pd.Timestamp('2024-01-05', tz='UTC'), 'close_decimal'] == '13.35'
    with pytest.raises(ValueError): parse_counterpart(body.replace(b'13.50', b'13.01'))
    with pytest.raises(ValueError): parse_counterpart(body+body.split(b'\n')[1]+b'\n')


def test_shared_veto_full_exit_bypasses_active_position_band():
    from r10_gold import simulate_spot
    index = pd.date_range('2023-01-01', '2024-01-16', tz='UTC')
    px = pd.DataFrame({c: 100. for c in ['BTC_open','BTC_close','ETH_open','ETH_close']}, index=index)
    base = pd.DataFrame({'BTC': .04, 'ETH': .21}, index=index)
    a = feature(); b = feature(raw(vintage='2024-01-13', day='2024-01-12', values='18,17'), '2024-01-15', '2024-01-13')
    target, _ = overlay(base, {a['decision']: a, b['decision']: b})
    curve = simulate_spot(px, target, '2024-01-08', '2024-01-16', .0012, terminal=False)
    assert curve.loc['2024-01-08', 'units_BTC'] > 0
    assert curve.loc['2024-01-15', ['units_BTC','units_ETH']].eq(0).all()


def test_counterpart_ancient_errors_retained_and_required_window_stays_strict():
    body = b'DATE,OPEN,HIGH,LOW,CLOSE\n02/11/1992,19.24,18.57,17.61,17.70\n01/05/2024,13.20,13.50,13.10,13.35\n'
    with pytest.raises(ValueError, match='Physically'): parse_counterpart(body)
    scoped = parse_counterpart(body, required_start='2021-01-01')
    assert len(scoped.attrs['outside_required_ohlc_errors']) == 1
    assert scoped.attrs['outside_required_ohlc_errors'][0][1] == '19.24'
    with pytest.raises(ValueError, match='Physically'): parse_counterpart(body.replace(b'13.50',b'13.01'), required_start='2021-01-01')
