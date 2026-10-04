import hashlib
import io
import zipfile

import pandas as pd
import pytest

from r14_data import checked_zip, parse_flow


def source(scale=1000):
    begin = int(pd.Timestamp('2024-02-01', tz='UTC').timestamp()) * scale
    return pd.DataFrame([[begin+i*86400*scale, '100', '101', '99', '100',
                          '10.00000000', begin+(i+1)*86400*scale-1, '1000.00000000', 20,
                          '5.00000000', '500.0000000000000001', '0'] for i in range(3)])


def parse(frame):
    return parse_flow(frame.to_csv(index=False, header=False).encode(),
                      pd.Timestamp('2024-02-01', tz='UTC'), pd.Timestamp('2024-02-04', tz='UTC'))


def test_epoch_units_and_original_decimal_strings():
    a, b = parse(source()), parse(source(1000000))
    pd.testing.assert_frame_equal(a, b)
    assert a.buy_quote_volume.iloc[0] == '500.0000000000000001'
    assert a.volume.iloc[0] == '10.00000000'
    assert a.available_at.iloc[0] == pd.Timestamp('2024-02-02', tz='UTC')


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'reorder', 'span', 'mixed', 'fractional'])
def test_calendar_and_candle_completion_are_strict(change):
    f = source()
    if change == 'missing': f = f.drop(index=1)
    if change == 'duplicate': f = pd.concat([f, f.iloc[:1]])
    if change == 'reorder': f = f.iloc[::-1]
    if change == 'span': f.loc[1, 6] += 1
    if change == 'mixed': f.loc[1, [0, 6]] *= 1000
    if change == 'fractional': f[0] = f[0].astype(object); f.loc[1, 0] = str(f.loc[1, 0])+'.1'
    with pytest.raises(ValueError): parse(f)


@pytest.mark.parametrize('column,value', [(1, '0'), (2, '98'), (3, '102'), (4, 'NaN'),
    (5, '0'), (7, '-1'), (8, '0'), (8, '1.1'), (9, '-1'), (9, '11'),
    (10, '1001'), (10, 'NaN'), (9, '0'), (10, '0'), (10, '5')])
def test_unknown_bounds_activity_and_unit_mix_cannot_be_flow(column, value):
    f = source().astype(object); f.loc[1, column] = value
    with pytest.raises(ValueError): parse(f)


def test_absent_buy_or_sell_side_is_valid_only_with_matching_quote_units():
    f = source(); f.loc[0, [9, 10]] = ['0', '0']; f.loc[1, [9, 10]] = ['10.00000000', '1000.00000000']
    result = parse(f)
    assert result.buy_quote_volume.iloc[0] == '0'
    assert result.buy_quote_volume.iloc[1] == result.quote_volume.iloc[1]


def test_raw_checksums_and_member_identity_are_required():
    csv = source().to_csv(index=False, header=False).encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as z: z.writestr('BTCUSDT-1d-2024-02.csv', csv)
    raw = stream.getvalue(); sha = hashlib.sha256(raw).hexdigest()
    assert checked_zip(raw, f'{sha} BTCUSDT-1d-2024-02.zip'.encode(), 'BTCUSDT-1d-2024-02.zip') == csv
    with pytest.raises(ValueError): checked_zip(raw, b'bad BTCUSDT-1d-2024-02.zip', 'BTCUSDT-1d-2024-02.zip')
    with pytest.raises(ValueError): checked_zip(raw, f'{sha} ETHUSDT-1d-2024-02.zip'.encode(), 'BTCUSDT-1d-2024-02.zip')


def test_amount_decimal_is_preserved_across_old_csv_float_rounding():
    from r7_data import parse_archive
    f = source()
    f[5], f[7], f[9], f[10] = '9396633.873149893', '939663387.31498930', '4698316.9365749465', '469831693.65749465'
    csv = f.to_csv(index=False, header=False).encode()
    old = parse_archive(csv, pd.Timestamp('2024-02-01', tz='UTC'), pd.Timestamp('2024-02-04', tz='UTC'), include_activity=True)
    preserved = parse(f)
    assert preserved.quote_volume.iloc[0] == '939663387.31498930'
    assert float(preserved.quote_volume.iloc[0]) != old.quote_volume.iloc[0]
    pd.testing.assert_frame_equal(preserved[['open','high','low','close']], old[['open','high','low','close']], check_dtype=False)
