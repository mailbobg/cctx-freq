import tempfile
import unittest
from pathlib import Path
from dataclasses import replace

from cross_venue.engine import PaperEngine, Quote, Instrument, FundingPoint

NOW = 1_790_500_000_000
SETTLE = NOW + 3_600_000


def quotes(rate=.006):
    return {
        'bybit': Quote('bybit', 'BTC', 100., 100.02, 100., 100., 100.01,
                       rate, SETTLE, 28800, NOW, NOW),
        'okx': Quote('okx', 'BTC', 100., 100.02, 100., 100., 100.01,
                     0., SETTLE, 28800, NOW, NOW),
    }


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'paper.sqlite'
        self.cfg = {'dry_run': True, 'wallet_total': 10000., 'fee': .0005,
                    'slippage': .0002, 'basis_buffer': .001, 'weight': .4,
                    'min_entry_seconds': 600, 'max_entry_seconds': 7200,
                    'quote_age_seconds': 15, 'rate_age_seconds': 90,
                    'max_hold_hours': 12, 'stoploss': .2}
        self.instruments = {v: Instrument(v, 'BTC', '.001' if v == 'bybit' else '.0001',
                                         .001, 5.) for v in ['bybit', 'okx']}
        self.engine = PaperEngine(self.cfg, self.path)

    def tearDown(self):
        self.engine.close_db()
        self.tmp.cleanup()

    def open(self):
        result = self.engine.candidate(quotes(), self.instruments, NOW)
        self.assertTrue(result['eligible'], result)
        return self.engine.open_position(result, quotes(), NOW)

    def test_reject_live_mode(self):
        with self.assertRaises(ValueError):
            PaperEngine({**self.cfg, 'dry_run': False}, self.path)

    def test_one_settlement_must_cover_all_costs(self):
        r = self.engine.candidate(quotes(.0001), self.instruments, NOW)
        self.assertFalse(r['eligible'])
        self.assertGreater(r['roundtrip_cost'], r['funding_estimate'])

    def test_outside_entry_window_still_explains_costs(self):
        q={v:replace(x,settlement_ms=NOW+10800000) for v,x in quotes().items()}
        result=self.engine.candidate(q,self.instruments,NOW)
        self.assertFalse(result['eligible'])
        self.assertIn('roundtrip_cost',result)

    def test_entry_rejects_changed_settlement_and_expired_window(self):
        candidate=self.engine.candidate(quotes(),self.instruments,NOW)
        moved={v:replace(q,settlement_ms=SETTLE+28800000) for v,q in quotes().items()}
        with self.assertRaises(ValueError):self.engine.open_position(candidate,moved,NOW)
        late=SETTLE-300000
        refreshed={v:replace(q,timestamp_ms=late,rate_timestamp_ms=late) for v,q in quotes().items()}
        with self.assertRaises(ValueError):self.engine.open_position(candidate,refreshed,late)

    def test_stale_quote_and_rate_reject_entry(self):
        for field, age in [('timestamp_ms', 16000), ('rate_timestamp_ms', 91000)]:
            q = quotes(); q['okx'] = replace(q['okx'], **{field: NOW-age})
            self.assertFalse(self.engine.candidate(q, self.instruments, NOW)['eligible'])

    def test_mismatched_settlement_or_interval_reject(self):
        for field, value in [('settlement_ms', SETTLE+3600000), ('interval_seconds', 14400)]:
            q = quotes(); q['okx'] = replace(q['okx'], **{field:value})
            self.assertFalse(self.engine.candidate(q, self.instruments, NOW)['eligible'])

    def test_future_quotes_invalid_prices_and_thin_depth_reject(self):
        for change in [{'timestamp_ms': NOW+6000}, {'bid':-1}, {'ask':99}, {'ask_qty':0}]:
            q=quotes(); q['okx']=replace(q['okx'],**change)
            self.assertFalse(self.engine.candidate(q,self.instruments,NOW)['eligible'])

    def test_matching_quantity_increments_and_margin(self):
        r=self.engine.candidate(quotes(),self.instruments,NOW)
        pos=self.engine.open_position(r,quotes(),NOW)
        self.assertAlmostEqual(pos['quantity']/.001, round(pos['quantity']/.001))
        self.assertEqual(set(pos['legs']),{'bybit','okx'})
        self.assertEqual(pos['legs']['bybit']['side'],-1)
        self.assertEqual(pos['legs']['okx']['side'],1)
        self.assertLessEqual(max(l['notional'] for l in pos['legs'].values()),4000)
        self.assertEqual(len(self.engine.active_positions()),1)
        self.assertFalse(self.engine.candidate(quotes(),self.instruments,NOW)['eligible'])

    def test_restart_preserves_both_legs(self):
        p=self.open(); self.engine.close_db()
        self.engine=PaperEngine(self.cfg,self.path)
        self.assertEqual(self.engine.active_positions()[0]['id'],p['id'])
        self.assertEqual(self.engine.active_positions()[0]['legs'],p['legs'])

    def test_no_forecast_funding_credit(self):
        p=self.open()
        self.assertEqual(sum(l['funding'] for l in p['legs'].values()),0)

    def test_funding_sign_mark_and_dedup(self):
        p=self.open()
        point=FundingPoint('bybit','BTC',SETTLE,.002,120.)
        self.assertTrue(self.engine.book_funding(p['id'],point,SETTLE+1000))
        self.assertFalse(self.engine.book_funding(p['id'],point,SETTLE+1000))
        p=self.engine.active_positions()[0]
        self.assertAlmostEqual(p['legs']['bybit']['funding'],p['quantity']*120*.002)
        self.engine.book_funding(p['id'],FundingPoint('okx','BTC',SETTLE,.001,110.),SETTLE+1000)
        self.assertAlmostEqual(self.engine.active_positions()[0]['legs']['okx']['funding'],-p['quantity']*110*.001)

    def test_actual_settlement_after_interval_change_is_booked(self):
        p=self.open()
        point=FundingPoint('bybit','BTC',SETTLE+3600000,.002,120.)
        self.assertTrue(self.engine.book_funding(p['id'],point,SETTLE+3601000))

    def test_cancelled_expected_event_can_reconcile_empty_actual_history(self):
        p=self.open();end=SETTLE+180000
        for v in ['bybit','okx']:
            self.engine.mark_reconciled(p['id'],v,end,[],end+120000)
        self.assertEqual(self.engine.pending_positions(end+120000),[])

    def test_reconciliation_coverage_and_income_survive_restart(self):
        p=self.open();end=SETTLE+180000
        self.engine.mark_reconciled(p['id'],'bybit',end,[FundingPoint('bybit','BTC',SETTLE,.002,100.)],end+120000)
        self.engine.close_db();self.engine=PaperEngine(self.cfg,self.path)
        p=self.engine.active_positions()[0]
        self.assertEqual(self.engine.coverage(p,'bybit'),end)
        self.assertGreater(p['legs']['bybit']['funding'],0)

    def test_funding_before_entry_future_and_off_grid_reject(self):
        p=self.open()
        for ts in [NOW-1,SETTLE,NOW+1000]:
            self.assertFalse(self.engine.book_funding(p['id'],FundingPoint('bybit','BTC',ts,.002,100.),NOW))

    def test_pending_funding_blocks_new_entries_after_close(self):
        p=self.open(); q={k:replace(v,timestamp_ms=SETTLE+2000,rate_timestamp_ms=SETTLE+2000) for k,v in quotes().items()}
        self.engine.close_position(p['id'],q,SETTLE+2000,'risk_exit')
        self.assertEqual(len(self.engine.pending_positions(SETTLE+2000)),1)
        self.assertFalse(self.engine.candidate(q,self.instruments,SETTLE+2000)['eligible'])
        for v in ['bybit','okx']:
            self.engine.book_funding(p['id'],FundingPoint(v,'BTC',SETTLE,0.,100.),SETTLE+2000)
            self.engine.mark_reconciled(p['id'],v,SETTLE+2000,[],SETTLE+122000)
        self.assertEqual(self.engine.pending_positions(SETTLE+2000),[])

    def test_stale_leg_prevents_any_exit_fill(self):
        p=self.open(); q=quotes();q['okx']=replace(q['okx'],timestamp_ms=NOW-16000)
        with self.assertRaises(ValueError):self.engine.close_position(p['id'],q,NOW,'test')
        self.assertEqual(len(self.engine.active_positions()),1)

    def test_roundtrip_cash_pnl_reconciles_each_venue(self):
        p=self.open(); p=self.engine.close_position(p['id'],quotes(),NOW+1000,'test')
        for l in p['legs'].values():
            expected=l['side']*p['quantity']*(l['exit']-l['entry'])-l['entry_fee']-l['exit_fee']
            self.assertAlmostEqual(l['profit'],expected)
        summary=self.engine.summary(quotes(),NOW+1000)
        self.assertAlmostEqual(summary['equity'],10000+sum(l['profit'] for l in p['legs'].values()))
        self.assertEqual(summary['open_trades'],0)

    def test_partial_quote_input_cannot_create_single_leg(self):
        r=self.engine.candidate(quotes(),self.instruments,NOW)
        with self.assertRaises(ValueError):self.engine.open_position(r,{'bybit':quotes()['bybit']},NOW)
        self.assertEqual(self.engine.active_positions(),[])


if __name__ == '__main__':unittest.main()
