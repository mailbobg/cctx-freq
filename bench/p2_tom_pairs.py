"""P2 backtest: replication of TOM's pair_trading_run_V6 (spec: specs/2026-09-29-p2-tom-pairs-replication.md).

Data: data_x14/5m closes/opens (x14_fetch.py), weekly top-60 universe, funding from data_p1. Walk-forward: formation on
the N days before each 90-day trading window; decisions on closed 5m candles, fills at the next candle's open.
One continuous mark-to-market run; years and halves are cut from that equity curve.

Usage:
  python user_data/bench/p2_tom_pairs.py            # full suite
  python user_data/bench/p2_tom_pairs.py quick      # main run only
  python user_data/bench/p2_tom_pairs.py stop       # variant (a): author's z > 3 stop restored
"""

import json
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.tsa.adfvalues import mackinnonp

sys.path.insert(0, str(Path(__file__).parent))
from x14_flush import BTC, load, monthly_ci  # noqa: E402

OUT = Path("user_data/bench/events")
START, END = "2021-04-01", "2026-09-23"
BAR = pd.Timedelta(minutes=5)


@dataclass(frozen=True)
class Params:
    form_days: int = 90
    trade_days: int = 90
    entry_z: float = 1.0
    exit_z: float = 0.8
    stop_z: float = 0.0                 # 0 = no stop (author's live code); variant (a): 3
    n_pairs: int = 6                    # max_open_trades 12 / 2
    max_legs: int = 12
    adf_max: float = -6.0
    p_max: float = 0.01
    half_life_max: float = 500.0        # candles
    min_cover: float = 0.90
    profit_exit: float = 0.01           # pair open P&L / realised equity
    profit_min_bars: int = 288          # 1 day
    fee: float = 0.0005
    slip: float = 0.0002
    start_equity: float = 10_000.0


# ---------------------------------------------------------------- formation

def fit_pair(y: np.ndarray, x: np.ndarray):
    """OLS y = c + g x, residual stats, ADF(maxlag 1, const) on residual, half-life. Mirrors get_para_V6.fn_ecm."""
    xm, ym = x.mean(), y.mean()
    vx = ((x - xm) ** 2).sum()
    if vx <= 0:
        return None
    g = ((x - xm) * (y - ym)).sum() / vx
    c = ym - g * xm
    z = y - c - g * x
    zm, zs = z.mean(), z.std(ddof=1)
    dz = np.diff(z)
    # ADF: dz_t = a + b z_{t-1} + d dz_{t-1}
    Y = dz[1:]
    X = np.column_stack([np.ones(len(Y)), z[1:-1], dz[:-1]])
    XtX = X.T @ X
    try:
        inv = np.linalg.inv(XtX)
    except np.linalg.LinAlgError:
        return None
    beta = inv @ (X.T @ Y)
    resid = Y - X @ beta
    s2 = resid @ resid / (len(Y) - 3)
    adf = beta[1] / np.sqrt(s2 * inv[1, 1])
    # half-life: dz = a + b z_lag
    zl = z[:-1]
    zlm = zl.mean()
    b = ((zl - zlm) * (dz - dz.mean())).sum() / ((zl - zlm) ** 2).sum()
    hl = -np.log(2) / b if b != 0 else np.nan
    return g, c, zm, zs, adf, hl


def formation(C: np.ndarray, cols: list, universe: dict, idx: pd.DatetimeIndex, w_row: int, p: Params):
    w_time = idx[w_row]
    mondays = [m for m in universe if m <= w_time]
    if not mondays:
        return []
    uni = [s for s in universe[max(mondays)] if s in cols]
    r0 = max(0, w_row - p.form_days * 288)
    block = C[r0:w_row]
    cands = []
    for s in uni:
        j = cols.index(s)
        col = block[:, j]
        if np.isnan(C[w_row - 1, j]) or np.isfinite(col).mean() < p.min_cover:
            continue
        cands.append((s, j, pd.Series(col.astype(np.float64)).ffill().to_numpy()))
    fits = []
    for (sy, jy, y) in cands:
        for (sx, jx, x) in cands:
            if sy == sx:
                continue
            m = np.isfinite(y) & np.isfinite(x)
            if m.sum() < 300:
                continue
            f = fit_pair(y[m], x[m])
            if f is None:
                continue
            g, c, zm, zs, adf, hl = f
            if not (adf < p.adf_max) or not (hl < p.half_life_max) or g <= 0 or zs <= 0:
                continue
            if mackinnonp(adf, regression="c", N=1) > p.p_max:
                continue
            fits.append({"y": sy, "x": sx, "jy": jy, "jx": jx, "g": g, "c": c, "zm": zm, "zs": zs, "adf": adf, "hl": hl})
    fits.sort(key=lambda d: d["hl"])
    taken, used = [], set()
    for f in fits:
        if len(taken) >= p.n_pairs:
            break
        if f["y"] in used or f["x"] in used:
            continue
        taken.append(f)
        used.update((f["y"], f["x"]))
    return taken


