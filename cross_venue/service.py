"""Public-data-only cross-venue paper service; never sends exchange orders."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import secrets
import sys
import threading
import time

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
import jwt
from fastapi import Body, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPBearer, HTTPAuthorizationCredentials
from cross_venue.engine import PaperEngine, Quote
from cross_venue.frequi import FreqUIBridge
from cross_venue.provider import PublicProvider

log = logging.getLogger('cross_venue')


class Runtime:
    def __init__(self, config, database, provider=None):
        self.cfg = config
        self.engine = PaperEngine(config, database)
        self.provider = provider or PublicProvider(config['symbols'])
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.instruments = {}
        self.quotes = {}
        self.candidates = {}
        self.health = {'status': 'initializing', 'note': '等待第一次公开行情采集', 'updated_ms': None}
        control = self.engine.db.execute("SELECT payload FROM state WHERE key='entries_enabled'").fetchone()
        self.entries_enabled = json.loads(control[0]) if control else True
        previous = self.engine.db.execute('SELECT payload FROM snapshots ORDER BY ts DESC LIMIT 1').fetchone()
        if previous:
            snapshot = json.loads(previous[0])
            self.quotes = {s: {v: Quote(**q) for v, q in rows.items()}
                           for s, rows in snapshot.get('quotes', {}).items()}

    def _summary(self, now):
        active = self.engine.active_positions()
        q = self.quotes.get(active[0]['symbol'], {}) if active else {}
        return self.engine.summary(q, now)

    def public_summary(self):
        with self.lock:
            now = int(time.time()*1000)
            health = dict(self.health)
            summary = self._summary(now)
            updated = health.get('updated_ms')
            health['age_seconds'] = (now-updated)/1000 if updated else None
            if updated and now-updated > max(90, self.cfg['poll_seconds']*3)*1000:
                health.update(status='stale', note='公开行情采集已过期，禁止新开仓')
            if summary['open_trades'] and not summary['marks_fresh']:
                message = '双边持仓行情不完整或过期，估值为最近记录，退出暂缓'
                health.update(status='fault', note=message, faults=[*health.get('faults',[]),message])
            return {**summary, 'health': health, 'candidates': self.candidates,
                    'quotes': {s: {v: asdict(q) for v, q in rows.items()} for s, rows in self.quotes.items()},
                    'entries_enabled': self.entries_enabled, 'strategy': self.cfg['strategy'],
                    'bot_name': self.cfg['bot_name'], 'dry_run': True,
                    'assumptions': {k: self.cfg[k] for k in ['fee', 'slippage', 'basis_buffer', 'weight']}}

    def _persist(self, now, raw=None):
        summary = self._summary(now)
        payload = {'quotes': {s: {v: asdict(q) for v, q in rows.items()} for s, rows in self.quotes.items()},
                   'candidates': self.candidates, 'health': self.health, 'raw': raw}
        self.engine.save_snapshot(now, payload, summary)

    def set_entries(self, enabled):
        with self.lock, self.engine.lock, self.engine.db:
            self.entries_enabled = enabled
            self.engine.db.execute("INSERT OR REPLACE INTO state VALUES ('entries_enabled',?)", (json.dumps(enabled),))

    def refresh(self):
        if not all(set(self.instruments.get(s,{}))=={'bybit','okx'} for s in self.cfg['symbols']):
            self.instruments = self.provider.load_instruments()
        try:
            quotes, raw = self.provider.snapshot()
        except Exception as e:
            quotes, raw = {}, {'_errors':[str(e)]}
        now = int(time.time()*1000)
        reconciled, failures = [], list(raw.get('_errors',[]))
        with self.lock, self.engine.lock:
            due = [(p['id'], v, p['symbol'], self.engine.coverage(p,v), target)
                   for p in self.engine.pending_positions(now)
                   for v, target in self.engine.missing_funding(p, now) if now >= target+120000]
        # Public network requests stay outside the portfolio lock so controls remain responsive.
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = {pool.submit(self.provider.funding_events, v, s, start, end): (pid,v,s,end)
                    for pid,v,s,start,end in due}
            for future in as_completed(jobs):
                pid, v, s, end = jobs[future]
                try:
                    reconciled.append((pid,v,end,future.result()))
                except Exception as e:
                    failures.append(f'{v}/{s} 资金费对账: {e}')
        now = int(time.time()*1000)
        with self.lock:
            for symbol,rows in quotes.items():
                self.quotes.setdefault(symbol,{}).update(rows)
            for pid,venue,end,points in reconciled:
                self.engine.mark_reconciled(pid,venue,end,points,now)
            for position in self.engine.active_positions():
                q = quotes.get(position['symbol'], {})
                reason = self.engine.exit_reason(position, q, now)
                if reason:
                    try:
                        closed = self.engine.close_position(position['id'], q, now, reason)
                        log.info('PAPER close portfolio=%s reason=%s profit=%.4f', closed['id'], reason, closed['profit'])
                    except ValueError as e:
                        failures.append(str(e))
            self.candidates = {s: self.engine.candidate(quotes.get(s, {}), self.instruments.get(s,{}), now)
                               for s in self.cfg['symbols']}
            eligible = [c for c in self.candidates.values() if c['eligible']]
            if self.entries_enabled and eligible:
                candidate = max(eligible, key=lambda c: c['expected_net'])
                opened = self.engine.open_position(candidate, quotes[candidate['symbol']], now)
                log.info('PAPER open portfolio=%s symbol=%s quantity=%s long=%s short=%s',
                         opened['id'], opened['symbol'], opened['quantity'], opened['long_venue'], opened['short_venue'])
            summary = self._summary(now)
            note = '；'.join(f'{s}: {c["reason"]}' for s, c in self.candidates.items())
            self.health = {'status': 'holding' if summary['open_trades'] else 'monitoring',
                           'note': note, 'updated_ms': now, 'faults': failures}
            if summary['funding_pending']:
                self.health.update(status='reconciling', note='等待已结算资金费对账；禁止新增组合')
            for position in self.engine.active_positions():
                if not self.engine._quotes(quotes.get(position['symbol'],{}),now):
                    failures.append(f"{position['symbol']} 双边可执行行情不完整或过期，退出暂缓")
            if failures:
                self.health.update(status='fault', note='；'.join(failures))
            self._persist(now, raw)
            log.info('public snapshot equity=%.4f portfolios=%s %s', summary['equity'], summary['open_trades'], self.health['note'])

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.refresh()
            except Exception as e:
                log.exception('public-data cycle failed')
                with self.lock:
                    self.health.update(status='fault', note=f'公开数据故障，禁止新开仓: {e}')
                    self._persist(int(time.time()*1000))
            self.stop_event.wait(self.cfg['poll_seconds'])

    def force_exit(self, portfolio_id=None):
        # Freeze the requested portfolio set before network IO; a replacement is never a target.
        with self.lock:
            targets={p['id'] for p in self.engine.active_positions()
                     if portfolio_id is None or p['id']==portfolio_id}
            if portfolio_id is not None and not targets:
                raise ValueError('requested paper portfolio is already closed')
        if not targets:
            return 0
        # Refresh both executable paper prices before a manual close; stale cached prices cannot fill.
        quotes, _ = self.provider.snapshot()
        now = int(time.time()*1000)
        with self.lock:
            active=[p for p in self.engine.active_positions() if p['id'] in targets]
            if portfolio_id is not None and not active:
                raise ValueError('requested paper portfolio closed during price refresh')
            self.quotes = quotes
            for p in active:
                self.engine.close_position(p['id'], quotes.get(p['symbol'], {}), now, 'manual_paper_exit')
            self._persist(now)
            return len(active)


def create_app(runtime):
    app = FastAPI(title='Cross-venue paper portfolio')
    app.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:8089','http://localhost:8089',
                                                  'http://127.0.0.1:8090','http://127.0.0.1:8086'],
                       allow_credentials=True,allow_methods=['GET','POST','DELETE','OPTIONS'],allow_headers=['Authorization','Content-Type'])
    basic = HTTPBasic(auto_error=False)
    bearer = HTTPBearer(auto_error=False)
    bridge = FreqUIBridge(runtime)
    api = runtime.cfg['api_server']
    secret_key = api.get('jwt_secret_key') or secrets.token_urlsafe(32)
    audience = runtime.cfg['bot_name']

    def basic_valid(creds):
        return (creds is not None and secrets.compare_digest(creds.username.encode(),api['username'].encode())
                and secrets.compare_digest(creds.password.encode(),api['password'].encode()))

    def check_token(token,kind):
        try:
            payload=jwt.decode(token,secret_key,algorithms=['HS256'],audience=audience,issuer='cross_venue_paper')
            if payload.get('type')!=kind or payload.get('identity',{}).get('u')!=api['username']:
                raise jwt.InvalidTokenError('wrong token purpose')
        except jwt.PyJWTError as e:
            raise HTTPException(401,'Invalid or expired paper token',headers={'WWW-Authenticate':'Bearer'}) from e

    def token(kind):
        now=int(time.time())
        return jwt.encode({'identity':{'u':api['username']},'type':kind,'iat':now,
                           'exp':now+(900 if kind=='access' else 30*86400),
                           'iss':'cross_venue_paper','aud':audience},secret_key,algorithm='HS256')

    def authenticated(creds: HTTPBasicCredentials | None = Depends(basic),
                      authorization: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if authorization:
            check_token(authorization.credentials,'access')
        elif not basic_valid(creds):
            raise HTTPException(401,'Invalid credentials',headers={'WWW-Authenticate':'Basic'})

    auth = [Depends(authenticated)]

    @app.get('/')
    def page():
        return RedirectResponse('http://127.0.0.1:8089/?bot='+runtime.cfg['bot_name'])

    @app.get('/portfolio', response_class=HTMLResponse)
    def portfolio_page():
        return Path(__file__).with_name('index.html').read_text()

    @app.post('/api/v1/token/login')
    def login(creds: HTTPBasicCredentials | None = Depends(basic)):
        if not basic_valid(creds):raise HTTPException(401,'Invalid credentials')
        return {'access_token':token('access'),'refresh_token':token('refresh')}

    @app.post('/api/v1/token/refresh')
    def refresh_token(authorization: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if not authorization:raise HTTPException(401,'Refresh token required')
        check_token(authorization.credentials,'refresh')
        return {'access_token':token('access')}

    @app.get('/summary')
    def summary():
        return runtime.public_summary()

    @app.get('/api/v1/ping')
    def ping():
        return {'status': 'pong'}

    @app.get('/api/v1/show_config', dependencies=auth)
    def config():
        return bridge.config()

    @app.get('/api/v1/profit', dependencies=auth)
    def profit():
        return bridge.profit()

    @app.get('/api/v1/portfolio', dependencies=auth)
    def portfolio():
        return runtime.public_summary()

    @app.get('/api/v1/count', dependencies=auth)
    def count():
        return bridge.count()

    @app.get('/api/v1/status', dependencies=auth)
    def positions():
        return [t for t in bridge.trades() if t['is_open']]

    @app.get('/api/v1/trades', dependencies=auth)
    def trades(limit:int=500,offset:int=0):
        rows=sorted((t for t in bridge.trades() if not t['is_open']),
                    key=lambda t:(t['close_timestamp'],t['trade_id']),reverse=True)
        subset=rows[max(offset,0):max(offset,0)+min(max(limit,1),500)]
        return {'trades':subset,'trades_count':len(subset),'total_trades':len(rows),'offset':max(offset,0)}

    @app.get('/api/v1/trades/{trade_id}/custom-data', dependencies=auth)
    def custom_data(trade_id:int):
        return []

    @app.get('/api/v1/trades/{trade_id}', dependencies=auth)
    @app.get('/api/v1/trade/{trade_id}', dependencies=auth)
    def trade(trade_id:int):
        row=next((t for t in bridge.trades() if t['trade_id']==trade_id),None)
        if row is None:raise HTTPException(404,'Paper leg not found')
        return row

    @app.get('/api/v1/balance', dependencies=auth)
    def balance():
        return bridge.balance()

    @app.get('/api/v1/version', dependencies=auth)
    def version():
        return {'version':'CrossVenuePaper 1.1'}

    @app.get('/api/v1/whitelist', dependencies=auth)
    def whitelist():
        pairs=[s+'/USDT:USDT' for s in runtime.cfg['symbols']]
        return {'whitelist':pairs,'length':len(pairs),'method':['Static paper BTC/ETH universe']}

    @app.get('/api/v1/blacklist', dependencies=auth)
    def blacklist():
        return {'blacklist':[],'blacklist_expanded':[],'length':0,'method':[],'errors':{}}

    @app.get('/api/v1/locks', dependencies=auth)
    def locks():
        return {'lock_count':0,'locks':[]}

    @app.get('/api/v1/performance', dependencies=auth)
    def performance():
        return bridge.performance()

    @app.get('/api/v1/logs', dependencies=auth)
    def logs(limit:int=100):
        filename=Path(runtime.engine.db.execute('PRAGMA database_list').fetchone()[2])
        path=filename.parent/'logs'/f"{runtime.cfg['bot_name']}.log"
        rows=[]
        if path.is_file():
            with path.open('rb') as f:
                f.seek(0,2);f.seek(max(0,f.tell()-200000))
                rows=[[line.split(' - ',1)[0],0,'cross_venue','INFO',line] for line in f.read().decode(errors='replace').splitlines()[-min(max(limit,1),1000):]]
        return {'log_count':len(rows),'logs':rows}

    @app.get('/api/v1/health', dependencies=auth)
    def health():
        return runtime.public_summary()['health']

    @app.get('/api/v1/daily', dependencies=auth)
    def daily(timescale: int = 14,scope:str='legs'):
        return bridge.daily(timescale,portfolio=scope=='portfolio')

    @app.post('/api/v1/pause', dependencies=auth)
    @app.post('/api/v1/stop', dependencies=auth)
    @app.post('/api/v1/stopbuy', dependencies=auth)
    @app.post('/api/v1/stopentry', dependencies=auth)
    def pause():
        runtime.set_entries(False)
        return {'status': 'paper entries paused; existing portfolios remain managed'}

    @app.post('/api/v1/start', dependencies=auth)
    def start():
        runtime.set_entries(True)
        return {'status': 'paper entries enabled'}

    @app.post('/api/v1/forceexit', dependencies=auth)
    @app.post('/api/v1/forcesell', dependencies=auth)
    def exit_all(payload:dict=Body(default={})):
        if payload.get('amount') is not None or payload.get('ordertype') not in (None,'market'):
            raise HTTPException(409,'仅支持整个双边组合的模拟市价退出，不支持部分/限价退出')
        trade_id=payload.get('tradeid','all')
        portfolio_id=None
        if str(trade_id)!='all':
            leg=next((t for t in bridge.trades() if t['is_open'] and str(t['trade_id'])==str(trade_id)),None)
            if leg is None:raise HTTPException(404,'Paper leg not found')
            portfolio_id=leg['portfolio_id']
        try:
            closed=runtime.force_exit(portfolio_id)
        except Exception as e:
            raise HTTPException(409, f'Cannot close both paper legs: {e}') from e
        return {'status': 'both paper legs closed' if closed else 'no targeted open paper portfolio remains'}

    @app.post('/api/v1/forceenter', dependencies=auth)
    @app.post('/api/v1/forcebuy', dependencies=auth)
    @app.post('/api/v1/reload_config', dependencies=auth)
    @app.delete('/api/v1/trades/{trade_id}', dependencies=auth)
    def unsupported():
        raise HTTPException(409,'跨平台模拟组合不支持手工单腿入场、删除持仓或在线重载配置')

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    path = Path(args.config).resolve()
    cfg = json.loads(path.read_text())
    for extra in cfg.get('add_config_files', []):
        addition = json.loads((path.parent/extra).read_text())
        for k, v in addition.items():
            cfg[k] = {**cfg.get(k, {}), **v} if isinstance(v, dict) else v
    if cfg['api_server'].get('listen_ip_address') != '127.0.0.1':
        raise ValueError('paper API must bind to localhost')
    if not cfg['db_url'].startswith('sqlite:///'):
        raise ValueError('paper service requires SQLite')
    root = path.parents[2]
    database = root/cfg['db_url'].removeprefix('sqlite:///')
    logs = root/'user_data/logs'
    logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler(logs/f"{cfg['bot_name']}.log")])
    runtime = Runtime(cfg, database)
    worker = threading.Thread(target=runtime.run, daemon=True, name='public-paper-data')
    worker.start()
    try:
        uvicorn.run(create_app(runtime), host='127.0.0.1', port=cfg['api_server']['listen_port'], log_level='warning')
    finally:
        runtime.stop_event.set()
        worker.join(timeout=20)
        if not worker.is_alive():
            runtime.engine.close_db()


if __name__ == '__main__':
    main()
