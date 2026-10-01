"""U1 backtest: cross-sectional token-unlock pressure short, BTC-hedged, with a random-coin control
(spec: specs/2026-10-01-u1-unlock-pressure-cross-section.md).

Daily resolution. Every Monday: universe = top 100 perps by 30-day quote volume; score S = unlock value in the next
14 days / (14 × mean daily quote volume of the previous 30 days); short the top-k coins with S ≥ threshold (5 % of equity
each) against an equal BTC long. Control: same weeks and counts, random coins with no unlock within ±14 days.

Usage:
  python user_data/bench/u1_unlock.py            # main spec, full suite
  python user_data/bench/u1_unlock.py cats       # variant (a): insiders + privateSale only
"""

import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from f1_carry import load as load_f1, monthly_ci, seg  # noqa: E402

OUT = Path("user_data/bench/events")
START, END = pd.Timestamp("2022-01-03", tz="UTC"), pd.Timestamp("2026-08-31", tz="UTC")
BTC = "BTCUSDT"
LIQ = 1.95
ALL_CATS = ("insiders", "privateSale", "publicSale", "airdrop")


@dataclass(frozen=True)
class Params:
    top_n: int = 100
    thr: float = 0.25
    k: int = 6
    weight: float = 0.05
    horizon_days: int = 14
    min_hist: int = 60
    cats: tuple = ALL_CATS
    fee: float = 0.0005
    slip: float = 0.0005
    slip_btc: float = 0.0002
    start_equity: float = 10_000.0


class Data:
    def __init__(self):
        close, qv, _, fday = load_f1()
        ohlc = pd.read_parquet("user_data/data_f1/daily_ohlc.parquet")
        high = ohlc.pivot(index="date", columns="symbol", values="high").reindex(index=close.index, columns=close.columns)
        self.cols = list(close.columns)
        self.idx = close.index
        self.C, self.QV, self.F = close.to_numpy(float), qv.to_numpy(float), fday.to_numpy(float)
        self.H = high.to_numpy(float)
        self.H = np.where(np.isnan(self.H), self.C, self.H)
        self.btc = self.cols.index(BTC)
        self.sec = self.idx.as_unit("s").asi8                      # epoch seconds of each day's 00:00 UTC
        self.first = np.array([np.argmax(~np.isnan(self.C[:, j])) if (~np.isnan(self.C[:, j])).any() else 10**9
                               for j in range(len(self.cols))])
        u = pd.read_parquet("user_data/bench/events/unlocks.parquet")
        m = pd.read_parquet("user_data/bench/events/unlock_symbol_map.parquet")[["protocol", "perp"]]
        u = u.merge(m, on="protocol")
        u = u[u.perp.notna() & u.perp.isin(self.cols)]
        self.ev = {}
        for (sym, cat), g in u.groupby(["perp", "category"]):
            g = g.sort_values("ts")
            self.ev[(self.cols.index(sym), cat)] = (g.ts.dt.as_unit("s").astype("int64").to_numpy(), g.tokens.to_numpy(float))
        self.ev_syms = sorted({j for j, _ in self.ev})


def prep(C, QV):
    """Causal rolling stats: row t uses rows t-30 … t-1 only."""
    q = pd.DataFrame(QV)
    return q.rolling(30, min_periods=20).sum().shift(1).to_numpy(), q.rolling(30, min_periods=20).mean().shift(1).to_numpy()


def unlock_usd(D: Data, t: int, p: Params, price_row: np.ndarray, lo_days: int, hi_days: int) -> dict:
    """Σ tokens × price over included events with ts in (day_t + lo, day_t + hi]; {col: usd}."""
    t0 = D.sec[t] + lo_days * 86400
    t1 = D.sec[t] + hi_days * 86400
    out = {}
    for j in D.ev_syms:
        tot, hit = 0.0, False
        for cat in p.cats:
            e = D.ev.get((j, cat))
            if e is None:
                continue
            a, b = np.searchsorted(e[0], t0, "right"), np.searchsorted(e[0], t1, "right")
            if b > a:
                hit = True
                tot += e[1][a:b].sum()
        if hit:
            out[j] = tot * price_row[j]
    return out


