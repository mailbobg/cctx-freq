"""Freeze R17 rate-only decisions; no mark-weighted income or price/P&L screen."""
import hashlib
import json
from pathlib import Path

import pandas as pd

from r17_carry import ASSETS,HURDLE,FundingSeries,funding_forecast,select_pairs
from r17_data import DATA,START,END,freeze_local


HERE=Path(__file__).resolve().parent


def main():
    freeze_local()
    manifest=json.loads((DATA/'funding_manifest.json').read_text())
    for name,expected in manifest['output_sha256'].items():
        if hashlib.sha256((DATA/name).read_bytes()).hexdigest()!=expected:raise ValueError('Frozen source funding data changed')
    series={symbol:FundingSeries(pd.read_parquet(DATA/f'{symbol}_funding.parquet'),START,END) for symbol in ASSETS}
    rows=[];decisions=[];full_prefix=True
    for decision in pd.date_range('2024-01-01','2026-08-31',freq='W-MON',tz='UTC')+pd.Timedelta(hours=1):
        cutoff=decision-pd.Timedelta(hours=1)
        scores={};prefix_scores={}
        for symbol in ASSETS:
            source=series[symbol];score=funding_forecast(source,decision)
            prefix=FundingSeries(source.events.loc[source.events.slot<=cutoff],START,decision)
            prefix_scores[symbol]=funding_forecast(prefix,decision)
            if prefix_scores[symbol]!=score:raise ValueError('Rate forecast prefix uses unseen future information')
            scores[symbol]=score
        selected=select_pairs(scores)
        if select_pairs(prefix_scores)!=selected:raise ValueError('Selected asset identities differ under prefix')
        decisions.append(dict(decision=decision.isoformat(),cutoff=cutoff.isoformat(),selected=list(selected),slot_count=len(selected)))
        for symbol in ASSETS:
            rows.append(dict(decision=decision,cutoff=cutoff,symbol=symbol,forecast_decimal=str(scores[symbol]),
                             qualifies=scores[symbol]>HURDLE,
                             selected=symbol in selected,slot_count=len(selected),
                             candidate_sleeve_fraction=.5 if symbol in selected else 0.,
                             equal_control_sleeve_fraction=len(selected)/(2*len(ASSETS))))
    frame=pd.DataFrame(rows)
    frame.to_csv(HERE/'r17_decision_audit.csv',index=False)
    decision_path=HERE/'r17_decisions.json';decision_path.write_text(json.dumps(decisions,indent=2)+'\n')
    occupied=frame.loc[frame.selected]
    validation=dict(source_only=True,new_performance=False,decision_weeks=len(decisions),full_prefix_forecasts_equal=full_prefix,
                    qualified_coin_weeks=int(frame.qualifies.sum()),selected_coin_weeks=len(occupied),
                    active_weeks=int(frame.groupby('decision').selected.any().sum()),
                    selected_asset_counts={s:int(occupied.symbol.eq(s).sum()) for s in ASSETS},
                    no_position_on_unqualified_weeks=bool((~frame.selected | frame.qualifies).all()),
                    decision_outputs_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [decision_path,HERE/'r17_decision_audit.csv']},
                    limitations='Forecast selection is not a mark-weighted cash payment, total return, profitability bound or risk result. Reused history, no parameter rescue or activation.')
    (HERE/'r17_forecast_validation.json').write_text(json.dumps(validation,indent=2)+'\n')
    print(json.dumps(validation,indent=2))


if __name__=='__main__':main()
