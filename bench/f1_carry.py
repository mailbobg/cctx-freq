"""F1 backtest: cross-sectional funding carry (spec: specs/2026-09-29-f1-funding-carry-cross-section.md).

Daily resolution. Every Monday: universe top-40 by 30-day quote volume, signal = mean funding per settlement over the
previous N days; short the k highest, long the k lowest, 50 % of equity per side. Close-to-close price P&L plus funding.

Usage:
  python user_data/bench/f1_carry.py          # main spec, full suite
  python user_data/bench/f1_carry.py invvol   # variant (a): inverse-volatility weights within each side
"""

import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from p1_fetch import eligible, non_crypto  # noqa: E402

OUT = Path("user_data/bench/events")
START, END = pd.Timestamp("2021-02-01", tz="UTC"), pd.Timestamp("2026-08-31", tz="UTC")
BTC = "BTCUSDT"


@dataclass(frozen=True)
class Params:
    top_n: int = 40
    k: int = 8
    lookback: int = 7
    side_gross: float = 0.5
    min_hist: int = 60
    fee: float = 0.0005
    slip: float = 0.0005
    invvol: bool = False
    start_equity: float = 10_000.0


def load():
    d = pd.read_parquet("user_data/data_p1/daily.parquet")
    drop = non_crypto()
    d = d[d.symbol.map(eligible) & ~d.symbol.isin(drop)]
    close = d.pivot(index="date", columns="symbol", values="close").sort_index()
    qv = d.pivot(index="date", columns="symbol", values="quote_volume").sort_index().reindex(close.index)
    f = pd.read_parquet("user_data/data_f1/funding.parquet")
    f = f[f.symbol.isin(close.columns)]
    f["day"] = f["time"].dt.floor("D")
    fday = f.pivot_table(index="day", columns="symbol", values="rate", aggfunc="sum").reindex(close.index).fillna(0.0)
    return close, qv, f, fday


def select(close, qv, fund, monday, p: Params):
    """Universe and signal from data strictly before `monday` (look-ahead safe: every input is sliced here)."""
    c = close[close.index < monday]
    q = qv[qv.index < monday]
    first = c.apply(pd.Series.first_valid_index)
    hist_ok = first[first <= monday - pd.Timedelta(days=p.min_hist)].index
    last = c.iloc[-1]
    live = last[last.notna()].index
    vol30 = q[q.index >= monday - pd.Timedelta(days=30)].sum()
    cand = vol30[vol30.index.isin(hist_ok) & vol30.index.isin(live)].nlargest(p.top_n).index
    fw = fund[(fund.time < monday) & (fund.time >= monday - pd.Timedelta(days=p.lookback)) & fund.symbol.isin(cand)]
    sig = fw.groupby("symbol")["rate"].mean().dropna()
    if len(sig) < 2 * p.k:
        return None
    sig = sig.sort_values()
    longs, shorts = list(sig.index[:p.k]), list(sig.index[-p.k:])
    w = {}
    for side, names in ((1, longs), (-1, shorts)):
        if p.invvol:
            r = np.log(c[names].iloc[-61:]).diff().iloc[1:]
            iv = 1 / r.std().replace(0, np.nan)
            iv = iv.fillna(iv.mean())
            ws = iv / iv.sum()
        else:
            ws = pd.Series(1 / len(names), index=names)
        for s in names:
            w[s] = side * p.side_gross * ws[s]
    return {"weights": w, "signal": sig, "universe": list(cand)}


