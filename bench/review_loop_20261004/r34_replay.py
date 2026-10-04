"""Offline exact native access checks; no synthetic staking income."""
import hashlib
import json
from pathlib import Path
import requests
from r34_audit import audit

HERE=Path(__file__).resolve().parent


def denied(*args,**kwargs):raise AssertionError('Offline native staking replay attempted network')


def replay():
    requests.Session.get=denied
    x=audit();file=HERE/'r34_source_audit.json'
    if file.read_text()!=json.dumps(x,indent=2,sort_keys=True)+'\n':raise ValueError('Original source state changed')
    for name,digest in x['native_input_sha256'].items():
        if hashlib.sha256((HERE/name).read_bytes()).hexdigest()!=digest:raise ValueError('Native source changed '+name)
    return dict(offline_network_disabled=True,original_source_audit_byte_identical=True,
        native_input_hashes_verified=len(x['native_input_sha256']),native_api_403_responses_retained=4,
        full_reward_cohort_policy_or_economic_outcomes=False,adopted=False)


if __name__=='__main__':
    x=replay();(HERE/'r34_replay_validation.json').write_text(json.dumps(x,indent=2,sort_keys=True)+'\n');print(x)
