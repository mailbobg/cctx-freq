"""Bitget funding proxy (spec X14-V data note): Bitget's API keeps ~33 days of funding history, so use Bybit's
funding rates for the same symbols. Symbols without a Bybit series are left out (the engine treats them as 0).

Usage: python user_data/bench/venue_funding_proxy.py
"""

from pathlib import Path

import pandas as pd

SRC = Path("user_data/data_x14_bybit/funding.parquet")
DST_DIR = Path("user_data/data_x14_bitget")

syms = {p.stem for p in (DST_DIR / "5m").glob("*.parquet")}
src = pd.read_parquet(SRC)
out = src[src.symbol.isin(syms)].copy()
covered = out.symbol.nunique()
own = DST_DIR / "funding_bitget_recent.parquet"
if (DST_DIR / "funding.parquet").exists():
    (DST_DIR / "funding.parquet").rename(own)          # keep Bitget's own (short) history for reference
out.to_parquet(DST_DIR / "funding.parquet")
print(f"proxy funding: {covered} of {len(syms)} Bitget symbols covered by Bybit funding, {len(out)} rows; "
      f"missing: {sorted(syms - set(out.symbol))[:15]}")
