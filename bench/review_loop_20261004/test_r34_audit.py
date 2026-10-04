import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from r34_audit import audit,endpoint_state


def test_actual_endpoint_access_failure_not_income_or_empty_schema():
    x=audit()
    assert x['original_requests']==10 and x['documentation_http200_roles']==6
    assert x['native_api_http403_roles']==4
    assert not x['full_current_or_historical_reward_source_gate']
    assert all(not r['source_known'] and r['unknown_not_zero_income'] for r in x['original_api_endpoint_states'].values())


def test_http200_html_does_not_become_favorable_zero_reward():
    x=endpoint_state(dict(status=200,sha256='a'*64,bytes=20),b'<html>Swagger</html>')
    assert not x['source_known'] and 'no native JSON' in x['original_failure']


def test_unverified_numeric_json_units_cannot_be_inferred_from_scale():
    x=endpoint_state(dict(status=200,sha256='a'*64,bytes=10),json.dumps({'apr':3.2}).encode())
    assert not x['source_known'] and x['native_field_names']==['apr']
    assert 'units not yet verified' in x['original_failure']


def test_transport_unknown_distinct_from_http_denial():
    x=endpoint_state(dict(status=None),None)
    assert x['original_failure']=='transport' and not x['source_known']


def test_native_documentation_is_current_context_not_history_or_global_address_feed():
    x=audit();docs=x['original_documentation']
    assert docs['subgraph_docs']['native_gateway_requires_api_key_placeholder']
    assert docs['api_docs']['address_specific_reward_history_not_global_protocol_cohort']
    assert docs['api_docs']['legacy_versus_V2_rebase_semantics_disclosed']


def test_unregistered_source_plan_cannot_certify_native_attempts(tmp_path,monkeypatch):
    import r34_audit
    (tmp_path/'manifest.json').write_text(json.dumps(dict(source_plan_commit='foreign',source_stage_only=True)))
    monkeypatch.setattr(r34_audit,'DATA',tmp_path)
    with pytest.raises(ValueError,match='source-plan identity'):r34_audit.audit()
