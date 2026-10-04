"""Retain one stable current public delisting catalog and fixed source bodies."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import re

import pandas as pd
import requests

from r18_catalog import parse_page,stable_catalog


HERE=Path(__file__).resolve().parent
DATA=HERE/'r18_sources'
BASE='https://www.binance.com/bapi/composite/v1/public/cms/article/'
FIXED={
 'xmr_ant_spot_vs_coinm':'f73b083ba6834771b07dbe5319917ae5',
 'alpaca_spot_chain':'192a7ab5b39b42d2a24330f7d15ef3e8',
 'matic_migration':'6a6de383727f4659a3050f7982e1620f',
 'alpaca_frequency_four_to_two':'3880a020618b4652a6ed8cb6edbb6385',
 'alpaca_frequency_two_to_one':'209e18f8600f41c09f064d2909e17474',
 'future_delisting_rule':'dd60dfbf654d4055aa6b217ea6d5ddba',
}


def main(fetch=False):
    DATA.mkdir(exist_ok=True);(DATA/'raw').mkdir(exist_ok=True)
    sources_path=DATA/'manifest.json'
    sources=json.loads(sources_path.read_text())['sources'] if sources_path.exists() else {}
    with requests.Session() as session:
        def retained(key,url):
            if key in sources:
                record=sources[key];body=(DATA/record['path']).read_bytes()
                if record['url']!=url or hashlib.sha256(body).hexdigest()!=record['sha256'] or len(body)!=record['bytes']:
                    raise ValueError('Retained source identity/body changed')
            elif fetch:
                response=session.get(url,timeout=(5,20));body=response.content
                p=DATA/'raw'/hashlib.sha256((key+' '+url).encode()).hexdigest();p.write_bytes(body)
                record=dict(url=url,path=str(p.relative_to(DATA)),status=response.status_code,bytes=len(body),
                            sha256=hashlib.sha256(body).hexdigest(),retrieved_utc=datetime.now(timezone.utc).isoformat())
                sources[key]=record;sources_path.write_text(json.dumps({'sources':sources},indent=2,sort_keys=True)+'\n')
            else:raise ValueError('Source unavailable; fetch ordinary public response explicitly')
            return body,record

        def list_url(n):return BASE+f'list/query?type=1&pageNo={n}&pageSize=50&catalogId=161'
        def catalog_page(key,n):
            raw,record=retained(key,list_url(n))
            if record['status']!=200:raise ValueError('Catalog HTTP error/empty response is not an event page')
            return parse_page(raw)
        pages=[catalog_page('catalog_page_1',1)]
        for n in range(2,max(1,math.ceil(pages[0][0]/50))+1):pages.append(catalog_page(f'catalog_page_{n}',n))
        repeat=catalog_page('catalog_page_1_after_paging',1)
        rows=stable_catalog(pages,repeat)
        frame=pd.DataFrame(rows);frame['release_label_utc']=pd.to_datetime(frame.releaseDate,unit='ms',utc=True)
        frame.to_csv(DATA/'current_catalog.csv',index=False)
        all_codes={r['code'] for r in rows};first_capture=pd.Timestamp(sources['catalog_page_1']['retrieved_utc'])
        print('Current catalog stable:',len(rows),'rows; oldest/latest provider labels',str(frame.release_label_utc.min()),str(frame.release_label_utc.max()),flush=True)
        # Current catalog metadata never becomes a historical publication clock.
        targets=dict(FIXED);relationships={}
        for row in rows:
            if 'ALPACA' in row['title'].upper():
                key='catalog_alpaca_'+row['code'];targets[key]=row['code'];relationships[key]='Exact ALPACA token in catalog title; not price-selected'
            dates=re.findall(r'\b\d{4}-\d{2}-\d{2}\b',row['title'])
            if dates and max(pd.Timestamp(d,tz='UTC') for d in dates)>=first_capture.floor('D'):
                key='upcoming_'+row['code'];targets[key]=row['code'];relationships[key]='Upcoming/current dated title at catalog local capture; information observation only'
        detail_index={};observations=[]
        for key,code in targets.items():
            raw,record=retained('detail_'+code,BASE+'detail/query?articleCode='+code)
            result=dict(article_code=code,source_record='detail_'+code,current_catalog_contains_code=code in all_codes,
                        relationship=relationships.get(key,'Frozen forensic source/instrument role'),
                        retrieved_utc=record['retrieved_utc'],status=record['status'])
            try:
                root=json.loads(raw)
                if record['status']!=200 or root.get('code')!='000000' or root.get('success') is not True or not isinstance(root.get('data'),dict):
                    raise ValueError('Unsuccessful/unknown article source')
                article=root['data'];result['current_metadata']={k:v for k,v in article.items() if k not in ('body','banner','image','icon')}
                result['body_type']=type(article.get('body')).__name__;result['body_available']=bool(article.get('body'))
                result['metadata_keys']=list(article)
                # Valid capture time authenticates current local knowledge only.
                if key.startswith('upcoming_'):
                    observations.append(dict(article_code=code,raw_sha256=record['sha256'],known_after=record['retrieved_utc'],
                                             evidence_type='local_capture',current_title=article.get('title'),
                                             body_present=result['body_available'],historical_backdating=False,
                                             no_order_or_strategy_activation=True))
            except (json.JSONDecodeError,ValueError) as exc:result['source_error']=str(exc);result['body_available']=False
            detail_index[key]=result
            print(key,'HTTP',record['status'],'body',result['body_available'],flush=True)
        (DATA/'detail_index.json').write_text(json.dumps(detail_index,indent=2,sort_keys=True)+'\n')
        (DATA/'local_capture_observations.json').write_text(json.dumps(observations,indent=2,sort_keys=True)+'\n')
    metadata=dict(sources=sources,source_only=True,new_performance=False,catalog_current_snapshot_stable=True,
                  catalog_returned_total=len(rows),pages=len(pages),first_local_capture=sources['catalog_page_1']['retrieved_utc'],
                  after_paging_local_capture=sources['catalog_page_1_after_paging']['retrieved_utc'],
                  first_last_metadata_labels=[str(frame.release_label_utc.min()),str(frame.release_label_utc.max())],
                  not_complete_historical_event_vintage=True,
                  output_sha256={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in DATA.glob('*.json') if f.name!='manifest.json'},
                  limitations='Current category inventory and current article body captures, not historically complete unchanged publication-vintage evidence. No prices, profitability, strategy rule or order activation.')
    metadata['output_sha256']['current_catalog.csv']=hashlib.sha256((DATA/'current_catalog.csv').read_bytes()).hexdigest()
    sources_path.write_text(json.dumps(metadata,indent=2,sort_keys=True)+'\n')
    print('Source snapshot retained; no alpha/P&L.',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--fetch',action='store_true');main(p.parse_args().fetch)
