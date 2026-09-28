"""Two-leg paper accounting. Forecast rates never create wallet income."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
import json
import math
from pathlib import Path
import sqlite3
import threading


@dataclass(frozen=True)
class Instrument:
    venue: str
    symbol: str
    base_step: str
    min_quantity: float
    min_notional: float
    interval_seconds: int = 0
    contract_value: float = 1.


@dataclass(frozen=True)
class Quote:
    venue: str
    symbol: str
    bid: float
    ask: float
    bid_qty: float
    ask_qty: float
    mark: float
    rate: float
    settlement_ms: int
    interval_seconds: int
    timestamp_ms: int
    rate_timestamp_ms: int


@dataclass(frozen=True)
class FundingPoint:
    venue: str
    symbol: str
    timestamp_ms: int
    rate: float
    mark: float


def common_step(values):
    decimals = [Decimal(x) for x in values]
    if any(x <= 0 or not x.is_finite() for x in decimals):
        raise ValueError('invalid quantity increment')
    exponent = min(x.as_tuple().exponent for x in decimals)
    unit = Decimal(10) ** exponent
    return Decimal(math.lcm(*(int(x / unit) for x in decimals))) * unit


class PaperEngine:
    def __init__(self, config, database):
        if config.get('dry_run') is not True:
            raise ValueError('cross-venue service only supports dry_run=true')
        self.cfg = config
        if not 0 < config['weight'] <= .4 or config['wallet_total'] <= 0:
            raise ValueError('invalid paper allocation')
        for key in ['fee', 'slippage', 'basis_buffer']:
            if not math.isfinite(config[key]) or config[key] < 0:
                raise ValueError(f'invalid {key}')
        self.lock = threading.RLock()
        Path(database).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS positions (id INTEGER PRIMARY KEY, data TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS funding (position_id INTEGER, venue TEXT, ts INTEGER,
             rate REAL, mark REAL, amount REAL, PRIMARY KEY(position_id,venue,ts));
          CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS snapshots (ts INTEGER PRIMARY KEY, payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS funding_coverage (position_id INTEGER, venue TEXT,
             through_ms INTEGER, PRIMARY KEY(position_id,venue));
        ''')
        self.db.commit()
        signature = json.dumps({k: config[k] for k in ['wallet_total', 'fee', 'slippage',
                               'basis_buffer', 'weight']}, sort_keys=True)
        previous = self.db.execute("SELECT payload FROM state WHERE key='accounting_config'").fetchone()
        if previous and previous[0] != signature:
            self.db.close()
            raise ValueError('paper accounting configuration changed; use a new database')
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO state VALUES ('accounting_config',?)", (signature,))

    def close_db(self):
        self.db.close()

    def positions(self):
        with self.lock:
            return [json.loads(x[0]) for x in self.db.execute('SELECT data FROM positions ORDER BY id')]

    def active_positions(self):
        return [p for p in self.positions() if p['state'] == 'open']

    def _save(self, p):
        self.db.execute('UPDATE positions SET data=? WHERE id=?', (json.dumps(p), p['id']))

    def _valid(self, q, now, rates=False):
        if not q or q.venue not in ('bybit', 'okx'):
            return False
        numbers = [q.bid, q.ask, q.mark, q.bid_qty, q.ask_qty]
        if not all(math.isfinite(x) and x > 0 for x in numbers) or q.ask < q.bid:
            return False
        if not -5000 <= now-q.timestamp_ms <= self.cfg['quote_age_seconds']*1000:
            return False
        if rates:
            return (math.isfinite(q.rate) and q.interval_seconds > 0
                    and -5000 <= now-q.rate_timestamp_ms <= self.cfg['rate_age_seconds']*1000)
        return True

    def _quotes(self, quotes, now, rates=False):
        return (set(quotes) == {'bybit', 'okx'} and
                all(self._valid(q, now, rates) and q.venue == v for v, q in quotes.items()) and
                len({q.symbol for q in quotes.values()}) == 1)

    def _fill(self, q, side):
        return q.ask*(1+self.cfg['slippage']) if side == 1 else q.bid*(1-self.cfg['slippage'])

    def reconciliation_target(self, position, now):
        # Leave publication time for newly settled events. Closed positions require full coverage.
        return position.get('closed_ms', max(position['opened_ms'], ((now-120000)//60000)*60000))

    def coverage(self, position, venue):
        row = self.db.execute('SELECT through_ms FROM funding_coverage WHERE position_id=? AND venue=?',
                              (position['id'],venue)).fetchone()
        return row[0] if row else position['opened_ms']

    def missing_funding(self, p, now):
        target = self.reconciliation_target(p, now)
        return [(v,target) for v in p['legs'] if self.coverage(p,v)<target]

    def mark_reconciled(self, position_id, venue, through_ms, points, now):
        with self.lock:
            p = next(p for p in self.positions() if p['id']==position_id)
            if (venue not in p['legs'] or through_ms>min(now-120000,p.get('closed_ms',now))
                    or through_ms<p['opened_ms'] or any(not self._funding_point_valid(p,x,now)
                    or x.venue!=venue or x.timestamp_ms>through_ms for x in points)):
                raise ValueError('invalid history coverage or funding points')
            for point in points:
                self.book_funding(position_id,point,now)
            with self.db:
                self.db.execute('INSERT INTO funding_coverage VALUES (?,?,?) ON CONFLICT(position_id,venue) '
                                'DO UPDATE SET through_ms=MAX(through_ms,excluded.through_ms)',
                                (position_id,venue,through_ms))

    def pending_positions(self, now):
        with self.lock:
            return [p for p in self.positions() if self.missing_funding(p, now)]

    def candidate(self, quotes, instruments, now):
        result = {'eligible':False, 'reason':'行情或资金费预估过期/不完整'}
        with self.lock:
            if self.active_positions():
                return {**result, 'reason':'已有双边组合持仓'}
            if self.pending_positions(now):
                return {**result, 'reason':'等待历史资金费对账'}
            if not self._quotes(quotes, now, rates=True) or set(instruments) != {'bybit','okx'}:
                return result
            if len({q.settlement_ms for q in quotes.values()}) != 1 or len({q.interval_seconds for q in quotes.values()}) != 1:
                return {**result, 'reason':'两平台结算时间或周期不一致'}
            next_ts = quotes['bybit'].settlement_ms
            left = (next_ts-now)/1000
            if any(i.venue != v or i.symbol != quotes[v].symbol for v,i in instruments.items()):
                return {**result, 'reason':'合约规格与行情不匹配'}
            summary = self.summary(quotes, now)
            target = min(self.cfg['weight']*summary['equity'],
                         *(w['cash']*.8 for w in summary['wallets'].values()))
            step = common_step([i.base_step for i in instruments.values()])
            ceiling = min(target/max(self._fill(q,1)*(1+self.cfg['fee']) for q in quotes.values()),
                          *(min(q.bid_qty,q.ask_qty) for q in quotes.values()))
            qty = float((Decimal(str(max(ceiling,0))) / step).to_integral_value(rounding=ROUND_FLOOR)*step)
            if any(qty < i.min_quantity or qty*quotes[v].mark < i.min_notional for v,i in instruments.items()) or qty <= 0:
                return {**result, 'reason':'数量/深度不足，无法同时满足两边最小订单'}
            r = self._opportunity(quotes, qty)
            in_window = self.cfg['min_entry_seconds'] <= left <= self.cfg['max_entry_seconds']
            return {**r, 'symbol':quotes['bybit'].symbol, 'settlement_ms':next_ts,
                    'interval_seconds':quotes['bybit'].interval_seconds,
                    'quantity':qty, 'eligible':in_window and r['expected_net']>0,
                    'reason':('尚未进入结算前开仓窗口' if not in_window else
                              '可模拟开仓' if r['expected_net']>0 else '预估资金费不足以覆盖开平仓成本与缓冲')}

    def _opportunity(self, quotes, qty):
        short = max(quotes, key=lambda v: quotes[v].mark*quotes[v].rate)
        long = next(v for v in quotes if v != short)
        funding = qty*(quotes[short].mark*quotes[short].rate-quotes[long].mark*quotes[long].rate)
        cost = 0.
        for venue, side in [(long,1),(short,-1)]:
            enter, leave = self._fill(quotes[venue], side), self._fill(quotes[venue], -side)
            cost += -side*qty*(leave-enter)+qty*(enter+leave)*self.cfg['fee']
        notional = qty*sum(q.mark for q in quotes.values())/2
        buffer = notional*self.cfg['basis_buffer']
        return {'short_venue':short,'long_venue':long,'funding_estimate':funding,
                'roundtrip_cost':cost,'basis_buffer':buffer,'expected_net':funding-cost-buffer,
                'notional_per_leg':notional, 'spread_bps':funding/notional*10000}

    def open_position(self, candidate, quotes, now):
        with self.lock, self.db:
            if (not candidate.get('eligible') or self.active_positions() or self.pending_positions(now)
                    or not self._quotes(quotes, now, rates=True)
                    or len({q.settlement_ms for q in quotes.values()}) != 1
                    or len({q.interval_seconds for q in quotes.values()}) != 1
                    or any(q.symbol != candidate['symbol'] or q.settlement_ms != candidate['settlement_ms']
                           or q.interval_seconds != candidate['interval_seconds'] for q in quotes.values())
                    or not self.cfg['min_entry_seconds'] <= (candidate['settlement_ms']-now)/1000 <= self.cfg['max_entry_seconds']):
                raise ValueError('no valid matched paper entry')
            qty = candidate['quantity']
            if not math.isfinite(qty) or qty <= 0:
                raise ValueError('invalid entry quantity')
            summary = self.summary(quotes, now)
            for v, q in quotes.items():
                cost = qty*self._fill(q, 1)*(1+self.cfg['fee'])
                if cost > min(self.cfg['weight']*summary['equity'], summary['wallets'][v]['cash']*.8)+1e-8:
                    raise ValueError('entry exceeds portfolio or venue margin budget')
            refreshed = self._opportunity(quotes, qty)
            if refreshed['expected_net'] <= 0 or refreshed['long_venue'] != candidate['long_venue']:
                raise ValueError('entry edge changed')
            if any(qty > min(q.bid_qty,q.ask_qty) for q in quotes.values()):
                raise ValueError('insufficient displayed liquidity')
            legs = {}
            for v,q in quotes.items():
                side = 1 if v == candidate['long_venue'] else -1
                fill = self._fill(q,side)
                legs[v] = {'side':side,'entry':fill,'entry_fee':qty*fill*self.cfg['fee'],
                           'notional':qty*fill,'funding':0.}
            p = {'state':'open','symbol':candidate['symbol'],'opened_ms':now,'quantity':qty,
                 'settlement_ms':candidate['settlement_ms'],'interval_seconds':candidate['interval_seconds'],
                 'long_venue':candidate['long_venue'],'short_venue':candidate['short_venue'],'legs':legs}
            cursor = self.db.execute('INSERT INTO positions(data) VALUES (?)',(json.dumps(p),))
            p['id'] = cursor.lastrowid
            self._save(p)
            return p

    def _funding_point_valid(self,p,point,now):
        return (point.venue in p['legs'] and point.symbol==p['symbol']
                and p['opened_ms']<point.timestamp_ms<=min(now,p.get('closed_ms',now))
                and math.isfinite(point.rate) and math.isfinite(point.mark) and point.mark>0)

    def book_funding(self, position_id, point, now):
        with self.lock, self.db:
            p = next(p for p in self.positions() if p['id']==position_id)
            if not self._funding_point_valid(p,point,now):
                return False
            side = p['legs'][point.venue]['side']
            cash = -side*p['quantity']*point.mark*point.rate
            inserted = self.db.execute('INSERT OR IGNORE INTO funding VALUES (?,?,?,?,?,?)',
                (p['id'],point.venue,point.timestamp_ms,point.rate,point.mark,cash)).rowcount
            if not inserted:
                return False
            leg = p['legs'][point.venue]
            leg['funding'] += cash
            if p['state']=='closed':
                leg['profit'] += cash
                p['profit'] += cash
            self._save(p)
            return True

    def close_position(self, position_id, quotes, now, reason):
        with self.lock, self.db:
            p = next(p for p in self.active_positions() if p['id']==position_id)
            if not self._quotes(quotes,now) or any(q.symbol != p['symbol'] for q in quotes.values()):
                raise ValueError('both fresh quotes required to close')
            if any(p['quantity'] > (q.bid_qty if p['legs'][v]['side']==1 else q.ask_qty) for v,q in quotes.items()):
                raise ValueError('insufficient displayed exit liquidity')
            for v,l in p['legs'].items():
                l['exit'] = self._fill(quotes[v],-l['side'])
                l['exit_fee'] = p['quantity']*l['exit']*self.cfg['fee']
                l['profit'] = (l['side']*p['quantity']*(l['exit']-l['entry'])
                               -l['entry_fee']-l['exit_fee']+l['funding'])
            p.update(state='closed',closed_ms=now,exit_reason=reason,
                     profit=sum(l['profit'] for l in p['legs'].values()))
            self._save(p)
            return p

    def exit_reason(self,p,quotes,now):
        if not self._quotes(quotes,now):
            return None
        for v,l in p['legs'].items():
            pnl=l['side']*p['quantity']*(quotes[v].mark-l['entry'])+l['funding']-l['entry_fee']
            if pnl <= -self.cfg['stoploss']*l['notional']:
                return 'leg_risk_limit'
        if now-p['opened_ms'] >= self.cfg['max_hold_hours']*3600000:
            return 'max_hold'
        if now >= p['settlement_ms']+120000 and not self.missing_funding(p,now):
            return 'settlement_reconciled'
        return None

    def summary(self,quotes,now):
        with self.lock:
            positions=self.positions();active=[p for p in positions if p['state']=='open']
            closed=[p for p in positions if p['state']=='closed']
            wallets={v:{'cash':self.cfg['wallet_total']/2,'margin_used':0.,'equity':self.cfg['wallet_total']/2}
                     for v in ['bybit','okx']}
            profit_closed=sum(p['profit'] for p in closed)
            for p in closed:
                for v,l in p['legs'].items():wallets[v]['cash']+=l['profit']
            marks_fresh=True;open_rows=[];open_net=0.
            for p in active:
                net=0.
                for v,l in p['legs'].items():
                    q=quotes.get(v)
                    if q and q.symbol==p['symbol']:
                        mark=q.mark
                        marks_fresh &= self._valid(q,now)
                    else:
                        mark=l['entry'];marks_fresh=False
                    pnl=l['side']*p['quantity']*(mark-l['entry'])
                    reserve=p['quantity']*mark*self.cfg['fee']
                    wallets[v]['cash']+=l['funding']-l['entry_fee']
                    wallets[v]['margin_used']+=l['notional']
                    wallets[v]['equity']+=pnl-reserve
                    net+=pnl+l['funding']-l['entry_fee']-reserve
                open_net+=net
                open_rows.append({'pair':f"{p['symbol']}/USDT · 多 {p['long_venue']} / 空 {p['short_venue']}",
                    'profit_abs':net,'profit_ratio':net/sum(l['notional'] for l in p['legs'].values()),
                    'open_date':datetime.fromtimestamp(p['opened_ms']/1000,timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
                    'stake_amount':sum(l['notional'] for l in p['legs'].values()),'enter_tag':'funding_spread','legs':p['legs']})
            for v,w in wallets.items():
                w['equity']+=w['cash']-self.cfg['wallet_total']/2
                w['free_margin']=w['equity']-w['margin_used']
            return {'equity':sum(w['equity'] for w in wallets.values()),'wallets':wallets,
                'profit_closed_coin':profit_closed,'profit_all_coin':profit_closed+open_net,
                'closed_trade_count':len(closed),'winrate':sum(p['profit']>0 for p in closed)/len(closed) if closed else 0.,
                'open_trades':len(active),'positions':open_rows,'marks_fresh':marks_fresh,
                'funding_pending':len(self.pending_positions(now)),'funding_total':sum(l['funding'] for p in positions for l in p['legs'].values())}

    def save_snapshot(self,now,payload,summary):
        with self.lock,self.db:
            self.db.execute('INSERT OR REPLACE INTO snapshots VALUES (?,?)',(now,json.dumps(payload)))
            self.db.execute('DELETE FROM snapshots WHERE ts<?',(now-7*86400000,))
            self.db.execute("INSERT OR REPLACE INTO state VALUES ('summary',?)",(json.dumps(summary),))