# ---------------------------------------------------------------- engine

@dataclass
class Leg:
    j: int
    sym: str
    side: int
    qty: float
    entry: float
    open: bool = True
    pnl: float = 0.0
    funding: float = 0.0
    reason: str = ""


@dataclass
class Slot:
    key: str
    fit: dict
    current: bool = True
    legs: list = field(default_factory=list)
    entry_row: int = -1
    tid: int = -1


class Engine:
    def __init__(self, close: pd.DataFrame, open_: pd.DataFrame, funding: dict, universe: dict):
        self.idx = close.index
        self.cols = list(close.columns)
        self.C = close.to_numpy()
        self.O = open_.to_numpy()
        self.funding = funding
        self.universe = universe
        self.form_cache = {}

    def window_rows(self, p: Params):
        rows, t = [], pd.Timestamp(START, tz="UTC")
        end = pd.Timestamp(END, tz="UTC")
        while t < end:
            rows.append(int(self.idx.searchsorted(t)))
            t += pd.Timedelta(days=p.trade_days)
        return rows

    def select(self, w_row, p: Params, C=None, cache=True):
        key = (w_row, p.form_days, p.adf_max, p.half_life_max, p.n_pairs)
        if cache and key in self.form_cache:
            return self.form_cache[key]
        sel = formation(self.C if C is None else C, self.cols, self.universe, self.idx, w_row, p)
        if cache:
            self.form_cache[key] = sel
        return sel

    def run(self, p: Params, stop_row: int | None = None, C=None, O=None, fresh_window: int | None = None):
        C = self.C if C is None else C
        O = self.O if O is None else O
        wrows = self.window_rows(p)
        first = wrows[0]
        last = (len(self.idx) - 2) if stop_row is None else stop_row
        cash = p.start_equity
        slots: dict[str, Slot] = {}
        trades, events = [], []
        eq = np.full(last + 1 - first, np.nan)
        busy: dict[int, str] = {}            # column -> slot key holding an open leg
        tid = 0
        lastpx = {}
        wset = set(wrows)

        def z_of(s: Slot, t):
            f = s.fit
            y, x = C[t, f["jy"]], C[t, f["jx"]]
            return (y - f["g"] * x - f["c"] - f["zm"]) / f["zs"]

        def close_leg(s: Slot, lg: Leg, px, reason, t):
            nonlocal cash
            pnl = lg.side * lg.qty * (px - lg.entry)
            cost = (p.fee + p.slip) * lg.qty * px
            cash += pnl - cost
            lg.pnl += pnl - cost
            lg.open, lg.reason = False, reason
            busy.pop(lg.j, None)

        def finish(s: Slot, t, reason):
            tr = {"tid": s.tid, "key": s.key, "pair": "_".join(sorted((s.fit["y"], s.fit["x"]))),
                  "entry": self.idx[s.entry_row], "exit": self.idx[min(t, len(self.idx) - 1)],
                  "bars": t - s.entry_row, "pnl": sum(lg.pnl for lg in s.legs),
                  "funding": sum(lg.funding for lg in s.legs),
                  "notional": sum(lg.qty * lg.entry for lg in s.legs),
                  "reason": reason, "liq": sum(lg.reason == "liq" for lg in s.legs)}
            trades.append(tr)
            events.append(("exit", s.key, t, reason))
            s.legs, s.entry_row = [], -1

        for t in range(first, last + 1):
            # 1. window roll: new formation, old pairs become legacy
            if t in wset:
                sel = self.select(t, p, C=C, cache=(fresh_window != t))
                new = {f"{f['y']}|{f['x']}": f for f in sel}
                for k, s in list(slots.items()):
                    if k in new:
                        s.fit, s.current = new[k], True
                    else:
                        s.current = False
                        if not s.legs:
                            slots.pop(k)
                for k, f in new.items():
                    if k not in slots:
                        slots[k] = Slot(k, f)
                n_cur = max(1, len(new))
                events.append(("form", t, tuple(sorted(new))))
            ts = self.idx[t]
            hourly = ts.minute == 0
            # 2. manage open positions at close[t], fill at open[t+1]
            for k, s in list(slots.items()):
                if not s.legs:
                    continue
                for lg in s.legs:
                    if not lg.open:
                        continue
                    px = C[t, lg.j]
                    if np.isnan(px):
                        if t - lastpx.get(lg.j, (t, 0))[0] > 4 * 288:   # data ended (> 4 days, longer than the 2022-02-26/28 archive gap): close at last close
                            close_leg(s, lg, lastpx[lg.j][1], "delist", t)
                        continue
                    lastpx[lg.j] = (t, px)
                    if hourly:
                        rate = self.funding.get(lg.sym, {}).get(ts, 0.0)
                        if rate:
                            f_amt = lg.side * rate * lg.qty * px
                            cash -= f_amt
                            lg.funding -= f_amt
                            lg.pnl -= f_amt
                    if lg.side == -1 and px >= 2 * lg.entry:
                        close_leg(s, lg, 2 * lg.entry, "liq", t)
                        events.append(("liq", k, t, lg.sym))
                if not any(lg.open for lg in s.legs):
                    finish(s, t, "legs_gone")
                    if not s.current:
                        slots.pop(k)
                    continue
                z = z_of(s, t)
                reason = None
                if np.isfinite(z):
                    if abs(z) < p.exit_z:
                        reason = "revert"
                    elif p.stop_z and abs(z) > p.stop_z:
                        reason = "stop"
                if reason is None and t - s.entry_row > p.profit_min_bars:
                    opnl = sum(lg.side * lg.qty * (C[t, lg.j] - lg.entry) for lg in s.legs if lg.open and np.isfinite(C[t, lg.j]))
                    if opnl > p.profit_exit * cash:
                        reason = "pair_profit"
                if reason:
                    for lg in s.legs:
                        if lg.open:
                            px = O[t + 1, lg.j]
                            if np.isnan(px):
                                px = C[t, lg.j] if np.isfinite(C[t, lg.j]) else lastpx.get(lg.j, (0, lg.entry))[1]
                            close_leg(s, lg, px, reason, t + 1)
                    finish(s, t + 1, reason)
                    if not s.current:
                        slots.pop(k)
            # 3. entries for current pairs
            n_open = sum(1 for s in slots.values() for lg in s.legs if lg.open)
            for k, s in slots.items():
                if not s.current or s.legs or n_open + 2 > p.max_legs:
                    continue
                f = s.fit
                if f["jy"] in busy or f["jx"] in busy:
                    continue
                z = z_of(s, t)
                if not np.isfinite(z) or abs(z) <= p.entry_z:
                    continue
                if p.stop_z and abs(z) > p.stop_z:
                    continue
                py, px_ = C[t, f["jy"]], C[t, f["jx"]]
                oy, ox = O[t + 1, f["jy"]], O[t + 1, f["jx"]]
                if not all(np.isfinite([py, px_, oy, ox])):
                    continue
                budget = cash / n_cur
                qy = budget / (py + f["g"] * px_)
                qx = f["g"] * qy
                if qy * py < 5 or qx * px_ < 5:
                    continue
                sy = -1 if z > 0 else 1
                s.legs = [Leg(f["jy"], f["y"], sy, qy, oy), Leg(f["jx"], f["x"], -sy, qx, ox)]
                for lg in s.legs:
                    cash -= (p.fee + p.slip) * lg.qty * lg.entry
                    lg.pnl -= (p.fee + p.slip) * lg.qty * lg.entry
                    busy[lg.j] = k
                s.entry_row, s.tid = t + 1, tid
                tid += 1
                n_open += 2
                events.append(("entry", k, t + 1, sy))
            # 4. mark to market
            u = 0.0
            for s in slots.values():
                for lg in s.legs:
                    if lg.open:
                        px = C[t, lg.j]
                        if np.isnan(px):
                            px = lastpx.get(lg.j, (0, lg.entry))[1]
                        u += lg.side * lg.qty * (px - lg.entry)
            eq[t - first] = cash + u
        unreal = eq[-1] - cash
        open_trades = [(s.key, self.idx[s.entry_row], s.entry_row) for s in slots.values() if s.legs]
        equity = pd.Series(eq, index=self.idx[first:last + 1])
        return {"equity": equity, "trades": pd.DataFrame(trades), "events": events, "unreal": unreal,
                "open": open_trades, "cash": cash}


