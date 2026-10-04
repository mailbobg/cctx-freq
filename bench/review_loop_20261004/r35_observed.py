"""Native valid-row diagnostic; failed strict source contract stays failed."""
import io
import json
from pathlib import Path

import pandas as pd

from r35_native import HERE,checked_csv,parse_minutes,assert_inputs,digest,write_json

DATA=HERE/'r35_observed_data'


def observed_rows(body,start,end):
    x=pd.read_csv(io.BytesIO(body),header=None)
    if x.empty or x.shape[1]!=12:raise ValueError('Invalid native source schema')
    x=x.apply(pd.to_numeric,errors='raise')
    opened=x[0];closed=x[6]
    micro=opened.ge(10**15)
    if micro.any() and not micro.all():raise ValueError('Mixed original epoch units')
    scale=1000000 if micro.all() else 1000
    unit='us' if micro.all() else 'ms'
    keep=closed.sub(opened).eq(60*scale-1)
    lines=body.splitlines(keepends=True)
    if len(lines)!=len(x):raise ValueError('Cannot preserve original CSV row lexical identity')
    retained=b''.join(line for line,known in zip(lines,keep) if known)
    frame,info=parse_minutes(retained,start,end)
    excluded=[dict(original_csv_zero_based_row=int(i),original_numeric_row=x.loc[i].tolist(),
                   original_open_utc=str(pd.Timestamp(int(x.loc[i,0]),unit=unit,tz='UTC')),
                   original_close_span_seconds=float((x.loc[i,6]-x.loc[i,0]+1)/scale))
              for i in x.index[~keep]]
    return frame,dict(info,excluded_nonstandard_span_rows=excluded,
        no_original_timestamp_or_price_repair=True,original_strict_contract_failed=bool(excluded or info['missing_minutes']))


def derive(verify=False):
    plan=assert_inputs();records=json.loads((HERE/'r35_data/sources.json').read_text())
    old=json.loads((HERE/'r35_source_audit.json').read_text())
    if old['source_contract_complete'] is not False:raise ValueError('Must preserve original strict source failure')
    for name,sha in old['native_input_sha256'].items():
        if digest(HERE/name)!=sha:raise ValueError('Retained native body changed')
    DATA.mkdir(exist_ok=True)
    out=dict(observed_only_diagnostic=True,original_full_source_contract_pass=False,
        initial_strict_audit_sha256=digest(HERE/'r35_initial_strict_source_audit.json'),
        original_strict_audit_sha256=digest(HERE/'r35_source_audit.json'),
        supplement_sha256=digest(HERE/'R35_OBSERVED_DIAGNOSTIC.md'),
        native_input_sha256=old['native_input_sha256'],archives=[],output_sha256={},validation={},
        missing_minutes_never_interpolated=True,continuous_risk_or_execution_certified=False,
        new_requests=0,no_account_risk_outcomes_yet=True)
    for asset in ('BTC','ETH'):
        parts=[]
        for j in plan['jobs']:
            if j['asset']!=asset:continue
            rs=[records[j[k]] for k in ('url','checksum_url')]
            if any(r['status']!=200 for r in rs):raise ValueError('Original native HTTP failure')
            body=checked_csv(*[(HERE/r['path']).read_bytes() for r in rs],j['filename'])
            frame,info=observed_rows(body,j['start'],j['end'])
            parts.append(frame);out['archives'].append(dict(asset=asset,filename=j['filename'],**info))
        joined=pd.concat(parts)
        if joined.index.has_duplicates or not joined.index.is_monotonic_increasing:raise ValueError('Invalid observed archive join')
        target=DATA/f'{asset}USDT_1m.parquet'
        if verify:pd.testing.assert_frame_equal(pd.read_parquet(target),joined)
        else:joined.to_parquet(target)
        out['output_sha256'][str(target.relative_to(HERE))]=digest(target)
        original=pd.read_feather(HERE/f'r1_data/{asset}_USDT-1d.feather').set_index('date')
        daily=joined.resample('D').agg({'open':'first','high':'max','low':'min','close':'last'})
        reference=original.reindex(daily.index)[['open','high','low','close']]
        gap=(daily-reference).abs();mask=gap.max(axis=1).gt(1e-8)|gap.isna().any(axis=1)
        out['validation'][asset]=dict(rows=len(joined),
            excluded_nonstandard_span_rows=sum(len(x['excluded_nonstandard_span_rows']) for x in out['archives'] if x['asset']==asset),
            unknown_minutes=sum(x['missing_minutes'] for x in out['archives'] if x['asset']==asset),
            native_zero_activity_minutes=int(joined.trades.eq(0).sum()),
            observed_daily_OHLC_max_gap=float(gap.max().max()),
            observed_daily_OHLC_mismatches=[dict(date=str(t),native_observed=daily.loc[t].to_dict(),original_daily=reference.loc[t].to_dict()) for t in daily.index[mask]])
    dest=HERE/'r35_observed_source_audit.json';text=json.dumps(out,indent=2,sort_keys=True,allow_nan=False)+'\n'
    if verify:
        if dest.read_text()!=text:raise ValueError('Observed source diagnostic bytes changed')
    else:dest.write_text(text)
    print(out['validation'],flush=True)
    return out


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--verify',action='store_true');args=p.parse_args();derive(args.verify)
