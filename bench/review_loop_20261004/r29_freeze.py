"""Reject changed native inputs, accounting code or regenerated target artifacts."""
import hashlib
import json
from pathlib import Path
import re

REQUIRED_FROZEN = (
    'R29_PROTOCOL.md', 'r29_funding.py', 'r29_run.py', 'r29_reflection.py',
    'r29_cohort_sources.py', 'r29_view.py', 'r29_freeze.py', 'test_r29_freeze.py',
    'r29_source_audit.json', 'r29_native_feature_validation.json',
    'r29_input_manifest.json', 'r29_decision_audit.csv', 'r29_weight_audit.csv',
)


def _check(base, name, digest):
    path = Path(name)
    if path.is_absolute() or '..' in path.parts or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('Invalid frozen file identity '+name)
    file = base/path
    if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != digest:
        raise ValueError('Frozen source/code/cohort changed '+name)


def assert_frozen_inputs(here, fixed=None):
    here = Path(here)
    if fixed is None:
        freeze = here/'r29_prefreeze_manifest.json'
        if not freeze.exists():
            raise ValueError('Explicit source/code/cohort prefreeze required before any full outcome')
        fixed = json.loads(freeze.read_text())
    if fixed['protocol_commit'] != '58d4344' or fixed['new_full_performance'] is not False:
        raise ValueError('Unreviewed preregistered alpha freeze')
    mapping = fixed['source_and_cohort_sha256']
    if not set(REQUIRED_FROZEN) <= mapping.keys():
        raise ValueError('Required frozen economic source/code/cohort digests missing')
    for name, sha in mapping.items():
        _check(here, name, sha)
    inputs = json.loads((here/'r29_input_manifest.json').read_text())
    for group in ('source_inputs_sha256', 'source_code_sha256'):
        if not inputs[group]:
            raise ValueError('Native input or calculation code digests absent')
        for name, sha in inputs[group].items():
            base = here.parents[2] if name.startswith('user_data/') else here
            _check(base, name, sha)
    return fixed
