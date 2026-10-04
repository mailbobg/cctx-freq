"""Size-granularity diagnostic on existing control holdings; no option return model."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent


def main():
    folder = HERE/'r11_probe'
    meta = json.loads((folder/'source_validation.json').read_text())['metadata']
    references = {'early_reused':{'BTC':'BTC-26MAR21-28000-P','ETH':'ETH-26MAR21-960-P'},
                  'recent_reused':{'BTC':'BTC-29MAR24-40000-P','ETH':'ETH-29MAR24-2000-P'}}
    result = {}
    for label in references:
        path = HERE/f'r10_{label}_Cov10_baseline_cost0.0012_daily.csv'
        curve = pd.read_csv(path,index_col='date',parse_dates=True)
        block = {}
        for asset in ('BTC','ETH'):
            source = references[label][asset]
            minimum = meta[source]['min_trade_amount']
            units = curve['pre_close_units_'+asset]*10000
            weeks = units.loc[units.index.weekday==0]
            active = weeks[weeks>1e-12]
            covers = np.ceil(active/minimum-1e-12)*minimum
            ratio = covers/active
            block[asset] = dict(source_instrument=source,observed_min_native_option_amount=minimum,
                active_research_Monday_holdings=len(active),holdings_below_one_option_lot=int((active<minimum-1e-12).sum()),
                fraction_below_one_lot_pct=float((active<minimum-1e-12).mean()*100),
                median_ceil_cover_to_underlying_quantity_ratio=float(ratio.median()),
                max_ceil_cover_to_underlying_quantity_ratio=float(ratio.max()),median_active_asset_quantity=float(active.median()),
                baseline_curve_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        result[label] = block
    out = {'initial_USDT_diagnostic_scale':10000,'holdings_quantity_fit':result,
           'limitations':'Quantity-fit diagnostic on unchanged common research Cov10 spot holdings, not actual Freqtrade option execution/P&L. Source-sampled inverse-option minimum quantities, not a reconstructed historical schedule for every option. Ceil purchases extra downside notional, not exact price/delta hedging. No premiums, strikes or profits optimized.'}
    (folder/'quantity_fit.json').write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps(out,indent=2))


if __name__ == '__main__':
    main()
