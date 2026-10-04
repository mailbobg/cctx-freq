"""Frozen R4: daily covariance reductions; unchanged weekly signals/additions."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r1_r2b_risk import (ASSETS, DATA, HERE, WINDOWS, RiskHoldR2b, cap_weights,
                         metrics, risk_weights, signals, weekly_difference,
                         simulate as baseline_simulate)


def covariance(px):
    closes = px[[f"{a}_close" for a in ASSETS]].set_axis(ASSETS, axis=1)
    return np.log(closes).diff().rolling(30).cov() * 365


def simulate(px, weights, cov, start, end, cost, overlay=True):
    cash, units = 1., np.zeros(2)
    rows = []
    for i, day in enumerate(px.index):
        if not (start <= str(day.date()) <= end) or i < 200:
            continue
        opened = px.loc[day, [f"{a}_open" for a in ASSETS]].to_numpy(dtype=float)
        closed = px.loc[day, [f"{a}_close" for a in ASSETS]].to_numpy(dtype=float)
        current = units * opened
        value = cash + current.sum()
        desired = current.copy()
        if day.weekday() == 0:
            desired += weekly_difference(current, weights.iloc[i - 1].to_numpy(), value)
        if overlay:
            matrix = cov.loc[px.index[i - 1]].reindex(index=ASSETS, columns=ASSETS).to_numpy()
            desired = cap_weights(desired / value, matrix, vol_target=.10, gross_cap=1.) * value
        diff = desired - current
        fees, turnover = 0., 0.
        for k in np.argsort(diff):
            amount = max(diff[k], -current[k])
            if amount > 0:
                amount = min(amount, max(0., cash) / (1 + cost))
            units[k] += amount / opened[k]
            cash -= amount + abs(amount) * cost
            fees += abs(amount) * cost
            turnover += abs(amount)
        assert cash >= -1e-10 and units.min() >= -1e-10
        equity = cash + units @ closed
        rows.append((day, equity, cash, (units @ closed) / equity, fees, turnover))
    return pd.DataFrame(rows, columns=["date", "equity", "cash", "exposure", "cost", "turnover"]).set_index("date")


def verify(px, weights, cov):
    disabled = simulate(px, weights, cov, "2018-03-05", "2026-09-21", .0012, overlay=False)
    baseline = baseline_simulate(px, weights, "2018-03-05", "2026-09-21", .0012)
    pd.testing.assert_frame_equal(disabled, baseline, atol=1e-12, rtol=1e-12)
    full = simulate(px, weights, cov, "2018-03-05", "2026-09-21", .0012)
    part = px.loc[:"2022-12-31"]
    w_prefix = risk_weights(part, signals(part, RiskHoldR2b))
    prefix = simulate(part, w_prefix, covariance(part), "2018-03-05", "2026-09-21", .0012)
    pd.testing.assert_frame_equal(prefix, full.loc[prefix.index].rename_axis("date"))
    # The overlay may trade on another weekday only by selling. The difference
    # between opening marked holdings and closing equity cannot test this,
    # so simulate records aggregate turnover, and verify the cash movement.
    cash_change = full.cash.diff()
    off_monday = full.index.weekday != 0
    assert (cash_change.loc[off_monday].dropna() >= -1e-10).all()
    print("disabled control, prefix causality and no off-Monday buys PASS", flush=True)


def main():
    px = pd.read_parquet(DATA).loc[:"2026-09-21"]
    weights = risk_weights(px, signals(px, RiskHoldR2b))
    cov = covariance(px)
    verify(px, weights, cov)
    output = {"captured_utc": datetime.now(timezone.utc).isoformat(),
              "data_sha256": hashlib.sha256(DATA.read_bytes()).hexdigest(),
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "results": {}}
    for label, start, end in WINDOWS:
        for cost in (.0012, .0024):
            curve = simulate(px, weights, cov, start, end, cost)
            result = metrics(curve.equity)
            result.update(min_cash=float(curve.cash.min()), mean_exposure_pct=float(curve.exposure.mean() * 100),
                          max_exposure_pct=float(curve.exposure.max() * 100), cumulative_cost=float(curve.cost.sum()),
                          default_gate=bool(result["CAGR_pct"] > 12 and result["maxDD_pct"] >= -5))
            key = f"{label}_cost{cost}"
            output["results"][key] = result
            curve.to_csv(HERE / f"r4_{key}.csv")
            print(key, {k: round(result[k], 3) for k in ("CAGR_pct", "maxDD_pct", "sharpe", "mean_exposure_pct")},
                  "gate", result["default_gate"], flush=True)
    (HERE / "r4_results.json").write_text(json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    main()
