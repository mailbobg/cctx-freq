"""Post-outcome independent minute marks; reuse the tested original checker."""
import argparse
import json
from pathlib import Path
import pandas as pd
from r19_reflection import read_frame
from r29_mark_audit import ASSETS,mark_path
from r33_freeze import assert_frozen_inputs

HERE=Path(__file__).resolve().parent


def audit():
    assert_frozen_inputs(HERE)
    minutes={a:pd.read_parquet(HERE/f'r13_data/{a}USDT_1m.parquet') for a in ASSETS}
    results={}
    for cost in (.0012,.0024):
        for name in ('Cov10_baseline','reserve_quantity'):
            key=f'recent_reused_{name}_cost{cost}'
            curve=read_frame(HERE/f'r33_{key}_daily.csv',index_col='date',parse_dates=True)
            orders=read_frame(HERE/f'r33_{key}_orders.csv',parse_dates=['time'])
            results[key]=mark_path(curve,orders,minutes,cost)
    return dict(post_outcome_checker=True,new_economic_variant=False,results=results,
        no_original_code_curve_order_or_rule_changed=True,
        scope='Same tested independent native minute OPEN/CLOSE capital checker, including prefill and terminal fees. No continuous quote/tick, actual exchange fill, source first-public clock/edition or forward certification.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--verify-only',action='store_true');args=p.parse_args()
    x=audit();file=HERE/'r33_mark_audit.json';text=json.dumps(x,indent=2,sort_keys=True)+'\n'
    if args.verify_only:
        if file.read_text()!=text:raise ValueError('Independent original minute risk marks do not replay')
        print('Four native original-report account mark paths replay PASS')
    else:
        file.write_text(text)
        for key,value in x['results'].items():print(key,value['native_account_max_DD_pct'],value['first_5pct_breach'],value['worst_trough'])
