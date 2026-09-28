import unittest
from unittest.mock import patch
import time
from cross_venue.provider import PublicProvider, parse_instrument, parse_quotes, settled_rate, candle_open
from cross_venue.engine import Instrument


class ProviderTests(unittest.TestCase):
    def test_eth_failure_does_not_discard_btc_quotes_or_exit_prices(self):
        provider=PublicProvider(['BTC','ETH'])
        provider.instruments={s:{v:Instrument(v,s,'.001',.001,0.,28800,.01 if v=='okx' else 1.)
                                 for v in ['bybit','okx']} for s in ['BTC','ETH']}
        def response(v,path,params):
            symbol=params.get('symbol',params.get('instId'))[:3]
            if symbol=='ETH' and path.endswith('mark-price'):raise ValueError('ETH mark down')
            if v=='bybit':return {'time':1000,'result':{'list':[{'symbol':symbol+'USDT','bid1Price':'100',
                'ask1Price':'101','bid1Size':'10','ask1Size':'10','markPrice':'100.5',
                'fundingRate':'.001','nextFundingTime':'3600000'}]}}
            if path.endswith('funding-rate'):raise ValueError('prediction endpoint down')
            row={'bidPx':'100','askPx':'101','bidSz':'100','askSz':'100','markPx':'100.5','ts':'1000'}
            return {'data':[row]}
        with patch.object(provider,'get',side_effect=response):panel,raw=provider.snapshot()
        self.assertEqual(set(panel['BTC']),{'bybit','okx'})
        self.assertEqual(panel['BTC']['okx'].timestamp_ms,1000)
        self.assertEqual(panel['BTC']['okx'].rate_timestamp_ms,0)
        self.assertTrue(raw['_errors'])

    def test_history_paginates_and_discovers_changed_interval_events(self):
        provider=PublicProvider(['BTC'])
        def response(v,path,params):
            end=params['endTime'];rows=[{'fundingRateTimestamp':str(i*1000),'fundingRate':'.001'}
                 for i in range(250,0,-1) if i*1000<=end][:200]
            return {'result':{'list':rows}}
        with patch.object(provider,'get',side_effect=response), \
             patch.object(provider,'settlement_mark',return_value=100.), \
             patch('cross_venue.provider.time.time',return_value=250.):
            events=provider.funding_events('bybit','BTC',0,250000)
        self.assertEqual(len(events),250)
        self.assertIn(61000,[p.timestamp_ms for p in events])

    def test_expired_history_cannot_be_marked_complete_from_empty_response(self):
        provider=PublicProvider(['BTC']);end=int((time.time()-100*86400)*1000)
        with patch.object(provider,'get',return_value={'data':[]}),self.assertRaises(ValueError):
            provider.funding_events('okx','BTC',end-3600000,end)

    def test_loaded_metadata_is_returned_to_runtime(self):
        bybit={'symbol':'BTCUSDT','status':'Trading','contractType':'LinearPerpetual',
               'settleCoin':'USDT','baseCoin':'BTC','fundingInterval':480,
               'lotSizeFilter':{'qtyStep':'.001','minOrderQty':'.001','minNotionalValue':'5'}}
        okx={'instId':'BTC-USDT-SWAP','state':'live','ctType':'linear','settleCcy':'USDT',
             'ctValCcy':'BTC','ctVal':'0.01','ctMult':'1','lotSz':'0.01','minSz':'0.01'}
        provider=PublicProvider(['BTC'])
        def response(v,*args):return {'result':{'list':[bybit]}} if v=='bybit' else {'data':[okx]}
        with patch.object(provider,'get',side_effect=response):meta=provider.load_instruments()
        self.assertEqual(set(meta['BTC']),{'bybit','okx'})

    def test_okx_contract_units_become_base_units(self):
        row={'instId':'BTC-USDT-SWAP','state':'live','ctType':'linear','settleCcy':'USDT',
             'ctValCcy':'BTC','ctVal':'0.01','ctMult':'1','lotSz':'0.01','minSz':'0.01'}
        i=parse_instrument('okx','BTC',row)
        self.assertAlmostEqual(float(i.base_step),.0001)
        self.assertAlmostEqual(i.min_quantity,.0001)
        self.assertAlmostEqual(i.contract_value,.01)

    def test_inverse_and_wrong_collateral_are_rejected(self):
        row={'instId':'BTC-USDT-SWAP','state':'live','ctType':'inverse','settleCcy':'USDT'}
        with self.assertRaises(ValueError):parse_instrument('okx','BTC',row)

    def test_okx_realized_rate_never_uses_forecast(self):
        row={'fundingTime':'1000','fundingRate':'.1','realizedRate':''}
        with self.assertRaises(ValueError):settled_rate('okx',[row],1000)
        row['realizedRate']='.002'
        self.assertEqual(settled_rate('okx',[row],1000),.002)

    def test_exact_settlement_and_mark_timestamp_required(self):
        with self.assertRaises(ValueError):settled_rate('bybit',[{'fundingRateTimestamp':'1001','fundingRate':'.001'}],1000)
        with self.assertRaises(ValueError):candle_open([['1001','99']],1000)
        self.assertEqual(candle_open([['1000','101','103','99']],1000),101.)

    def test_quote_uses_okx_funding_time_not_next_period(self):
        row={'instId':'BTC-USDT-SWAP','state':'live','ctType':'linear','settleCcy':'USDT',
             'ctValCcy':'BTC','ctVal':'0.01','ctMult':'1','lotSz':'0.01','minSz':'0.01'}
        i=parse_instrument('okx','BTC',row)
        q=parse_quotes('okx','BTC',{'bidPx':'100','askPx':'101','bidSz':'10','askSz':'20','ts':'900'},
            {'method':'current_period','fundingRate':'.001','fundingTime':'1000','nextFundingTime':'28801000','ts':'800'},
            {'markPx':'100.5','ts':'850'},i)
        self.assertEqual(q.settlement_ms,1000)
        self.assertEqual(q.interval_seconds,28800)
        self.assertAlmostEqual(q.bid_qty,.1)
        self.assertEqual(q.timestamp_ms,850)


if __name__=='__main__':unittest.main()
