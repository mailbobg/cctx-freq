"""P1 pairs-trading backtest (spec: specs/2026-09-28-p1-crypto-pairs-design.md).

Event-driven, two legs per position, hourly candles, weekly formation, funding every 8h, all decisions on closed
candles and all fills at the next hourly open. Data from p1_fetch.py (user_data/data_p1/).

Usage:
  python user_data/bench/p1_pairs.py            # per-year, halves, full, fee×2, top-3-pairs-removed, grid, look-ahead check
  python user_data/bench/p1_pairs.py quick      # full window only
  python user_data/bench/p1_pairs.py coint      # pre-registered variant (a): cointegration-based pair selection
"""

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("user_data/data_p1")
OUT = Path("user_data/bench/events")
H = pd.Timedelta(hours=1)


@dataclass
class Params:
    entry_z: float = 2.0
    exit_z: float = 0.0
    stop_z: float = 4.0
    max_hold_hours: int = 7 * 24
    form_days: int = 60
    n_pairs: int = 5
    corr_min: float = 0.7
    gross_per_pair: float = 0.20
    fee: float = 0.0005
    slip: float = 0.0002
    start_equity: float = 10_000.0
    selection: str = "distance"       # "distance" (main spec) or "coint" (pre-registered variant a)
    coint_p: float = 0.05


_SEL_CACHE: dict = {}                 # (t, form_days, selection, n_pairs, corr_min) -> selected pairs; past data only
USE_CACHE = True


@dataclass
class Position:
    a: str
    b: str
    side: int                     # +1: long A / short B (z was ≤ −entry); −1: short A / long B (z ≥ +entry)
    entry_time: pd.Timestamp
    pa: float
    pb: float
    units_a: float
    units_b: float
    beta: float
    mu: float
    sd: float
    entry_z: float
    costs: float = 0.0
    funding: float = 0.0
    week: str = ""

    def mtm(self, pa: float, pb: float) -> float:
        return self.side * (self.units_a * (pa - self.pa) - self.units_b * (pb - self.pb)) - self.funding


@dataclass
class Result:
    equity: pd.Series                 # hourly mark-to-market
    trades: pd.DataFrame
    params: Params
    events: list = field(default_factory=list)


def load():
    close = pd.read_parquet(ROOT / "hourly_close.parquet")
    open_ = pd.read_parquet(ROOT / "hourly_open.parquet")
    fund = pd.read_parquet(ROOT / "funding.parquet")
    fund["time"] = fund["time"].dt.floor("h")            # archive stamps carry ms offsets (12:00:00.001); some symbols settle every 4h
    funding = {s: g.groupby("time")["rate"].sum() for s, g in fund.groupby("symbol")}
    uni = json.loads((ROOT / "universe.json").read_text())
    universe = {pd.Timestamp(k, tz="UTC"): v for k, v in uni["mondays"].items()}
    return close, open_, funding, universe


def select_pairs(close: pd.DataFrame, syms: list[str], t: pd.Timestamp, p: Params) -> list[dict]:
    """Formation on the window [t − form_days, t): distance method (main spec) or Engle–Granger cointegration
    (variant a: the n_pairs lowest p-values below coint_p). Correlation filter applies to both."""
    key = (t, p.form_days, p.selection, p.n_pairs, p.corr_min, p.coint_p)
    if USE_CACHE and key in _SEL_CACHE:
        return _SEL_CACHE[key]
    out = _select_pairs(close, syms, t, p)
    if USE_CACHE:
        _SEL_CACHE[key] = out
    return out


def _select_pairs(close: pd.DataFrame, syms: list[str], t: pd.Timestamp, p: Params) -> list[dict]:
    win = close.loc[t - pd.Timedelta(days=p.form_days): t - H, [s for s in syms if s in close.columns]]
    win = win.loc[:, win.notna().mean() >= 0.95].ffill().dropna()
    if len(win) < p.form_days * 24 * 0.9 or win.shape[1] < 2:
        return []
    norm = (win / win.iloc[0]).to_numpy()
    logp = np.log(win.to_numpy())
    rets = np.diff(logp, axis=0)
    corr = np.corrcoef(rets.T)
    n = win.shape[1]
    cand = []
    for i in range(n):
        for j in range(i + 1, n):
            if corr[i, j] < p.corr_min:
                continue
            if p.selection == "coint":
                from statsmodels.tsa.stattools import coint
                pval = float(coint(logp[:, i], logp[:, j], trend="c", autolag=None, maxlag=4)[1])
                if pval >= p.coint_p:
                    continue
                cand.append((pval, i, j))
            else:
                dist = float(np.sum((norm[:, i] - norm[:, j]) ** 2))
                cand.append((dist, i, j))
    cand.sort()
    out = []
    for dist, i, j in cand[: p.n_pairs]:
        x, y = logp[:, j], logp[:, i]                      # regress log A (i) on log B (j)
        beta = float(np.cov(x, y, bias=True)[0, 1] / np.var(x))
        if not (0.2 <= beta <= 5):
            continue
        spread = y - beta * x
        sd = float(spread.std(ddof=1))
        if not sd > 0:
            continue
        out.append({"a": win.columns[i], "b": win.columns[j], "beta": beta, "mu": float(spread.mean()),
                    "sd": sd, "dist": dist, "corr": float(corr[i, j])})
    return out


