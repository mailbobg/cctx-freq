"""Fixed-origin, public-only Binance REST gateway shared by local bot processes."""
from collections import Counter, OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import asyncio
import json
import math
import threading
import time

import requests
from fastapi import HTTPException, Request
from fastapi.responses import Response


ORIGINS={'spot':'https://data-api.binance.vision/api/v3',
         'usd-m':'https://fapi.binance.com/fapi/v1',
         'usd-m-v2':'https://fapi.binance.com/fapi/v2'}
COMMON={'exchangeInfo','klines','ticker/24hr','ticker/price','ticker/bookTicker','depth','time','ping'}
ALLOWED={'spot':COMMON|{'uiKlines','avgPrice','trades','aggTrades'},
         'usd-m':COMMON|{'markPriceKlines','indexPriceKlines','premiumIndexKlines',
                        'premiumIndex','fundingRate','fundingInfo','openInterest','continuousKlines'},
         'usd-m-v2':{'ticker/price'}}

# Keep upstream connections bounded, and reserve capacity for the market metadata
# that Freqtrade must load before a bot can start.
MARKET_WORKERS=ThreadPoolExecutor(max_workers=8,thread_name_prefix='market-data')
METADATA_WORKERS=ThreadPoolExecutor(max_workers=2,thread_name_prefix='market-metadata')


@dataclass(frozen=True)
class Reply:
    status:int
    body:bytes
    headers:dict


