"""1m spot candles 2024-01 → 2026-09-23 for the 118 bench pairs, from data.binance.vision.

Monthly archives up to 2026-08, daily archives for 2026-09-01..23. Merged with any existing
user_data/data/binance/<PAIR>-1m.feather (e.g. from freqtrade download-data).

Usage: python user_data/bench/fetch_vision_1m.py
"""

import io
import json
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from fetch_vision import months, read_csv, session  # noqa: E402

VISION = "https://data.binance.vision/data/spot"
RAW = Path("user_data/data_1m_raw")
OUT = Path("user_data/data/binance")
PAIRS = json.load(open("user_data/config-bench.json"))["exchange"]["pair_whitelist"]


def fetch(url: str, dest: Path) -> Path | None:
    if dest.exists():
        return dest
    if dest.with_suffix(".missing").exists():
        return None
    for attempt in range(5):
        try:
            r = session.get(url, timeout=90)
            if r.status_code == 404:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.with_suffix(".missing").touch()
                return None
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                data = z.read(z.namelist()[0])
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            return dest
        except Exception as e:  # noqa: BLE001
            if attempt == 4:
                print(f"FAIL {url}: {e}", flush=True)
                return None
            time.sleep(3 * (attempt + 1))
    return None


def jobs_for(sym: str):
    for m in months("2023-12", "2026-08"):
        yield f"{VISION}/monthly/klines/{sym}/1m/{sym}-1m-{m}.zip", RAW / sym / f"{sym}-1m-{m}.csv"
    for d in pd.date_range("2026-09-01", "2026-09-23"):
        day = d.strftime("%Y-%m-%d")
        yield f"{VISION}/daily/klines/{sym}/1m/{sym}-1m-{day}.zip", RAW / sym / f"{sym}-1m-{day}.csv"


def main() -> None:
    syms = [p.replace("/", "") for p in PAIRS]
    jobs = [j for s in syms for j in jobs_for(s)]
    print(f"{len(syms)} pairs, {len(jobs)} archives", flush=True)
    done = 0
    with ThreadPoolExecutor(16) as ex:
        for _ in ex.map(lambda j: fetch(*j), jobs):
            done += 1
            if done % 1000 == 0:
                print(f"  {done}/{len(jobs)}", flush=True)
    for pair, sym in zip(PAIRS, syms):
        files = sorted((RAW / sym).glob("*.csv"))
        if not files:
            print(f"no 1m data for {pair}", flush=True)
            continue
        df = pd.concat([read_csv(p) for p in files])[["date", "open", "high", "low", "close", "volume"]]
        out = OUT / f"{pair.replace('/', '_')}-1m.feather"
        if out.exists():
            df = pd.concat([df, pd.read_feather(out)])
        df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
        df.to_feather(out)
    print("converted", flush=True)


if __name__ == "__main__":
    main()
