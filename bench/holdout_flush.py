"""X11 flush effect on the 2021–2023 holdout (spec: specs/2026-09-26-holdout-2021-2023.md).

Uses the exact X11 detector (`flush_panel` in strategies/FlushReboundX11.py) on each year's point-in-time
universe, with no cap on the number of coins, and measures per episode the mean 24h return of the flushed
coins in excess of BTC (entry at the next 5m open, exit at the close 288 bars later).
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "user_data/strategies")
from FlushReboundX11 import flush_panel  # noqa: E402

DATA = Path("user_data/data_holdout/binance")
UNIV = json.loads(Path("user_data/bench/holdout_universe.json").read_text())


def load(pair: str) -> pd.DataFrame:
    return pd.read_feather(DATA / f"{pair}-5m.feather").set_index("date")


def main() -> None:
    btc_all = load("BTC_USDT")
    rows = []
    for y, syms in UNIV.items():
        start, end = pd.Timestamp(f"{y}-01-01", tz="UTC"), pd.Timestamp(f"{int(y) + 1}-01-01", tz="UTC")
        opens, closes = {}, {}
        for s in syms:
            if s == "BTCUSDT" or not (DATA / f"{s[:-4]}_USDT-5m.feather").exists():
                continue
            df = load(f"{s[:-4]}_USDT")
            opens[s], closes[s] = df["open"], df["close"]
        idx = btc_all.index[(btc_all.index >= start - pd.Timedelta(days=1)) & (btc_all.index < end)]
        close = pd.DataFrame(closes).reindex(idx)
        opn = pd.DataFrame(opens).reindex(idx)
        btc_c, btc_o = btc_all["close"].reindex(idx), btc_all["open"].reindex(idx)
        sel = flush_panel(close, btc_c, max_coins=10**6)
        sel = sel[sel.index >= start]
        for t in sel.index[sel.any(axis=1)]:
            i = idx.get_loc(t)
            if i + 289 >= len(idx):
                continue
            picks = sel.columns[sel.loc[t]]
            ent, ext = opn.iloc[i + 1][picks], close.iloc[i + 288][picks]
            r = np.log(ext / ent).dropna()
            b = np.log(btc_c.iloc[i + 288] / btc_o.iloc[i + 1])
            rows.append({"date": t, "year": int(y), "coins": len(r), "raw": r.mean(), "excess": (r - b).mean()})
    ev = pd.DataFrame(rows)
    ev.to_csv("user_data/bench/events/holdout_flush_episodes.csv", index=False)

    def line(d, label):
        x = d["excess"] * 100
        t = x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 1 else float("nan")
        print(f"{label:6s} episodes {len(x):4d}  coins/ep {d['coins'].mean():5.1f}  24h excess {x.mean():+.2f}%  "
              f"t {t:5.2f}  win {(x > 0).mean() * 100:4.0f}%  raw {d['raw'].mean() * 100:+.2f}%")

    for y, d in ev.groupby("year"):
        line(d, str(y))
    line(ev, "all")


if __name__ == "__main__":
    main()