def run(close, qv, fund, fday, p: Params):
    days = close.index[(close.index >= START) & (close.index <= END)]
    mondays = [d for d in days if d.weekday() == 0]
    C = close
    units = {}                        # symbol -> units (signed)
    entry = {}                        # symbol -> entry price for liquidation (short)
    eq = p.start_equity
    rows, attrib = [], {}
    price_pnl = fund_pnl = cost_tot = 0.0
    side_pnl = {1: 0.0, -1: 0.0}
    liqs = 0
    turnover = 0.0
    prev_day = None
    sels = {}
    for d in days:
        if d in mondays and prev_day is not None:   # rebalance at Monday 00:00 = Sunday close, before Monday's P&L
            sel = select(close, qv, fund, d, p)
            sels[d] = sel
            target = {}
            if sel:
                for s, w in sel["weights"].items():
                    px = C.at[prev_day, s] if prev_day is not None else np.nan
                    if np.isfinite(px) and px > 0:
                        target[s] = w * eq / px
            for s in set(units) | set(target):
                px = C.at[prev_day, s]
                du = target.get(s, 0.0) - units.get(s, 0.0)
                if abs(du) < 1e-12:
                    continue
                cost = abs(du) * px * (p.fee + p.slip)
                turnover += abs(du) * px
                eq -= cost
                cost_tot += cost
                attrib[s] = attrib.get(s, 0) - cost
                new = target.get(s, 0.0)
                if new < 0:
                    old = units.get(s, 0.0)
                    if old < 0 and new <= old:
                        entry[s] = (entry[s] * old + px * (new - old)) / new
                    elif old >= 0:
                        entry[s] = px
                else:
                    entry.pop(s, None)
                if new == 0:
                    units.pop(s, None)
                else:
                    units[s] = new
        if prev_day is not None:
            # P&L of the day on positions held from prev close
            for s in list(units):
                u = units[s]
                p0, p1 = C.at[prev_day, s], C.at[d, s]
                if np.isnan(p1):          # data ended: close at last close
                    cost = abs(u) * p0 * (p.fee + p.slip)
                    eq -= cost
                    cost_tot += cost
                    attrib[s] = attrib.get(s, 0) - cost
                    units.pop(s)
                    entry.pop(s, None)
                    continue
                if u < 0 and p1 >= 2 * entry[s]:
                    p1 = 2 * entry[s]
                    liqs += 1
                pp = u * (p1 - p0)
                fp = -u * fday.at[d, s] * p0
                eq += pp + fp
                price_pnl += pp
                fund_pnl += fp
                side_pnl[1 if u > 0 else -1] += pp + fp
                attrib[s] = attrib.get(s, 0) + pp + fp
                if u < 0 and C.at[d, s] >= 2 * entry[s]:
                    units.pop(s)
                    entry.pop(s)
        rows.append((d, eq))
        prev_day = d
    equity = pd.Series(dict(rows))
    return {"equity": equity, "price": price_pnl, "funding": fund_pnl, "costs": cost_tot, "long": side_pnl[1],
            "short": side_pnl[-1], "liqs": liqs, "attrib": pd.Series(attrib), "turnover": turnover, "sels": sels}


def seg(eq: pd.Series, a, b, base):
    s = eq[(eq.index >= a) & (eq.index < b)]
    s2 = pd.concat([pd.Series([base], index=[s.index[0] - pd.Timedelta(days=1)]), s])
    return (s.iloc[-1] / base - 1) * 100, (s2 / s2.cummax() - 1).min() * 100


def monthly_ci(eq, n=5000):
    m = eq.resample("ME").last().pct_change().dropna() * 100
    rng = np.random.default_rng(0)
    dr = rng.choice(m.to_numpy(), size=(n, len(m)), replace=True).mean(axis=1)
    return float(m.mean()), *map(float, np.percentile(dr, [2.5, 97.5]))