def run(close: pd.DataFrame, open_: pd.DataFrame, funding: dict, universe: dict, start: str, end: str,
        p: Params, record_events: bool = False) -> Result:
    start_ts, end_ts = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    mondays = [t for t in sorted(universe) if start_ts <= t < end_ts]
    hours_all = close.index
    logc = np.log(close)
    cash = p.start_equity
    positions: dict[tuple, Position] = {}
    trades, events, eq_t, eq_v = [], [], [], []

    def price(frame, sym, t):
        v = frame.at[t, sym] if t in frame.index else np.nan
        return float(v)

    def last_price(sym, t):
        s = close[sym].loc[:t].dropna()
        return float(s.iloc[-1]) if len(s) else np.nan

    def close_position(pos: Position, t_exec: pd.Timestamp, reason: str):
        nonlocal cash
        pa, pb = price(open_, pos.a, t_exec), price(open_, pos.b, t_exec)
        if np.isnan(pa) or np.isnan(pb):                 # no open at t_exec (archive gap, delisting): last valid close
            pa, pb = last_price(pos.a, t_exec), last_price(pos.b, t_exec)
        cost = (p.fee + p.slip) * (pos.units_a * pa + pos.units_b * pb)
        pnl = pos.side * (pos.units_a * (pa - pos.pa) - pos.units_b * (pb - pos.pb)) - cost - pos.funding - pos.costs
        cash += pnl
        trades.append({"a": pos.a, "b": pos.b, "side": pos.side, "entry": pos.entry_time, "exit": t_exec,
                       "hours": (t_exec - pos.entry_time) / H, "entry_z": pos.entry_z, "reason": reason,
                       "pnl": pnl, "funding": pos.funding, "costs": cost + pos.costs, "week": pos.week,
                       "gross": pos.units_a * pos.pa + pos.units_b * pos.pb})
        if record_events:
            events.append(("exit", pos.a, pos.b, t_exec, reason))

    for wi, t in enumerate(mondays):
        week_end = min(mondays[wi + 1] if wi + 1 < len(mondays) else end_ts, end_ts)
        selected = select_pairs(close, universe[t], t, p)
        sel_keys = {(s["a"], s["b"]) for s in selected}
        for key in list(positions):                      # pairs that fell out of the selection: close at Monday open
            if key not in sel_keys:
                close_position(positions.pop(key), t, "deselected")
        params = {(s["a"], s["b"]): s for s in selected}
        hours = hours_all[(hours_all >= t) & (hours_all < week_end)]
        if len(hours) == 0:
            continue
        # z for entries with this week's parameters, computed on closes of the week
        zw = {}
        for key, s in params.items():
            sp = logc.loc[hours, s["a"]] - s["beta"] * logc.loc[hours, s["b"]]
            zw[key] = ((sp - s["mu"]) / s["sd"]).to_numpy()
        hour_pos = {h: i for i, h in enumerate(hours)}
        for h in hours:
            i = hour_pos[h]
            nxt = h + H
            has_next = nxt in open_.index and nxt < end_ts
            # funding whenever a settlement exists for the symbol at this hour (8h or 4h intervals; long pays positive rate)
            if h.hour % 4 == 0:
                for pos in positions.values():
                    ra = funding.get(pos.a, pd.Series(dtype=float)).get(h, 0.0)
                    rb = funding.get(pos.b, pd.Series(dtype=float)).get(h, 0.0)
                    ca, cb = price(close, pos.a, h), price(close, pos.b, h)
                    if not (np.isnan(ca) or np.isnan(cb)):
                        pos.funding += pos.side * (ra * pos.units_a * ca - rb * pos.units_b * cb)
            # exits (own frozen parameters), then entries (this week's parameters)
            for key, pos in list(positions.items()):
                ca, cb = price(close, pos.a, h), price(close, pos.b, h)
                if np.isnan(ca) or np.isnan(cb):
                    continue
                z = (np.log(ca) - pos.beta * np.log(cb) - pos.mu) / pos.sd
                reason = None
                if pos.side == -1 and z <= p.exit_z or pos.side == 1 and z >= -p.exit_z:
                    reason = "converged"
                elif abs(z) >= p.stop_z:
                    reason = "stop_z"
                elif (nxt - pos.entry_time) / H >= p.max_hold_hours:
                    reason = "max_hold"
                if reason and has_next:
                    close_position(positions.pop(key), nxt, reason)
            if has_next:
                equity_now = cash + sum(pos.mtm(price(close, pos.a, h), price(close, pos.b, h)) for pos in positions.values()
                                        if not (np.isnan(price(close, pos.a, h)) or np.isnan(price(close, pos.b, h))))
                for key, s in params.items():
                    if key in positions or len(positions) >= p.n_pairs:
                        continue
                    z = zw[key][i]
                    if np.isnan(z) or not (p.entry_z <= abs(z) < p.stop_z):
                        continue
                    pa, pb = price(open_, s["a"], nxt), price(open_, s["b"], nxt)
                    if np.isnan(pa) or np.isnan(pb):
                        continue
                    gross = p.gross_per_pair * equity_now
                    na, nb = gross / (1 + s["beta"]), gross * s["beta"] / (1 + s["beta"])
                    pos = Position(s["a"], s["b"], -1 if z > 0 else 1, nxt, pa, pb, na / pa, nb / pb,
                                   s["beta"], s["mu"], s["sd"], float(z), costs=(p.fee + p.slip) * gross,
                                   week=t.strftime("%Y-%m-%d"))
                    positions[key] = pos
                    if record_events:
                        events.append(("entry", s["a"], s["b"], nxt, pos.side))
            # mark to market at the close of h
            mtm = 0.0
            for pos in positions.values():
                ca, cb = price(close, pos.a, h), price(close, pos.b, h)
                if not (np.isnan(ca) or np.isnan(cb)):
                    mtm += pos.mtm(ca, cb)
            eq_t.append(h)
            eq_v.append(cash + mtm)
    last = eq_t[-1] + H if eq_t else end_ts
    for key in list(positions):
        close_position(positions.pop(key), last, "end")
    equity = pd.Series(eq_v, index=pd.DatetimeIndex(eq_t), name="equity")
    return Result(equity, pd.DataFrame(trades), p, events)


