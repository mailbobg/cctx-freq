"""Replay frozen crowding-veto drawdown attribution without new price inputs."""
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent


def main():
    result = {}
    for label in ("early_reused", "recent_reused"):
        curves = {}
        block = {}
        for name in ("Cov10_baseline", "Cov10_crowding_veto"):
            curve = pd.read_csv(HERE / f"r8_{label}_{name}_cost0.0012_daily.csv", index_col="date", parse_dates=True)
            equity = curve.equity
            peak_value = equity.cummax().clip(lower=1.)
            drawdown = equity / peak_value - 1.
            trough = drawdown.idxmin()
            peak = equity.loc[:trough].idxmax()
            annual, previous = {}, 1.
            for year, group in equity.groupby(equity.index.year):
                annual[str(year)] = float((group.iloc[-1] / previous - 1) * 100)
                previous = group.iloc[-1]
            block[name] = {"peak": str(peak), "trough": str(trough), "DD_pct": float(drawdown.loc[trough] * 100),
                           "equity_at_peak": float(equity.loc[peak]), "equity_at_trough": float(equity.loc[trough]),
                           "annual_returns": annual}
            curves[name] = equity
        interval = block["Cov10_baseline"]
        ratio = (curves["Cov10_crowding_veto"] / curves["Cov10_baseline"]).loc[interval["peak"]:interval["trough"]]
        block["worst_baseline_drawdown_candidate_base_equity_ratio_min_max"] = [float(ratio.min()), float(ratio.max())]
        block["reason_for_attribution"] = "An effectively constant relative equity ratio during the worst drawdown means the earlier veto changed capital level, not that loss path."
        result[label] = block
    old = json.loads((HERE / "r8_drawdown_attribution.json").read_text())
    # CSV precision may round the last digit; the economic attribution must reproduce.
    for label in result:
        for name in ("Cov10_baseline", "Cov10_crowding_veto"):
            assert result[label][name]["peak"] == old[label][name]["peak"]
            assert result[label][name]["trough"] == old[label][name]["trough"]
            assert abs(result[label][name]["DD_pct"] - old[label][name]["DD_pct"]) < 1e-10
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
