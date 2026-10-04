"""A freeze must reach actual raw inputs and code before outcomes can run."""
import hashlib
import json
from pathlib import Path

import pytest

from r33_freeze import REQUIRED_FROZEN, assert_frozen_inputs


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path):
    here = tmp_path/'user_data/bench/review_loop'
    here.mkdir(parents=True)
    for name in REQUIRED_FROZEN:
        (here/name).write_text('frozen '+name)
    native = here/'raw/native'
    native.parent.mkdir()
    native.write_text('original native quantities')
    code = here/'accounting_engine.py'
    code.write_text('original accounting engine')
    external = tmp_path/'user_data/bench/events/native'
    external.parent.mkdir()
    external.write_text('original native spot prices')
    inputs = dict(source_inputs_sha256={'raw/native':sha(native), 'user_data/bench/events/native':sha(external)},
                  source_code_sha256={'accounting_engine.py':sha(code)})
    (here/'r33_input_manifest.json').write_text(json.dumps(inputs))
    fixed = dict(protocol_commit='ff39936', new_full_performance=False,
                 source_and_cohort_sha256={n:sha(here/n) for n in REQUIRED_FROZEN})
    (here/'r33_prefreeze_manifest.json').write_text(json.dumps(fixed))
    return here, fixed


def test_complete_freeze_reaches_root_and_review_relative_native_inputs(tmp_path):
    here, fixed = fixture(tmp_path)
    assert assert_frozen_inputs(here) == fixed


@pytest.mark.parametrize('name', ['raw/native', 'accounting_engine.py',
    'user_data/bench/events/native', 'r33_weight_audit.csv', 'r33_input_manifest.json'])
def test_changed_dependency_or_regenerated_cohort_is_rejected_before_outcomes(tmp_path, name):
    here, fixed = fixture(tmp_path)
    path = here.parents[2]/name if name.startswith('user_data/') else here/name
    path.write_text('changed after freeze')
    with pytest.raises(ValueError):
        assert_frozen_inputs(here, fixed)


def test_omitted_target_digest_cannot_weaken_the_freeze(tmp_path):
    here, fixed = fixture(tmp_path)
    fixed['source_and_cohort_sha256'].pop('r33_weight_audit.csv')
    with pytest.raises(ValueError):
        assert_frozen_inputs(here, fixed)


def test_missing_preflight_freeze_rejects_without_materializing_outcomes(tmp_path):
    with pytest.raises(ValueError):
        assert_frozen_inputs(tmp_path)