# ---------------------------------------------------------------- metrics

def metrics(res: Result, btc_daily: pd.Series) -> dict:
    eq = res.equity
    t = res.trades
    daily = eq.resample("1D").last().dropna()
    dd = (daily / daily.cummax() - 1).min() * 100
    r = daily.pct_change().dropna()
    bt = btc_daily.reindex(r.index)
    corr = float(r.corr(bt)) if len(r) > 10 else float("nan")
    out = {"return%": (eq.iloc[-1] / res.params.start_equity - 1) * 100, "maxDD%": dd, "trades": len(t),
           "btc_corr": corr, "days": len(daily)}
    if len(t):
        by_pair = t.groupby(["a", "b"])["pnl"].sum().sort_values(ascending=False)
        out.update({"win%": (t.pnl > 0).mean() * 100, "avg_hours": t.hours.mean(),
                    "pnl": t.pnl.sum(), "funding": -t.funding.sum(), "costs": t.costs.sum(),
                    "pnl_wo_top3": t.pnl.sum() - by_pair.head(3).sum(),
                    "stopped_pnl": t.loc[t.reason == "stop_z", "pnl"].sum(),
                    "reasons": t.reason.value_counts().to_dict(), "top_pairs": {f"{a}/{b}": round(v, 0) for (a, b), v in by_pair.head(3).items()}})
    return out


def monthly_ci(eq: pd.Series, n: int = 5000) -> tuple[float, float, float]:
    m = eq.resample("ME").last().pct_change().dropna() * 100
    rng = np.random.default_rng(0)
    draws = rng.choice(m.to_numpy(), size=(n, len(m)), replace=True).mean(axis=1)
    return float(m.mean()), *map(float, np.percentile(draws, [2.5, 97.5]))