def select(D: Data, t: int, p: Params, QV30, MV, C=None):
    """Real selection at Monday row t from rows < t only. Returns (selected cols, universe cols)."""
    C = D.C if C is None else C
    last = C[t - 1]
    ok = ~np.isnan(last) & (D.first <= t - p.min_hist) & ~np.isnan(QV30[t])
    ok[D.btc] = False
    cand = np.where(ok)[0]
    cand = cand[np.lexsort((cand, -QV30[t][cand]))][:p.top_n]           # volume desc, ties by column order (= symbol order)
    ev = unlock_usd(D, t, p, last, 0, p.horizon_days)
    scores = []
    for j in cand:
        if j in ev and MV[t, j] > 0:
            s = ev[j] / (p.horizon_days * MV[t, j])
            if s >= p.thr:
                scores.append((-s, j))
    scores.sort()
    return [j for _, j in scores[:p.k]], cand


def control_pool(D: Data, t: int, p: Params, cand):
    """Universe coins with no included-category unlock in (day-14, day+14]."""
    ev = unlock_usd(D, t, p, np.ones(len(D.cols)), -p.horizon_days, p.horizon_days)
    return [j for j in cand if j not in ev]


def run(D: Data, p: Params, control_seed: int | None = None, sels_only: bool = False):
    QV30, MV = prep(D.C, D.QV)
    t0 = int(D.idx.searchsorted(START))
    t_end = int(D.idx.searchsorted(END))
    C, H, F = D.C, D.H, D.F
    eq = p.start_equity
    units: dict[int, float] = {}
    entry: dict[int, float] = {}
    eq_arr = np.full(len(D.idx), np.nan)
    sels, weekly_eq, coin_week_pnl = {}, [], []
    week_pnl: dict[int, float] = {}
    split = {"short": 0.0, "btc": 0.0, "funding": 0.0, "costs": 0.0}
    attrib: dict[int, float] = {}
    liqs = coin_weeks = 0
    week = 0
    for t in range(t0, t_end + 1):
        if D.idx[t].weekday() == 0:
            sel, cand = select(D, t, p, QV30, MV)
            sels[t] = tuple(sel)
            if sels_only:
                continue
            if control_seed is not None and sel:
                pool = control_pool(D, t, p, cand)
                rng = np.random.default_rng([control_seed, week])
                sel = list(rng.choice(pool, size=min(len(sel), len(pool)), replace=False)) if pool else []
            week += 1
            weekly_eq.append((t, eq))
            for j, v in week_pnl.items():
                coin_week_pnl.append(v)
            week_pnl = {}
            target = {}
            for j in sel:
                px = C[t - 1, j]
                if np.isfinite(px) and px > 0:
                    target[j] = -p.weight * eq / px
            if target:
                gross = sum(-u * C[t - 1, j] for j, u in target.items())
                target[D.btc] = gross / C[t - 1, D.btc]
            for j in set(units) | set(target):
                px = C[t - 1, j]
                du = target.get(j, 0.0) - units.get(j, 0.0)
                if abs(du) < 1e-12 or not np.isfinite(px):
                    continue
                cost = abs(du) * px * (p.fee + (p.slip_btc if j == D.btc else p.slip))
                eq -= cost
                split["costs"] += cost
                attrib[j] = attrib.get(j, 0.0) - cost
                if j != D.btc:
                    week_pnl[j] = week_pnl.get(j, 0.0) - cost
                new, old = target.get(j, 0.0), units.get(j, 0.0)
                if new < 0:
                    if old < 0 and new <= old:
                        entry[j] = (entry[j] * old + px * (new - old)) / new
                    elif old >= 0:
                        entry[j] = px
                else:
                    entry.pop(j, None)
                if new == 0:
                    units.pop(j, None)
                else:
                    units[j] = new
            coin_weeks += sum(1 for j in units if j != D.btc)
        for j in list(units):
            u = units[j]
            p0, p1 = C[t - 1, j], C[t, j]
            if np.isnan(p1):                                    # data ended: close at last close
                cost = abs(u) * p0 * (p.fee + p.slip)
                eq -= cost
                split["costs"] += cost
                attrib[j] = attrib.get(j, 0.0) - cost
                units.pop(j)
                entry.pop(j, None)
                continue
            liq = u < 0 and max(H[t, j], p1) >= LIQ * entry[j]
            if liq:
                p1 = 2 * entry[j]
                liqs += 1
            pp = u * (p1 - p0)
            fp = -u * F[t, j] * p0
            eq += pp + fp
            split["btc" if j == D.btc else "short"] += pp
            split["funding"] += fp
            attrib[j] = attrib.get(j, 0.0) + pp + fp
            if j != D.btc:
                week_pnl[j] = week_pnl.get(j, 0.0) + pp + fp
            if liq:
                units.pop(j)
                entry.pop(j, None)
        eq_arr[t] = eq
    equity = pd.Series(eq_arr[t0:t_end + 1], index=D.idx[t0:t_end + 1])
    weekly = pd.Series({D.idx[t]: v for t, v in weekly_eq})
    return {"equity": equity, "weekly": weekly, "sels": sels, "split": split, "liqs": liqs, "coin_weeks": coin_weeks,
            "attrib": pd.Series({D.cols[j]: v for j, v in attrib.items() if j != D.btc}),
            "coin_week_pnl": np.array(coin_week_pnl)}