# ---------------------------------------------------------------- metrics

def seg_metrics(eq: pd.Series, a, b, prev_end, btc_daily):
    s = eq[(eq.index >= pd.Timestamp(a, tz="UTC")) & (eq.index < pd.Timestamp(b, tz="UTC"))]
    if s.empty:
        return None
    base = prev_end if prev_end is not None else s.iloc[0]
    h = s.resample("1h").last().ffill()
    h = pd.concat([pd.Series([base], index=[h.index[0] - pd.Timedelta(hours=1)]), h])
    dd = (h / h.cummax() - 1).min() * 100
    d = s.resample("1D").last()
    r = d.pct_change().dropna()
    corr = float(r.corr(btc_daily.reindex(r.index))) if len(r) > 10 else float("nan")
    return {"return%": (s.iloc[-1] / base - 1) * 100, "maxDD%": dd, "btc_corr": corr}


def report(name, res, btc_daily, p: Params, verbose=True):
    eq, tr = res["equity"], res["trades"]
    out = {}
    segs = {"2021-2026": (START, END)}
    segs.update({str(y): (f"{y}-01-01", f"{y + 1}-01-01") for y in range(2021, 2026)})
    segs.update({"2026": ("2026-01-01", END), "2021-2023": (START, "2024-01-01"), "2024-2026": ("2024-01-01", END)})
    for k, (a, b) in segs.items():
        prev = None
        if k not in ("2021-2026", "2021-2023") and k != "2021":
            before = eq[eq.index < pd.Timestamp(a, tz="UTC")]
            prev = before.iloc[-1] if len(before) else p.start_equity
        else:
            prev = p.start_equity
        m = seg_metrics(eq, a, b, prev, btc_daily)
        sel = tr[(tr.exit >= pd.Timestamp(a, tz="UTC")) & (tr.exit < pd.Timestamp(b, tz="UTC"))] if len(tr) else tr
        m["trades"] = len(sel)
        if len(sel):
            m["win%"] = (sel.pnl > 0).mean() * 100
            m["avg_h"] = sel.bars.mean() / 12
            m["max_h"] = sel.bars.max() / 12
        out[k] = m
        if verbose:
            extra = f"  win {m.get('win%', 0):.0f}%  avg_hold {m.get('avg_h', 0):.0f}h  max_hold {m.get('max_h', 0) / 24:.0f}d" if len(sel) else ""
            print(f"{k:10s} ret {m['return%']:+7.1f}%  MTM maxDD {m['maxDD%']:6.1f}%  trades {m['trades']:5d}  btc_corr {m['btc_corr']:+.2f}{extra}", flush=True)
    return out


