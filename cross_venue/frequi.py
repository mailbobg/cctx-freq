"""REST presentation of actual paper legs for FreqUI, without exchange execution."""
from datetime import datetime, timezone, timedelta
import time


def iso(timestamp):
    return datetime.fromtimestamp(timestamp/1000,timezone.utc).strftime('%Y-%m-%d %H:%M:%S') if timestamp else ''


class FreqUIBridge:
    def __init__(self,runtime):
        self.runtime=runtime
        self.cfg=runtime.cfg
        self.started=int(time.time()*1000)

    def config(self):
        return {'version':'CrossVenuePaper 1.1','strategy_version':'1.1','api_version':2.0,
                'dry_run':True,'demo_trading':False,'trading_mode':'futures','margin_mode':'isolated',
                'short_allowed':True,'stake_currency':'USDT','stake_currency_decimals':2,
                'stake_amount':'unlimited','available_capital':self.cfg['wallet_total'],
                'max_open_trades':2,'portfolio_limit':1,'minimal_roi':{},'stoploss':-self.cfg['stoploss'],
                'stoploss_on_exchange':False,'trailing_stop':False,'use_custom_stoploss':True,
                'timeframe':None,'timeframe_ms':0,'timeframe_min':0,
                'exchange':'Bybit + OKX (paper)','strategy':self.cfg['strategy'],'force_entry_enable':False,
                'exit_pricing':{},'entry_pricing':{},'bot_name':self.cfg['bot_name'],
                'state':'running' if self.runtime.entries_enabled else 'stopped','runmode':'dry_run',
                'position_adjustment_enable':False,'max_entry_position_adjustment':0,
                'runtime':'cross_venue_paper','poll_seconds':self.cfg['poll_seconds'],
                'portfolio_url':'http://127.0.0.1:8086/portfolio','metrics_basis':'two paper legs per portfolio',
                'order_types':{'entry':'market','exit':'market','force_exit':'market',
                               'stoploss':'market','stoploss_on_exchange':False}}

    def trades(self):
        rows=[]
        with self.runtime.lock:
            for p in self.runtime.engine.positions():
                for index,venue in enumerate(['bybit','okx'],1):
                    leg=p['legs'][venue];active=p['state']=='open';qty=p['quantity']
                    q=self.runtime.quotes.get(p['symbol'],{}).get(venue)
                    mark=q.mark if q else leg['entry']
                    exit_fee=qty*mark*self.cfg['fee'] if active else leg['exit_fee']
                    pnl=(leg['side']*qty*(mark-leg['entry'])+leg['funding']-leg['entry_fee']-exit_fee
                         if active else leg['profit'])
                    ratio=pnl/leg['notional']
                    row={'trade_id':p['id']*10+index,'portfolio_id':p['id'],'pair':p['symbol']+'/USDT:USDT',
                         'base_currency':p['symbol'],'quote_currency':'USDT','is_open':active,
                         'is_short':leg['side']==-1,'exchange':venue,'amount':qty,'amount_requested':qty,
                         'stake_amount':leg['notional'],'max_stake_amount':leg['notional'],
                         'strategy':self.cfg['strategy'],'timeframe':0,
                         'enter_tag':f"组合#{p['id']} · 多 {p['long_venue']}/空 {p['short_venue']} · {venue}",
                         'fee_open':self.cfg['fee'],'fee_open_cost':leg['entry_fee'],'fee_open_currency':'USDT',
                         'fee_close':self.cfg['fee'],'fee_close_cost':None if active else leg['exit_fee'],
                         'fee_close_currency':'USDT','open_date':iso(p['opened_ms']),'open_timestamp':p['opened_ms'],
                         'open_fill_date':iso(p['opened_ms']),'open_fill_timestamp':p['opened_ms'],
                         'open_rate':leg['entry'],'open_trade_value':leg['notional'],
                         'close_date':None if active else iso(p['closed_ms']),
                         'close_timestamp':None if active else p['closed_ms'],
                         'close_rate':None if active else leg['exit'],
                         'close_profit':None if active else ratio,'close_profit_pct':None if active else ratio*100,
                         'close_profit_abs':None if active else pnl,'profit_abs':pnl,'profit_fiat':pnl,
                         'profit_ratio':ratio,'profit_pct':ratio*100,'realized_profit':leg['funding']-leg['entry_fee'] if active else pnl,
                         'exit_reason':p.get('exit_reason'),'nr_of_successful_entries':1,
                         'nr_of_successful_exits':0 if active else 1,'has_open_orders':False,'orders':[],
                         'leverage':1.,'funding_fees':leg['funding'],'trading_mode':'futures',
                         'current_rate':mark,'total_profit_abs':pnl,'total_profit_ratio':ratio,
                         'paired_exit_only':True,'marks_fresh':bool(q and self.runtime.engine._valid(q,int(time.time()*1000)))}
                    rows.append(row)
        return rows

    def count(self):
        legs=[t for t in self.trades() if t['is_open']]
        return {'current':len(legs),'max':2,'total_stake':sum(t['stake_amount'] for t in legs),
                'portfolio_count':len(legs)//2,'portfolio_limit':1}

    def balance(self):
        s=self.runtime.public_summary();equity=s['equity'];wallets=s['wallets'];base=self.cfg['wallet_total']
        free=sum(w['free_margin'] for w in wallets.values());used=sum(w['margin_used'] for w in wallets.values())
        return {'currencies':[{'currency':'USDT','free':free,'balance':equity,'used':used,'bot_owned':equity,
                              'est_stake':equity,'est_stake_bot':equity,'stake':'USDT','side':'',
                              'is_position':False,'position':0.,'is_bot_managed':True}],
                'total':equity,'total_bot':equity,'symbol':'USDT','value':equity,'value_bot':equity,'stake':'USDT',
                'note':'Bybit + OKX 虚拟账户；一个组合=两条腿；余额包含资金费与最近标记价估值',
                'starting_capital':base,'starting_capital_ratio':(equity-base)/base,
                'starting_capital_pct':(equity-base)/base*100,'starting_capital_fiat':base,
                'starting_capital_fiat_ratio':(equity-base)/base,'starting_capital_fiat_pct':(equity-base)/base*100,
                'venue_balances':wallets,'marks_fresh':s['marks_fresh']}

    def profit(self):
        rows=self.trades();closed=[r for r in rows if not r['is_open']];base=self.cfg['wallet_total']
        closed_sum=sum(r['profit_abs'] for r in closed);total=sum(r['profit_abs'] for r in rows)
        gains=sum(max(r['profit_abs'],0) for r in closed);losses=sum(min(r['profit_abs'],0) for r in closed)
        first=min((r['open_timestamp'] for r in rows),default=0);latest=max((r['open_timestamp'] for r in rows),default=0)
        performance=self._performance(rows)
        best=performance[0] if performance else None
        mean=lambda xs:sum(xs)/len(xs) if xs else 0.
        cr=[r['profit_ratio'] for r in closed];ar=[r['profit_ratio'] for r in rows]
        result={'profit_closed_coin':closed_sum,'profit_closed_ratio_mean':mean(cr),'profit_closed_percent_mean':mean(cr)*100,
                'profit_closed_ratio_sum':sum(cr),'profit_closed_percent_sum':sum(cr)*100,
                'profit_closed_ratio':closed_sum/base,'profit_closed_percent':closed_sum/base*100,'profit_closed_fiat':closed_sum,
                'profit_all_coin':total,'profit_all_ratio_mean':mean(ar),'profit_all_percent_mean':mean(ar)*100,
                'profit_all_ratio_sum':sum(ar),'profit_all_percent_sum':sum(ar)*100,
                'profit_all_ratio':total/base,'profit_all_percent':total/base*100,'profit_all_fiat':total,
                'trade_count':len(rows),'closed_trade_count':len(closed),
                'first_trade_date':iso(first),'first_trade_timestamp':first,'first_trade_humanized':'',
                'latest_trade_date':iso(latest),'latest_trade_timestamp':latest,'latest_trade_humanized':'',
                'avg_duration':str(timedelta(seconds=int(mean([(r['close_timestamp']-r['open_timestamp'])/1000 for r in closed])))),
                'best_pair':best['pair'] if best else '', 'best_rate':best['profit_ratio']*100 if best else 0.,
                'best_pair_profit_ratio':best['profit_ratio'] if best else 0.,'best_pair_profit_abs':best['profit_abs'] if best else 0.,
                'winning_trades':sum(r['profit_abs']>0 for r in closed),'losing_trades':sum(r['profit_abs']<0 for r in closed),
                'winrate':sum(r['profit_abs']>0 for r in closed)/len(closed) if closed else 0.,
                'profit_factor':gains/-losses if losses else None,'expectancy':mean([r['profit_abs'] for r in closed]),
                'expectancy_ratio':None,'trading_volume':sum(r['open_trade_value'] for r in rows)+sum(r['amount']*r['close_rate'] for r in closed),
                'bot_start_timestamp':self.started,'bot_start_date':iso(self.started),'metrics_basis':'paper legs',
                'max_drawdown_start':'','max_drawdown_start_timestamp':0,'max_drawdown_end':'','max_drawdown_end_timestamp':0,
                'current_drawdown_start':'','current_drawdown_start_timestamp':0}
        # Do not fabricate unavailable curve/risk statistics from paired leg observations.
        for name in ['sharpe','sortino','sqn','calmar','cagr','max_drawdown','max_drawdown_abs',
                     'current_drawdown','current_drawdown_abs','current_drawdown_high']:
            result[name]=None
        if losses:
            average_win=gains/result['winning_trades'] if result['winning_trades'] else 0.
            average_loss=-losses/result['losing_trades']
            result['expectancy_ratio']=(1+average_win/average_loss)*result['winrate']-1
        result['metrics_unavailable']='资金费组合风险指标请使用独立组合记录，尚未估计年化/回撤/夏普'
        return result

    def daily(self,timescale=14,portfolio=False):
        now=int(time.time());days={iso((now-i*86400)*1000)[:10]:{'abs_profit':0.,'trade_count':0}
                                  for i in range(min(max(timescale,1),365))}
        positions=self.runtime.engine.positions()
        for p in positions:
            if p['state']=='closed' and iso(p['closed_ms'])[:10] in days:
                row=days[iso(p['closed_ms'])[:10]];row['abs_profit']+=p['profit'];row['trade_count']+=1 if portfolio else 2
        balance=self.cfg['wallet_total']+sum(p['profit'] for p in positions
                  if p['state']=='closed' and iso(p['closed_ms'])[:10]<min(days))
        for date in sorted(days):
            row=days[date];row.update(rel_profit=row['abs_profit']/balance if balance else 0.,starting_balance=balance,fiat_value=row['abs_profit'])
            balance+=row['abs_profit']
        return {'data':[{'date':date,**row} for date,row in days.items()],
                'fiat_display_currency':'','stake_currency':'USDT'}

    def performance(self):
        return self._performance(self.trades())

    @staticmethod
    def _performance(rows):
        groups={}
        for row in rows:
            if not row['is_open']:
                key=row['pair'];g=groups.setdefault(key,{'pair':key,'profit_abs':0.,'capital':0.,'count':0})
                g['profit_abs']+=row['profit_abs'];g['capital']+=row['open_trade_value'];g['count']+=1
        result=[]
        for g in groups.values():
            capital=g.pop('capital');ratio=g['profit_abs']/capital if capital else 0.
            result.append({**g,'profit_ratio':ratio,'profit':ratio*100,'profit_pct':ratio*100})
        return sorted(result,key=lambda r:r['profit_abs'],reverse=True)
