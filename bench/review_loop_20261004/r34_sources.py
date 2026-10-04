"""Ten fixed native read-only staking source roles, before any policy."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import requests
from r20_audit import retained

HERE=Path(__file__).resolve().parent
DATA=HERE/'r34_sources'


def targets():
    return dict(api_docs='https://docs.lido.fi/integrations/api/',
        core_docs='https://docs.lido.fi/contracts/lido/',wrapper_docs='https://docs.lido.fi/contracts/wsteth/',
        deployment_docs='https://docs.lido.fi/deployed-contracts/',subgraph_docs='https://docs.lido.fi/integrations/subgraph/',
        risk_docs='https://docs.lido.fi/prd/',apr_swagger='https://eth-api.lido.fi/api/',
        reward_swagger='https://reward-history-backend.lido.fi/api',
        apr_last='https://eth-api.lido.fi/v1/protocol/steth/apr/last',
        apr_sma='https://eth-api.lido.fi/v1/protocol/steth/apr/sma')


def collect(fetch=False):
    DATA.mkdir(exist_ok=True);(DATA/'raw').mkdir(exist_ok=True)
    manifest=DATA/'manifest.json';records=json.loads(manifest.read_text())['sources'] if manifest.exists() else {}
    if set(records)-set(targets()):raise ValueError('Unregistered staking source role')
    with requests.Session() as client:
        for role,url in targets().items():
            if role in records:
                if records[role]['url']!=url:raise ValueError('Original source request identity differs')
                retained(DATA,records[role]);continue
            if not fetch:raise ValueError('Explicit original source fetch required '+role)
            raw=DATA/'raw'/hashlib.sha256((role+' '+url).encode()).hexdigest()
            if raw.exists():raise ValueError('Unregistered original body exists')
            start=datetime.now(timezone.utc).isoformat()
            try:
                response=client.get(url,timeout=(25,25));body=response.content;raw.write_bytes(body)
                record=dict(url=url,final_url=response.url,status=response.status_code,path='raw/'+raw.name,
                    bytes=len(body),sha256=hashlib.sha256(body).hexdigest(),requested_utc=start,
                    retrieved_utc=datetime.now(timezone.utc).isoformat(),
                    headers={k:response.headers[k] for k in ('Date','Content-Type','Last-Modified','ETag') if k in response.headers})
            except requests.RequestException as exc:
                record=dict(url=url,status=None,path=None,requested_utc=start,
                    failed_utc=datetime.now(timezone.utc).isoformat(),source_error=type(exc).__name__+': '+str(exc))
            records[role]=record;temp=manifest.with_suffix('.json.tmp')
            temp.write_text(json.dumps(dict(source_plan_commit='5c7e0f5',source_stage_only=True,
                no_full_source_cohort_or_alpha_defined=True,sources=records),indent=2,sort_keys=True)+'\n');temp.replace(manifest)
            print(role,record['status'],record.get('bytes',0),flush=True)
    return records


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--fetch',action='store_true');collect(p.parse_args().fetch)
