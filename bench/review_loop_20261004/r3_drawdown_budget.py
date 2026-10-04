"""R3 frozen protective overlay, with matched R1 and causal prefix controls."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from r1_r2b_risk import ASSETS, DATA, HERE, WINDOWS, RiskHoldR2b, metrics, risk_weights, signals, weekly_difference, simulate as r1


def simulate(px, weights, start, end, cost, overlay=True):
    cash, peak, units = 1., 1., np.zeros(2)
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
            diff = weekly_difference(current, weights.iloc[i-1].to_numpy(), value)
            desired += diff
        if overlay and value > 0:
            ceiling = min(value, 3 * max(value - .952 * peak, 0.))
            if desired.sum() > ceiling:
                desired *= ceiling / desired.sum()
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
        rows.append((day, equity, cash, (units @ closed)/equity, fees, turnover))
        peak = max(peak, equity)
    return pd.DataFrame(rows, columns=["date", "equity", "cash", "exposure", "cost", "turnover"]).set_index("date")


def main():
    px = pd.read_parquet(DATA).loc[:"2026-09-21"]
    weights = risk_weights(px, signals(px, RiskHoldR2b))
    disabled = simulate(px, weights, "2018-03-05", "2026-09-21", .0012, overlay=False)
    baseline = r1(px, weights, "2018-03-05", "2026-09-21", .0012)
    pd.testing.assert_frame_equal(disabled, baseline, atol=1e-12, rtol=1e-12)
    full = simulate(px, weights, "2018-03-05", "2026-09-21", .0012)
    part = px.loc[:"2022-12-31"]
    prefix = simulate(part, weights.loc[part.index], "2018-03-05", "2026-09-21", .0012)
    pd.testing.assert_frame_equal(prefix, full.loc[prefix.index].rename_axis("date"))
    print("disabled overlay exact R1 control and execution-prefix checks PASS", flush=True)
    output = {}
    for label, start, end in WINDOWS:
        for cost in (.0012, .0024):
            curve = simulate(px, weights, start, end, cost)
            result = metrics(curve.equity)
            result.update(min_cash=float(curve.cash.min()), max_exposure_pct=float(curve.exposure.max()*100),
                          cumulative_cost=float(curve.cost.sum()),
                          default_gate=bool(result["CAGR_pct"] >12 and result["maxDD_pct"] >= -5))
            key = f"{label}_cost{cost}"
            output[key] = result
            curve.to_csv(HERE / f"r3_{key}.csv")
            print(key, {k: round(result[k], 3) for k in ("CAGR_pct", "maxDD_pct", "sharpe", "max_exposure_pct")},
                  "gate", result["default_gate"], flush=True)
    (HERE / "r3_results.json").write_text(json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    main()
