"""One causal external-information veto on frozen Cov10 spot weights."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r1_r2b_risk import risk_weights, signals, simulate, RiskHoldR2b

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ASSETS = ["BTC", "ETH"]
WINDOWS = [("early_reused", "2021-02-01", "2023-12-31"),
           ("recent_reused", "2024-01-01", "2026-08-31")]


def overlay(base, funding):
    out, rows = base.copy(), []
    for sunday in base.index[base.index.weekday == 6]:
        monday = sunday + pd.Timedelta(days=1)
        begin = monday - pd.Timedelta(days=7)
        expected = pd.date_range(begin, monday, freq="8h", inclusive="left").as_unit("ns")
        for asset in ASSETS:
            data = funding[asset]
            selected = data.loc[(data.index >= begin) & (data.index < monday)].sort_index()
            slots = pd.DatetimeIndex(selected.slot).as_unit("ns")
            rates = selected.rate.to_numpy(float)
            complete = len(selected) == 21 and slots.equals(expected) and np.isfinite(rates).all()
            mean, veto = np.nan, False
            if complete:
                # API rates are decimal values: exact equality at the frozen
                # boundary must not become an accidental floating-point veto.
                total = sum((Decimal(str(float(rate))) for rate in rates), Decimal(0))
                mean = float(total / Decimal(21))
                veto = total > Decimal("0.0003") * 21
            old = float(base.loc[sunday, asset])
            if not complete or veto:
                out.loc[sunday, asset] = 0.
            rows.append((monday, asset, len(selected), bool(complete), mean, bool(veto),
                         old, float(out.loc[sunday, asset])))
    return out, pd.DataFrame(rows, columns=["decision", "asset", "event_count", "complete", "mean_rate",
                                           "veto", "baseline_weight", "target_weight"])


def close_at_final_close(px, weights, start, end, cost, terminal=True):
    curve = simulate(px, weights, start, end, cost, terminal_liquidation=False)
    if curve.empty or str(curve.index[-1].date()) != end:
        raise ValueError("Incomplete requested execution calendar")
    for column in ("equity", "cash", "exposure"):
        curve["pre_close_" + column] = curve[column]
    if terminal:
        date = curve.index[-1]
        sold = curve.loc[date, "equity"] * curve.loc[date, "exposure"]
        fee = sold * cost
        curve.loc[date, "cash"] += sold - fee
        curve.loc[date, "equity"] -= fee
        curve.loc[date, "exposure"] = 0.
        curve.loc[date, "cost"] += fee
        curve.loc[date, "turnover"] += sold
        if abs(curve.loc[date, "cash"] - curve.loc[date, "equity"]) > 1e-10:
            raise AssertionError("Final asset/cash closing ledger does not reconcile")
    return curve


def summarize(curve):
    equity = np.r_[1., curve.equity.to_numpy()]
    cagr = (equity[-1] ** (365 / len(curve)) - 1) * 100 if equity[-1] > 0 else -100.
    dd = (equity / np.maximum.accumulate(equity) - 1).min() * 100
    yearly, prior = {}, 1.
    for year, group in curve.groupby(curve.index.year):
        yearly[str(year)] = float((group.equity.iloc[-1] / prior - 1) * 100)
        prior = group.equity.iloc[-1]
    result = {"return_pct": float((equity[-1] - 1) * 100), "CAGR_pct": float(cagr),
              "daily_MTM_DD_pct": float(dd), "year_return_pct": yearly,
              "fees_USDT_10000_start": float(curve.cost.sum() * 10000),
              "turnover_times_starting_equity": float(curve.turnover.sum()),
              "min_cash": float(curve.cash.min()), "max_exposure_pct": float(curve.exposure.max() * 100),
              "terminal_cash_equity_gap": float(curve.cash.iloc[-1] - curve.equity.iloc[-1]),
              "necessary_screen_gate": bool(cagr > 12 and dd >= -5 and equity[-1] > 1)}
    if "adverse_equity_envelope" in curve:
        prior_peak = curve.pre_close_equity.cummax().shift(1).fillna(1.).clip(lower=1.)
        result["daily_low_envelope_vs_prior_close_peak_pct"] = float((curve.adverse_equity_envelope / prior_peak - 1).min() * 100)
        result["envelope_limitation"] = "All invested value assigned the worst asset's daily low/close ratio; deliberately loose price-only envelope, not simultaneous prices or exact intraday drawdown."
    return result


def load_inputs():
    sources = {}
    for folder in ("r6_data", "r7_data"):
        manifest = json.loads((HERE / folder / "manifest.json").read_text())
        required = [f"{asset}USDT_{'funding' if folder == 'r6_data' else 'spot'}.parquet" for asset in ASSETS]
        for filename in required:
            path = HERE / folder / filename
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            if sha != manifest["output_sha256"][filename]:
                raise ValueError("Prior validated price/funding source hash changed")
            sources[str(path.relative_to(ROOT))] = sha
    warm_path = ROOT / "user_data/bench/events/c_daily.parquet"
    sha = hashlib.sha256(warm_path.read_bytes()).hexdigest()
    if sha != json.loads((HERE / "r1_results.json").read_text())["data_sha256"]:
        raise ValueError("Prior frozen spot warmup changed")
    sources[str(warm_path.relative_to(ROOT))] = sha
    old = pd.read_parquet(warm_path).loc["2020-01-01":"2020-12-31"]
    spot = {asset: pd.read_parquet(HERE / "r7_data" / f"{asset}USDT_spot.parquet") for asset in ASSETS}
    recent = pd.DataFrame({f"{asset}_{col}": spot[asset][col] for asset in ASSETS for col in ("open", "close")})
    px = pd.concat([old, recent]).sort_index().asfreq("D")
    if not px.index.is_unique or not np.isfinite(px.to_numpy()).all() or not px.gt(0).all().all():
        raise ValueError("Unreviewed spot price calendar")
    funding = {asset: pd.read_parquet(HERE / "r6_data" / f"{asset}USDT_funding.parquet") for asset in ASSETS}
    low_ratio = pd.DataFrame({asset: spot[asset].low / spot[asset].close for asset in ASSETS}).min(axis=1)
    return px, funding, low_ratio, sources


def prepare():
    px, funding, low_ratio, sources = load_inputs()
    base = risk_weights(px, signals(px, RiskHoldR2b))
    veto, audit = overlay(base, funding)
    for cutoff in ("2021-12-31", "2024-12-31"):
        shorter = px.loc[:cutoff]
        part_base = risk_weights(shorter, signals(shorter, RiskHoldR2b))
        part_fund = {asset: frame.loc[:cutoff] for asset, frame in funding.items()}
        part_veto, _ = overlay(part_base, part_fund)
        pd.testing.assert_frame_equal(part_base, base.loc[:cutoff])
        pd.testing.assert_frame_equal(part_veto, veto.loc[:cutoff])
    # Real positions and a non-terminal cutoff ensure execution is causal too.
    full = close_at_final_close(px, veto, "2024-01-01", "2024-07-05", .0012, terminal=False)
    part = close_at_final_close(px, veto, "2024-01-01", "2024-06-01", .0012, terminal=False)
    pd.testing.assert_frame_equal(part, full.loc[part.index])
    assert part.exposure.gt(0).any()
    captured = {"source_inputs_sha256": sources, "price_rows": len(px),
                "warmup": "Frozen prior cached2020 spot opens/closes;2021+ independently checksummed R7 archives",
                "causal_price_funding_weight_prefixes": True, "causal_real_held_execution_prefix": True}
    manifest_path = HERE / "r8_input_manifest.json"
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != captured:
        raise ValueError("Frozen R8 input/verification manifest changed")
    manifest_path.write_text(json.dumps(captured, indent=2) + "\n")
    return px, base, veto, audit, low_ratio, captured


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    px, base, veto, audit, low_ratio, manifest = prepare()
    if args.verify_only:
        print("Frozen price/funding inputs, full/shortened weights and real-position execution causality PASS")
        return
    results, usage = {}, {}
    audit.to_csv(HERE / "r8_decision_audit.csv", index=False)
    for label, start, end in WINDOWS:
        decisions = audit.loc[(audit.decision >= pd.Timestamp(start, tz="UTC")) &
                              (audit.decision <= pd.Timestamp(end, tz="UTC"))]
        if not decisions.complete.all():
            raise ValueError("Unknown funding inputs in an active experimental decision")
        usage[label] = {asset: {"decision_weeks": len(frame), "veto_weeks": int(frame.veto.sum()),
                                "veto_while_baseline_on": int((frame.veto & frame.baseline_weight.gt(0)).sum())}
                       for asset, frame in decisions.groupby("asset")}
        for cost in (.0012, .0024):
            for name, target in (("Cov10_baseline", base), ("Cov10_crowding_veto", veto)):
                curve = close_at_final_close(px, target, start, end, cost)
                held = curve.pre_close_equity * curve.pre_close_exposure
                curve["adverse_equity_envelope"] = curve.pre_close_cash + held * low_ratio.reindex(curve.index)
                key = f"{label}_{name}_cost{cost}"
                result = summarize(curve)
                results[key] = result
                curve.to_csv(HERE / f"r8_{key}_daily.csv")
                print(key, {k: round(result[k], 4) for k in ("CAGR_pct", "daily_MTM_DD_pct", "fees_USDT_10000_start")}, "gate", result["necessary_screen_gate"], flush=True)
    sources = {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
               for name in ("R8_PROTOCOL.md", "r8_crowding.py", "test_r8_crowding.py", "r1_r2b_risk.py")}
    sources["../../strategies/portfolio_risk.py"] = hashlib.sha256((HERE / "../../strategies/portfolio_risk.py").read_bytes()).hexdigest()
    (HERE / "r8_results.json").write_text(json.dumps({"captured_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": sources, "input_manifest": manifest, "decision_frequencies": usage, "results": results,
        "limitations": "Reused history; external funding signal is strictly pre-Monday, never charged to spot. Common research OPEN fills,5pp band, real cash constraint and final CLOSE fees. Price-low envelopes do not certify mark/intraday risk. No actual Freqtrade adoption."}, indent=2) + "\n")


if __name__ == "__main__":
    main()
