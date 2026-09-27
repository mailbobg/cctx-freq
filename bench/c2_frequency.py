"""C2: C's rules at three decision intervals (weekly / daily / 4-hourly). Spec: specs/2026-09-27-c2-decision-frequency.md

Usage: python user_data/bench/c2_frequency.py [fee]
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

FEE = float(sys.argv[1]) if len(sys.argv) > 1 else 0.001
VOL_TARGET, CAP, BAND = 0.60, 0.5, 0.05
CACHE = Path("user_data/bench/events")
WINDOWS = {"2017-2020": ("2017-08-17", "2020-12-31"), "2021-2026": ("2021-01-01", "2026-09-21")}


def klines(interval: str) -> pd.DataFrame:
    f = CACHE / f"c2_{interval}.parquet"
    if f.exists():
        return pd.read_parquet(f)
    s = requests.Session()
    out = {}
    for sym in ("BTCUSDT", "ETHUSDT"):
        rows, start = [], int(pd.Timestamp("2017-08-01", tz="UTC").timestamp() * 1000)
        while True:
            d = s.get("https://data-api.binance.vision/api/v3/klines",
                      params={"symbol": sym, "interval": interval, "startTime": start, "limit": 1000}, timeout=30).json()
            if not d:
                break
            rows += d
            start = d[-1][0] + 1
            if len(d) < 1000:
                break
        df = pd.DataFrame(rows).iloc[:, :5]
        df.columns = ["t", "open", "high", "low", "close"]
        df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True)
        out[sym[:3]] = df.set_index("t").astype(float)
    wide = pd.concat({k: v[["open", "close"]] for k, v in out.items()}, axis=1).dropna()
    wide.columns = [f"{a}_{b}" for a, b in wide.columns]
    wide.to_parquet(f)
    return wide


def simulate(px: pd.DataFrame, start: str, end: str, bars_per_day: int, decide) -> pd.DataFrame:
    coins = ["BTC", "ETH"]
    closes = px[[f"{c}_close" for c in coins]].set_axis(coins, axis=1)
    opens = px[[f"{c}_open" for c in coins]].set_axis(coins, axis=1)
    n = 30 * bars_per_day
    mom = closes / closes.shift(n) - 1
    vol = np.log(closes).diff().rolling(n).std() * np.sqrt(365 * bars_per_day)
    valid = mom.notna().all(axis=1) & vol.notna().all(axis=1)
    idx = closes.loc[start:end].index
    pos = {t: i for i, t in enumerate(closes.index)}
    cash, units = 1.0, pd.Series(0.0, index=coins)
    started, rows, trades = False, [], 0
    for t in idx:
        prev = closes.index[pos[t] - 1]
        if decide(t) and valid.loc[prev]:
            started = True
            target = (CAP * np.minimum(1, VOL_TARGET / vol.loc[prev])).where(mom.loc[prev] > 0, 0.0)
            o = opens.loc[t]
            value = cash + (units * o).sum()
            cur = units * o / value
            for c in coins:
                if abs(target[c] - cur[c]) > BAND:
                    trade = (target[c] - cur[c]) * value
                    units[c] += trade / o[c]
                    cash -= trade + abs(trade) * FEE
                    trades += 1
        if started:
            v = (units * closes.loc[t]).sum()
            rows.append((t, cash + v, v))
    s = pd.DataFrame(rows, columns=["t", "eq", "inv"]).set_index("t")
    s["inv"] = s["inv"] / s["eq"]
    s.attrs["trades"] = trades
    return s


def stats(s: pd.DataFrame) -> dict:
    eq = s["eq"] / s["eq"].iloc[0]
    daily = eq.resample("1D").last().dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365
    r = daily.pct_change().dropna()
    y = daily.resample("YE").last()
    yr = y / pd.concat([pd.Series([daily.iloc[0]], index=[daily.index[0]]), y.iloc[:-1]]).values - 1
    return {"CAGR": (eq.iloc[-1] ** (1 / yrs) - 1) * 100, "maxDD": (eq / eq.cummax() - 1).min() * 100,
            "Sharpe": r.mean() / r.std() * np.sqrt(365), "worst_yr": yr.min() * 100,
            "trades_per_yr": s.attrs["trades"] / yrs, "invested": s["inv"].mean() * 100}


def main() -> None:
    d1, h4 = klines("1d"), klines("4h")
    variants = {
        "W  weekly": (d1, 1, lambda t: t.weekday() == 0),
        "D  daily": (d1, 1, lambda t: True),
        "H4 4-hourly": (h4, 6, lambda t: True),
    }
    print(f"fee {FEE}")
    for name, (a, b) in WINDOWS.items():
        print(f"\n=== {name}")
        res = {}
        for v, (px, bpd, dec) in variants.items():
            res[v] = stats(simulate(px, a, b, bpd, dec))
            x = res[v]
            print(f"{v:12s} CAGR {x['CAGR']:+6.1f}%  maxDD {x['maxDD']:6.1f}%  Sharpe {x['Sharpe']:.2f}  worst yr {x['worst_yr']:+6.1f}%  "
                  f"trades/yr {x['trades_per_yr']:6.1f}  invested {x['invested']:4.0f}%")
        w = res["W  weekly"]
        for v in ("D  daily", "H4 4-hourly"):
            x = res[v]
            ok = {"maxDD lower": x["maxDD"] > w["maxDD"], "Sharpe >=": x["Sharpe"] >= w["Sharpe"], "CAGR >= 90%": x["CAGR"] >= 0.9 * w["CAGR"]}
            print(f"   {v}: " + "  ".join(f"{'PASS' if k_ok else 'FAIL'} {k}" for k, k_ok in ok.items()))


if __name__ == "__main__":
    main()
