"""Export the included-category unlock events of every Binance-perp-mapped protocol to strategies/unlock_schedule.csv
(pair, ts, tokens, category): the schedule file the U2 forward bot reads. Source: bench/events/unlocks.parquet (DefiLlama
emissions collected 2026-09-25; the API is paid now, so this file is the only schedule there is and is refreshed by hand).

Usage: python user_data/bench/make_unlock_schedule.py
"""

import pandas as pd

INCLUDED = ("insiders", "privateSale", "publicSale", "airdrop")
u = pd.read_parquet("user_data/bench/events/unlocks.parquet")
m = pd.read_parquet("user_data/bench/events/unlock_symbol_map.parquet")[["protocol", "perp"]]
u = u.merge(m, on="protocol")
u = u[u.perp.notna() & u.category.isin(INCLUDED)].copy()
u["pair"] = u.perp.str[:-4] + "/USDT:USDT"
u["ts"] = u.ts.dt.as_unit("s").astype("int64")
out = u[["pair", "ts", "tokens", "category"]].sort_values(["pair", "ts"]).reset_index(drop=True)
out.to_csv("user_data/strategies/unlock_schedule.csv", index=False)
fut = out[out.ts > pd.Timestamp("2026-09-25", tz="UTC").timestamp()]
print(f"{len(out)} events, {out.pair.nunique()} pairs; after 2026-09-25: {len(fut)} events, {fut.pair.nunique()} pairs")
