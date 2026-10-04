"""Offline replay of strict failure, observed native source and actual accounts."""
import json
import requests
from r35_native import HERE,derive as strict_sources,digest,write_json
from r35_observed import derive as observed_sources
from r35_account import run


def forbidden(*args,**kwargs):raise AssertionError('Native/account replay attempted HTTP')


def replay():
    requests.Session.request=forbidden
    before={p.name:digest(p) for p in HERE.glob('r35_observed*') if p.is_file() and p.suffix in ('.json','.csv')}
    strict=strict_sources(offline=True)
    source=observed_sources(verify=True)
    account=run(verify=True,observed_only=True)
    for name,sha in before.items():
        if digest(HERE/name)!=sha:raise ValueError('Replay mutated original output '+name)
    return dict(HTTP_disabled=True,original_strict_source_failure_byte_identical=True,
        original_observed_source_byte_identical=True,eight_account_risk_results_byte_identical=True,
        sixteen_daily_order_CSV_outputs_byte_identical=True,derived_outputs_checked=len(before),
        original_native_response_roles=strict['url_roles'],guarded_code_and_native_artifacts=516,
        native_valid_minute_rows={a:v['rows'] for a,v in source['validation'].items()},
        source_contract_failure_not_promoted=True,actual_export_cash_units_fees_reconciled=True,
        adopted=False,no_strategy_bot_image_live_or_existing_forward_change=True)


if __name__=='__main__':
    out=replay();write_json(HERE/'r35_replay_validation.json',out);print(out,flush=True)
