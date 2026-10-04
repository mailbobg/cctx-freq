"""Small retained crypto-native source probe, without historical availability inference."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

import requests

HERE=Path(__file__).resolve().parent
DATA=HERE/'r20_sources'
REPO='https://api.github.com/repos/coinmetrics/data'
DATES=('2021-01-29','2022-11-04','2024-01-05')


def initial_targets():
    return dict(repo_identity=REPO, current_tree=REPO+'/git/trees/master?recursive=1',
        realized_definition='https://raw.githubusercontent.com/coinmetrics/docs-website/master/asset-metrics/market/caprealusd.md',
        daily_definition='https://raw.githubusercontent.com/coinmetrics/docs-website/master/asset-metrics/asset-metrics-overview.md',
        community_definition='https://raw.githubusercontent.com/coinmetrics/product-docs/master/docs/packages/coin-metrics-community-data.md',
        gharchive_collection='https://www.gharchive.org/',
        **{'api_btc_'+day:'https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?'+urlencode(dict(
            assets='btc',metrics='CapRealUSD,SplyCur,FeeTotNtv',frequency='1d',start_time=day,end_time=day,page_size=100)) for day in DATES})


def collect(targets,fetch=False):
    DATA.mkdir(exist_ok=True);(DATA/'raw').mkdir(exist_ok=True)
    path=DATA/'manifest.json';sources=json.loads(path.read_text())['sources'] if path.exists() else {}
    missing=[]
    for key,url in targets.items():
        if key not in sources:
            if not fetch:raise ValueError('Explicit ordinary fetch needed '+key)
            missing.append((key,url));continue
        r=sources[key]
        if r['url']!=url:raise ValueError('Source request identity changed')
        if r.get('path'):
            raw=(DATA/r['path']).read_bytes()
            if len(raw)!=r['bytes'] or hashlib.sha256(raw).hexdigest()!=r['sha256']:raise ValueError('Retained body changed')
    def get(item):
        key,url=item;began=datetime.now(timezone.utc).isoformat()
        try:
            response=requests.get(url,timeout=(5,25));body=response.content
            received=datetime.now(timezone.utc).isoformat();name=hashlib.sha256((key+' '+url).encode()).hexdigest()
            (DATA/'raw'/name).write_bytes(body)
            return key,dict(url=url,final_url=response.url,requested_utc=began,retrieved_utc=received,status=response.status_code,
                path='raw/'+name,bytes=len(body),sha256=hashlib.sha256(body).hexdigest(),
                headers={k:response.headers[k] for k in ('Content-Type','Last-Modified','ETag','X-RateLimit-Remaining','X-RateLimit-Reset') if k in response.headers})
        except requests.RequestException as exc:
            return key,dict(url=url,requested_utc=began,failed_utc=datetime.now(timezone.utc).isoformat(),status=None,path=None,
                source_error=type(exc).__name__+': '+str(exc))
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(get,item) for item in missing]):
            key,r=future.result();sources[key]=r
            path.write_text(json.dumps(dict(sources=sources,source_only=True,new_performance=False),indent=2,sort_keys=True)+'\n')
            print(key,r['status'],r.get('bytes',0),flush=True)
    return {key:((DATA/sources[key]['path']).read_bytes() if sources[key].get('path') else None,sources[key]) for key in targets}


def history_targets():
    records=collect(initial_targets());raw,r=records['current_tree']
    if r['status']!=200:raise ValueError('Current repository tree unavailable')
    tree=json.loads(raw)
    if tree.get('truncated') is not False or not isinstance(tree.get('tree'),list):raise ValueError('Unreviewed/incomplete Git tree')
    paths={entry['path']:entry for entry in tree['tree']}
    for n in ('csv/btc.csv','README.md'):
        if n not in paths or paths[n].get('type')!='blob':raise ValueError('Repository path not corroborated '+n)
    sha=tree['sha']
    return dict(current_btc_csv=f'https://raw.githubusercontent.com/coinmetrics/data/{sha}/csv/btc.csv',
        current_readme=f'https://raw.githubusercontent.com/coinmetrics/data/{sha}/README.md',
        **{'history_'+day:REPO+'/commits?'+urlencode(dict(path='csv/btc.csv',until=day+'T23:59:59Z',per_page=1)) for day in DATES})


def context_targets():
    raw,r=collect(initial_targets())['current_tree']
    if r['status']!=200:raise ValueError('Current tree unavailable')
    tree=json.loads(raw)
    scripts=[entry for entry in tree['tree'] if entry['path']=='scripts/generate.js' and entry['type']=='blob']
    if len(scripts)!=1:raise ValueError('Publisher script not corroborated')
    return dict(latest_commits=REPO+'/commits?per_page=3',
        publisher_script_0=f"https://raw.githubusercontent.com/coinmetrics/data/{tree['sha']}/scripts/generate.js",
        mvrv_definition='https://raw.githubusercontent.com/coinmetrics/docs-website/master/asset-metrics/market/capmvrvcur.md',
        supply_definition='https://raw.githubusercontent.com/coinmetrics/docs-website/master/asset-metrics/supply/splycur.md',
        completion_definition='https://gitbook-docs.coinmetrics.io/network-data/network-data-overview/availability/asseteodcompletiontime.md',
        public_metric_catalog='https://community-api.coinmetrics.io/v4/catalog/metrics?'+urlencode(dict(
            metrics='CapMVRVCur,SplyCur,FeeTotNtv,AssetCompletionTime,AssetEODCompletionTime',page_size=100)),
        **{'history_repo_'+day:REPO+'/commits?'+urlencode(dict(until=day+'T23:59:59Z',per_page=1)) for day in DATES},
        **{'api_free_'+day:'https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?'+urlencode(dict(
            assets='btc',metrics='CapMVRVCur,SplyCur,FeeTotNtv,AssetCompletionTime,AssetEODCompletionTime',
            frequency='1d',start_time=day,end_time=day,page_size=100)) for day in DATES})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--fetch',action='store_true');p.add_argument('--history',action='store_true');p.add_argument('--context',action='store_true');args=p.parse_args()
    records=collect(context_targets() if args.context else history_targets() if args.history else initial_targets(),args.fetch)
    for key,(body,r) in records.items():
        if key.startswith('api_') and body is not None:print(key,'original response',body.decode()[:700])
