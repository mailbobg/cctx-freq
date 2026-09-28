"""Validated public production GET data; no keys, signing or order endpoints."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
import math
import requests
import time

from .engine import Instrument, Quote, FundingPoint

BASE = {'bybit':'https://api.bybit.com', 'okx':'https://www.okx.com'}
PUBLIC = {
    'bybit': {'/v5/market/instruments-info','/v5/market/tickers',
              '/v5/market/funding/history','/v5/market/mark-price-kline'},
    'okx': {'/api/v5/public/instruments','/api/v5/market/ticker',
            '/api/v5/public/funding-rate','/api/v5/public/mark-price',
            '/api/v5/public/funding-rate-history','/api/v5/market/history-mark-price-candles'},
}


def parse_instrument(venue,symbol,row):
    if venue=='bybit':
        if (row.get('symbol')!=symbol+'USDT' or row.get('status')!='Trading'
                or row.get('contractType')!='LinearPerpetual' or row.get('settleCoin')!='USDT'
                or row.get('baseCoin')!=symbol or row.get('isPreListing')):
            raise ValueError('unsupported Bybit contract')
        lot=row['lotSizeFilter']
        result=Instrument(venue,symbol,lot['qtyStep'],float(lot['minOrderQty']),
                          float(lot['minNotionalValue']),int(row['fundingInterval'])*60)
    elif venue=='okx':
        if (row.get('instId')!=symbol+'-USDT-SWAP' or row.get('state')!='live'
                or row.get('ctType')!='linear' or row.get('settleCcy')!='USDT'
                or row.get('ctValCcy')!=symbol):
            raise ValueError('unsupported OKX contract')
        unit=Decimal(row['ctVal'])*Decimal(row.get('ctMult') or '1')
        result=Instrument(venue,symbol,str(Decimal(row['lotSz'])*unit),
                          float(Decimal(row['minSz'])*unit),0.,contract_value=float(unit))
    else:
        raise ValueError('unsupported venue')
    if (not Decimal(result.base_step).is_finite() or Decimal(result.base_step)<=0
            or not all(math.isfinite(x) and x>=0 for x in [result.min_quantity,result.min_notional])
            or result.contract_value<=0):
        raise ValueError('invalid contract quantities')
    return result


def parse_quotes(venue,symbol,ticker,funding,mark,instrument,server_ms=None):
    if venue=='bybit':
        if ticker.get('symbol')!=symbol+'USDT':
            raise ValueError('Bybit ticker symbol mismatch')
        interval=int(ticker.get('fundingIntervalHour') or instrument.interval_seconds/3600)*3600
        return Quote(venue,symbol,float(ticker['bid1Price']),float(ticker['ask1Price']),
            float(ticker['bid1Size']),float(ticker['ask1Size']),float(ticker['markPrice']),
            float(ticker['fundingRate']),int(ticker['nextFundingTime']),interval,int(server_ms),int(server_ms))
    if funding.get('method')!='current_period':
        raise ValueError('unsupported OKX funding method')
    return Quote(venue,symbol,float(ticker['bidPx']),float(ticker['askPx']),
        float(ticker['bidSz'])*instrument.contract_value,float(ticker['askSz'])*instrument.contract_value,
        float(mark['markPx']),float(funding['fundingRate']),int(funding['fundingTime']),
        (int(funding['nextFundingTime'])-int(funding['fundingTime']))//1000,
        min(int(ticker['ts']),int(mark['ts'])),int(funding['ts']))


def settled_rate(venue,rows,timestamp):
    key='fundingRateTimestamp' if venue=='bybit' else 'fundingTime'
    item=next((r for r in rows if int(r[key])==timestamp),None)
    if not item:
        raise ValueError(f'missing settled funding: {venue} {timestamp}')
    value=item.get('fundingRate') if venue=='bybit' else item.get('realizedRate')
    if value is None or value=='':
        raise ValueError('no realized funding value')
    result=float(value)
    if not math.isfinite(result):
        raise ValueError('nonfinite settled funding')
    return result


def candle_open(rows,timestamp):
    candle=next((r for r in rows if int(r[0])==timestamp),None)
    if not candle or not math.isfinite(float(candle[1])) or float(candle[1])<=0:
        raise ValueError('missing exact settlement-minute mark candle')
    return float(candle[1])


class PublicProvider:
    def __init__(self,symbols):
        if not symbols or not set(symbols)<= {'BTC','ETH'}:
            raise ValueError('paper universe is BTC/ETH')
        self.symbols=symbols
        self.instruments={}
        self.schema_errors=[]

    def get(self,venue,path,params):
        if path not in PUBLIC[venue]:
            raise ValueError('only approved public market-data GET endpoints are permitted')
        response=requests.get(BASE[venue]+path,params=params,timeout=(4,8))
        response.raise_for_status()
        data=response.json()
        if (venue=='bybit' and data.get('retCode')!=0) or (venue=='okx' and data.get('code')!='0'):
            raise ValueError(f'{venue}: {data.get("retMsg",data.get("msg"))}')
        return data

    def load_instruments(self):
        def one(job):
            v,s=job
            if v=='bybit':
                raw=self.get(v,'/v5/market/instruments-info',{'category':'linear','symbol':s+'USDT'})
                rows=raw['result']['list']
            else:
                raw=self.get(v,'/api/v5/public/instruments',{'instType':'SWAP','instId':s+'-USDT-SWAP'})
                rows=raw['data']
            if len(rows)!=1:
                raise ValueError(f'missing unique instrument {v}/{s}')
            return s,v,parse_instrument(v,s,rows[0])
        self.schema_errors=[]
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs={pool.submit(one,(v,s)):(v,s) for s in self.symbols for v in BASE}
            for future in as_completed(jobs):
                try:
                    s,v,i=future.result()
                    self.instruments.setdefault(s,{})[v]=i
                except Exception as e:
                    self.schema_errors.append(f'规格 {jobs[future]}: {e}')
        return self.instruments

    def snapshot(self):
        jobs=[]
        for s in self.symbols:
            jobs.append((s,'bybit','ticker','/v5/market/tickers',{'category':'linear','symbol':s+'USDT'}))
            for kind,path,params in [
                ('ticker','/api/v5/market/ticker',{'instId':s+'-USDT-SWAP'}),
                ('funding','/api/v5/public/funding-rate',{'instId':s+'-USDT-SWAP'}),
                ('mark','/api/v5/public/mark-price',{'instType':'SWAP','instId':s+'-USDT-SWAP'})]:
                jobs.append((s,'okx',kind,path,params))
        def one(job):
            s,v,k,path,params=job
            return s,v,k,self.get(v,path,params)
        raw={s:{v:{} for v in BASE} for s in self.symbols}
        raw['_errors']=list(self.schema_errors)
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures={pool.submit(one,job):job for job in jobs}
            for future in as_completed(futures):
                try:
                    s,v,k,r=future.result();raw[s][v][k]=r
                except Exception as e:
                    raw['_errors'].append(f'{futures[future][:3]}: {e}')
        panel={}
        for s in self.symbols:
            panel[s]={};vs=raw[s]
            for v in BASE:
                try:
                    i=self.instruments[s][v]
                    if v=='bybit':
                        b=vs[v]['ticker']
                        panel[s][v]=parse_quotes(v,s,b['result']['list'][0],None,None,i,b['time'])
                    else:
                        o=vs[v];ticker=o['ticker']['data'][0];mark=o['mark']['data'][0]
                        dummy={'method':'current_period','fundingRate':'0','fundingTime':'0','nextFundingTime':'0','ts':'0'}
                        # Entry needs a fresh forecast; exits need only executable prices and mark.
                        panel[s][v]=parse_quotes(v,s,ticker,dummy,mark,i)
                        panel[s][v]=parse_quotes(v,s,ticker,o['funding']['data'][0],mark,i)
                except Exception as e:
                    raw['_errors'].append(f'{v}/{s} 解析: {e}')
        return panel,raw

    def funding_events(self,venue,symbol,start_ms,end_ms):
        """Discover actual events with descending pagination, regardless of predicted intervals."""
        if (end_ms<start_ms or end_ms-start_ms>85*86400000
                or start_ms<int(time.time()*1000)-85*86400000):
            raise ValueError('funding history coverage window unavailable; manual reconciliation required')
        cursor=end_ms;events={};limit=200 if venue=='bybit' else 100
        for _ in range(100):
            if venue=='bybit':
                raw=self.get(venue,'/v5/market/funding/history',{'category':'linear','symbol':symbol+'USDT',
                    'startTime':start_ms+1,'endTime':cursor,'limit':limit})
                rows=raw['result']['list'];key='fundingRateTimestamp'
            else:
                raw=self.get(venue,'/api/v5/public/funding-rate-history',{'instId':symbol+'-USDT-SWAP',
                    'after':cursor+1,'limit':limit})
                rows=raw['data'];key='fundingTime'
            for row in rows:
                ts=int(row[key])
                if start_ms<ts<=end_ms:
                    events[ts]=settled_rate(venue,[row],ts)
            if not rows or len(rows)<limit or min(int(r[key]) for r in rows)<=start_ms:
                break
            following=min(int(r[key]) for r in rows)-1
            if following>=cursor:raise ValueError('funding history pagination did not advance')
            cursor=following
        else:
            raise ValueError('funding history pagination incomplete')
        def point(item):
            ts,rate=item
            return FundingPoint(venue,symbol,ts,rate,self.settlement_mark(venue,symbol,ts))
        with ThreadPoolExecutor(max_workers=4) as pool:
            return list(pool.map(point,sorted(events.items())))

    def funding(self,venue,symbol,timestamp):
        if venue=='bybit':
            raw=self.get(venue,'/v5/market/funding/history',{'category':'linear','symbol':symbol+'USDT',
                'startTime':timestamp,'endTime':timestamp+1,'limit':2})
            rate=settled_rate(venue,raw['result']['list'],timestamp)
        else:
            raw=self.get(venue,'/api/v5/public/funding-rate-history',{'instId':symbol+'-USDT-SWAP',
                'after':timestamp+1,'limit':2})
            rate=settled_rate(venue,raw['data'],timestamp)
        return FundingPoint(venue,symbol,timestamp,rate,self.settlement_mark(venue,symbol,timestamp))

    def settlement_mark(self,venue,symbol,timestamp):
        if venue=='bybit':
            mark=self.get(venue,'/v5/market/mark-price-kline',{'category':'linear','symbol':symbol+'USDT',
                'interval':'1','start':timestamp,'end':timestamp+59999,'limit':1})
            price=candle_open(mark['result']['list'],timestamp)
        else:
            mark=self.get(venue,'/api/v5/market/history-mark-price-candles',{'instId':symbol+'-USDT-SWAP',
                'bar':'1m','after':timestamp+60000,'before':timestamp-1,'limit':2})
            price=candle_open(mark['data'],timestamp)
        return price
