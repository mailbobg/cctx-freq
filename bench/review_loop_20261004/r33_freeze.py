"""Freeze every original input and actual imported project-code dependency."""
import hashlib
import json
from pathlib import Path
import re

REQUIRED_FROZEN=('R33_PROTOCOL.md','r33_calendar.py','r33_sources.py','r33_quantity.py','r33_audit.py',
    'r33_run.py','r33_reflection.py','r33_freeze.py','test_r33_freeze.py',
    'r33_planned_sources.json','r33_source_audit.json','r33_native_feature_validation.json',
    'r33_input_manifest.json','r33_decision_audit.csv','r33_weight_audit.csv')


def _check(base,name,digest):
    path=Path(name)
    if path.is_absolute() or '..' in path.parts or not re.fullmatch('[0-9a-f]{64}',digest):
        raise ValueError('Invalid frozen dependency identity '+name)
    file=base/path
    if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=digest:
        raise ValueError('Frozen source/code/cohort changed '+name)


def assert_frozen_inputs(here,fixed=None):
    here=Path(here)
    if fixed is None:
        freeze=here/'r33_prefreeze_manifest.json'
        if not freeze.exists():raise ValueError('Explicit source/code/cohort prefreeze required before outcomes')
        fixed=json.loads(freeze.read_text())
    if fixed['protocol_commit']!='ff39936' or fixed['new_full_performance'] is not False:
        raise ValueError('Unreviewed source/alpha freeze')
    mapping=fixed['source_and_cohort_sha256']
    if not set(REQUIRED_FROZEN)<=mapping.keys():raise ValueError('Required frozen target/code/source digests absent')
    for name,sha in mapping.items():_check(here,name,sha)
    inputs=json.loads((here/'r33_input_manifest.json').read_text())
    for group in ('source_inputs_sha256','source_code_sha256'):
        if not inputs[group]:raise ValueError('Native input or imported code closure absent')
        for name,sha in inputs[group].items():
            base=here.parents[2] if name.startswith(('user_data/','freqtrade/')) else here
            _check(base,name,sha)
    return fixed
