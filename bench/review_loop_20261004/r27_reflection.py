"""Independent native accounting and descriptive fixed-panel decisions."""
import argparse
from decimal import Decimal, localcontext
import json
from pathlib import Path

import pandas as pd

from r9_selection import load_prices
from r10_gold import WINDOWS
from r19_reflection import read_frame,daily_attribution,native_reconcile
from r27_activity import ASSETS,PANEL
from r20_audit import retained
from r17_prices import verified_zip
from r21_basis import parse_native
from r19_run import frozen_json

HERE=Path(__file__).resolve().parent


def validate_source_cohort(save=False):
    """Independently join declared activity cells to checksum-matched originals."""
    audit=json.loads((HERE/'r27_source_audit.json').read_text())
    records=json.loads((HERE/'r22_sources/manifest.json').read_text())['sources']
    coverage={(v['symbol'],v['month']):v for v in audit['native_archive_coverage']}
    cache={};checked=0;votes_checked=0
    cohort=audit['source_feature_cohort']
    if len(cohort)!=292:raise ValueError('Independent native decision count differs')
    for key,f in cohort.items():
        monday=pd.Timestamp(key);friday=monday-pd.Timedelta(days=3)
        if pd.Timestamp(f['decision'])!=monday or pd.Timestamp(f['observation_day'])!=friday:
            raise ValueError('Independent native decision/interval identity differs')
        expected=pd.date_range(friday-pd.Timedelta(days=59),friday,freq='D')
        if [v['symbol'] for v in f['native_votes']]!=list(PANEL):raise ValueError('Independent native members differ')
        positives=0
        for vote in f['native_votes']:
            symbol=vote['symbol'];values=[]
            if [v['native_day'] for v in vote['native_daily_volumes']]!=expected.strftime('%Y-%m-%d').tolist():
                raise ValueError('Independent native activity calendar differs')
            for day,cell in zip(expected,vote['native_daily_volumes']):
                month=day.strftime('%Y-%m');identity=(symbol,month);c=coverage[identity]
                r=records[c['selected_zip_record']];cr=records[c['selected_checksum_record']]
                if r['sha256']!=c['zip_sha256'] or cr['sha256']!=c['checksum_sha256']:
                    raise ValueError('Independent original source identities differ')
                if identity not in cache:
                    body=verified_zip(retained(HERE/'r22_sources',r),retained(HERE/'r22_sources',cr),f'{symbol}-1d-{month}.zip')
                    cache[identity]=parse_native(body)
                native=cache[identity][day]
                if native['volume']!=cell['volume_decimal'] or cell['source_sha256']!=r['sha256']:
                    raise ValueError('Activity quantity differs from exact native original')
                values.append(Decimal(native['volume']));checked+=1
            with localcontext() as ctx:
                ctx.prec=50;prior=sum(values[:30]);current=sum(values[30:])
            if (current!=Decimal(vote['current_volume_sum_decimal']) or prior!=Decimal(vote['prior_volume_sum_decimal']) or
                vote['positive']!=(current>prior)):
                raise ValueError('Independent original quantity sum/direction differs')
            positives+=int(current>prior);votes_checked+=1
        if f['positive_count']!=positives or f['allow']!=(positives>=4):
            raise ValueError('Independent activity majority differs')
    result=dict(source_stage_only=True,new_performance=False,independent_native_volume_cells_checked=checked,
        native_asset_votes_checked=votes_checked,exact_original_quantity_text_and_checksum_identities=True,
        independent_equal_window_decimal_sums_and_strict_majority_equal=True,
        historical_publication_and_actual_quote_depth_or_fills_certified=False)
    if save:frozen_json(HERE/'r27_native_feature_validation.json',result)
    return result


def reflect():
    source_validation=validate_source_cohort()
    px,_,_=load_prices();decisions=read_frame(HERE/'r27_decision_audit.csv',parse_dates=['decision'])
    minutes={a:pd.read_parquet(HERE/f'r13_data/{a}USDT_1m.parquet') for a in ASSETS};results={}
    for label,start,end in WINDOWS:
        versions={};selected=decisions.loc[decisions.window.eq(label)]
        coverage=dict(decision_weeks=len(selected),known_weeks=int(selected.known.sum()),
            source_reason_counts=selected.reason.value_counts().to_dict(),
            positive_member_count_histogram={int(k):int(v) for k,v in selected.positive_count.value_counts().sort_index().items()},
            per_member_positive_weeks={s:int(selected[s+'_positive'].sum()) for s in PANEL})
        active=selected[[f'baseline_{a}' for a in ASSETS]].sum(axis=1).gt(1e-12)
        coverage['vetoed_original_active_weeks_by_reason']=selected.loc[active & ~selected.allow].reason.value_counts().to_dict()
        coverage['active_original_weeks']=int(active.sum())
        coverage['vetoed_original_active_coin_weeks']={a:int((selected['baseline_'+a].gt(1e-12)&~selected.allow).sum()) for a in ASSETS}
        for cost in (.0012,.0024):
            for name in ('Cov10_baseline','activity_confirmation'):
                key=f'{label}_{name}_cost{cost}';curve=read_frame(HERE/f'r27_{key}_daily.csv',index_col='date',parse_dates=True)
                pnl,gap=daily_attribution(curve,px)
                dd=curve.equity/curve.equity.cummax().clip(lower=1.)-1;trough=dd.idxmin();past=curve.equity.loc[:trough]
                peak=past.idxmax() if past.max()>=1 else None
                episode=pnl.loc[:trough] if peak is None else pnl.loc[(pnl.index>peak)&(pnl.index<=trough)]
                v=dict(peak='initial cash' if peak is None else str(peak),trough=str(trough),
                    whole_window_asset_pnl_USDT_10000_start=(pnl.sum()*10000).to_dict(),
                    episode_asset_pnl_USDT_10000_start=(episode.sum()*10000).to_dict(),
                    all_days_attribution_max_abs_gap=gap,mean_held_exposure_pct=float(curve.pre_close_exposure.mean()*100),
                    wholly_cash_days=int(curve.pre_close_exposure.lt(1e-12).sum()),total_days=len(curve))
                if label=='recent_reused':
                    orders=read_frame(HERE/f'r27_{key}_orders.csv',parse_dates=['time'])
                    v['independent_source_native_ledger']=native_reconcile(curve,orders,minutes,cost)
                versions[f'{name}_cost{cost}']=v
        delta={a:versions['activity_confirmation_cost0.0012']['whole_window_asset_pnl_USDT_10000_start'][a]-versions['Cov10_baseline_cost0.0012']['whole_window_asset_pnl_USDT_10000_start'][a] for a in [*ASSETS,'fees']}
        results[label]=dict(versions=versions,decision_coverage=coverage,activity_minus_control_PnL_USDT_10000_start=delta)
    return dict(results=results,independent_native_source_feature_validation=source_validation,scope='Independent retained native price/fill accounting and descriptive panel decisions. No causal skipped-trade alpha or sign/horizon/member/majority/day/sizing/fee sweep; no adoption.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--verify-only',action='store_true');args=p.parse_args()
    value=reflect();path=HERE/'r27_reflection.json';text=json.dumps(value,indent=2,sort_keys=True)+'\n'
    if args.verify_only:
        if path.read_text()!=text:raise ValueError('Frozen native participation reflection did not replay')
        print('Every daily price/fee change and recent source/native fill ledger replay PASS')
    else:
        path.write_text(text)
        for label,r in value['results'].items():print(label,r['decision_coverage'],r['activity_minus_control_PnL_USDT_10000_start'])
