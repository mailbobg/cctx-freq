"""Offline byte-identical replay of original report/calendar/publication checks."""
import hashlib
import json
from pathlib import Path
import requests
from r32_audit import audit as source
from r32_publication import audit as publication

HERE=Path(__file__).resolve().parent


def denied(*args,**kwargs):raise AssertionError('Offline source replay attempted network')


def replay():
    requests.Session.get=denied
    joined={}
    for name,fn in [('r32_source_audit.json',source),('r32_publication_audit.json',publication)]:
        old=(HERE/name).read_bytes();new=(json.dumps(fn(),indent=2,sort_keys=True)+'\n').encode()
        if old!=new:raise ValueError('Original source audit bytes changed '+name)
        joined[name]=hashlib.sha256(old).hexdigest()
    x=json.loads((HERE/'r32_publication_audit.json').read_text())
    for name,sha in x['native_input_sha256'].items():
        if hashlib.sha256((HERE/name).read_bytes()).hexdigest()!=sha:raise ValueError('Source input changed '+name)
    return dict(offline_network_disabled=True,original_source_audits_byte_identical=True,
        source_input_hashes_verified=len(x['native_input_sha256']),original_audit_sha256=joined,adopted=False)


if __name__=='__main__':
    x=replay();(HERE/'r32_replay_validation.json').write_text(json.dumps(x,indent=2,sort_keys=True)+'\n');print(x)
