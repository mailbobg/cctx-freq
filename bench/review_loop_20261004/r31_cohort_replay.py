"""Independent literal source join and exact HTTP-disabled failed-cohort replay."""
import csv
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json

import requests

from r31_cohort_sources import DATA, HERE, collect
from r31_cohort_audit import audit


def blocked(*args,**kwargs):raise RuntimeError('HTTP disabled for native failed-cohort replay')


def main():
    path=HERE/'r31_full_source_validation.json'
    original=path.read_bytes();expected=json.loads(original)
    prior=requests.sessions.Session.request;requests.sessions.Session.request=blocked
    try:
        collect(False)
        report=audit(checkpoint_capture_utc=expected['checkpoint_capture_utc'])
    finally:requests.sessions.Session.request=prior
    if (json.dumps(report,indent=2,sort_keys=True)+'\n').encode()!=original:
        raise ValueError('Native failed-cohort audit snapshot replay differs')
    manifest=json.loads((DATA/'manifest.json').read_text());checked=0;missing=[];rows=0
    for entry in expected['source_coverage']:
        native=manifest['sources'][entry['selected_record']]
        body=(DATA/native['path']).read_bytes()
        if hashlib.sha256(body).hexdigest()!=entry['source_sha256']:raise ValueError('Original native source changed')
        reader=csv.reader(io.StringIO(body.decode()))
        if next(reader)!=['observation_date','WRESBAL_'+entry['vintage'].replace('-','')]:raise ValueError('Native independent header differs')
        pairs=list(reader);rows+=len(pairs)
        data={row[0]:row[1] for row in pairs}
        monday=date.fromisoformat(entry['monday']);current=monday-timedelta(days=5)
        known={r['observation_date']:r for r in entry['native_observations']}
        absent={r['observation_date']:r for r in entry['native_unavailable_required_observations']}
        for day in (current,current-timedelta(days=28)):
            key=day.isoformat();text=data.get(key)
            if text in (None,'','.'):
                state=absent[key]
                if state['native_text']!=text or state['native_row_present']!=(key in data):raise ValueError('Independent unavailable native state differs')
                missing.append(dict(monday=entry['monday'],vintage=entry['vintage'],observation_date=key,native_text=text))
            else:
                value=known[key]
                if value['native_text']!=text or pairs[value['original_row_index']]!=[key,text]:raise ValueError('Independent native literal/row join differs')
                checked+=1
    if checked!=expected['native_required_known_cells'] or len(missing)!=expected['native_unavailable_required_cells'] or rows!=expected['native_rows_parsed']:
        raise ValueError('Independent full source coverage differs')
    for name,digest in expected['native_input_sha256'].items():
        if hashlib.sha256((HERE/name).read_bytes()).hexdigest()!=digest:raise ValueError('Native input hash changed '+name)
    result=dict(replayed_utc=datetime.now(timezone.utc).isoformat(),http_disabled=True,
        original_audit_snapshot_clock_preserved_not_new_public_receipt=True,
        full_source_audit_byte_identical=True,native_input_hashes_checked=len(expected['native_input_sha256']),
        independent_known_exact_native_cells=checked,independent_unavailable_required_cells=len(missing),
        independent_full_native_rows=rows,unavailable_native_examples=missing,
        full_required_source_gate=expected['full_required_source_gate'],no_policy_weights_or_performance=True,
        source_audit_sha256=hashlib.sha256(original).hexdigest())
    (HERE/'r31_cohort_replay_validation.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
