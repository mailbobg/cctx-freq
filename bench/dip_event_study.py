"""Event study: what happens after a sharp 5m drop in an altcoin (spot, Binance).

Mechanism under test: panic sellers pay liquidity providers. After a fast drop relative to BTC, does the
price rebound by more than a round-trip cost (0.2% at 0.1% per side), over which horizon, and when?

Event (per pair, 5m candles):
  drop_k = log return of the coin over the last k bars minus that of BTC over the same bars,
  for k in {1, 3, 12} (5m, 15m, 1h). An event fires when drop_k <= −threshold on a closed bar.
  Events of the same pair within 4h of a previous event are skipped (no overlap).
Entry: next bar's open. Measured on raw prices (a spot long is what a strategy would hold):
  forward return to the close of +3, +12, +48, +288 bars (15m, 1h, 4h, 24h),
  maximum adverse excursion (lowest low vs entry) over the next 48 bars.
Conditioning variables recorded per event: drop size, volume ratio (event-bar quote volume vs the pair's
24h average), BTC's own move over the same k bars (market-wide vs idiosyncratic), hour of day, year.

Usage: python user_data/bench/dip_event_study.py  → writes user_data/bench/events/dip_events.parquet
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path("user_data/data/binance")
OUT = Path("user_data/bench/events/dip_events.parquet")
PAIRS = json.load(open("user_data/pairs-bench-full.json"))
START, END = pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2026-09-23", tz="UTC")
WINDOWS = {1: 0.015, 3: 0.025, 12: 0.04}  # bars → minimum excess drop (loose; tighter cuts are applied later)
COOLDOWN = 48
FWD = [3, 12, 48, 288]


def load(pair: str) -> pd.DataFrame:
    df = pd.read_feather(DATA / f"{pair.replace('/', '_')}-5m.feather")
    df = df[(df["date"] >= START) & (df["date"] <= END)].set_index("date")
    return df


def main() -> None:
    btc = load("BTC/USDT")[["close"]].rename(columns={"close": "btc"})
    rows = []
    for n, pair in enumerate(PAIRS, 1):
        if pair == "BTC/USDT":
            continue
        df = load(pair).join(btc, how="inner")
        lc, lb = np.log(df["close"]), np.log(df["btc"])
        qv = df["volume"] * df["close"]
        qv_24h = qv.rolling(288, min_periods=144).mean()
        nxt_open = df["open"].shift(-1)
        fwd = {h: df["close"].shift(-(h + 1)) / nxt_open - 1 for h in FWD}  # from next open to close h bars later
        low48 = df["low"][::-1].rolling(48, min_periods=1).min()[::-1].shift(-1)
        mae = low48 / nxt_open - 1
        for k, thr in WINDOWS.items():
            ex = (lc - lc.shift(k)) - (lb - lb.shift(k))
            btc_k = lb - lb.shift(k)
            cand = np.flatnonzero((ex <= -thr).to_numpy())
            last = -10**9
            for i in cand:
                if i - last < COOLDOWN or i + 289 >= len(df):
                    continue
                last = i
                rows.append({
                    "pair": pair, "date": df.index[i], "k": k, "drop": float(ex.iloc[i]), "btc_move": float(btc_k.iloc[i]),
                    "vol_ratio": float(qv.iloc[i - k + 1:i + 1].mean() / qv_24h.iloc[i]) if qv_24h.iloc[i] > 0 else np.nan,
                    "mae_4h": float(mae.iloc[i]), **{f"r{h}": float(fwd[h].iloc[i]) for h in FWD},
                })
        print(f"{n}/{len(PAIRS)} {pair}: {sum(1 for r in rows if r['pair'] == pair)} events", flush=True)
    ev = pd.DataFrame(rows)
    ev["year"] = ev["date"].dt.year
    ev["hour"] = ev["date"].dt.hour
    ev.to_parquet(OUT)
    print(f"saved {len(ev)} events")


if __name__ == "__main__":
    main()
