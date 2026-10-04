"""Reconcile real Freqtrade orders with the frozen R1 research weights."""
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

from r1_r2b_risk import DATA, HERE, RiskHoldR2b, metrics, risk_weights, signals, simulate

sys.path.insert(0, str(HERE.parent))
import mtm
from freqtrade.data.btanalysis import load_backtest_data, load_backtest_stats


def main():
    mtm.DATADIR = HERE / "r1_data"
    px = pd.read_parquet(DATA).loc[:"2026-09-21"]
    # Early Freqtrade's final Jan1 candle lies within this common data snapshot.
    weights = {"RiskHoldR2b": signals(px, RiskHoldR2b)}
    weights["RiskHoldR2bCov10"] = risk_weights(px, weights["RiskHoldR2b"])
    output = {}
    for label, cost in (("early", .0012), ("recent", .0012), ("early_cost2", .0024), ("recent_cost2", .0024)):
        sources = sorted((HERE.parents[1] / "backtest_results").glob(f"review_loop_r1_freq_{label}-*.zip"))
        if len(sources) != 1:
            raise ValueError(f"Expected one frozen result for {label}, got{len(sources)}")
        path = sources[0]
        for name, stat in load_backtest_stats(path)["strategy"].items():
            trades = load_backtest_data(path, strategy=name)
            if trades.empty:
                raise ValueError(f"No trades for {label}/{name}")
            days = pd.date_range(pd.Timestamp(stat["backtest_start"], tz="UTC"),
                                 pd.Timestamp(stat["backtest_end"], tz="UTC"))
            equity, cash, holdings = mtm.equity_curve(trades, stat["starting_balance"], days)
            final_gap = float(equity.iloc[-1] - stat["starting_balance"] - trades.profit_abs.sum())
            assert abs(final_gap) < .01
            result = metrics(equity / stat["starting_balance"])
            start, end = str(days[0].date()), str(days[-1].date())
            reference = simulate(px, weights[name], start, end, cost, terminal_liquidation=True)
            if reference.empty:
                raise ValueError(f"Empty reference window {start}–{end}")
            # Normalize pandas' millisecond/nanosecond index units explicitly.
            reference.index = pd.DatetimeIndex(reference.index).as_unit("ns")
            equity.index = pd.DatetimeIndex(equity.index).as_unit("ns")
            pd.testing.assert_index_equal(reference.index, equity.index, check_names=False)
            curve_gap = pd.Series(equity.to_numpy() / stat["starting_balance"] - reference.equity.to_numpy(),
                                  index=equity.index)
            result.update(trades=len(trades), closed_trade_DD_pct=stat["max_drawdown_account"] * 100,
                          accounting_gap_USDT=final_gap, min_cash_USDT=float(cash.min()),
                          reference_final_gap_pct=float(curve_gap.iloc[-1] * 100),
                          max_reference_curve_gap_pct=float(curve_gap.abs().max() * 100),
                          all_in_side_cost=cost,
                          actual_start=start, actual_end=end, source=str(path),
                          source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                          default_gate=bool(result["CAGR_pct"] > 12 and result["maxDD_pct"] >= -5))
            key = f"{label}_{name}"
            output[key] = result
            pd.DataFrame({"equity": equity, "cash": cash, "holdings": holdings.sum(axis=1),
                          "reference_equity": reference.equity * stat["starting_balance"],
                          "difference_USDT": curve_gap * stat["starting_balance"]}).to_csv(HERE / f"r1_freq_{key}.csv")
            print(key, {k: round(result[k], 5) for k in ("CAGR_pct", "maxDD_pct", "accounting_gap_USDT",
                                                        "reference_final_gap_pct", "max_reference_curve_gap_pct")}, flush=True)
    (HERE / "r1_freq_results.json").write_text(json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    main()
