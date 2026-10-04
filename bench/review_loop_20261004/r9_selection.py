"""Single selected major, with the unchanged trend signal and covariance budget."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r1_r2b_risk import signals, risk_weights, RiskHoldR2b
from r8_crowding import close_at_final_close, summarize

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ASSETS = ["BTC", "ETH"]
WINDOWS = [("early_reused", "2021-02-01", "2023-12-31"),
           ("recent_reused", "2024-01-01", "2026-08-31")]


def select_raw(px, raw):
    if not raw.index.equals(px.index):
        raise ValueError("Price and raw-signal calendars must agree")
    close = px[["BTC_close", "ETH_close"]].set_axis(ASSETS, axis=1)
    momentum = close / close.shift(30) - 1
    known = np.isfinite(momentum.to_numpy()).all(axis=1)
    winner = pd.Series(np.where(momentum.BTC >= momentum.ETH, "BTC", "ETH"), index=px.index)
    winner.loc[~known] = None
    selected = pd.DataFrame(0., index=raw.index, columns=ASSETS)
    for asset in ASSETS:
        active = winner.eq(asset) & momentum[asset].gt(0) & raw[asset].gt(0) & np.isfinite(raw[asset])
        selected.loc[active, asset] = raw.loc[active, asset]
    audit = momentum.rename(columns={"BTC": "BTC_momentum", "ETH": "ETH_momentum"})
    audit["winner"] = winner
    audit["active_raw_target"] = selected.sum(axis=1)
    return selected, audit


def selected_weights(px):
    # Keep genuine gaps as daily unknown observations for the covariance budget.
    px = px.sort_index().asfreq("D")
    selected, audit = select_raw(px, signals(px, RiskHoldR2b))
    return risk_weights(px, selected), audit


def load_prices():
    previous = json.loads((HERE / "r8_input_manifest.json").read_text())["source_inputs_sha256"]
    paths = ["user_data/bench/events/c_daily.parquet", *[
        f"user_data/bench/review_loop_20261004/r7_data/{asset}USDT_spot.parquet" for asset in ASSETS]]
    hashes = {}
    for path in paths:
        sha = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        if sha != previous[path]:
            raise ValueError("Unchanged frozen spot inputs no longer match")
        hashes[path] = sha
    warm = pd.read_parquet(ROOT / paths[0]).loc["2020-01-01":"2020-12-31"]
    spot = {asset: pd.read_parquet(HERE / "r7_data" / f"{asset}USDT_spot.parquet") for asset in ASSETS}
    recent = pd.DataFrame({f"{asset}_{col}": spot[asset][col] for asset in ASSETS for col in ("open", "close")})
    px = pd.concat([warm, recent]).sort_index().asfreq("D")
    if not px.index.is_unique or not np.isfinite(px.to_numpy()).all() or not px.gt(0).all().all():
        raise ValueError("Unreviewed complete spot execution calendar")
    return px, spot, hashes


def single_asset_envelopes(curve, target, spot):
    """Held single-asset daily traded-price extremes after Monday OPEN fills.

    Earlier days' highs are known to precede today's low. Same-day high/low
    order is unknown. Pre-order equity at Monday OPEN is not captured here;
    this is a necessary risk diagnostic, not exact execution path proof.
    """
    curve = curve.copy()
    low, high, held = [], [], None
    for day, row in curve.iterrows():
        if day.weekday() == 0:
            active = target.loc[day - pd.Timedelta(days=1)].gt(0)
            if active.sum() > 1:
                raise ValueError("The exact single-asset envelope cannot hold two assets")
            held = active.index[active][0] if active.any() else None
        invested = row.pre_close_equity * row.pre_close_exposure
        if invested > 1e-12:
            if held is None:
                raise AssertionError("Held quantity without its causal weekly label")
            frame = spot[held]
            low.append(row.pre_close_cash + invested * frame.loc[day, "low"] / frame.loc[day, "close"])
            high.append(row.pre_close_cash + invested * frame.loc[day, "high"] / frame.loc[day, "close"])
        else:
            low.append(row.pre_close_cash)
            high.append(row.pre_close_cash)
    curve["held_low_equity"] = np.minimum(low, curve.equity)
    curve["held_high_equity"] = np.maximum(high, curve.equity)
    return curve


def envelope_metrics(curve):
    prior_peak = curve.held_high_equity.cummax().shift(1).fillna(1.).clip(lower=1.)
    same_peak = np.maximum(prior_peak, curve.held_high_equity)
    return {"observed_held_low_vs_prior_high_peak_DD_pct": float((curve.held_low_equity / prior_peak - 1).min() * 100),
            "unknown_same_day_order_envelope_DD_pct": float((curve.held_low_equity / same_peak - 1).min() * 100),
            "risk_limitations": "Single held asset lows/highs after modeled Monday OPEN fills; same-day ordering and before-order gap/fee state unknown. Trade-price extremes are not real quote/fill proof."}


def prepare():
    px, spot, hashes = load_prices()
    baseline = risk_weights(px, signals(px, RiskHoldR2b))
    selected, audit = selected_weights(px)
    for cutoff in ("2021-12-31", "2024-12-31"):
        part, _ = selected_weights(px.loc[:cutoff])
        pd.testing.assert_frame_equal(part, selected.loc[:cutoff])
    full = close_at_final_close(px, selected, "2024-01-01", "2024-07-05", .0012, terminal=False)
    part = close_at_final_close(px, selected, "2024-01-01", "2024-06-01", .0012, terminal=False)
    pd.testing.assert_frame_equal(part, full.loc[part.index])
    assert part.exposure.gt(0).any()
    gaps = {}
    for label, start, end in WINDOWS:
        for cost in (.0012, .0024):
            curve = close_at_final_close(px, baseline, start, end, cost)
            old = pd.read_csv(HERE / f"r8_{label}_Cov10_baseline_cost{cost}_daily.csv", index_col="date", parse_dates=True)
            if not old.index.as_unit("ns").equals(curve.index.as_unit("ns")):
                raise ValueError("Baseline calendar changed")
            columns = ["equity", "cash", "exposure", "cost", "turnover"]
            gap = float(np.abs(curve[columns].to_numpy() - old[columns].to_numpy()).max())
            if gap > 1e-12:
                raise ValueError("Matched control is not unchanged")
            gaps[f"{label}_cost{cost}"] = gap
    manifest = {"source_inputs_sha256": hashes, "baseline_max_abs_csv_serialization_gap": gaps,
                "full_prefix_weights_equal": True, "real_held_execution_prefix_equal": True}
    path = HERE / "r9_input_manifest.json"
    if path.exists() and json.loads(path.read_text()) != manifest:
        raise ValueError("Frozen R9 inputs/control checks changed")
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return px, spot, baseline, selected, audit, manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    px, spot, baseline, selected, audit, manifest = prepare()
    if args.verify_only:
        print("Stable raw selection, weights/execution prefixes and every matched baseline curve PASS")
        return
    results, frequency = {}, {}
    audit.to_csv(HERE / "r9_selection_audit.csv")
    for label, start, end in WINDOWS:
        # The last Sunday may decide beyond an endpoint; only decisions filled inside the window count.
        decision_weights = selected.loc[(selected.index + pd.Timedelta(days=1) >= pd.Timestamp(start, tz="UTC")) &
                                        (selected.index + pd.Timedelta(days=1) <= pd.Timestamp(end, tz="UTC")) &
                                        (selected.index.weekday == 6)]
        labels = pd.Series("cash", index=decision_weights.index)
        for asset in ASSETS:
            labels.loc[decision_weights[asset].gt(0)] = asset
        frequency[label] = {"decision_weeks": len(labels), "held_asset_weeks": labels.value_counts().to_dict(),
                            "non_cash_asset_switches": int((labels.ne(labels.shift()) & labels.ne("cash") & labels.shift().isin(ASSETS)).sum())}
        for cost in (.0012, .0024):
            for name, target in (("Cov10_baseline", baseline), ("selected_major", selected)):
                curve = close_at_final_close(px, target, start, end, cost)
                result = summarize(curve)
                if name == "selected_major":
                    curve = single_asset_envelopes(curve, target, spot)
                    result.update(envelope_metrics(curve))
                key = f"{label}_{name}_cost{cost}"
                results[key] = result
                curve.to_csv(HERE / f"r9_{key}_daily.csv")
                print(key, {k: round(result[k], 4) for k in ("CAGR_pct", "daily_MTM_DD_pct", "fees_USDT_10000_start")}, "gate", result["necessary_screen_gate"], flush=True)
    sources = {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
               for name in ("R9_PROTOCOL.md", "r9_selection.py", "test_r9_selection.py", "r8_crowding.py", "r1_r2b_risk.py")}
    sources["../../strategies/portfolio_risk.py"] = hashlib.sha256((HERE / "../../strategies/portfolio_risk.py").read_bytes()).hexdigest()
    (HERE / "r9_results.json").write_text(json.dumps({"captured_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": sources, "input_manifest": manifest, "allocation_frequency": frequency, "results": results,
        "limitations": "Fixed BTC/ETH long-only selection, no short/funding/threshold/horizon sweep. Reused history, research OPEN fills with cost allowance, common cash/band ledger and final CLOSE fees; no actual Freqtrade adoption."}, indent=2) + "\n")


if __name__ == "__main__":
    main()
