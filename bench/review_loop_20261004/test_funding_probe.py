import importlib.util
from pathlib import Path
import unittest
import hashlib
import tempfile
import requests

import pandas as pd

FILE = Path(__file__).parent / 'funding_probe.py'
if FILE.exists():
    spec = importlib.util.spec_from_file_location('incremental_funding', FILE)
    funding = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(funding)
else:
    funding = None


class ImplementationTest(unittest.TestCase):
    def test_funding_implementation_exists(self):
        self.assertIsNotNone(funding, 'causal funding diagnostic is not implemented')


@unittest.skipIf(funding is None, 'implementation absent')
class FundingTest(unittest.TestCase):
    def setUp(self):
        self.dates = pd.date_range('2026-07-05', '2026-10-04', freq='8h', inclusive='left', tz='UTC')
        self.a = pd.Series(.0002, index=self.dates)
        self.b = pd.Series(0., index=self.dates)

    def test_rule_direction_depends_only_on_available_past_rates(self):
        row = funding.weekly_probe(self.a, self.b)[0]
        self.assertEqual(row['short_venue'], 'bybit')
        self.assertTrue(row['eligible'])
        later = self.a.copy(); later.loc[later.index > pd.Timestamp(row['decision'])] = -.01
        altered = funding.weekly_probe(later, self.b)[0]
        self.assertEqual(row['short_venue'], altered['short_venue'])
        self.assertEqual(row['forecast_spread'], altered['forecast_spread'])
        self.assertLess(altered['realized_spread'], 0)

    def test_small_spread_does_not_pay_four_executions(self):
        rows = funding.weekly_probe(self.a * .01, self.b)
        self.assertTrue(all(not r['eligible'] for r in rows))

    def test_missing_duplicate_or_nonfinite_history_fails(self):
        for series in (self.a.drop(self.dates[5]), pd.concat([self.a, self.a.iloc[:1]]),
                       self.a.mask(self.a.index == self.dates[5])):
            with self.assertRaises(ValueError):
                funding.weekly_probe(series, self.b)

    def test_settlement_on_entry_excluded_and_on_exit_included(self):
        start = self.dates[0];end = self.dates[2]
        self.assertAlmostEqual(funding.period_sum(self.a, start, end), .0004)

    def test_history_rejects_another_instrument(self):
        raw = {'retCode': 0, 'result': {'list': [{'symbol': 'ETHUSDT',
               'fundingRateTimestamp': '1000', 'fundingRate': '.001'}]}}
        with self.assertRaises(ValueError):
            funding.collect_history('bybit', 'BTC', 1000, 2000, lambda *args: raw)

    def test_descending_pages_preserve_every_settlement_once(self):
        pages = iter([[3000, 2000], [1000, 0]])
        calls = []
        def fetch(venue, path, params):
            calls.append(params)
            return {'code': '0', 'data': [{'instId': 'BTC-USDT-SWAP',
                     'fundingTime': str(t), 'realizedRate': '.001'} for t in next(pages)]}
        result = funding.collect_history('okx', 'BTC', 0, 4000, fetch)
        self.assertEqual(len(result), 4)
        self.assertEqual(calls[1]['after'], '2000')

    def test_repeated_page_and_missing_realized_rate_fail(self):
        for value in ('.001', ''):
            raw = {'code': '0', 'data': [{'instId': 'BTC-USDT-SWAP',
                   'fundingTime': '3000', 'realizedRate': value}]}
            with self.assertRaises(ValueError):
                funding.collect_history('okx', 'BTC', 0, 4000, lambda *args: raw)

    def test_archived_pages_reproduce_without_network_and_reject_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'page.json'; body = b'{"code":"0","data":[]}'
            path.write_bytes(body)
            params = {'instId': 'BTC-USDT-SWAP', 'limit': '100'}
            url = requests.Request('GET', 'https://www.okx.com/api/v5/public/funding-rate-history',
                                   params=params).prepare().url
            records = [{'url': url, 'path': str(path), 'sha256': hashlib.sha256(body).hexdigest()}]
            loader = getattr(funding, 'archived_fetch', None)
            self.assertIsNotNone(loader, 'offline funding reproduction is absent')
            fetch = loader(records)
            self.assertEqual(fetch('okx', '/api/v5/public/funding-rate-history', params)['code'], '0')
            path.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                fetch('okx', '/api/v5/public/funding-rate-history', params)


if __name__ == '__main__':
    unittest.main()
