"""Offline native source replay; never refetch or replace an archive denial."""
import hashlib
import json
from pathlib import Path

import requests

from r30_native import audit
from r30_sources import collect


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def blocked(*args, **kwargs):
    raise RuntimeError('HTTP disabled for exact native source replay')


def main():
    here = Path(__file__).resolve().parent
    path = here/'r30_native_source_audit.json'
    original = path.read_bytes()
    expected = json.loads(original)
    before = {name:sha(here/'r30_sources'/name) for name in expected['native_input_sha256']}
    previous = requests.sessions.Session.request
    requests.sessions.Session.request = blocked
    try:
        collect(fetch=False)
        checked = audit()
    finally:
        requests.sessions.Session.request = previous
    reconstructed = (json.dumps(checked,indent=2,sort_keys=True)+'\n').encode()
    if reconstructed != original or before != {name:sha(here/'r30_sources'/name) for name in before}:
        raise ValueError('Original probe state or native source audit changed during offline replay')
    report = dict(http_disabled=True, native_input_hashes_checked=len(before),
        original_native_source_audit_byte_identical=True, artifact_sha256={'r30_native_source_audit.json':sha(path)},
        original_failure_state_retained=True, no_current_history_substitution=True,
        source_code_sha256={name:sha(here/name) for name in
            ('R30_SOURCE_PLAN.md','r30_sources.py','r30_native.py','test_r30_native.py','r30_replay.py')})
    (here/'r30_replay_validation.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps({k:v for k,v in report.items() if 'sha256' not in k},indent=2))


if __name__ == '__main__':
    main()
