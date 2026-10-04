"""Freeze calendar/request identity before full inventory collection."""
from urllib.parse import urlparse
from r26_sources import monday_dates, required_days, targets, INHERITED


def test_exact_frozen_inventory_request_calendar():
    mondays=monday_dates();days=required_days();requests=targets()
    assert len(mondays)==292 and all(day.weekday()==0 for day in mondays)
    assert len(days)==584 and len(requests)==1168
    assert days[0]=='2020-12-30' and days[-1]=='2026-08-28'
    for day in days:
        key='BTCUSDT_'+day
        url=f'https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-{day}.zip'
        assert requests[key]==url and requests[key+'_checksum']==url+'.CHECKSUM'
        assert urlparse(requests[key]).query==''


def test_inherited_exact_sources_are_not_unavailable_eth_substitutions():
    assert set(INHERITED)=={'2020-12-30','2021-01-29','2026-06-24','2026-06-26'}
    assert set(INHERITED).issubset(required_days())
    assert INHERITED['2020-12-30']==('r24_sources','metrics_BTCUSDT_2020-12-30')
    assert INHERITED['2026-06-26']==('r23_sources','BTCUSDT_2026-06-26')
