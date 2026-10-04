"""Report the completed clean X8 comparison; reject incomplete/misaccounted data."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "review_20260927"))
from compare_c import metrics
from mtm import equity_curve
from freqtrade.data.btanalysis import load_backtest_data, load_backtest_stats


def main():
    sources = sorted((HERE.parents[1] / "backtest_results").glob("review_loop_r2_clean_2024-*.zip"))
    if len(sources) != 1:
        raise ValueError(f"Expected exactly one complete clean archive, got{len(sources)}")
    path = sources[0]
    strategies = load_backtest_stats(path)["strategy"]
    expected = {"NostalgiaForInfinityX8PF", "NostalgiaForInfinityX8CostCap"}
    if set(strategies) != expected:
        raise ValueError("Both matched strategies must be exported before assessment")
    output = {"assessed_utc": datetime.now(timezone.utc).isoformat(), "source": str(path),
              "archive_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "results": {}}
    for name, stat in strategies.items():
        trades = load_backtest_data(path, strategy=name)
        if trades.empty:
            raise ValueError("Empty trade export needs a separate zero-exposure review")
        days = pd.date_range(pd.to_datetime(stat["backtest_start"], utc=True).floor("D"),
                             pd.to_datetime(stat["backtest_end"], utc=True).floor("D"))
        equity, cash, holdings = equity_curve(trades, stat["starting_balance"], days)
        gap = float(equity.iloc[-1] - stat["starting_balance"] - trades.profit_abs.sum())
        if abs(gap) >= .01:
            raise ValueError(f"Final balance does not reconcile for {name}: {gap}USDT")
        if cash.min() < -.01 or equity.min() <= 0:
            raise ValueError("Negative cash/equity cannot be called a spot result")
        result = metrics(equity / stat["starting_balance"])
        dd = equity / equity.cummax().clip(lower=stat["starting_balance"]) - 1
        worst = dd.idxmin()
        largest_at_worst = holdings.loc[worst].sort_values(ascending=False).head(5)
        result.update(trades=len(trades), closed_trade_DD_pct=stat["max_drawdown_account"] * 100,
                      accounting_gap_USDT=gap, minimum_cash_USDT=float(cash.min()),
                      max_daily_exposure_pct=float((holdings.sum(axis=1) / equity).max() * 100),
                      max_single_name_marked_weight_pct=float(holdings.div(equity, axis=0).max().max() * 100),
                      worst_marked_day=str(worst),
                      largest_holdings_at_worst_pct={pair: float(value / equity[worst] * 100)
                                                   for pair, value in largest_at_worst.items()},
                      profit_without_best3_trades_USDT=float(trades.profit_abs.sum() - trades.profit_abs.nlargest(3).sum()),
                      by_entry_tag=trades.groupby("enter_tag", dropna=False).profit_abs.agg(["count", "sum"]).reset_index().to_dict("records"),
                      actual_start=str(days[0]), actual_end=str(days[-1]),
                      default_gate=bool(result["CAGR_pct"] > 12 and result["maxDD_pct"] >= -5))
        output["results"][name] = result
        pd.DataFrame({"equity": equity, "cash": cash, "holdings": holdings.sum(axis=1)}).to_csv(HERE / f"r2_{name}_equity.csv")
        holdings.to_csv(HERE / f"r2_{name}_holdings.csv")
        print(name, {key: round(result[key], 5) for key in ("CAGR_pct", "maxDD_pct", "accounting_gap_USDT",
                                                         "max_daily_exposure_pct", "max_single_name_marked_weight_pct")},
              "gate", result["default_gate"], flush=True)
    output["candidate_first_screen_passed"] = output["results"]["NostalgiaForInfinityX8CostCap"]["default_gate"]
    output["next_step"] = ("2021 matched precision-limited screen, then finer-data verification before promotion"
                           if output["candidate_first_screen_passed"] else "Reject this5% cost-cap version; no cap sweep or additional-year tuning")
    (HERE / "r2_results.json").write_text(json.dumps(output, indent=2) + "\n")


if __name__ == "__main__":
    main()
