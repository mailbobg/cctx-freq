"""Exact offline reproduction of the frozen rule and independent native accounts."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import httpx
import requests

HERE=Path(__file__).resolve().parent


def blocked(*args, **kwargs):
    raise AssertionError('HTTP forbidden in exact R29 native replay')


requests.sessions.Session.request=blocked
httpx.Client.request=blocked
httpx.AsyncClient.request=blocked

import r29_run
from r29_reflection import reflect
from r29_mark_audit import audit
from r29_freeze import assert_frozen_inputs

paths=sorted(list(HERE.glob('r29_*_daily.csv'))+list(HERE.glob('r29_*_orders.csv'))+[
    HERE/n for n in ['r29_source_audit.json','r29_native_feature_validation.json',
        'r29_decision_audit.csv','r29_weight_audit.csv','r29_input_manifest.json',
        'r29_prefreeze_manifest.json','r29_results.json','r29_reflection.json',
        'r29_mark_audit.json','r29_official_funding_screen.png']])
if len(paths)!=22:raise ValueError('Exact outcome/audit/figure replay scope changed')
snapshot={str(p.relative_to(HERE)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
fixed=assert_frozen_inputs(HERE)
sys.argv=['r29_run.py']
r29_run.main()
print('Eight fixed outcomes replayed with HTTP disabled',flush=True)
expected=json.dumps(reflect(),indent=2,sort_keys=True)+'\n'
if (HERE/'r29_reflection.json').read_text()!=expected:raise ValueError('Source/rate/price/fill reflection differs')
expected=json.dumps(audit(),indent=2,sort_keys=True)+'\n'
if (HERE/'r29_mark_audit.json').read_text()!=expected:raise ValueError('Independent native minute capital paths differ')
subprocess.run(['uv','run','--offline','--no-project','--with','matplotlib','--with','pandas',
                'python',str(HERE/'r29_plot.py')],check=True)
for p in paths:
    if hashlib.sha256(p.read_bytes()).hexdigest()!=snapshot[str(p.relative_to(HERE))]:
        raise ValueError('Frozen replay artifact differs '+p.name)
assert_frozen_inputs(HERE,fixed)
input_manifest=json.loads((HERE/'r29_input_manifest.json').read_text())
result=dict(http_disabled=True,all_eight_frozen_outcomes_replayed=True,
    all_source_native_rate_and_daily_price_fill_cash_accounts_replayed=True,
    all_four_independent_native_minute_account_paths_replayed=True,
    exact_artifact_count=len(paths),artifact_sha256=snapshot,
    exact_regenerated_standard_plot_sha_equal=True,
    retained_native_input_hashes_verified=len(input_manifest['source_inputs_sha256']),
    prefreeze_commit='f4a707e',protocol_commit='58d4344',
    no_original_source_code_curve_order_rule_or_forward_cohort_change=True,
    adopted=False)
(HERE/'r29_replay_validation.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
print('R29 offline exact model/native-account/figure replay PASS',len(paths),'artifacts',flush=True)