def fmt(name: str, m: dict) -> str:
    extra = ""
    if "win%" in m:
        extra = (f"  win {m['win%']:.0f}%  hold {m['avg_hours']:.0f}h  funding {m['funding']:+.0f}  costs {m['costs']:.0f}"
                 f"  wo_top3 {m['pnl_wo_top3']:+.0f}  stops {m['stopped_pnl']:+.0f}  {m['reasons']}")
    return f"{name:14s} ret {m['return%']:+7.1f}%  maxDD {m['maxDD%']:6.1f}%  trades {m['trades']:4d}  btc_corr {m['btc_corr']:+.2f}{extra}"


def lookahead_check(close, open_, funding, universe, start, end, p, n=50) -> int:
    """Decisions on data truncated at random times must match the full run's decisions before those times."""
    global USE_CACHE
    USE_CACHE = False
    full = run(close, open_, funding, universe, start, end, p, record_events=True)
    rng = np.random.default_rng(1)
    hours = close.index[(close.index > pd.Timestamp(start, tz="UTC") + pd.Timedelta(days=90)) & (close.index < pd.Timestamp(end, tz="UTC"))]
    same = 0
    for T in rng.choice(hours, n, replace=False):
        T = pd.Timestamp(T)
        cut = run(close.loc[:T], open_.loc[:T], funding, universe, start, T.strftime("%Y-%m-%d %H:%M"), p, record_events=True)
        f = [e for e in full.events if e[3] <= T - 2 * H]
        c = [e for e in cut.events if e[3] <= T - 2 * H and e[4] != "end"]
        same += f == c
    USE_CACHE = True
    return same


def main() -> None:
    quick = "quick" in sys.argv[1:]
    variant = "coint" if "coint" in sys.argv[1:] else "distance"
    tag = "" if variant == "distance" else "_coint"
    close, open_, funding, universe = load()
    btc_daily = close["BTCUSDT"].resample("1D").last().pct_change()
    p = Params(selection=variant)
    results = {"variant": variant}
    windows = {"2021-2026": ("2021-01-04", "2026-09-23")}
    if not quick:
        windows.update({str(y): (f"{y}-01-01", f"{y + 1}-01-01") for y in range(2021, 2026)})
        windows.update({"2026": ("2026-01-01", "2026-09-23"), "2021-2023": ("2021-01-04", "2024-01-01"),
                        "2024-2026": ("2024-01-01", "2026-09-23")})
    print("== main spec")
    full = None
    for name, (a, b) in windows.items():
        res = run(close, open_, funding, universe, a, b, p)
        m = metrics(res, btc_daily)
        results[name] = m
        print(fmt(name, m))
        if name == "2021-2026":
            full = res
            mean, lo, hi = monthly_ci(res.equity)
            results["monthly_ci"] = {"mean%": mean, "lo": lo, "hi": hi}
            print(f"  monthly mean {mean:+.2f}%  bootstrap 95% CI [{lo:+.2f}, {hi:+.2f}]")
            res.trades.to_csv(OUT / f"p1_trades_full{tag}.csv", index=False)
            res.equity.to_csv(OUT / f"p1_equity_full{tag}.csv")
    if quick:
        return
    print("\n== fee ×2")
    m = metrics(run(close, open_, funding, universe, "2021-01-04", "2026-09-23", Params(fee=0.001, selection=variant)), btc_daily)
    results["fee2"] = m
    print(fmt("fee2", m))
    print("\n== robustness grid (entry z × formation days), 2021-2026 return")
    grid = {}
    for ez in (1.5, 2.0, 2.5):
        row = []
        for fd in (30, 60, 90):
            r = metrics(run(close, open_, funding, universe, "2021-01-04", "2026-09-23", Params(entry_z=ez, form_days=fd, selection=variant)), btc_daily)
            grid[f"z{ez}_f{fd}"] = r["return%"]
            row.append(f"z{ez} f{fd}: {r['return%']:+7.1f}% (DD {r['maxDD%']:.0f}%, n {r['trades']})")
        print("  " + " | ".join(row))
    results["grid"] = grid
    print("\n== look-ahead self-check")
    same = lookahead_check(close, open_, funding, universe, "2021-01-04", "2026-09-23", p, n=50)
    results["lookahead_same"] = same
    print(f"  {same}/50 truncated reruns identical")
    (OUT / f"p1_results{tag}.json").write_text(json.dumps(results, indent=1, default=str))


if __name__ == "__main__":
    main()
