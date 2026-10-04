"""One separately frozen actual Monday-release forensic source."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import requests
from r20_audit import retained

HERE=Path(__file__).resolve().parent
DATA=HERE/'r32_delayed_source'
URL='https://www.federalreserve.gov/releases/h41/20251229/'


def collect(fetch=False):
    DATA.mkdir(exist_ok=True);(DATA/'raw').mkdir(exist_ok=True)
    manifest=DATA/'manifest.json'
    if manifest.exists():
        item=json.loads(manifest.read_text())
        if item['anomaly_plan_commit']!='cbf2d76' or item['source']['url']!=URL:
            raise ValueError('Unregistered delayed-release source identity')
        retained(DATA,item['source']);return item['source']
    if not fetch:raise ValueError('Explicit original anomaly source fetch required')
    raw=DATA/'raw'/hashlib.sha256(URL.encode()).hexdigest()
    if raw.exists():raise ValueError('Unregistered original body exists')
    start=datetime.now(timezone.utc).isoformat()
    try:
        with requests.Session() as client:response=client.get(URL,timeout=(25,25))
        body=response.content;raw.write_bytes(body)
        record=dict(url=URL,final_url=response.url,status=response.status_code,path='raw/'+raw.name,
            bytes=len(body),sha256=hashlib.sha256(body).hexdigest(),requested_utc=start,
            retrieved_utc=datetime.now(timezone.utc).isoformat(),
            headers={k:response.headers[k] for k in ('Date','Content-Type','Last-Modified','ETag') if k in response.headers})
    except requests.RequestException as exc:
        record=dict(url=URL,status=None,path=None,requested_utc=start,failed_utc=datetime.now(timezone.utc).isoformat(),
                    source_error=type(exc).__name__+': '+str(exc))
    manifest.write_text(json.dumps(dict(anomaly_plan_commit='cbf2d76',source_stage_only=True,
        no_new_full_cohort_or_policy_or_performance=True,source=record),indent=2,sort_keys=True)+'\n')
    return record


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--fetch',action='store_true')
    print(json.dumps(collect(p.parse_args().fetch),indent=2))