def control_mean(D: Data, p: Params, seeds) -> dict:
    runs = [run(D, p, control_seed=s) for s in seeds]
    wk = pd.concat([r["weekly"].pct_change() for r in runs], axis=1).mean(axis=1)
    eq = pd.concat([r["equity"] for r in runs], axis=1).mean(axis=1)
    return {"weekly_ret": wk, "equity": eq, "final_mean": float(np.mean([r["equity"].iloc[-1] for r in runs]))}


def seg_stats(eq, a, b, base):
    return seg(eq, a, b, base)


def report(res, D: Data, p: Params, verbose=True):
    eq = res["equity"]
    out = {}
    segs = {"full": (START, END + pd.Timedelta(days=1))}
    segs.update({str(y): (pd.Timestamp(f"{y}-01-01", tz="UTC"), pd.Timestamp(f"{y + 1}-01-01", tz="UTC")) for y in range(2022, 2027)})
    segs.update({"2022-2024H1": (START, pd.Timestamp("2024-07-01", tz="UTC")),
                 "2024H2-2026": (pd.Timestamp("2024-07-01", tz="UTC"), END + pd.Timedelta(days=1))})
    for k, (a, b) in segs.items():
        before = eq[eq.index < a]
        base = before.iloc[-1] if len(before) else p.start_equity
        r, dd = seg_stats(eq, max(a, eq.index[0]), b, base)
        wk = [t for t in res["sels"] if a <= D.idx[t] < b]
        out[k] = {"ret": r, "dd": dd, "coin_weeks": int(sum(len(res["sels"][t]) for t in wk))}
    btc = pd.Series(D.C[:, D.btc], index=D.idx).pct_change()
    dr = eq.pct_change().dropna()
    out["btc_corr"] = float(dr.corr(btc.reindex(dr.index)))
    out["ci"] = monthly_ci(eq)
    a = res["attrib"].sort_values()
    out["wo_top3"] = float(a.sum() - a.nlargest(3).sum()) if len(a) else 0.0
    cw = res["coin_week_pnl"]
    out["win"] = float((cw > 0).mean() * 100) if len(cw) else float("nan")
    out["coin_weeks_total"] = int(res["coin_weeks"])
    if verbose:
        for k in segs:
            print(f"{k:12s} ret {out[k]['ret']:+7.1f}%  MTM maxDD {out[k]['dd']:6.1f}%  coin-weeks {out[k]['coin_weeks']:4d}", flush=True)
        sp = res["split"]
        print(f"  btc_corr {out['btc_corr']:+.2f}  monthly mean {out['ci'][0]:+.2f}% CI [{out['ci'][1]:+.2f}, {out['ci'][2]:+.2f}]")
        print(f"  coin-weeks {out['coin_weeks_total']}  win {out['win']:.0f}%  P&L split: shorts {sp['short']:+.0f}  BTC hedge {sp['btc']:+.0f}  "
              f"funding {sp['funding']:+.0f}  costs {-sp['costs']:+.0f}  liquidations {res['liqs']}")
        print(f"  without top-3 coins: {out['wo_top3']:+.0f}   best {a.tail(3).round(0).to_dict()}  worst {a.head(3).round(0).to_dict()}")
    return out


def boot_diff(real_weekly: pd.Series, ctrl_weekly: pd.Series, n=5000):
    d = (real_weekly.pct_change() - ctrl_weekly).dropna() * 100
    rng = np.random.default_rng(0)
    dr = rng.choice(d.to_numpy(), size=(n, len(d)), replace=True).mean(axis=1)
    return float(d.mean()), *map(float, np.percentile(dr, [2.5, 97.5]))


