import unittest
from dataclasses import replace
from unittest.mock import patch
import jwt
from types import SimpleNamespace
from cross_venue import test_service as fixtures
from cross_venue.frequi import FreqUIBridge
from freqtrade.rpc.api_server.api_schemas import ShowConfig, Balances, OpenTradeSchema, TradeResponse, Count, DailyWeeklyMonthly


class FreqUITests(unittest.TestCase):
    setUp=fixtures.ServiceTests.setUp
    tearDown=fixtures.ServiceTests.tearDown
    _open=fixtures.ServiceTests._open
    def login(self):
        r=self.client.post('/api/v1/token/login',auth=('test','secret'))
        self.assertEqual(r.status_code,200)
        return r.json()

    def test_scoped_access_and_refresh_authentication(self):
        tokens=self.login();access={'Authorization':'Bearer '+tokens['access_token']}
        self.assertEqual(self.client.get('/api/v1/show_config',headers=access).status_code,200)
        self.assertEqual(self.client.post('/api/v1/pause',headers={'Authorization':'Bearer '+tokens['refresh_token']}).status_code,401)
        r=self.client.post('/api/v1/token/refresh',headers={'Authorization':'Bearer '+tokens['refresh_token']})
        self.assertEqual(r.status_code,200)
        self.assertEqual(self.client.post('/api/v1/token/refresh',headers=access).status_code,401)
        self.assertEqual(self.client.post('/api/v1/token/login',auth=('test','wrong')).status_code,401)

    def test_expired_or_other_bot_tokens_cannot_control_paper_entries(self):
        original=self.login()['access_token']
        payload=jwt.decode(original,options={'verify_signature':False})
        for change in [{'exp':0},{'aud':'different-bot'},{'iss':'freqtrade'}]:
            signed=jwt.encode({**payload,**change},self.runtime.cfg['api_server']['jwt_secret_key'],algorithm='HS256')
            self.assertEqual(self.client.post('/api/v1/pause',headers={'Authorization':'Bearer '+signed}).status_code,401)
            self.assertTrue(self.runtime.entries_enabled)

    def test_native_bootstrap_contract_and_loopback_cors(self):
        access={'Authorization':'Bearer '+self.login()['access_token']}
        ShowConfig.model_validate(self.client.get('/api/v1/show_config',headers=access).json())
        Balances.model_validate(self.client.get('/api/v1/balance',headers=access).json())
        Count.model_validate(self.client.get('/api/v1/count',headers=access).json())
        TradeResponse.model_validate(self.client.get('/api/v1/trades',headers=access).json())
        DailyWeeklyMonthly.model_validate(self.client.get('/api/v1/daily',headers=access).json())
        for path in ['version','profit','status','whitelist','blacklist','locks','performance','logs']:
            self.assertEqual(self.client.get('/api/v1/'+path,headers=access).status_code,200,path)
        r=self.client.options('/api/v1/show_config',headers={'Origin':'http://127.0.0.1:8089','Access-Control-Request-Method':'GET','Access-Control-Request-Headers':'authorization'})
        self.assertEqual(r.headers.get('access-control-allow-origin'),'http://127.0.0.1:8089')
        r=self.client.options('/api/v1/show_config',headers={'Origin':'https://untrusted.example','Access-Control-Request-Method':'GET'})
        self.assertNotIn('access-control-allow-origin',r.headers)

    def test_two_legs_truthfully_reconcile_to_one_portfolio(self):
        self._open()
        from cross_venue.test_engine import NOW, quotes
        self.runtime.quotes={'BTC':quotes()}
        with patch('cross_venue.service.time.time',return_value=NOW/1000):
            legs=self.client.get('/api/v1/status',auth=('test','secret')).json()
            self.assertEqual(len(legs),2)
            for leg in legs:OpenTradeSchema.model_validate(leg)
            self.assertEqual({r['is_short'] for r in legs},{True,False})
            self.assertEqual(legs[0]['amount'],legs[1]['amount'])
            native=self.client.get('/api/v1/count',auth=('test','secret')).json()
            self.assertEqual(native['current'],2);self.assertEqual(native['max'],2)
            portfolio=self.client.get('/api/v1/portfolio',auth=('test','secret')).json()
            self.assertEqual(portfolio['open_trades'],1)
            self.assertAlmostEqual(sum(x['profit_abs'] for x in legs),portfolio['profit_all_coin'])
            balance=self.client.get('/api/v1/balance',auth=('test','secret')).json()
            self.assertAlmostEqual(balance['total'],portfolio['equity'])

    def test_ui_partial_exit_and_force_entry_are_refused(self):
        self.assertEqual(self.client.post('/api/v1/forceenter',auth=('test','secret'),json={'pair':'BTC/USDT:USDT'}).status_code,409)
        self.assertEqual(self.client.post('/api/v1/forceexit',auth=('test','secret'),json={'tradeid':'all','amount':.01}).status_code,409)

    def test_exiting_one_displayed_leg_closes_both_and_keeps_aggregate_profit(self):
        self._open()
        from cross_venue.test_engine import NOW,quotes
        self.runtime.provider=SimpleNamespace(snapshot=lambda:({'BTC':quotes()},{}))
        with patch('cross_venue.service.time.time',return_value=NOW/1000):
            response=self.client.post('/api/v1/forceexit',auth=('test','secret'),json={'tradeid':12,'ordertype':'market'})
            self.assertEqual(response.status_code,200)
            trades=self.client.get('/api/v1/trades',auth=('test','secret')).json()['trades']
            self.assertEqual(len(trades),2)
            self.assertTrue(all(not t['is_open'] for t in trades))
            TradeResponse.model_validate(self.client.get('/api/v1/trades',auth=('test','secret')).json())
            portfolio=self.client.get('/api/v1/portfolio',auth=('test','secret')).json()
            native=self.client.get('/api/v1/profit',auth=('test','secret')).json()
            self.assertAlmostEqual(native['profit_closed_coin'],portfolio['profit_closed_coin'])
            self.assertEqual(native['closed_trade_count'],2)
            self.assertEqual(portfolio['closed_trade_count'],1)

    def test_port_8086_opens_selected_bot_and_keeps_raw_portfolio(self):
        r=self.client.get('/',follow_redirects=False)
        self.assertEqual(r.status_code,307)
        self.assertEqual(r.headers['location'],'http://127.0.0.1:8089/?bot=funding-spread')
        self.assertEqual(self.client.get('/portfolio').status_code,200)

    def test_targeted_exit_does_not_close_replacement_during_price_fetch(self):
        from cross_venue.test_engine import NOW, quotes
        for trade_id in [11, 'all']:
            with self.subTest(trade_id=trade_id):
                self.runtime.engine.db.execute('DELETE FROM positions')
                self.runtime.engine.db.commit()
                old=self._open()
                def replaced_during_snapshot():
                    with self.runtime.lock:
                        self.runtime.engine.close_position(old['id'],quotes(),NOW,'risk_exit')
                        self._open()
                    return {'BTC':quotes()},{}
                self.runtime.provider=SimpleNamespace(snapshot=replaced_during_snapshot)
                with patch('cross_venue.service.time.time',return_value=NOW/1000):
                    response=self.client.post('/api/v1/forceexit',auth=('test','secret'),json={'tradeid':trade_id})
                self.assertEqual(response.status_code,409 if trade_id==11 else 200)
                active=self.runtime.engine.active_positions()
                self.assertEqual([p['id'] for p in active],[2])

    def test_daily_same_day_has_same_balance_across_query_windows(self):
        from cross_venue.test_engine import NOW
        positions=[{'state':'closed','closed_ms':NOW-30*86400000,'profit':1000.},
                   {'state':'closed','closed_ms':NOW,'profit':100.}]
        with patch('cross_venue.frequi.time.time',return_value=NOW/1000), \
             patch.object(self.runtime.engine,'positions',return_value=positions):
            short=self.client.get('/api/v1/daily?timescale=1',auth=('test','secret')).json()['data'][0]
            long=self.client.get('/api/v1/daily?timescale=31',auth=('test','secret')).json()['data'][0]
        self.assertEqual(short,long)
        self.assertEqual(short['starting_balance'],11000.)
        self.assertAlmostEqual(short['rel_profit'],100/11000)

    def test_history_excludes_open_legs_and_pages_newest_close_first(self):
        from cross_venue.test_engine import NOW,quotes
        self._open()
        history=self.client.get('/api/v1/trades',auth=('test','secret')).json()
        self.assertEqual(history['total_trades'],0)
        self.runtime.engine.close_position(1,quotes(),NOW,'test')
        self._open()
        history=self.client.get('/api/v1/trades',auth=('test','secret')).json()
        self.assertEqual(history['total_trades'],2)
        self.runtime.engine.close_position(2,quotes(),NOW,'test')
        p=self.runtime.engine.positions()[1];p['closed_ms']=NOW+1000
        with self.runtime.engine.db:self.runtime.engine._save(p)
        history=self.client.get('/api/v1/trades?limit=2',auth=('test','secret')).json()
        self.assertEqual(history['total_trades'],4)
        self.assertEqual({r['portfolio_id'] for r in history['trades']},{2})
        self.assertEqual(self.client.get('/api/v1/trades?limit=2&offset=2',auth=('test','secret')).json()['trades'][0]['portfolio_id'],1)

    def test_pair_performance_offsets_legs_and_weights_entry_capital(self):
        rows=[{'is_open':False,'pair':'BTC/USDT:USDT','profit_abs':100.,'profit_ratio':.1,'open_trade_value':1000.},
              {'is_open':False,'pair':'BTC/USDT:USDT','profit_abs':-110.,'profit_ratio':-.11,'open_trade_value':1000.},
              {'is_open':False,'pair':'ETH/USDT:USDT','profit_abs':10.,'profit_ratio':.1,'open_trade_value':100.},
              {'is_open':False,'pair':'ETH/USDT:USDT','profit_abs':20.,'profit_ratio':.01,'open_trade_value':2000.}]
        for row in rows:row.update(open_timestamp=1000,close_timestamp=3661000,amount=1.,close_rate=100.)
        bridge=FreqUIBridge(self.runtime)
        with patch.object(bridge,'trades',return_value=rows):
            performance=bridge.performance();profit=bridge.profit()
        self.assertEqual(performance[0]['pair'],'ETH/USDT:USDT')
        self.assertAlmostEqual(performance[0]['profit_ratio'],30/2100)
        self.assertAlmostEqual(performance[1]['profit_ratio'],-10/2000)
        self.assertEqual(profit['best_pair'],'ETH/USDT:USDT')
        self.assertEqual(profit['best_pair_profit_abs'],30.)
        self.assertAlmostEqual(profit['best_pair_profit_ratio'],30/2100)
        self.assertAlmostEqual(profit['expectancy_ratio'],((1+(130/3)/110)*.75)-1)
        self.assertEqual(profit['avg_duration'],'1:01:00')


if __name__=='__main__':unittest.main()
