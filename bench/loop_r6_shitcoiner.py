"""Loop round 6: SHITCOINER (r/algotradingcrypto 2026-08-09) as a two-leg simulation.

Short old alts on local spikes, hedge each short with an equal-notional BTC long. Every 2h: if an alt is
>= 4% above its price 2h ago, open one unit (short alt + long BTC). Close a unit when its combined P&L
(both legs, fees and funding) reaches +2%; cut it at −30%. One open unit per coin at a time.
Universe: our 63 perps minus BTC, listed >= 1.5 years before each date. Fee 0.05%/side/leg; funding
applied to both legs from the downloaded 8h funding series. Variant: no BTC hedge.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

FUT = Path("user_data/data/binance/futures")
L = pd.read_parquet("user_data/bench/events/listings.parquet").set_index("symbol")["onboard"]
PAIRS = [p for p in json.load(open("user_data/pairs-x9.json")) if not p.startswith("BTC/")]
FEE, TP, SL, SPIKE = 0.0005, 0.02, -0.30, 0.04
START, END = pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2026-09-23", tz="UTC")


def load(pair):
    f = FUT / f"{pair.replace('/', '_').replace(':', '_')}-1h-futures.feather"
    d = pd.read_feather(f).set_index("date")["close"]
    return d[(d.index >= START - pd.Timedelta(days=2)) & (d.index <= END)]


def funding(pair):
    f = FUT / f"{pair.replace('/', '_').replace(':', '_')}-1h-funding_rate.feather"
    return pd.read_feather(f).set_index("date")["funding_rate"] if f.exists() else pd.Series(dtype=float)


def run(hedge: bool):
    btc = load("BTC/USDT:USDT"); btc_f = funding("BTC/USDT:USDT")
    rows = []
    for pair in PAIRS:
        sym = pair.split(":")[0].replace("/", "")
        onboard = pd.Timestamp(L.get(sym, pd.NaT)).tz_localize("UTC") if pd.notna(L.get(sym, pd.NaT)) else None
        px = load(pair); fr = funding(pair)
        idx = px.index.intersection(btc.index)
        px, b = px.reindex(idx), btc.reindex(idx)
        i, n = 0, len(idx)
        while i < n - 1:
            t = idx[i]
            eligible = onboard is not None and t >= onboard + pd.Timedelta(days=548) and t >= START
            if not (eligible and t.hour % 2 == 0 and i >= 2 and px.iloc[i] / px.iloc[i - 2] - 1 >= SPIKE):
                i += 1; continue
            e_a, e_b = px.iloc[i], b.iloc[i]
            j = i + 1; exit_reason = "end"
            while j < n:
                short_leg = -(px.iloc[j] / e_a - 1)
                long_leg = (b.iloc[j] / e_b - 1) if hedge else 0.0
                f_short = fr[(fr.index > t) & (fr.index <= idx[j])].sum()          # short receives +rate
                f_long = -btc_f[(btc_f.index > t) & (btc_f.index <= idx[j])].sum() if hedge else 0.0
                legs = 2 if hedge else 1
                pnl = (short_leg + long_leg + f_short + f_long) / legs - 2 * FEE    # per unit of total notional
                if pnl >= TP: exit_reason = "tp"; break
                if pnl <= SL: exit_reason = "sl"; break
                j += 1
            j = min(j, n - 1)
            rows.append({"pair": pair, "open": t, "close": idx[j], "pnl": pnl, "reason": exit_reason})
            i = j + 1
    return pd.DataFrame(rows)


for hedge in (True, False):
    R = run(hedge)
    R["month"] = R["close"].dt.tz_localize(None).dt.to_period("M")
    m = R.groupby("month")["pnl"].sum()
    by_y = R.groupby(R["close"].dt.year)["pnl"].agg(["count", "mean", "sum"])
    top3 = m.sort_values(ascending=False).iloc[:3].sum()
    print(f"\n=== {'hedged (short alt + long BTC)' if hedge else 'unhedged (short alt only)'}: {len(R)} units, "
          f"win {(R.pnl > 0).mean() * 100:.0f}%, mean per unit {R.pnl.mean() * 100:+.2f}%, "
          f"median hold {(R.close - R.open).median()}")
    print("  exits:", R.reason.value_counts().to_dict())
    print("  per year (units / mean % / sum of unit returns %):", {y: (int(r['count']), round(r['mean'] * 100, 2), round(r['sum'] * 100, 1)) for y, r in by_y.iterrows()})
    print(f"  months {len(m)}, positive {(m > 0).mean() * 100:.0f}%, sum {m.sum() * 100:+.1f}, sum minus best 3 months {(m.sum() - top3) * 100:+.1f}, worst unit {R.pnl.min() * 100:+.1f}%")
