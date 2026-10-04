"""Native endpoint availability is not staking income or profitable P&L."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from r20_audit import retained
from r34_sources import DATA,collect,targets

HERE=Path(__file__).resolve().parent
API_ROLES=('apr_last','apr_sma','apr_swagger','reward_swagger')


class Text(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.parts=[]
    def handle_data(self,value):self.parts.append(value)


def endpoint_state(record,body):
    result=dict(status=record['status'],source_sha256=record.get('sha256'),bytes=record.get('bytes'),
        source_known=False,native_current_apr_or_schema_unavailable=True,
        unknown_not_zero_income=True,not_original_public_receipt_or_historical_cohort=True)
    if record['status']!=200:
        result['original_failure']='transport' if record['status'] is None else 'HTTP '+str(record['status'])
        if body:
            p=Text();p.feed(body.decode('utf-8',errors='strict'));joined=' '.join(p.parts)
            title=re.findall(r'<title[^>]*>(.*?)</title>',body.decode('utf-8'),re.S|re.I)
            result['original_html_title']=title
            result['native_access_challenge_markers']=bool('Just a moment' in joined or 'challenge-platform' in body.decode('utf-8'))
        return result
    # An HTTP200Swagger shell is not a complete response schema; a JSON body
    # also needs its native schema before economic fields/units can be used.
    try:native=json.loads(body)
    except (json.JSONDecodeError,UnicodeDecodeError):
        result['original_failure']='HTTP200body has no native JSON schema/current value'
    else:
        result['original_json_structure_type']=type(native).__name__
        result['native_field_names']=list(native) if isinstance(native,dict) else None
        result['original_failure']='Native schema/units not yet verified; cannot infer income'
    return result


def audit():
    manifest=json.loads((DATA/'manifest.json').read_text())
    if manifest['source_plan_commit']!='5c7e0f5' or manifest['source_stage_only'] is not True:
        raise ValueError('Original staking source-plan identity differs')
    records=collect()
    if set(records)!=set(targets()):raise ValueError('All ten original source attempts required')
    hashes={'r34_sources/manifest.json':hashlib.sha256((DATA/'manifest.json').read_bytes()).hexdigest()}
    documentation={};states={}
    for role,r in records.items():
        body=retained(DATA,r)
        if body is not None:hashes['r34_sources/'+r['path']]=r['sha256']
        if role in API_ROLES:states[role]=endpoint_state(r,body)
        else:
            if r['status']!=200 or body is None:raise ValueError('Original descriptive documentation unavailable '+role)
            p=Text();p.feed(body.decode('utf-8'));joined=''.join(p.parts)
            documentation[role]=dict(status=200,source_sha256=r['sha256'],current_doc_not_historical_protocol_certificate=True)
            if role=='api_docs':
                for url in (targets()['apr_last'],targets()['apr_sma']):
                    if url not in joined:raise ValueError('Native documented exact endpoint differs')
                documentation[role]['last_and_sma_literal_endpoint_identities_verified']=True
                documentation[role]['address_specific_reward_history_not_global_protocol_cohort']='address' in joined and 'Reward History' in joined
                documentation[role]['legacy_versus_V2_rebase_semantics_disclosed']='TokenRebased' in joined and 'preTotalShares' in joined and 'postTotalShares' in joined
            if role=='subgraph_docs':
                documentation[role]['native_gateway_requires_api_key_placeholder']='[api-key]' in joined
            if role=='wrapper_docs':
                documentation[role]['nonrebasing_balance_and_changing_conversion_value_disclosed']='balance does not change' in joined
    return dict(source_plan_commit='5c7e0f5',source_only=True,original_requests=len(records),
        documentation_http200_roles=len(documentation),native_api_http403_roles=sum(r['status']==403 for role,r in records.items() if role in API_ROLES),
        original_api_endpoint_states=states,original_documentation= documentation,
        full_current_or_historical_reward_source_gate=False,no_full_cashflow_price_funding_cohort_or_alpha_defined=True,
        no_current_apr_backfill_or_parity_or_double_counted_synthetic_income=True,
        no_authenticated_historical_protocol_or_publication_clock_or_execution=True,
        native_input_sha256=hashes,adopted=False)


if __name__=='__main__':
    x=audit();(HERE/'r34_source_audit.json').write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')
    print({k:x[k] for k in ('original_requests','documentation_http200_roles','native_api_http403_roles','full_current_or_historical_reward_source_gate','adopted')})