def main():
    args = sys.argv[1:]
    quick = "quick" in args
    variant = "stop" in args
    tag = "_stop" if variant else ""
    close, open_, funding, universe = load()
    eng = Engine(close, open_, funding, universe)
    btc_daily = close[BTC].resample("1D").last().pct_change()
    p = Params(stop_z=3.0) if variant else Params()
    print(f"== P2 TOM V6 replication ({'variant (a): z>3 stop' if variant else 'main: no stop'})", flush=True)
    res = eng.run(p)
    results = {"main": report("main", res, btc_daily, p)}
    tr = res["trades"]
    mean, lo, hi = monthly_ci(res["equity"])
    by_pair = tr.groupby("pair")["pnl"].sum().sort_values()
    results.update({"monthly_ci": [mean, lo, hi], "unrealised_end": res["unreal"], "open_end": len(res["open"]),
                    "funding": tr.funding.sum(), "liq_legs": int(tr.liq.sum()),
                    "wo_top3": float(by_pair.sum() - by_pair.nlargest(3).sum()),
                    "reasons": tr.groupby("reason")["pnl"].agg(["count", "sum", "mean"]).round(1).to_dict()})
    print(f"  monthly mean {mean:+.2f}%  bootstrap 95% CI [{lo:+.2f}, {hi:+.2f}]")
    print(f"  closed trades: {len(tr)}  win {(tr.pnl > 0).mean() * 100:.0f}%  closed P&L {tr.pnl.sum():+.0f}  "
          f"unrealised at end {res['unreal']:+.0f} ({len(res['open'])} open)  funding {tr.funding.sum():+.0f}  "
          f"liquidated legs {int(tr.liq.sum())}")
    print("  by exit reason:\n" + tr.groupby("reason")["pnl"].agg(["count", "sum", "mean"]).round(1).to_string())
    print(f"  worst pairs: {by_pair.head(5).round(0).to_dict()}")
    print(f"  best pairs:  {by_pair.tail(5).round(0).to_dict()}")
    print(f"  without top-3 pairs: {results['wo_top3']:+.0f}")
    worst = tr.nsmallest(5, "pnl")[["pair", "entry", "exit", "bars", "pnl", "reason"]]
    worst["days"] = (worst.bars / 288).round(1)
    print("  worst trades:\n" + worst.drop(columns="bars").to_string(index=False))
    OUT.mkdir(parents=True, exist_ok=True)
    tr.to_csv(OUT / f"p2_trades{tag}.csv", index=False)
    res["equity"].resample("1h").last().to_csv(OUT / f"p2_equity{tag}.csv")
    forms = [e for e in res["events"] if e[0] == "form"]
    print(f"  formations: {len(forms)}, pairs per window: {[len(e[2]) for e in forms]}")
    if quick:
        (OUT / f"p2_results{tag}.json").write_text(json.dumps(results, indent=1, default=str))
        return
    print("== fee ×2", flush=True)
    r2 = eng.run(replace(p, fee=2 * p.fee))
    results["fee2"] = report("fee2", r2, btc_daily, p, verbose=False)["2021-2026"]
    print(f"fee2 ret {results['fee2']['return%']:+.1f}%  maxDD {results['fee2']['maxDD%']:.1f}%", flush=True)
    print("== grid: entry z × formation days, 2021-2026 MTM return", flush=True)
    grid = {}
    for ez in (1.0, 1.5, 2.0):
        row = []
        for fd in (60, 90, 120):
            r = eng.run(replace(p, entry_z=ez, form_days=fd))
            m = report("g", r, btc_daily, p, verbose=False)["2021-2026"]
            grid[f"z{ez}_f{fd}"] = m["return%"]
            row.append(f"z{ez:.1f} f{fd}d: {m['return%']:+7.1f}% (DD {m['maxDD%']:.0f}%, n {m['trades']})")
        print("  " + " | ".join(row), flush=True)
    results["grid"] = grid
    print("== look-ahead self-check", flush=True)
    rng = np.random.default_rng(1)
    first = eng.window_rows(p)[0]
    same = 0
    full_ev = res["events"]
    for T in sorted(rng.choice(np.arange(first + 288, len(eng.idx) - 2), 50, replace=False)):
        T = int(T)
        w = max(r for r in eng.window_rows(p) if r <= T)
        c = eng.run(p, stop_row=T, C=eng.C[:T + 1], O=eng.O[:T + 2], fresh_window=w)
        key = lambda e: e[1] if e[0] == "form" else e[2]
        f_ev = [e for e in full_ev if key(e) <= T]
        c_ev = [e for e in c["events"] if key(e) <= T]
        same += f_ev == c_ev
    results["lookahead_same"] = same
    print(f"  {same}/50 truncated reruns identical", flush=True)
    (OUT / f"p2_results{tag}.json").write_text(json.dumps(results, indent=1, default=str))


if __name__ == "__main__":
    main()
