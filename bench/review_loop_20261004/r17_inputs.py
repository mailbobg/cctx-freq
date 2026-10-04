"""Replay/freeze inputs and actual invested R17 execution prefixes, no metrics."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r17_carry import ASSETS,FundingSeries
from r17_data import START,END,freeze_local
from r17_engine import simulate
from r17_prices import BEGIN


HERE=Path(__file__).resolve().parent
COSTS=(.0012,.0024)
POLICIES=('ranked','equal_pairs')
SOURCES=('R17_PROTOCOL.md','R17_DATA_PLAN.md','r17_carry.py','test_r17_carry.py',
         'r17_data.py','test_r17_data.py','r17_forecasts.py','r17_prices.py','test_r17_prices.py',
         'r17_price_reconciliation.json','r17_engine.py','test_r17_engine.py','r17_inputs.py','r17_screen.py','test_r17_screen.py',
         'r6_relative.py','r16_knowledge.py')


def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def load():
    freeze_local()
    funding_manifest=json.loads((HERE/'r17_data/funding_manifest.json').read_text())
    price_manifest=json.loads((HERE/'r17_prices/manifest.json').read_text())
    forecast_manifest=json.loads((HERE/'r17_forecast_validation.json').read_text())
    for folder,manifest in [('r17_data',funding_manifest),('r17_prices',price_manifest)]:
        for name,expected in manifest['output_sha256'].items():
            if digest(HERE/folder/name)!=expected:raise ValueError('Frozen R17 input output changed')
        source_name='api_sources.json' if folder=='r17_data' else 'sources.json'
        records=json.loads((HERE/folder/source_name).read_text())
        for record in records.values():
            path=HERE/folder/record['path']
            if path.stat().st_size!=record['bytes'] or digest(path)!=record['sha256']:raise ValueError('Retained R17 source body changed')
    for relative,sha in price_manifest['reused_dependency_sha256'].items():
        if digest(HERE/relative)!=sha:raise ValueError('Reused R17 input/source dependency changed')
    for name,expected in forecast_manifest['decision_outputs_sha256'].items():
        if digest(HERE/name)!=expected:raise ValueError('Frozen decision changed')
    if price_manifest['decision_hashes']!=forecast_manifest['decision_outputs_sha256']:raise ValueError('Required-price retrieval decisions differ')
    price=[{s:pd.read_parquet(HERE/'r17_prices'/f'{s}_{kind}.parquet') for s in ASSETS} for kind in ('spot','trade','mark')]
    fund={s:FundingSeries(pd.read_parquet(HERE/'r17_data'/f'{s}_funding.parquet'),START,END) for s in ASSETS}
    decisions=json.loads((HERE/'r17_decisions.json').read_text())
    return *price,fund,decisions,BEGIN,END


def main():
    inputs=load();paths=[];cases={}
    for policy in POLICIES:
        for cost in COSTS:
            key=f'recent_{policy}_cost{cost}'
            _,full,orders=simulate(*inputs,cost=cost,policy=policy,terminal=False)
            checked=[]
            for date in ('2024-01-04','2024-03-13','2024-12-12'):
                finish=pd.Timestamp(date,tz='UTC');prefix=list(inputs);prefix[6]=finish
                prefix[4]=[r for r in inputs[4] if pd.Timestamp(r['decision'])<finish]
                for k in range(3):prefix[k]={s:f.loc[f.index<finish].copy() for s,f in inputs[k].items()}
                prefix[3]={s:FundingSeries(f.events.loc[f.events.slot<finish],f.coverage_start,finish) for s,f in inputs[3].items()}
                _,partial,po=simulate(*prefix,cost=cost,policy=policy,terminal=False)
                pd.testing.assert_frame_equal(full.loc[full.index<finish],partial,check_exact=True)
                pd.testing.assert_frame_equal(orders.loc[orders.date<finish].reset_index(drop=True),po,check_exact=True)
                if not partial.iloc[-1].filter(like='_spot_quantity').gt(0).any():raise AssertionError('Prefix endpoint must be genuinely invested')
                if po.empty:raise AssertionError('Prefix must contain actual orders')
                checked.append(date)
            daily,hourly,terminal_orders=simulate(*inputs,cost=cost,policy=policy,terminal=True)
            gap=daily.equity.iloc[-1]-(10000+daily.realized_price_pnl.sum()+daily.funding.sum()-daily.cost.sum())
            if abs(gap)>1e-6:raise AssertionError('Actual terminal accounting differs')
            if abs(terminal_orders.cost.sum()-daily.cost.sum())>1e-7:raise AssertionError('Actual four-fill cost audit differs')
            for name,frame in [('daily.csv',daily),('hourly.parquet',hourly),('orders.csv',terminal_orders)]:
                path=HERE/f'r17_{key}_{name}'
                if name.endswith('.csv'):frame.to_csv(path,index=name!='orders.csv')
                else:frame.to_parquet(path)
                paths.append(path)
            cases[key]=dict(real_invested_prefix_endpoints=checked,hourly_and_orders_exact_prefix=True,
                            terminal_cash_identity_abs_error_USDT=abs(float(gap)),
                            order_cost_identity_abs_error_USDT=abs(float(terminal_orders.cost.sum()-daily.cost.sum())))
            print(key,'execution prefixes and native cash identities verified; no aggregated metrics',flush=True)
    cash=pd.DataFrame({'equity':np.full(len(pd.date_range(BEGIN,END,freq='D',inclusive='left')),10000.)},
                      index=pd.date_range(BEGIN,END,freq='D',inclusive='left').rename('date'))
    cash_path=HERE/'r17_cash_control.csv';cash.to_csv(cash_path);paths.append(cash_path)
    frozen=dict(aggregate_performance_not_run=True,execution_outputs_contain_equity_and_Pnl=True,
                source_sha256={name:digest(HERE/name) for name in SOURCES},
                input_manifests_sha256={name:digest(HERE/name) for name in ('r17_data/funding_manifest.json','r17_data/api_sources.json',
                    'r17_data/legacy_sources.json','r17_prices/manifest.json','r17_prices/sources.json',
                    'r17_forecast_validation.json','r17_decisions.json','r17_decision_audit.csv')},
                execution_output_sha256={p.name:digest(p) for p in paths},cases=cases,
                cash_independent_constant_control=True,
                source_replay='Original funding pages/CSVs, price raw bodies and reused dependencies checked before ledger replay. Quote/tick, actual lots/tiers and independent forward remain unverified.',
                preregistered_before_outcomes=dict(protocol='344953e',forecast_ledger='c5bb2a9',funding_source_parser='8b38432',
                    rate_decisions_price_plan='a6a1299',hourly_execution_oracles_and_source_reconciliation='096c0c2'))
    (HERE/'r17_input_validation.json').write_text(json.dumps(frozen,indent=2)+'\n')
    print('Inputs, control executions and actual invested prefixes frozen before aggregate screen.',flush=True)


if __name__=='__main__':main()
