import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from dataclasses import replace
from fastapi.testclient import TestClient
from cross_venue.service import Runtime, create_app
from cross_venue.test_engine import quotes, NOW, SETTLE
from cross_venue.engine import Instrument, FundingPoint


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        cfg={'dry_run':True,'wallet_total':10000.,'fee':.0005,'slippage':.0002,
             'basis_buffer':.001,'weight':.4,'min_entry_seconds':600,'max_entry_seconds':7200,
             'quote_age_seconds':15,'rate_age_seconds':90,'max_hold_hours':12,'stoploss':.2,
             'symbols':['BTC','ETH'],'poll_seconds':30,'strategy':'CrossVenueFundingV1',
             'bot_name':'funding-spread','api_server':{'username':'test','password':'secret','listen_port':8086,
                'jwt_secret_key':'unit-test-only-paper-secret-key-32bytes'}}
        self.runtime=Runtime(cfg,Path(self.tmp.name)/'paper.sqlite')
        self.client=TestClient(create_app(self.runtime))

    def tearDown(self):
        self.runtime.engine.close_db();self.tmp.cleanup()

    def test_api_shows_one_paper_portfolio(self):
        r=self.client.get('/api/v1/show_config',auth=('test','secret'))
        self.assertEqual(r.status_code,200)
        self.assertTrue(r.json()['dry_run'])
        self.assertEqual(r.json()['max_open_trades'],2)
        self.assertEqual(r.json()['portfolio_limit'],1)
        self.assertEqual(self.client.get('/api/v1/count',auth=('test','secret')).json()['current'],0)

    def test_control_requires_authentication(self):
        self.assertEqual(self.client.post('/api/v1/pause').status_code,401)
        self.assertTrue(self.runtime.entries_enabled)
        self.assertEqual(self.client.post('/api/v1/pause',auth=('test','secret')).status_code,200)
        self.assertFalse(self.runtime.entries_enabled)

    def test_portfolio_page_and_summary_report_monitoring(self):
        self.assertEqual(self.client.get('/portfolio').status_code,200)
        j=self.client.get('/summary').json()
        self.assertEqual(j['equity'],10000.)
        self.assertEqual(j['health']['status'],'initializing')

    def _open(self):
        instruments={v:Instrument(v,'BTC','.001',.001,5.) for v in ['bybit','okx']}
        c=self.runtime.engine.candidate(quotes(),instruments,NOW)
        p=self.runtime.engine.open_position(c,quotes(),NOW)
        self.runtime.cfg['symbols']=['BTC']
        self.runtime.instruments={'BTC':instruments}
        return p

    def test_history_reconciles_even_if_current_snapshot_fails(self):
        p=self._open();now=SETTLE+180000
        def unavailable():raise ValueError('prediction endpoint down')
        def history(v,s,start,end):return [FundingPoint(v,s,SETTLE,.002 if v=='bybit' else 0.,100.)]
        self.runtime.provider=SimpleNamespace(snapshot=unavailable,funding_events=history)
        with patch('cross_venue.service.time.time',return_value=now/1000):self.runtime.refresh()
        self.assertGreater(self.runtime.engine.active_positions()[0]['legs']['bybit']['funding'],0)

    def test_stale_position_quotes_surface_as_fault(self):
        self._open()
        stale={v:replace(q,timestamp_ms=NOW-16000) for v,q in quotes().items()}
        self.runtime.provider=SimpleNamespace(snapshot=lambda:({'BTC':stale},{}))
        with patch('cross_venue.service.time.time',return_value=NOW/1000):
            self.runtime.refresh();s=self.runtime.public_summary()
        self.assertEqual(s['health']['status'],'fault')
        self.assertTrue(s['health']['faults'])

    def test_quotes_expiring_between_polls_surface_as_fault(self):
        self._open();self.runtime.provider=SimpleNamespace(snapshot=lambda:({'BTC':quotes()},{}))
        with patch('cross_venue.service.time.time',return_value=NOW/1000):self.runtime.refresh()
        with patch('cross_venue.service.time.time',return_value=(NOW+16000)/1000):s=self.runtime.public_summary()
        self.assertFalse(s['marks_fresh'])
        self.assertEqual(s['health']['status'],'fault')

    def test_btc_manual_exit_survives_unheld_eth_failure(self):
        self._open()
        self.runtime.provider=SimpleNamespace(snapshot=lambda:({'BTC':quotes(),'ETH':{}},{'_errors':['ETH down']}))
        with patch('cross_venue.service.time.time',return_value=NOW/1000):self.runtime.force_exit()
        self.assertEqual(self.runtime.engine.active_positions(),[])
        p=self.runtime.engine.positions()[0]
        self.assertEqual(p['state'],'closed')
        self.assertTrue(all('exit' in leg for leg in p['legs'].values()))


if __name__=='__main__':unittest.main()
