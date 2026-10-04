"""A clean child repeats all eight frozen outcomes with network disabled."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from r33_freeze import assert_frozen_inputs

HERE=Path(__file__).resolve().parent


def replay():
    assert_frozen_inputs(HERE)
    names=['r33_results.json','r33_input_manifest.json','r33_source_audit.json',
        'r33_native_feature_validation.json','r33_decision_audit.csv','r33_weight_audit.csv',
        'r33_reflection.json',*sorted(p.name for p in HERE.glob('r33_*_cost*_daily.csv')),
        *sorted(p.name for p in HERE.glob('r33_*_cost*_orders.csv'))]
    if len(names)!=19:raise ValueError('Original eight-outcome and reflection artifact scope differs')
    before={n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in names}
    # run_path keeps the same actual model module population as the original script.
    code="""import sys,requests,runpy
from pathlib import Path
def denied(*a,**k):raise AssertionError('Offline outcome replay attempted network')
requests.Session.get=denied
script=Path(sys.argv[1]);sys.path.insert(0,str(script.parent));sys.argv=[str(script)]
runpy.run_path(str(script),run_name='__main__')
"""
    process=subprocess.run([sys.executable,'-c',code,str(HERE/'r33_run.py')],cwd=HERE.parents[2],capture_output=True,text=True)
    (HERE/'r33_offline_outcome_log.txt').write_text(process.stdout+process.stderr)
    if process.returncode:raise ValueError('Offline full outcome replay failed; retained log')
    verify=subprocess.run([sys.executable,str(HERE/'r33_reflection.py'),'--verify-only'],cwd=HERE.parents[2],capture_output=True,text=True)
    (HERE/'r33_offline_reflection_log.txt').write_text(verify.stdout+verify.stderr)
    if verify.returncode:raise ValueError('Independent native account replay failed; retained log')
    after={n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in names}
    if before!=after:raise ValueError('Frozen complete output bytes changed after offline replay')
    assert_frozen_inputs(HERE)
    inputs=json.loads((HERE/'r33_input_manifest.json').read_text())
    return dict(all_eight_outcomes_repeated_offline=True,network_disabled_for_outcomes=True,
        independent_native_report_targets_and_minute_accounts_replayed=True,
        original_output_artifacts_byte_identical=len(names),output_sha256=after,
        source_and_account_inputs_verified=len(inputs['source_inputs_sha256']),
        actual_imported_project_code_digests_verified=len(inputs['source_code_sha256']),adopted=False)


if __name__=='__main__':
    x=replay();(HERE/'r33_replay_validation.json').write_text(json.dumps(x,indent=2,sort_keys=True)+'\n')
    print({k:v for k,v in x.items() if k!='output_sha256'})
