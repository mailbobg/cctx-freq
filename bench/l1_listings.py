"""L1 backtest: short newly listed USDT-M perpetuals, BTC-hedged (spec: specs/2026-09-29-l1-short-new-listings.md).

Daily resolution. Listing date D0 = first daily bar. Short the coin at the close of D0 + delay, long BTC with equal
notional, 5 % of equity per leg, ≤ 10 events open; exit after `hold` days or when the coin closes ≥ 1.5 × entry.

Usage:
  python user_data/bench/l1_listings.py           # main spec, full suite
  python user_data/bench/l1_listings.py nostop    # variant (a): no stop, volatility-scaled size
"""

import json
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from f1_carry import load, monthly_ci, seg  # noqa: E402

OUT = Path("user_data/bench/events")
EV_START, EV_END = pd.Timestamp("2021-02-01", tz="UTC"), pd.Timestamp("2026-07-31", tz="UTC")
END = pd.Timestamp("2026-08-31", tz="UTC")
BTC = "BTCUSDT"
LIQ = 1.95                                     # liquidation trigger (maintenance margin of new listings ~2.5-5 %)
_hl = pd.read_parquet("user_data/data_f1/daily_hl.parquet")
HIGH = dict(zip(zip(_hl.symbol, _hl.date), _hl.high))


@dataclass(frozen=True)
class Params:
    delay: int = 2
    hold: int = 21
    size: float = 0.05
    max_open: int = 10
    stop: float = 1.5                  # 0 = no stop
    cross_stop: float = 0.0            # L2 reference: cross margin, exit when the daily high reaches this × entry (no liquidation)
    volscale: bool = False
    fee: float = 0.0005
    slip_coin: float = 0.0010
    slip_btc: float = 0.0002
    start_equity: float = 10_000.0


def norm_base(sym: str) -> str:
    return re.sub(r"^(1000000|1000|1M)", "", sym[:-4])


def events(close: pd.DataFrame, cutoff=None):
    """New listings from data up to `cutoff` (look-ahead safe)."""
    c = close if cutoff is None else close[close.index <= cutoff]
    first = c.apply(pd.Series.first_valid_index).dropna()
    first = first.sort_index().sort_values(kind="mergesort")      # ties: same-day listings in symbol order
    seen, out = {}, []
    for s, d0 in first.items():
        b = norm_base(s)
        if b in seen and seen[b] < d0:
            continue                                   # re-listing / rename of an existing base
        seen.setdefault(b, d0)
        if EV_START <= d0 <= EV_END and s != BTC:
            out.append((d0, s))
    return out


