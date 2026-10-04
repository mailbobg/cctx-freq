"""Frozen R1 experiment: actual bot signals, matched cash-constrained execution."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "user_data/strategies"))
sys.path.insert(0, str(ROOT / "user_data/bench/review_20260927"))
from RiskHoldC import RiskHoldC
from RiskHoldR2b import RiskHoldR2b
from portfolio_risk import cap_weights
from compare_c import metrics

ASSETS = ["BTC", "ETH"]
DATA = ROOT / "user_data/bench/events/c_daily.parquet"
WINDOWS = [("early_reused", "2018-03-05", "2020-12-31"),
           ("recent_reused", "2021-01-01", "2026-09-21")]


def signals(px, cls):
    result = {}
    strategy = cls({"stake_currency": "USDT"})
    for asset in ASSETS:
        frame = pd.DataFrame({"date": px.index, "close": px[f"{asset}_close"].to_numpy()})
        frame = strategy.populate_indicators(frame, {"pair": f"{asset}/USDT"})
        result[asset] = frame.set_index("date")["weight"]
    return pd.DataFrame(result)


def risk_weights(px, raw):
    closes = px[[f"{a}_close" for a in ASSETS]].set_axis(ASSETS, axis=1)
    covariance = np.log(closes).diff().rolling(30).cov() * 365
    result = raw.copy() * 0
    for day in px.index:
        matrix = covariance.loc[day].reindex(index=ASSETS, columns=ASSETS).to_numpy()
        result.loc[day] = cap_weights(raw.loc[day], matrix, vol_target=.10, gross_cap=1.)
    return result


def simulate(px, weights, start, end, cost):
    cash, units = 1., np.zeros(2)
    rows = []
    for i, day in enumerate(px.index):
        if not (start <= str(day.date()) <= end) or i < 200:
            continue
        opened = px.loc[day, [f"{a}_open" for a in ASSETS]].to_numpy(dtype=float)
        closed = px.loc[day, [f"{a}_close" for a in ASSETS]].to_numpy(dtype=float)
        fees, turnover = 0., 0.
        if day.weekday() == 0:
            value = cash + units @ opened
            diff = weights.iloc[i-1].to_numpy() * .99 * value - units * opened
            diff[np.abs(diff) <= .05 * value] = 0.
            for k in np.argsort(diff):
                amount = max(diff[k], -units[k] * opened[k])
                if amount > 0:
                    amount = min(amount, max(0., cash) / (1 + cost))
                units[k] += amount / opened[k]
                cash -= amount + abs(amount) * cost
                fees += abs(amount) * cost
                turnover += abs(amount)
            if cash < -1e-10 or units.min() < -1e-10:
                raise AssertionError("cash or units below zero")
        equity = cash + units @ closed
        rows.append((day, equity, cash, (units @ closed) / equity, fees, turnover))
    return pd.DataFrame(rows, columns=["date", "equity", "cash", "exposure", "cost", "turnover"]).set_index("date")


def verify(px, raw, risk):
    # The same bot indicator implementation is used above. Prefix tests check
    # covariance and execution, rather than merely comparing formulas.
    for length in (240, 365, 730, len(px)-1):
        part = px.iloc[:length]
        subset = risk_weights(part, signals(part, RiskHoldR2b))
        pd.testing.assert_frame_equal(subset, risk.iloc[:length])
    full = simulate(px, raw, "2018-03-05", "2026-09-21", .0012)
    cut = px.loc[:"2022-12-31"]
    prefix = simulate(cut, raw.loc[cut.index], "2018-03-05", "2026-09-21", .0012)
    pd.testing.assert_frame_equal(prefix, full.loc[prefix.index].rename_axis("date"))
    flat = px.iloc[:210].copy()
    flat.loc[:, :] = 100.
    all_in = pd.DataFrame(.5, index=flat.index, columns=ASSETS)
    curve = simulate(flat, all_in, "2018-03-05", "2026-09-21", .0012)
    assert curve.cash.min() >= -1e-10
    assert curve.cost.sum() > 0 and curve.equity.iloc[-1] < 1.
    print("causality, cash and initial-fee checks PASS", flush=True)


def main():
    px = pd.read_parquet(DATA).loc[:"2026-09-21"]
    raw = signals(px, RiskHoldR2b)
    risk = risk_weights(px, raw)
    weights = {"C": signals(px, RiskHoldC), "R2b": raw, "R2b_vol10": risk}
    verify(px, raw, risk)
    output = {"captured_utc": datetime.now(timezone.utc).isoformat(),
              "data_sha256": hashlib.sha256(DATA.read_bytes()).hexdigest(),
              "source_sha256": {name: hashlib.sha256((ROOT / "user_data/strategies" / name).read_bytes()).hexdigest()
                                for name in ("RiskHoldC.py", "RiskHoldR2b.py", "portfolio_risk.py")},
              "results": {}}
    for label, start, end in WINDOWS:
        for cost in (.0012, .0024):
            for name, w in weights.items():
                curve = simulate(px, w, start, end, cost)
                result = metrics(curve.equity)
                result.update(min_cash=float(curve.cash.min()), mean_exposure_pct=float(curve.exposure.mean()*100),
                              max_exposure_pct=float(curve.exposure.max()*100), cumulative_cost=float(curve.cost.sum()),
                              default_gate=bool(result["CAGR_pct"] > 12 and result["maxDD_pct"] >= -5))
                key = f"{label}_cost{cost}_{name}"
                output["results"][key] = result
                curve.to_csv(HERE / f"r1_{key}.csv")
                print(key, {k: round(result[k], 3) for k in ("CAGR_pct", "maxDD_pct", "sharpe", "mean_exposure_pct")},
                      "gate", result["default_gate"], flush=True)
    (HERE / "r1_results.json").write_text(json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    main()