class MarketDataGateway:
    def __init__(self,fetch=None,clock=time.monotonic,wall=time.time,max_entries=512,max_bytes=64*1024*1024):
        self.clock=clock;self.wall=wall;self.max_entries=max_entries;self.max_bytes=max_bytes
        self.lock=threading.RLock();self.cache=OrderedDict();self.pending={};self.blocked={}
        self.counters=Counter();self.endpoints={};self.local=threading.local()
        self.slots=threading.BoundedSemaphore(8)
        self.metadata_slots=threading.BoundedSemaphore(2)
        self.fetch=fetch or self._fetch

    def _fetch(self,url,params):
        if not hasattr(self.local,'session'):
            session=requests.Session()
            adapter=requests.adapters.HTTPAdapter(pool_connections=2,pool_maxsize=1,pool_block=True)
            session.mount('https://',adapter)
            session.mount('http://',adapter)
            self.local.session=session
        response=self.local.session.get(url,params=params,timeout=(4,12),allow_redirects=False)
        return Reply(response.status_code,response.content,
                     {k:v for k,v in response.headers.items() if k.lower() in {'content-type','retry-after'}})

    def _count(self,name,endpoint):
        self.counters[name]+=1;self.endpoints.setdefault(endpoint,Counter())[name]+=1

    def stats(self):
        with self.lock:
            return {**dict(self.counters),'cache_entries':len(self.cache),
                    'cache_bytes':sum(len(x[1].body) for x in self.cache.values()),
                    'in_flight':len(self.pending),'endpoints':{k:dict(v) for k,v in self.endpoints.items()}}

    def _backoff_reply(self,family,endpoint):
        blocked=self.blocked.get(family)
        if blocked and blocked[0]>self.clock():
            self._count('rate_limited',endpoint)
            return Reply(blocked[1],b'{"code":-1003,"msg":"Shared market data backoff"}',
                         {'Retry-After':str(math.ceil(blocked[0]-self.clock()))})
        return None

    def _record_backoff(self,family,response):
        try:delay=float(next((v for k,v in response.headers.items() if k.lower()=='retry-after'),'30'))
        except (ValueError,TypeError):delay=30.
        if not math.isfinite(delay):delay=30.
        previous=self.blocked.get(family,(0,0))
        self.blocked[family]=(max(previous[0],self.clock()+max(1.,delay)),
                              418 if (previous[0]>self.clock() and previous[1]==418) or response.status==418 else 429)

    def _ttl(self,path,params,response):
        if response.status!=200:return 0.
        try:data=json.loads(response.body)
        except (ValueError,TypeError):return 0.
        if isinstance(data,dict) and isinstance(data.get('code'),(int,float)) and data['code']<0:return 0.
        if path=='exchangeInfo':return 300. if isinstance(data,dict) and isinstance(data.get('symbols'),list) else 0.
        if path in {'klines','uiKlines','markPriceKlines','indexPriceKlines','premiumIndexKlines','continuousKlines'}:
            if not isinstance(data,list) or not data:return 0.
            try:
                close=max(float(c[6]) for c in data)/1000+.001
                end=float(dict(params).get('endTime','inf'))/1000
            except (ValueError,TypeError,IndexError,KeyError,OverflowError):return 0.
            now=self.wall()
            if not math.isfinite(close):return 0.
            if end<now-3 and close<=end+.001:return 300.
            return max(0.,min(2.,close-now))
        if path in {'fundingRate','fundingInfo'}:return 5.
        return 0.  # Execution quotes and exchange clock never receive a retained cache.

    def get(self,market,path,params):
        endpoint=market+'/'+path;params=tuple(sorted((str(k),str(v)) for k,v in params))
        key=(market,path,params);family='usd-m' if market.startswith('usd-m') else market
        with self.lock:
            self._count('requests',endpoint)
            if path not in ALLOWED.get(market,set()) or any(k.lower() in {'signature','timestamp','recvwindow','apikey','api_key'} for k,v in params):
                self._count('rejected',endpoint)
                raise HTTPException(403,'Only unsigned allowlisted public market data is supported')
            now=self.clock();cached=self.cache.get(key)
            if cached and cached[0]>now:
                self.cache.move_to_end(key);self._count('cache_hits',endpoint)
                return cached[1]
            self.cache.pop(key,None)
            backoff=self._backoff_reply(family,endpoint)
            if backoff:return backoff
            future=self.pending.get(key)
            leader=future is None
            if leader:
                future=Future();self.pending[key]=future
            else:self._count('coalesced',endpoint)
        if not leader:return future.result()
        try:
            with (self.metadata_slots if path=='exchangeInfo' else self.slots):
                requested=self.clock()
                with self.lock:
                    response=self._backoff_reply(family,endpoint)
                    if response is None:self._count('upstream_requests',endpoint)
                if response is None:
                    response=self.fetch(ORIGINS[market]+'/'+path,params)
                    if response.status in (418,429):
                        with self.lock:self._record_backoff(family,response)
        except Exception:
            response=Reply(502,b'{"code":-1,"msg":"Public market data unavailable"}',{})
        with self.lock:
            ttl=self._ttl(path,params,response)
            if response.status!=200:self._count('errors',endpoint)
            # Live candles must expire at their wall-clock boundary even after network latency.
            expires=min(requested+ttl,self.clock()+ttl)
            if ttl>0 and expires>self.clock() and len(response.body)<=self.max_bytes:
                self.cache[key]=(expires,response);self.cache.move_to_end(key)
                while len(self.cache)>self.max_entries or sum(len(x[1].body) for x in self.cache.values())>self.max_bytes:
                    self.cache.popitem(last=False)
            future.set_result(response);self.pending.pop(key,None)
        return response


def install_routes(app,gateway):
    @app.get('/market/{market}/{path:path}')
    async def market_data(market:str,path:str,request:Request):
        if (request.client is None or request.client.host not in {'127.0.0.1','::1'}
                or 'x-mbx-apikey' in request.headers or 'authorization' in request.headers):
            raise HTTPException(403,'Local public market clients only; no credentials accepted')
        # Market network waits use bounded dedicated pools, leaving panel controls available.
        executor=METADATA_WORKERS if path=='exchangeInfo' else MARKET_WORKERS
        result=await asyncio.get_running_loop().run_in_executor(
            executor,gateway.get,market,path,request.query_params.multi_items())
        return Response(result.body,status_code=result.status,headers=result.headers,media_type='application/json')

    @app.get('/api/market-data/stats')
    def market_stats():
        return gateway.stats()