def run(close, fday, p: Params, stop_at=None):
    evs = events(close)
    days = close.index[(close.index >= EV_START) & (close.index <= END)]
    if stop_at is not None:
        days = days[days <= stop_at]
    C = close
    by_entry = {}
    for d0, s in evs:
        e = d0 + pd.Timedelta(days=p.delay)
        by_entry.setdefault(e, []).append((d0, s))
    eq = p.start_equity
    open_ev, log, rows, decisions = [], [], [], []
    ranges = []
    split = {"coin": 0.0, "btc": 0.0, "funding": 0.0, "costs": 0.0}
    prev = None

    def close_ev(ev, px, reason, d):
        nonlocal eq
        cost = abs(ev["u"]) * px * (p.fee + p.slip_coin) + ev["ub"] * C.at[d, BTC] * (p.fee + p.slip_btc)
        eq -= cost
        split["costs"] += cost
        ev["pnl"] -= cost
        log.append({"symbol": ev["s"], "d0": ev["d0"], "entry": ev["entry_day"], "exit": d, "entry_px": ev["px"],
                    "exit_px": px, "pnl": ev["pnl"], "reason": reason, "notional": ev["notional"]})
        decisions.append(("exit", ev["s"], d, reason))

    for d in days:
        if prev is not None:
            still = []
            for ev in open_ev:
                s = ev["s"]
                p0, p1 = C.at[prev, s], C.at[d, s]
                b0, b1 = C.at[prev, BTC], C.at[d, BTC]
                if np.isnan(p1):
                    close_ev(ev, p0, "delist", prev)
                    continue
                hi = HIGH.get((s, d), p1)
                if p.cross_stop:
                    liq = max(hi, p1) >= p.cross_stop * ev["px"]
                    if liq:
                        p1 = p.cross_stop * ev["px"]
                else:
                    liq = max(hi, p1) >= LIQ * ev["px"]   # 1x isolated short: intraday high reaching ~2x entry liquidates it;
                    if liq:                               # the whole margin (= leg notional) is lost
                        p1 = 2 * ev["px"]
                cp = -ev["u"] * (p1 - p0)
                bp = ev["ub"] * (b1 - b0)
                fp = ev["u"] * fday.at[d, s] * p0 - ev["ub"] * fday.at[d, BTC] * b0
                eq += cp + bp + fp
                split["coin"] += cp
                split["btc"] += bp
                split["funding"] += fp
                ev["pnl"] += cp + bp + fp
                if liq:
                    close_ev(ev, p1, "liq", d)
                elif p.stop and p1 >= p.stop * ev["px"]:
                    close_ev(ev, p1, "stop", d)
                elif d >= ev["exit_day"]:
                    close_ev(ev, p1, "time", d)
                else:
                    still.append(ev)
            open_ev = still
        for d0, s in by_entry.get(d, []):
            px, bpx = C.at[d, s], C.at[d, BTC]
            if not (np.isfinite(px) and np.isfinite(bpx)):
                continue
            win = C.loc[d0:d, s].dropna()
            rng_ = (win.max() - win.min()) / win.min() if len(win) > 1 else 0.0
            if len(open_ev) >= p.max_open:
                decisions.append(("skip", s, d))
                ranges.append(rng_)
                continue
            size = p.size
            if p.volscale:
                med = np.median(ranges) if ranges else rng_
                size = min(p.size, p.size * med / rng_) if rng_ > 0 else p.size
            ranges.append(rng_)
            notional = size * eq
            u, ub = notional / px, notional / bpx
            cost = notional * (p.fee + p.slip_coin) + notional * (p.fee + p.slip_btc)
            eq -= cost
            split["costs"] += cost
            open_ev.append({"s": s, "d0": d0, "entry_day": d, "exit_day": d + pd.Timedelta(days=p.hold), "px": px,
                            "u": u, "ub": ub, "pnl": -cost, "notional": notional})
            decisions.append(("entry", s, d))
        rows.append((d, eq))
        prev = d
    for ev in open_ev:
        close_ev(ev, C.at[prev, ev["s"]], "end", prev)
    rows[-1] = (prev, eq)
    return {"equity": pd.Series(dict(rows)), "trades": pd.DataFrame(log), "split": split, "decisions": decisions,
            "n_events": len(evs)}


def excess_distribution(close, p: Params):
    out = []
    for d0, s in events(close):
        e = d0 + pd.Timedelta(days=p.delay)
        x = e + pd.Timedelta(days=p.hold)
        if x > close.index[-1] or e not in close.index or x not in close.index:
            continue
        c0, c1, b0, b1 = close.at[e, s], close.at[x, s], close.at[e, BTC], close.at[x, BTC]
        if np.isfinite([c0, c1, b0, b1]).all():
            out.append((c1 / c0) - (b1 / b0))
    return np.array(out)


def report(res, btc, p: Params, verbose=True):
    eq, tr = res["equity"], res["trades"]
    out = {}
    start = eq.index[0]
    segs = {"full": (start, END + pd.Timedelta(days=1))}
    segs.update({str(y): (pd.Timestamp(f"{y}-01-01", tz="UTC"), pd.Timestamp(f"{y + 1}-01-01", tz="UTC")) for y in range(2021, 2027)})
    segs.update({"2021-2023": (start, pd.Timestamp("2024-01-01", tz="UTC")),
                 "2024-2026": (pd.Timestamp("2024-01-01", tz="UTC"), END + pd.Timedelta(days=1))})
    for k, (a, b) in segs.items():
        before = eq[eq.index < a]
        base = before.iloc[-1] if len(before) else p.start_equity
        r, dd = seg(eq, max(a, start), b, base)
        n = int(((tr.exit >= a) & (tr.exit < b)).sum()) if len(tr) else 0
        out[k] = {"ret": r, "dd": dd, "events": n}
    dr = eq.pct_change().dropna()
    out["btc_corr"] = float(dr.corr(btc.reindex(dr.index)))
    out["ci"] = monthly_ci(eq)
    out["wo_top3"] = float(tr.pnl.sum() - tr.pnl.nlargest(3).sum()) if len(tr) else 0.0
    out["traded"] = len(tr)
    if verbose:
        for k in segs:
            print(f"{k:10s} ret {out[k]['ret']:+7.1f}%  MTM maxDD {out[k]['dd']:6.1f}%  events {out[k]['events']:4d}", flush=True)
        sp = res["split"]
        print(f"  btc_corr {out['btc_corr']:+.2f}  monthly mean {out['ci'][0]:+.2f}% CI [{out['ci'][1]:+.2f}, {out['ci'][2]:+.2f}]")
        print(f"  listings found {res['n_events']}, traded {len(tr)}, win {(tr.pnl > 0).mean() * 100:.0f}%  "
              f"P&L split: coin short {sp['coin']:+.0f}  btc long {sp['btc']:+.0f}  funding {sp['funding']:+.0f}  costs {-sp['costs']:+.0f}")
        print("  exits:\n" + tr.groupby("reason")["pnl"].agg(["count", "sum", "mean"]).round(1).to_string())
        print(f"  without top-3 events: {out['wo_top3']:+.0f}")
        w = tr.nsmallest(5, "pnl")[["symbol", "entry", "exit", "entry_px", "exit_px", "pnl", "reason"]]
        print("  worst events:\n" + w.to_string(index=False))
    return out