def main():
    variant = "cats" in sys.argv[1:]
    tag = "_cats" if variant else ""
    D = Data()
    p = Params(cats=("insiders", "privateSale")) if variant else Params()
    print(f"== U1 unlock pressure ({'variant (a): insiders + privateSale' if variant else 'main spec'}); "
          f"{len(D.ev_syms)} perps with included events", flush=True)
    res = run(D, p)
    results = {"main": report(res, D, p)}
    OUT.mkdir(parents=True, exist_ok=True)
    res["equity"].to_csv(OUT / f"u1_equity{tag}.csv")
    res["attrib"].to_csv(OUT / f"u1_attrib{tag}.csv")
    print("== control (50 random draws, mean)", flush=True)
    ctrl = control_mean(D, p, range(50))
    ce = ctrl["equity"]
    cr = seg_stats(ce, ce.index[0], END + pd.Timedelta(days=1), p.start_equity)
    mean, lo, hi = boot_diff(res["weekly"], ctrl["weekly_ret"])
    results["control"] = {"ret": cr[0], "dd": cr[1], "diff_week_mean%": mean, "diff_ci": [lo, hi]}
    print(f"control mean-equity return {cr[0]:+.1f}%  (DD of the mean curve {cr[1]:.1f}%)   real − control weekly: mean {mean:+.3f}%  CI [{lo:+.3f}, {hi:+.3f}]", flush=True)
    for name, (a, b) in {"2022-2024H1": (START, pd.Timestamp("2024-07-01", tz="UTC")),
                         "2024H2-2026": (pd.Timestamp("2024-07-01", tz="UTC"), END + pd.Timedelta(days=1))}.items():
        sub_r = res["weekly"].pct_change()
        sub_r = sub_r[(sub_r.index >= a) & (sub_r.index < b)]
        sub_c = ctrl["weekly_ret"].reindex(sub_r.index)
        print(f"  {name}: real {((1 + sub_r.fillna(0)).prod() - 1) * 100:+.1f}%  control {((1 + sub_c.fillna(0)).prod() - 1) * 100:+.1f}% (weekly compounding)", flush=True)
    print("== fee ×2", flush=True)
    r2 = report(run(D, replace(p, fee=2 * p.fee)), D, p, verbose=False)
    results["fee2"] = r2["full"]
    print(f"fee2 ret {r2['full']['ret']:+.1f}%  DD {r2['full']['dd']:.1f}%", flush=True)
    print("== grid: S threshold × k (real return | real − control mean-equity return)", flush=True)
    grid = {}
    for thr in (0.1, 0.25, 0.5):
        row = []
        for k in (3, 6, 10):
            pp = replace(p, thr=thr, k=k)
            g = report(run(D, pp), D, pp, verbose=False)["full"]
            c = control_mean(D, pp, range(20))
            ctr = (c["equity"].iloc[-1] / p.start_equity - 1) * 100
            grid[f"thr{thr}_k{k}"] = {"real": g["ret"], "control": ctr}
            row.append(f"S≥{thr} k{k}: {g['ret']:+6.1f}% | {g['ret'] - ctr:+6.1f} (DD {g['dd']:.0f}%)")
        print("  " + " | ".join(row), flush=True)
    results["grid"] = grid
    print("== look-ahead self-check", flush=True)
    rng = np.random.default_rng(1)
    mondays = list(res["sels"])
    same = 0
    for i in rng.choice(len(mondays), 50, replace=False):
        t = mondays[i]
        nan_row = np.full((1, D.C.shape[1]), np.nan)
        C_cut = np.vstack([D.C[:t], nan_row])                  # rows before the decision day only; row t is empty
        QV_cut = np.vstack([D.QV[:t], nan_row])
        QV30, MV = prep(C_cut, QV_cut)                         # row t = stats over rows t-30 … t-1 of the truncated data
        sel, _ = select(D, t, p, QV30, MV, C=C_cut)
        same += tuple(sel) == res["sels"][t]
    results["lookahead_same"] = same
    print(f"  {same}/50 truncated selections identical", flush=True)
    (OUT / f"u1_results{tag}.json").write_text(json.dumps(results, indent=1, default=str))


if __name__ == "__main__":
    main()
