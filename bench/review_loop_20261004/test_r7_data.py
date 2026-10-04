"""An expired delivery contract cannot be treated as a continuous perpetual."""
import io
import pandas as pd
import pytest

from r7_data import expiry, parse_archive, contract_calendar


def raw(dates, micros=False):
    rows = []
    scale = 1000 if micros else 1
    for d in dates:
        t = int(d.timestamp() * 1000) * scale
        rows.append([t, 100, 105, 95, 101, 1, t + 86400000 * scale - 1, 100, 1, 1, 100, 0])
    return pd.DataFrame(rows).to_csv(index=False, header=False).encode()


def test_symbol_expiry_is_the_actual_last_friday_of_the_quarter():
    assert expiry('BTCUSDT_210326') == pd.Timestamp('2021-03-26 08:00', tz='UTC')
    with pytest.raises(ValueError):
        expiry('BTCUSDT_210331')
    with pytest.raises(ValueError):
        expiry('SOLUSDT_210326')
    with pytest.raises(ValueError):
        expiry('BTCUSDT')


def test_real_partial_listing_archive_is_allowed_but_interior_gap_is_not():
    dates = pd.date_range('2021-02-03', '2021-02-28', tz='UTC')
    frame = parse_archive(raw(dates), '2021-02-01', '2021-03-01')
    assert len(frame) == 26
    result = contract_calendar([frame], 'BTCUSDT_210326', pd.Timestamp('2021-03-01',tz='UTC'))
    assert result.index.equals(dates)
    with pytest.raises(ValueError, match='Interior'):
        contract_calendar([frame.drop(dates[10])], 'BTCUSDT_210326', pd.Timestamp('2021-03-01',tz='UTC'))


def test_spot_microsecond_archive_has_exact_same_daily_prices_as_milliseconds():
    dates = pd.date_range('2025-01-01', '2025-01-31', tz='UTC')
    a = parse_archive(raw(dates), '2025-01-01', '2025-02-01')
    b = parse_archive(raw(dates, True), '2025-01-01', '2025-02-01')
    pd.testing.assert_frame_equal(a,b)


def test_archive_cannot_create_trade_rows_after_expiry():
    dates = pd.date_range('2021-03-25', '2021-03-27',tz='UTC')
    frame = parse_archive(raw(dates), '2021-03-01', '2021-04-01')
    with pytest.raises(ValueError, match='after expiry'):
        contract_calendar([frame], 'BTCUSDT_210326', pd.Timestamp('2021-04-01',tz='UTC'))


def test_only_verified_zero_trade_flat_expired_padding_can_be_removed():
    dates = pd.date_range('2021-03-25', '2021-03-31',tz='UTC')
    frame = parse_archive(raw(dates), '2021-03-01', '2021-04-01',include_activity=True)
    frame.loc[dates[2:], ['open','high','low','close']] = 101.
    frame.loc[dates[2:], ['volume','quote_volume','trades']] = 0.
    out=contract_calendar([frame], 'BTCUSDT_210326', pd.Timestamp('2021-04-01',tz='UTC'))
    assert out.index.equals(dates[:2])
    assert out.attrs['zero_trade_expired_padding_removed'] == 5
    frame.loc[dates[2],'trades']=1.
    with pytest.raises(ValueError,match='after expiry'):
        contract_calendar([frame], 'BTCUSDT_210326', pd.Timestamp('2021-04-01',tz='UTC'))