def main():
    variant = "nostop" in sys.argv[1:]
    l2 = "l2" in sys.argv[1:]
    tag = "_nostop" if variant else "_l2" if l2 else ""
    close, _, _, fday = load()
    btc = close[BTC].pct_change()
    p = Params(stop=0.0, volscale=True) if variant else Params(size=0.025, stop=0.0, cross_stop=3.0) if l2 else Params()
    print(f"== L1 short new listings ({'variant (a): no stop, vol-scaled' if variant else 'L2 reference: 2.5 %, cross, exit at 3x' if l2 else 'main spec'})", flush=True)
    ex = excess_distribution(close, p)
    print(f"  {len(ex)} listings with a full window: coin − BTC over {p.hold} d after D0+{p.delay}: median {np.median(ex) * 100:+.1f}%  "
          f"mean {ex.mean() * 100:+.1f}%  coin beat BTC {(ex > 0).mean() * 100:.0f}%  coin excess > +50% {(ex > 0.5).mean() * 100:.1f}%  "
          f"> +100% {(ex > 1).mean() * 100:.1f}%", flush=True)
    res = run(close, fday, p)
    results = {"main": report(res, btc, p)}
    OUT.mkdir(parents=True, exist_ok=True)
    res["trades"].to_csv(OUT / f"l1_trades{tag}.csv", index=False)
    res["equity"].to_csv(OUT / f"l1_equity{tag}.csv")
    print("== fee ×2", flush=True)
    r2 = report(run(close, fday, replace(p, fee=2 * p.fee)), btc, p, verbose=False)
    results["fee2"] = r2["full"]
    print(f"fee2 ret {r2['full']['ret']:+.1f}%  DD {r2['full']['dd']:.1f}%", flush=True)
    print("== grid: entry delay × hold days", flush=True)
    grid = {}
    for dl in (1, 2, 5):
        row = []
        for h in (14, 21, 28):
            g = report(run(close, fday, replace(p, delay=dl, hold=h)), btc, p, verbose=False)
            grid[f"d{dl}_h{h}"] = g["full"]["ret"]
            row.append(f"delay {dl}d hold {h}d: {g['full']['ret']:+7.1f}% (DD {g['full']['dd']:.0f}%, n {g['traded']})")
        print("  " + " | ".join(row), flush=True)
    results["grid"] = grid
    print("== look-ahead self-check", flush=True)
    rng = np.random.default_rng(1)
    days = res["equity"].index
    same = 0
    for i in rng.choice(np.arange(30, len(days) - 1), 50, replace=False):
        T = days[i]
        c = run(close[close.index <= T], fday[fday.index <= T], p, stop_at=T)
        key = lambda e: e[2]
        f_d = [e for e in res["decisions"] if key(e) <= T and not (e[0] == "exit" and e[3] == "end")]
        c_d = [e for e in c["decisions"] if key(e) <= T and not (e[0] == "exit" and e[3] == "end")]
        same += f_d == c_d
    results["lookahead_same"] = same
    print(f"  {same}/50 truncated reruns identical", flush=True)
    (OUT / f"l1_results{tag}.json").write_text(json.dumps(results, indent=1, default=str))


if __name__ == "__main__":
    main()
