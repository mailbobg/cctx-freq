"""Source/instrument/calendar findings; no alpha rules or return computation."""
import hashlib
import json
from pathlib import Path
import re

import pandas as pd

from r17_carry import FundingSeries,rate_decimal
from r17_data import parse_funding_csv
from r17_prices import verified_zip
from r18_catalog import body_text,parse_detail
from r18_calendar import Boundary,TransitionCalendar


HERE=Path(__file__).resolve().parent;DATA=HERE/'r18_sources'


def main():
    manifest=json.loads((DATA/'manifest.json').read_text());sources=manifest['sources']
    for record in sources.values():
        p=DATA/record['path']
        if p.stat().st_size!=record['bytes'] or hashlib.sha256(p.read_bytes()).hexdigest()!=record['sha256']:
            raise ValueError('Retained original source body changed')
    for name,sha in manifest['output_sha256'].items():
        if hashlib.sha256((DATA/name).read_bytes()).hexdigest()!=sha:raise ValueError('Frozen current source output changed')
    def article(code):
        record=sources['detail_'+code]
        if record['status']!=200:raise ValueError('Unknown original article source')
        data=parse_detail((DATA/record['path']).read_bytes(),code)
        return data,body_text(data['body']),record
    catalog=pd.read_csv(DATA/'current_catalog.csv')
    catalog['release_label_utc']=pd.to_datetime(catalog.release_label_utc,utc=True,format='mixed')
    kinds={}
    for title in catalog.title:
        if 'Margin' in title:kind='margin_or_loans_title'
        elif 'Futures' in title or 'Perpetual' in title:kind='futures_contract_title'
        elif title.startswith('Notice of Removal of Spot Trading Pairs'):kind='spot_pair_removal_title'
        elif title.startswith('Binance Will Delist'):kind='full_token_spot_candidate_title_only'
        else:kind='other_or_unclassified_title'
        kinds[kind]=kinds.get(kind,0)+1
    fixed=[]
    for role,code,required,finding in [
      ('XMR_ANT','f73b083ba6834771b07dbe5319917ae5',['XMRUSDT','ANTUSDT','Coin-M XMRUSD','2024-02-07'],
       'Spot tokens removed; one COIN-M contract closes earlier; USD-M XMRUSDT/ANTUSDT explicitly survive. Product identity differs; current body disclosed later clarification.'),
      ('ALPACA_spot_actual_catalog','0274f9d47da1437990bc13eb17b0ec99',['ALPACA','2025-05-02 03:00','2025-04-30 09:00','2025-04-30 08:30'],
       'Actual full-token spot notice located in catalog, not the wrong initial URL. Current body has earlier futures close/new-entry cutoff as well as later spot close, and a disclosed same-day amendment; current full body is not original vintage proof.'),
      ('MATIC_migration','6a6de383727f4659a3050f7982e1620f',['1 MATIC = 1 POL','2024-09-04 09:00','2024-09-10 03:00'],
       'One-to-one migration; earlier USDT/USDC futures and separate COIN-M closure times versus spot migration. Cannot classify as permanent loss of the native underlying.'),
      ('ALPACA_frequency_revision','209e18f8600f41c09f064d2909e17474',['every one hour','2025-04-30 08:00','amended on 2025-04-29'],
       'Current final-funding detail was added after provider original publish label. lastUpdateTime0 is contradicted by explicit body amendment; unknown update sentinel cannot establish vintage.'),
      ('Upcoming_current_capture','2f1a743334f044abbd82e34093c7b771',['2026-10-05 09:00','PROMPTUSDT','PUMPBTCUSDT','1000000BOBUSDT'],
       'Genuine pre-event local body capture; preserve exact native contract symbols, including numeric multiplier-looking name without stripping it. Futures-only notice is not full-token spot removal. No orders or historical backdating.'),
    ]:
        data,text,record=article(code);normalized=' '.join(text.split())
        if any(needle not in normalized for needle in required):raise ValueError('Frozen forensic source field missing or changed')
        dates=re.findall(r'(?:last updated on|amended on|updated on) (\d{4}-\d{2}-\d{2})',normalized)
        fixed.append(dict(role=role,article_code=code,title=data['title'],raw_sha256=record['sha256'],
                          provider_publishDate_ms=data['publishDate'],provider_lastUpdateTime_ms=data['lastUpdateTime'],
                          explicit_body_revision_dates=dates,current_capture_known_after=record['retrieved_utc'],
                          historical_publication_of_current_full_body_certified=False,finding=finding))
    zip_record=sources['alpaca_funding_month'];check=sources['alpaca_funding_month_checksum'];api_record=sources['alpaca_funding_api_transition']
    if any(r['status']!=200 for r in (zip_record,check,api_record)):raise ValueError('Funding counterpart response unavailable')
    native=verified_zip((DATA/zip_record['path']).read_bytes(),(DATA/check['path']).read_bytes(),'ALPACAUSDT-fundingRate-2025-04.zip')
    csv_frame=parse_funding_csv(native,pd.Timestamp('2025-04-01',tz='UTC'),pd.Timestamp('2025-05-01',tz='UTC'))
    begin=pd.Timestamp('2025-04-24',tz='UTC');finish=pd.Timestamp('2025-04-26',tz='UTC')
    selected=csv_frame.loc[(csv_frame.slot>=begin)&(csv_frame.slot<finish)].copy()
    api=json.loads((DATA/api_record['path']).read_bytes());actual=[]
    lookup=selected.set_index('slot')
    if len(api)!=len(selected):raise ValueError('Actual funding/CSV calendar size differs')
    for row in api:
        timestamp=pd.Timestamp(row['fundingTime'],unit='ms',tz='UTC');slot=timestamp.floor('h')
        if row['symbol']!='ALPACAUSDT' or slot not in lookup.index or rate_decimal(row['fundingRate'])!=rate_decimal(lookup.loc[slot,'rate_decimal']):
            raise ValueError('Native actual funding source/calendar/rate differs')
        actual.append(dict(date=timestamp,slot=slot,rate_decimal=row['fundingRate'],interval_hours=int(lookup.loc[slot,'interval_hours'])))
    events=pd.DataFrame(actual).set_index('date')
    context,text,notice=article('209e18f8600f41c09f064d2909e17474')
    if '2025-04-25 08:00 (UTC)' not in text or 'every two hours to  every one hour' not in text:raise ValueError('New frequency timetable not corroborated')
    boundary=Boundary('2025-04-25T06:00:00Z','2025-04-25T08:00:00Z',2,1,'detail_'+context['code'],notice['sha256'],notice['retrieved_utc'])
    try:FundingSeries(events,begin,finish);old_reject=False
    except ValueError as exc:old_reject=str(exc)
    if not old_reject:raise AssertionError('Original continuity failure not reproduced')
    reviewed=TransitionCalendar(events,begin,finish,(boundary,))
    try:reviewed.events_known_at('2025-04-25T12:00:00Z');backdate_reject=False
    except ValueError:backdate_reject=True
    if not backdate_reject:raise AssertionError('Late source capture improperly admitted as historical knowledge')
    reviewed.events.to_csv(DATA/'alpaca_actual_transition_events.csv')
    bad=events.drop(events.index[events.slot.eq(pd.Timestamp('2025-04-25T09:00:00Z'))])
    try:TransitionCalendar(bad,begin,finish,(boundary,));missing_rejected=False
    except ValueError:missing_rejected=True
    if not missing_rejected:raise AssertionError('Ordinary following event gap was silently admitted')
    transitions=[]
    for i in range(1,len(events)):
        if events.interval_hours.iloc[i]!=events.interval_hours.iloc[i-1]:
            transitions.append(dict(previous_slot=events.slot.iloc[i-1].isoformat(),first_new_slot=events.slot.iloc[i].isoformat(),
                                    old_declared_hours=int(events.interval_hours.iloc[i-1]),new_declared_hours=int(events.interval_hours.iloc[i]),
                                    preceding_gap_hours=float((events.slot.iloc[i]-events.slot.iloc[i-1])/pd.Timedelta(hours=1))))
    wrong,_,wrong_record=article('192a7ab5b39b42d2a24330f7d15ef3e8')
    output=dict(source_only=True,new_alpha_trial=False,new_performance=False,adopted=False,
                catalog_returned_rows=len(catalog),current_snapshot_stable=True,source_response_count=len(sources),
                current_catalog_label_year_counts={str(y):int(n) for y,n in catalog.groupby(catalog.release_label_utc.dt.year).size().items()},
                title_family_counts_not_event_eligibility=kinds,
                historical_catalog_coverage_complete=False,original_full_body_publication_vintages_certified=False,
                fixed_forensic_roles=fixed,
                initial_wrong_lead=dict(code=wrong['code'],observed_title=wrong['title'],raw_sha256=wrong_record['sha256'],
                                        disposition='Reject ALPACA delisting attribution; correct role independently found in retained current catalog. Original failed lead kept.'),
                calendar_context=dict(actual_api_and_native_csv_rates_slots_match=True,actual_events=len(events),
                     changes=transitions,old_frozen_R17_validator_rejects_real_transition=old_reject,
                     reviewed_boundary_preserves_all_actual_rates_and_events=True,ordinary_missing_event_still_rejected=missing_rejected,
                     receipt_local_capture_not_backdated=True,original_R17_history_engine_and_rejection_unchanged=True,
                     current_sample_is_not_a_profitability_trial=True),
                next_action='Do not build a past delisting return trial from this partial/current vintage. Historical original-content/coverage and native executable lifecycle remain unproven. A source-feasible subsequent mechanism or genuinely captured prospective inventory is required; no known-after clock, event omission or trading rule is fabricated.',
                source_sha256={n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in ('R18_SOURCE_PLAN.md','r18_catalog.py','test_r18_catalog.py','r18_sources.py','r18_calendar.py','test_r18_calendar.py','r18_audit.py')})
    (HERE/'r18_source_audit.json').write_text(json.dumps(output,indent=2)+'\n')
    print('R18 source/instrument boundaries and actual frequency transition verified; no alpha/P&L.',flush=True)


if __name__=='__main__':main()
