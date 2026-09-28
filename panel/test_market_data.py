import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from panel.market_data import MarketDataGateway, Reply, install_routes


def reply(body=None,status=200,headers=None):
    return Reply(status,json.dumps({} if body is None else body).encode(),headers or {})


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.now=1000.
        self.calls=[]
        def fetch(url,params):
            self.calls.append((url,params));return reply({'symbols':[]})
        self.gateway=MarketDataGateway(fetch=fetch,clock=lambda:self.now,wall=lambda:self.now)

    def test_public_clients_reuse_rules_and_separate_market_and_parameters(self):
        g=self.gateway
        for _ in range(3):g.get('spot','exchangeInfo',[])
        self.assertEqual(len(self.calls),1)
        g.get('usd-m','exchangeInfo',[])
        g.get('spot','exchangeInfo',[('symbol','BTCUSDT')])
        self.assertEqual(len(self.calls),3)
        g.get('spot','exchangeInfo',[('symbol','BTCUSDT')])
        self.now+=301;g.get('spot','exchangeInfo',[])
        self.assertEqual(len(self.calls),4)

    def test_simultaneous_uncached_quotes_share_one_network_call(self):
        release=threading.Event();entered=threading.Event()
        def fetch(url,params):
            self.calls.append(url);entered.set();release.wait(2)
            return reply({'price':'100'})
        g=MarketDataGateway(fetch=fetch)
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures=[pool.submit(g.get,'spot','ticker/price',[('symbol','BTCUSDT')])]
            self.assertTrue(entered.wait(1))
            futures += [pool.submit(g.get,'spot','ticker/price',[('symbol','BTCUSDT')]) for _ in range(7)]
            # Ensure every caller is waiting on this one future before releasing the provider.
            import time
            deadline=time.monotonic()+2
            while g.stats()['coalesced']<7 and time.monotonic()<deadline:time.sleep(.005)
            self.assertEqual(g.stats()['coalesced'],7)
            release.set()
            self.assertTrue(all(f.result().status==200 for f in futures))
        self.assertEqual(len(self.calls),1)
        g.get('spot','ticker/price',[('symbol','BTCUSDT')])
        self.assertEqual(len(self.calls),2)  # no retained cache for execution quotes

    def test_candle_cache_expires_at_close_and_past_queries_are_reusable(self):
        candle=[900000,'1','2','1','2','10',1000999,'0',1,'0','0','0']
        def fetch(url,params):
            self.calls.append(url)
            return reply([[*candle[:6],989999,*candle[7:]]] if 'endTime' in dict(params) else [candle])
        self.gateway.fetch=fetch
        params=[('symbol','BTCUSDT'),('interval','1m'),('limit','1')]
        self.gateway.get('spot','klines',params)
        self.now=1000.5;self.gateway.get('spot','klines',params)
        self.assertEqual(len(self.calls),1)
        self.now=1001.;self.gateway.get('spot','klines',params)
        self.assertEqual(len(self.calls),2)
        past=[*params,('endTime','990000')]
        self.gateway.get('spot','klines',past)
        self.now+=100;self.gateway.get('spot','klines',past)
        self.assertEqual(len(self.calls),3)

    def test_errors_and_empty_candles_do_not_become_stale_successes(self):
        responses=iter([reply(status=502),reply({'symbols':[]}),reply([],200),reply([],200)])
        self.gateway.fetch=lambda *args:(self.calls.append(args) or next(responses))
        self.assertEqual(self.gateway.get('spot','exchangeInfo',[]).status,502)
        self.assertEqual(self.gateway.get('spot','exchangeInfo',[]).status,200)
        self.gateway.get('spot','klines',[]);self.gateway.get('spot','klines',[])
        self.assertEqual(len(self.calls),4)

    def test_network_failure_propagates_and_next_call_retries(self):
        def broken(*args):raise TimeoutError('test provider timeout')
        self.gateway.fetch=broken
        self.assertEqual(self.gateway.get('spot','exchangeInfo',[]).status,502)
        self.gateway.fetch=lambda *args:reply({'symbols':[]})
        self.assertEqual(self.gateway.get('spot','exchangeInfo',[]).status,200)

    def test_malformed_candles_are_not_cached_and_never_strand_waiters(self):
        for body in [[{}],[None],[['bad']]]:
            with self.subTest(body=body):
                g=MarketDataGateway(fetch=lambda *args:reply(body))
                self.assertEqual(g.get('spot','klines',[]).status,200)
                self.assertEqual(g.stats()['in_flight'],0)
                self.assertEqual(g.stats()['cache_entries'],0)

    def test_rate_limit_blocks_new_queries_until_retry_after(self):
        self.gateway.fetch=lambda *args:(self.calls.append(args) or reply({'code':-1003},429,{'Retry-After':'10'}))
        self.assertEqual(self.gateway.get('spot','exchangeInfo',[]).status,429)
        self.assertEqual(self.gateway.get('spot','klines',[]).status,429)
        self.assertEqual(len(self.calls),1)
        self.now+=11;self.gateway.get('spot','exchangeInfo',[])
        self.assertEqual(len(self.calls),2)

    def test_queued_requests_respect_backoff_before_sending(self):
        release=threading.Event();entered=threading.Event()
        def fetch(*args):
            self.calls.append(args);entered.set();release.wait(2)
            return reply({'code':-1003},429,{'Retry-After':'10'})
        g=MarketDataGateway(fetch=fetch);g.slots=threading.BoundedSemaphore(1)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(g.get,'spot','time',[])
            self.assertTrue(entered.wait(1))
            second=pool.submit(g.get,'spot','klines',[])
            import time
            deadline=time.monotonic()+1
            while g.stats()['requests']<2 and time.monotonic()<deadline:time.sleep(.005)
            release.set()
            self.assertEqual(first.result().status,429)
            self.assertEqual(second.result().status,429)
        self.assertEqual(len(self.calls),1)

    def test_exchange_info_is_not_blocked_by_slow_candles(self):
        release=threading.Event();entered=threading.Event()
        def fetch(url,params):
            if url.endswith('/klines'):
                entered.set();release.wait(2)
                return reply([])
            return reply({'symbols':[]})
        g=MarketDataGateway(fetch=fetch)
        g.slots=threading.BoundedSemaphore(1)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                candle=pool.submit(g.get,'spot','klines',[('symbol','BTCUSDT')])
                self.assertTrue(entered.wait(1))
                metadata=pool.submit(g.get,'spot','exchangeInfo',[])
                self.assertEqual(metadata.result(timeout=.5).status,200)
                release.set();self.assertEqual(candle.result(timeout=1).status,200)
        finally:
            release.set()

    def test_expired_ban_does_not_change_new_rate_limit_into_a_ban(self):
        responses=iter([reply(status=418,headers={'Retry-After':'1'}),
                        reply(status=429,headers={'Retry-After':'10'})])
        self.gateway.fetch=lambda *args:next(responses)
        self.assertEqual(self.gateway.get('spot','time',[]).status,418)
        self.now+=2
        self.assertEqual(self.gateway.get('spot','time',[]).status,429)
        self.assertEqual(self.gateway.get('spot','ping',[]).status,429)

    def test_slow_market_calls_leave_panel_control_workers_available(self):
        import asyncio,httpx
        release=threading.Event()
        self.gateway.get=lambda *args:(release.wait(3) or True) and reply()
        app=FastAPI();install_routes(app,self.gateway)
        @app.get('/control')
        def control():return {'ok':True}
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1)),base_url='http://local') as client:
                jobs=[asyncio.create_task(client.get('/market/spot/time')) for _ in range(40)]
                try:
                    await asyncio.sleep(.1)
                    result=await asyncio.wait_for(client.get('/control'),timeout=.5)
                    self.assertEqual(result.status_code,200)
                finally:
                    release.set();await asyncio.gather(*jobs,return_exceptions=True)
        asyncio.run(check())

    def test_bounded_storage_and_param_order(self):
        g=MarketDataGateway(fetch=self.gateway.fetch,max_entries=2)
        a=[('symbol','BTCUSDT'),('permissions','SPOT')]
        g.get('spot','exchangeInfo',a);g.get('spot','exchangeInfo',list(reversed(a)))
        self.assertEqual(len(self.calls),1)
        for symbol in ['ETHUSDT','SOLUSDT']:g.get('spot','exchangeInfo',[('symbol',symbol)])
        self.assertEqual(g.stats()['cache_entries'],2)

    def test_routes_refuse_private_signed_arbitrary_or_nonlocal_requests(self):
        app=FastAPI();install_routes(app,self.gateway)
        client=TestClient(app,client=('127.0.0.1',12345))
        self.assertEqual(client.get('/market/spot/exchangeInfo').status_code,200)
        for path,kwargs in [('/market/spot/account',{}),('/market/spot/order',{}),
                            ('/market/spot/exchangeInfo?signature=secret',{}),
                            ('/market/spot/exchangeInfo',{'headers':{'X-MBX-APIKEY':'secret'}}),
                            ('/market/evil/exchangeInfo',{})]:
            self.assertEqual(client.get(path,**kwargs).status_code,403)
        self.assertEqual(client.post('/market/spot/order').status_code,405)
        external=TestClient(app,client=('10.0.0.1',12345))
        self.assertEqual(external.get('/market/spot/exchangeInfo').status_code,403)
        self.assertEqual(len(self.calls),1)


if __name__=='__main__':unittest.main()