def report(res, btc, p: Params, verbose=True):
    eq = res["equity"]
    out = {}
    segs = {"full": (START, END + pd.Timedelta(days=1))}
    segs.update({str(y): (pd.Timestamp(f"{y}-01-01", tz="UTC"), pd.Timestamp(f"{y + 1}-01-01", tz="UTC")) for y in range(2021, 2027)})
    segs.update({"2021-2023": (START, pd.Timestamp("2024-01-01", tz="UTC")),
                 "2024-2026": (pd.Timestamp("2024-01-01", tz="UTC"), END + pd.Timedelta(days=1))})
    for k, (a, b) in segs.items():
        before = eq[eq.index < a]
        base = before.iloc[-1] if len(before) else p.start_equity
        r, dd = seg(eq, a, b, base)
        out[k] = {"ret": r, "dd": dd}
    dr = eq.pct_change().dropna()
    out["btc_corr"] = float(dr.corr(btc.reindex(dr.index)))
    out["ci"] = monthly_ci(eq)
    if verbose:
        for k in list(segs):
            print(f"{k:10s} ret {out[k]['ret']:+7.1f}%  MTM maxDD {out[k]['dd']:6.1f}%", flush=True)
        a = res["attrib"].sort_values()
        print(f"  btc_corr {out['btc_corr']:+.2f}  monthly mean {out['ci'][0]:+.2f}% CI [{out['ci'][1]:+.2f}, {out['ci'][2]:+.2f}]")
        print(f"  P&L split: price {res['price']:+.0f}  funding {res['funding']:+.0f}  costs {-res['costs']:+.0f}  |  "
              f"long side {res['long']:+.0f}  short side {res['short']:+.0f}  liquidations {res['liqs']}  "
              f"turnover {res['turnover'] / p.start_equity:.0f}× start equity")
        print(f"  without top-3 coins: {a.sum() - a.nlargest(3).sum():+.0f}   best {a.tail(3).round(0).to_dict()}  worst {a.head(3).round(0).to_dict()}")
    out["wo_top3"] = float(res["attrib"].sum() - res["attrib"].nlargest(3).sum())
    return out


def main():
    variant = "invvol" in sys.argv[1:]
    tag = "_invvol" if variant else ""
    close, qv, fund, fday = load()
    btc = close[BTC].pct_change()
    p = Params(invvol=variant)
    print(f"== F1 funding carry ({'variant (a) inverse-vol weights' if variant else 'main spec'})", flush=True)
    res = run(close, qv, fund, fday, p)
    results = {"main": report(res, btc, p)}
    OUT.mkdir(parents=True, exist_ok=True)
    res["equity"].to_csv(OUT / f"f1_equity{tag}.csv")
    res["attrib"].to_csv(OUT / f"f1_attrib{tag}.csv")
    print("== fee ×2", flush=True)
    r2 = report(run(close, qv, fund, fday, replace(p, fee=2 * p.fee)), btc, p, verbose=False)
    results["fee2"] = r2["full"]
    print(f"fee2 ret {r2['full']['ret']:+.1f}%  DD {r2['full']['dd']:.1f}%", flush=True)
    print("== grid: names per side × lookback days", flush=True)
    grid = {}
    for k in (5, 8, 10):
        row = []
        for lb in (3, 7, 14):
            g = report(run(close, qv, fund, fday, replace(p, k=k, lookback=lb)), btc, p, verbose=False)
            grid[f"k{k}_lb{lb}"] = g["full"]["ret"]
            row.append(f"k{k} lb{lb}d: {g['full']['ret']:+7.1f}% (DD {g['full']['dd']:.0f}%)")
        print("  " + " | ".join(row), flush=True)
    results["grid"] = grid
    print("== look-ahead self-check", flush=True)
    rng = np.random.default_rng(1)
    mondays = list(res["sels"])
    same = 0
    for m in rng.choice(len(mondays), 50, replace=False):
        m = mondays[m]
        cut_close, cut_qv = close[close.index < m], qv[qv.index < m]
        cut_f = fund[fund.time < m]
        a, b = res["sels"][m], select(cut_close, cut_qv, cut_f, m, p)
        same += (a is None and b is None) or (a is not None and b is not None and a["weights"] == b["weights"])
    results["lookahead_same"] = same
    print(f"  {same}/50 truncated selections identical", flush=True)
    (OUT / f"f1_results{tag}.json").write_text(json.dumps(results, indent=1, default=str))


if __name__ == "__main__":
    main()
