"""HTTP-disabled exact replay of original failed and version-aware source contracts."""
import hashlib
import json
from pathlib import Path

import requests

from r31_native import observe
from r31_sources import collect
from r31_units import audit
from r31_units_sources import collect as collect_units


def blocked(*args,**kwargs):raise RuntimeError('HTTP disabled for native R31 replay')


def main():
    here=Path(__file__).resolve().parent
    paths=('r31_initial_source_observation.json','r31_version_aware_source_audit.json')
    before={n:(here/n).read_bytes() for n in paths}
    expected=json.loads(before[paths[1]])
    native={name:hashlib.sha256((here/name).read_bytes()).hexdigest() for name in expected['native_input_sha256']}
    previous=requests.sessions.Session.request;requests.sessions.Session.request=blocked
    try:
        collect(False);collect_units(False)
        original=observe();normalized=audit()
    finally:requests.sessions.Session.request=previous
    for name,result in zip(paths,(original,normalized)):
        if (json.dumps(result,indent=2,sort_keys=True)+'\n').encode()!=before[name]:
            raise ValueError('Original native source contract replay differs '+name)
    for name,digest in native.items():
        if hashlib.sha256((here/name).read_bytes()).hexdigest()!=digest:raise ValueError('Original source input changed '+name)
    report=dict(http_disabled=True,artifacts_byte_identical=len(paths),native_input_hashes_checked=len(native),
        artifact_sha256={n:hashlib.sha256(before[n]).hexdigest() for n in paths},
        original_unverified_same_unit_failure_retained=True,all_original_payloads_bodies_versions_unchanged=True,
        no_alpha_rule_weights_cohort_or_performance=True,
        source_code_sha256={n:hashlib.sha256((here/n).read_bytes()).hexdigest() for n in
            ('R31_SOURCE_PLAN.md','R31_UNIT_PLAN.md','r31_sources.py','r31_native.py','r31_units_sources.py','r31_units.py','r31_replay.py','test_r31_native.py','test_r31_units.py')})
    (here/'r31_replay_validation.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps({k:v for k,v in report.items() if 'sha256' not in k},indent=2))


if __name__=='__main__':main()
