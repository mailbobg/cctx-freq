"""Source-version unit proof; preserve the failed unverified same-unit contract."""
from datetime import date, timedelta
from decimal import Decimal
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re

from r31_native import LABEL, compact, parse_archive, parse_publisher
from r31_sources import DATA as ORIGINAL, PROBES
from r31_units_sources import DATA, targets


class Text(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.parts=[]
    def handle_data(self,value):self.parts.append(value)


def text(value):
    parser=Text();parser.feed(value)
    return re.sub(r'\s+',' ',''.join(parser.parts)).strip()


def isolate(body):
    whole=body.decode('utf-8')
    blocks=re.split(r'<tr\s+class="series-pager-title">\s*',whole)
    selected=[b for b in blocks if 'href="/series?seid=WRESBAL"' in b]
    if len(selected)!=1:raise ValueError('Native WRESBAL metadata identity/scope differs')
    block=selected[0]
    anchor=re.findall(r'<a href="/series\?seid=WRESBAL">(.*?)</a>',block,re.S)
    if len(anchor)!=1 or compact(text(anchor[0])).casefold()!=compact('Liabilities and Capital: Other Factors Draining Reserve Balances: '+LABEL+': Week Average').casefold():
        raise ValueError('Native reserve average series label differs')
    fields={kind:[text(v) for v in re.findall(r'<span class="series-meta-'+kind+r'"[^>]*>(.*?)</span>',block,re.S)]
            for kind in ('units','rt-prd','obs-prd')}
    return whole,fields


def interval(value):
    match=re.fullmatch(r'(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})',value)
    if match is None:raise ValueError('Unknown native metadata interval')
    start,end=map(date.fromisoformat,match.groups())
    if start>end:raise ValueError('Reversed native metadata interval')
    return start.isoformat(),end.isoformat()


def parse_versions(body):
    whole,f=isolate(body)
    if [f[k][0] for k in ('units','rt-prd','obs-prd')]!=['Units','Release Dates','Observation Period'] or len({len(v) for v in f.values()})!=1:
        raise ValueError('Native version-table column semantics differ')
    rows=[]
    for units,release,observation in zip(f['units'][1:],f['rt-prd'][1:],f['obs-prd'][1:]):
        start,end=interval(release);obs_start,obs_end=interval(observation)
        rows.append(dict(units=units,release_start=start,release_end=end,
            observation_start=obs_start,observation_end=obs_end,
            native_release_interval=release,native_observation_interval=observation))
    if not rows:raise ValueError('Native unit version table unavailable')
    return rows


def version_units(rows,vintage):
    day=date.fromisoformat(vintage)
    selected=[r for r in rows if date.fromisoformat(r['release_start'])<=day<=date.fromisoformat(r['release_end'])]
    if len(selected)!=1:raise ValueError('Missing/ambiguous native unit version interval')
    row=selected[0]
    units=row['units']
    if units not in ('Billions of Dollars','Billions of U.S. Dollars','Millions of U.S. Dollars'):
        raise ValueError('Unknown native unit cannot be guessed from numeric scale')
    return dict(**row,exact_multiplier_to_million_usd=1000 if units.startswith('Billions') else 1)


def parse_release_meta(body,release):
    whole,f=isolate(body)
    title=re.findall(r'<title>(.*?)</title>',whole,re.S)
    if len(title)!=1 or 'Release Date: '+release not in text(title[0]):
        raise ValueError('Native dated metadata release identity differs')
    if (len({len(v) for v in f.values()})!=1 or len(f['units'])!=2 or
        [f[k][0] for k in ('units','rt-prd','obs-prd')]!=['Units','# of New/Revised Obs.','Last Observation']):
        raise ValueError('Native dated metadata column scope differs')
    observation=re.fullmatch(r'(\d{4}-\d{2}-\d{2}) (\d+(?:\.\d+)?)',f['obs-prd'][1])
    if observation is None or not re.fullmatch(r'\d+',f['rt-prd'][1]):
        raise ValueError('Native dated metadata last observation unknown')
    return dict(release=release,units=f['units'][1],new_or_revised_observations=int(f['rt-prd'][1]),
                last_observation_date=observation[1],last_native_value=observation[2])


def normalized(archive,observation,rows):
    value=archive['rows'].get(observation)
    if value in (None,'','.'):raise ValueError('Required native quantity unavailable, never zero')
    units=version_units(rows,archive['vintage'])
    if not units['observation_start']<=observation<=units['observation_end']:
        raise ValueError('Native observation outside version metadata history')
    converted=Decimal(value)*units['exact_multiplier_to_million_usd']
    return converted,units,value


def reconcile_native(publisher,archive,rows):
    value,units,original=normalized(archive,publisher['observation_date'],rows)
    if value!=publisher['week_average_million_usd']:
        raise ValueError('Native unit proof does not eliminate actual value/revision discrepancy')
    return dict(release=publisher['release_date'],observation_date=publisher['observation_date'],
        vintage=archive['vintage'],native_archive_text=original,native_version_units=units,
        archive_normalized_million_usd=format(value,'f'),
        publisher_native_million_usd=publisher['week_average_million_usd'],normalized_native_value_exact=True,
        literal_native_value_unchanged=True,not_raw_serialization_identity=True)


def audit():
    manifest=json.loads((DATA/'manifest.json').read_text());scope=targets();bodies={}
    if manifest['unit_plan_commit']!='cc1af5b' or set(manifest['sources'])!=set(scope):
        raise ValueError('Native unit preregistration scope differs')
    hashes={'r31_units_sources/manifest.json':hashlib.sha256((DATA/'manifest.json').read_bytes()).hexdigest()}
    for role,r in manifest['sources'].items():
        if r['url']!=scope[role] or r['status']!=200 or not r.get('path'):raise ValueError('Native unit source unavailable/different')
        raw=DATA/r['path'];body=raw.read_bytes()
        if raw.parent!=DATA/'raw' or len(body)!=r['bytes'] or hashlib.sha256(body).hexdigest()!=r['sha256']:
            raise ValueError('Original native unit body changed')
        bodies[role]=body;hashes['r31_units_sources/'+r['path']]=r['sha256']
    rows=parse_versions(bodies['unit_versions'])
    initial_path=DATA.parent/'r31_initial_source_observation.json'
    initial=json.loads(initial_path.read_text())
    hashes['r31_initial_source_observation.json']=hashlib.sha256(initial_path.read_bytes()).hexdigest()
    for name,digest in initial['native_input_sha256'].items():
        if hashlib.sha256((ORIGINAL/name).read_bytes()).hexdigest()!=digest:raise ValueError('Original twenty-role state changed')
        hashes['r31_sources/'+name]=digest
    comparisons=[]
    for monday,current,prior in PROBES:
        vintage=(date.fromisoformat(monday)-timedelta(days=2)).isoformat()
        archive=initial['original_archives']['archive_'+vintage]
        for release in (current,prior):
            comparisons.append(reconcile_native(initial['original_publisher_reports']['publisher_'+release],archive,rows))
    boundary_archives={v:parse_archive(bodies['archive_'+v],v) for v in ('2025-11-08','2025-11-15')}
    metadata=[]
    for release,vintage in (('2025-11-06','2025-11-08'),('2025-11-13','2025-11-15')):
        publisher=parse_publisher(bodies['publisher_'+release],release)
        archive=boundary_archives[vintage]
        comparisons.append(reconcile_native(publisher,archive,rows))
        meta=parse_release_meta(bodies['release_metadata_'+release],release)
        if (meta['units']!=version_units(rows,vintage)['units'] or meta['last_observation_date']!=publisher['observation_date'] or
            Decimal(meta['last_native_value'])!=Decimal(archive['rows'][meta['last_observation_date']])):
            raise ValueError('Independent dated native metadata units/date/value differ')
        metadata.append(meta)
    # The same pre-boundary observation must match in the post-boundary vintage.
    old_publisher=parse_publisher(bodies['publisher_2025-11-06'],'2025-11-06')
    comparisons.append(reconcile_native(old_publisher,boundary_archives['2025-11-15'],rows))
    shared=[]
    a,b=boundary_archives.values()
    for day in sorted(a['rows'].keys() & b['rows'].keys()):
        av,au,at=normalized(a,day,rows);bv,bu,bt=normalized(b,day,rows)
        if av!=bv:raise ValueError('Real boundary revision cannot be hidden by unit scaling')
        shared.append(dict(observation_date=day,pre_native_text=at,post_native_text=bt,
                           normalized_million_usd=format(av,'f'),exact_same_quantity=True))
    return dict(unit_plan_commit='cc1af5b',original_source_plan_commit='f93dd94',source_only=True,
        no_alpha_rule_cohort_weights_or_performance=True,adopted=False,
        original_failed_same_unit_contract_retained=True,
        native_unit_versions=rows,native_dated_boundary_metadata=metadata,
        native_version_aware_counterparts=comparisons,boundary_overlap_observations=shared,
        original_native_responses=20,additional_unit_responses=7,all_original_http200=True,
        version_aware_forensic_source_gate=True,native_input_sha256=hashes,
        publication_schedule_not_original_first_receipt_or_immutable_history_certificate=True,
        full_quantity_cohort_or_alpha_defined=False)


if __name__=='__main__':
    result=audit();(DATA.parent/'r31_version_aware_source_audit.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(json.dumps(dict(version_aware_forensic_source_gate=True,
        native_counterparts=len(result['native_version_aware_counterparts']),
        boundary_overlap=len(result['boundary_overlap_observations']),
        inputs=len(result['native_input_sha256']),adopted=False),indent=2))
