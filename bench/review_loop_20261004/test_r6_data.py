"""Data defects must stop a paired futures funding assessment."""
import pandas as pd
import pytest

from r6_data import complete_candles, parse_funding, parse_klines


def candles(header=False):
    rows = [f"{i * 3600000},100,101,99,100,0,{(i + 1) * 3600000 - 1},0,0,0,0,0" for i in range(3)]
    return (("open_time,open,high,low,close,volume,close_time,x,y,z,a,b\n" if header else "") + "\n".join(rows)).encode()


@pytest.mark.parametrize("header", [False, True])
def test_mark_candle_schema_and_calendar(header):
    frame = parse_klines(candles(header), "1h", pd.Timestamp(0, tz="UTC"), pd.Timestamp(10800000, unit="ms", tz="UTC"))
    assert frame.open.tolist() == [100, 100, 100]
    assert str(frame.index.tz) == "UTC"


def test_missing_or_duplicate_mark_candle_is_not_filled():
    rows = candles().splitlines()
    for bad in (b"\n".join(rows[:1] + rows[2:]), b"\n".join(rows + rows[-1:])):
        with pytest.raises(ValueError):
            parse_klines(bad, "1h", pd.Timestamp(0, tz="UTC"), pd.Timestamp(10800000, unit="ms", tz="UTC"))


def test_nonfinite_or_invalid_ohlc_cannot_value_positions():
    for bad in (candles().replace(b",100,101", b",nan,101", 1),
                candles().replace(b",101,99,", b",98,99,", 1)):
        with pytest.raises(ValueError):
            parse_klines(bad, "1h", pd.Timestamp(0, tz="UTC"), pd.Timestamp(10800000, unit="ms", tz="UTC"))


def test_gap_requires_real_supplement_and_conflicting_sources_are_rejected():
    start, end = pd.Timestamp(0, tz="UTC"), pd.Timestamp(10800000, unit="ms", tz="UTC")
    full = parse_klines(candles(), "1h", start, end)
    partial = full.drop(index=full.index[1])
    with pytest.raises(ValueError):
        complete_candles([partial], "1h", start, end)
    actual = complete_candles([partial, full.iloc[1:2]], "1h", start, end)
    pd.testing.assert_frame_equal(actual, full, check_freq=False)
    conflicting = full.iloc[:1].copy()
    conflicting["close"] = 100.5
    with pytest.raises(ValueError):
        complete_candles([full, conflicting], "1h", start, end)


def fund_rows():
    return [{"symbol": "BTCUSDT", "fundingTime": i * 28800000 + 2, "fundingRate": ".0001", "markPrice": ""} for i in range(3)]


def test_funding_preserves_actual_time_and_flags_unavailable_mark():
    frame = parse_funding(fund_rows(), "BTCUSDT", pd.Timestamp(0, tz="UTC"), pd.Timestamp(86400000, unit="ms", tz="UTC"))
    assert frame.index[0].microsecond == 2000
    assert frame.mark.isna().all()
    assert frame.slot.iloc[1].hour == 8


def test_missing_repeated_wrong_symbol_or_nonfinite_funding_stops():
    rows = fund_rows()
    for bad in (rows[:2], rows + rows[-1:], [{**r, "symbol": "ETHUSDT"} for r in rows],
                [{**r, "fundingRate": "nan"} for r in rows]):
        with pytest.raises(ValueError):
            parse_funding(bad, "BTCUSDT", pd.Timestamp(0, tz="UTC"), pd.Timestamp(86400000, unit="ms", tz="UTC"))
