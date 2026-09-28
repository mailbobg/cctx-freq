"""X14 backtest: long the flushed altcoins, short BTC, exit after a fixed hold (spec: specs/2026-09-28-x14-hedged-flush-design.md).

Data: user_data/data_x14/5m/<sym>.parquet (x14_fetch.py), funding from user_data/data_p1/funding.parquet, weekly
universe from data_x14/universe.json. Every decision uses closed 5m candles; every fill is the next candle's open.

Usage:
  python user_data/bench/x14_flush.py            # full suite: years, halves, full, fee×2, grid, look-ahead check
  python user_data/bench/x14_flush.py quick      # 2021-2026 only
"""

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("user_data/data_x14")
P1 = Path("user_data/data_p1")
OUT = Path("user_data/bench/events")
BTC = "BTCUSDT"
LOOKBACK, COOLDOWN = 12, 48                    # 1 h of 5m candles; 4 h cooldown after a flush
CANDLE = pd.Timedelta(minutes=5)


@dataclass
class Params:
    btc_drop: float = -0.005
    alt_drop: float = -0.04
    breadth_frac: float = 0.10                 # of the weekly universe, minimum 6 (10 % of 60)
    breadth_min: int = 6
    n_coins: int = 5
    weight: float = 0.04                       # of equity per long leg
    hold: int = 288                            # candles (24 h)
    stop: float = -0.25                        # disaster stop on a long leg
    max_episodes: int = 2
    hedge: str = "dollar"                      # "dollar" (main) or "beta" (variant a)
    fee: float = 0.0005
    slip: float = 0.0002
    start_equity: float = 10_000.0


@dataclass
class Leg:
    sym: str
    col: int
    side: int                                  # +1 long alt, −1 short BTC
    units: float
    entry_px: float
    notional: float
    open: bool = True
    funding: float = 0.0
    pnl: float = 0.0
    reason: str = ""


@dataclass
class Episode:
    eid: int
    fire_row: int
    entry_row: int
    exit_row: int
    legs: list = field(default_factory=list)
    costs: float = 0.0


def load():
    files = sorted((ROOT / "5m").glob("*.parquet"))
    idx = pd.date_range("2020-10-01", "2026-09-23", freq="5min", tz="UTC")
    C = np.full((len(idx), len(files)), np.nan, dtype=np.float32)
    Op = np.full((len(idx), len(files)), np.nan, dtype=np.float32)
    base = idx.as_unit("ns").asi8[0]       # parquet stamps are ms, date_range gives µs: compare in ns explicitly
    step = 5 * 60 * 10**9
    for j, f in enumerate(files):
        d = pd.read_parquet(f)
        ns = pd.DatetimeIndex(d["date"]).as_unit("ns").asi8
        pos = (ns - base) // step
        ok = (pos >= 0) & (pos < len(idx)) & ((ns - base) % step == 0)
        C[pos[ok], j] = d["close"].to_numpy()[ok]
        Op[pos[ok], j] = d["open"].to_numpy()[ok]
    cols = [f.stem for f in files]
    close = pd.DataFrame(C, index=idx, columns=cols)
    open_ = pd.DataFrame(Op, index=idx, columns=cols)
    fund = pd.read_parquet(P1 / "funding.parquet")
    fund["time"] = fund["time"].dt.floor("h")
    funding = {s: g.groupby("time")["rate"].sum().to_dict() for s, g in fund.groupby("symbol")}
    uni = json.loads((ROOT / "universe.json").read_text())
    universe = {pd.Timestamp(k, tz="UTC"): v for k, v in uni["mondays"].items()}
    return close, open_, funding, universe


class Engine:
    def __init__(self, close: pd.DataFrame, open_: pd.DataFrame, funding: dict, universe: dict):
        self.idx = close.index
        self.C = close.to_numpy(np.float32)
        self.O = open_.to_numpy(np.float32)
        self.logC = np.log(self.C)
        self.col = {s: j for j, s in enumerate(close.columns)}
        self.funding = funding
        self.universe = universe
        self.hour_rows = np.flatnonzero((self.idx.minute == 0))
        self.is_hour = (self.idx.minute == 0)

    def beta(self, j: int, row: int) -> float:
        """30-day hourly-return beta of column j to BTC, from data before `row` (variant a)."""
        a = max(0, row - 30 * 288)
        h = self.hour_rows[(self.hour_rows >= a) & (self.hour_rows <= row)]
        x = np.diff(self.logC[h, self.col[BTC]])
        y = np.diff(self.logC[h, j])
        ok = np.isfinite(x) & np.isfinite(y)
        if ok.sum() < 200 or np.var(x[ok]) == 0:
            return 1.0
        b = float(np.cov(x[ok], y[ok], bias=True)[0, 1] / np.var(x[ok]))
        return float(np.clip(b, 0.5, 3.0))

    def run(self, start: str, end: str, p: Params, record_events: bool = False):
        idx = self.idx
        s_row = idx.searchsorted(pd.Timestamp(start, tz="UTC"))
        e_row = min(idx.searchsorted(pd.Timestamp(end, tz="UTC")), len(idx) - 1)
        jb = self.col[BTC]
        mondays = sorted(t for t in self.universe if idx[s_row] <= t < idx[e_row])
        if not mondays or mondays[0] > idx[s_row]:
            prev = [t for t in sorted(self.universe) if t <= idx[s_row]]
            mondays = ([prev[-1]] if prev else []) + mondays
        cash = p.start_equity
        episodes: list[Episode] = []
        open_eps: list[Episode] = []
        eq_rows, eq_vals, events, legs_log = [], [], [], []
        last_fire = -10**9
        eid = 0

        def mark(row):
            v = 0.0
            for ep in open_eps:
                for lg in ep.legs:
                    if lg.open:
                        px = self.C[row, lg.col]
                        if np.isfinite(px):
                            v += lg.side * lg.units * (px - lg.entry_px) - lg.funding
            return cash + v

        def close_leg(ep, lg, row, reason):
            nonlocal cash
            px = self.O[row, lg.col]
            if not np.isfinite(px):
                past = self.C[:row, lg.col]
                past = past[np.isfinite(past)]
                px = past[-1] if len(past) else lg.entry_px
            cost = (p.fee + p.slip) * lg.units * px
            lg.pnl = lg.side * lg.units * (px - lg.entry_px) - lg.funding - cost - (p.fee + p.slip) * lg.notional
            lg.open, lg.reason = False, reason
            cash += lg.pnl
            legs_log.append({"eid": ep.eid, "sym": lg.sym, "side": lg.side, "entry": idx[ep.entry_row], "exit": idx[row],
                             "notional": lg.notional, "pnl": lg.pnl, "funding": lg.funding, "reason": reason})
            if record_events:
                events.append(("exit", ep.eid, lg.sym, row, reason))

        for wi, t in enumerate(mondays):
            a = max(idx.searchsorted(t), s_row)
            b = idx.searchsorted(mondays[wi + 1]) if wi + 1 < len(mondays) else e_row
            b = min(b, e_row)
            if b <= a:
                continue
            cols = [self.col[s] for s in self.universe[t] if s in self.col]
            need = max(p.breadth_min, int(np.ceil(p.breadth_frac * len(cols))))
            lo = a - LOOKBACK
            r_alt = self.logC[a:b, cols] - self.logC[lo:b - LOOKBACK, cols]
            r_btc = self.logC[a:b, jb] - self.logC[lo:b - LOOKBACK, jb]
            x = r_alt - r_btc[:, None]
            dropped = x <= p.alt_drop
            breadth = np.nansum(dropped, axis=1)
            cand = (r_btc <= p.btc_drop) & (breadth >= need)
            for i in range(b - a):
                row = a + i
                if not open_eps and not cand[i]:
                    if self.is_hour[row]:                     # flat: equity = cash, recorded hourly
                        eq_rows.append(row)
                        eq_vals.append(cash)
                    continue
                ts = idx[row]
                # funding settlements on open legs (hourly stamps; 8h or 4h intervals)
                if ts.minute == 0 and ts.hour % 4 == 0 and open_eps:
                    for ep in open_eps:
                        for lg in ep.legs:
                            if lg.open:
                                rate = self.funding.get(lg.sym, {}).get(ts, 0.0)
                                if rate:
                                    px = self.C[row, lg.col]
                                    if np.isfinite(px):
                                        lg.funding += lg.side * rate * lg.units * px
                # exits decided on this closed candle, filled at the next open
                nxt = row + 1
                if nxt >= e_row:
                    break
                for ep in list(open_eps):
                    for lg in ep.legs:
                        if not lg.open:
                            continue
                        if lg.side == 1:
                            px = self.C[row, lg.col]
                            if np.isfinite(px) and px / lg.entry_px - 1 <= p.stop:
                                close_leg(ep, lg, nxt, "stop")
                                hedge = next((h for h in ep.legs if h.side == -1 and h.open), None)
                                if hedge:                                  # shrink the hedge by this leg's share
                                    share = lg.notional / sum(l.notional for l in ep.legs if l.side == 1)
                                    part = Leg(hedge.sym, hedge.col, -1, hedge.units * share, hedge.entry_px,
                                               hedge.notional * share, funding=hedge.funding * share)
                                    hedge.units -= part.units
                                    hedge.notional -= part.notional
                                    hedge.funding -= part.funding
                                    close_leg(ep, part, nxt, "stop_hedge")
                    if nxt >= ep.exit_row:
                        for lg in ep.legs:
                            if lg.open:
                                close_leg(ep, lg, nxt, "time")
                    if not any(lg.open for lg in ep.legs):
                        open_eps.remove(ep)
                # entry on a flush
                if cand[i] and row - last_fire >= COOLDOWN:
                    last_fire = row
                    if record_events:
                        events.append(("fire", row))
                    if len(open_eps) < p.max_episodes:
                        xi = x[i]
                        order = np.argsort(np.where(np.isfinite(xi) & dropped[i], xi, np.inf))
                        picks = [cols[k] for k in order[: p.n_coins] if dropped[i][k] and np.isfinite(self.O[nxt, cols[k]])]
                        if picks and np.isfinite(self.O[nxt, jb]):
                            equity_now = mark(row)
                            eid += 1
                            ep = Episode(eid, row, nxt, nxt + p.hold)
                            total_long = 0.0
                            hedge_notional = 0.0
                            for j in picks:
                                notional = p.weight * equity_now
                                px = self.O[nxt, j]
                                ep.legs.append(Leg(self._sym(j), j, 1, notional / px, px, notional))
                                total_long += notional
                                hedge_notional += notional * (self.beta(j, row) if p.hedge == "beta" else 1.0)
                            pxb = self.O[nxt, jb]
                            ep.legs.append(Leg(BTC, jb, -1, hedge_notional / pxb, pxb, hedge_notional))
                            episodes.append(ep)
                            open_eps.append(ep)
                            if record_events:
                                events.append(("entry", eid, nxt, tuple(self._sym(j) for j in picks)))
                eq_rows.append(row)
                eq_vals.append(mark(row) if open_eps else cash)
        last = min(e_row, len(idx) - 1)
        for ep in list(open_eps):
            for lg in ep.legs:
                if lg.open:
                    close_leg(ep, lg, last, "end")
        eq_rows.append(last)
        eq_vals.append(cash)
        equity = pd.Series(eq_vals, index=idx[eq_rows]).groupby(level=0).last()
        legs = pd.DataFrame(legs_log)
        eps = self._episodes(episodes, legs)
        return {"equity": equity, "legs": legs, "episodes": eps, "events": events, "params": p}

    def _sym(self, j):
        return list(self.col)[j]

    @staticmethod
    def _episodes(episodes, legs):
        if legs.empty:
            return pd.DataFrame()
        g = legs.groupby("eid")
        out = pd.DataFrame({
            "entry": g["entry"].first(), "exit": g["exit"].max(),
            "pnl": g["pnl"].sum(),
            "pnl_long": legs[legs.side == 1].groupby("eid")["pnl"].sum(),
            "pnl_btc": legs[legs.side == -1].groupby("eid")["pnl"].sum(),
            "funding": -g["funding"].sum(),
            "stops": legs[legs.reason == "stop"].groupby("eid").size(),
            "notional": legs[legs.side == 1].groupby("eid")["notional"].sum(),
        }).fillna(0.0)
        return out


# ---------------------------------------------------------------- metrics

def metrics(res: dict, btc_daily: pd.Series, p: Params) -> dict:
    eq = res["equity"]
    eps = res["episodes"]
    daily = eq.resample("1D").last().dropna()
    hourly = eq.resample("1h").last().ffill()
    dd = (hourly / hourly.cummax() - 1).min() * 100
    r = daily.pct_change().dropna()
    corr = float(r.corr(btc_daily.reindex(r.index))) if len(r) > 10 else float("nan")
    out = {"return%": (eq.iloc[-1] / p.start_equity - 1) * 100, "maxDD%": dd, "episodes": len(eps), "btc_corr": corr}
    if len(eps):
        out.update({"win%": (eps.pnl > 0).mean() * 100, "pnl": eps.pnl.sum(), "pnl_long": eps.pnl_long.sum(),
                    "pnl_btc": eps.pnl_btc.sum(), "funding": eps.funding.sum(),
                    "per_episode_pct": (eps.pnl / eps.notional).mean() * 100,
                    "wo_top3": eps.pnl.sum() - eps.pnl.nlargest(3).sum(), "stopped_legs": int(eps.stops.sum())})
    return out


def monthly_ci(eq: pd.Series, n: int = 5000):
    m = eq.resample("ME").last().pct_change().dropna() * 100
    rng = np.random.default_rng(0)
    draws = rng.choice(m.to_numpy(), size=(n, len(m)), replace=True).mean(axis=1)
    return float(m.mean()), *map(float, np.percentile(draws, [2.5, 97.5]))


def fmt(name, m):
    extra = ""
    if "win%" in m:
        extra = (f"  win {m['win%']:.0f}%  long {m['pnl_long']:+.0f}  btc {m['pnl_btc']:+.0f}  funding {m['funding']:+.0f}"
                 f"  per_ep {m['per_episode_pct']:+.2f}%  wo_top3 {m['wo_top3']:+.0f}  stops {m['stopped_legs']}")
    return f"{name:10s} ret {m['return%']:+7.1f}%  maxDD {m['maxDD%']:6.1f}%  episodes {m['episodes']:4d}  btc_corr {m['btc_corr']:+.2f}{extra}"


def lookahead_check(eng: Engine, start, end, p, n=50, min_days=90) -> int:
    full = eng.run(start, end, p, record_events=True)
    rng = np.random.default_rng(1)
    rows = np.arange(eng.idx.searchsorted(pd.Timestamp(start, tz="UTC")) + min_days * 288, eng.idx.searchsorted(pd.Timestamp(end, tz="UTC")))
    same = 0
    for T in rng.choice(rows, n, replace=False):
        cut = Engine(pd.DataFrame(eng.C[:T], index=eng.idx[:T], columns=list(eng.col)),
                     pd.DataFrame(eng.O[:T], index=eng.idx[:T], columns=list(eng.col)), eng.funding, eng.universe)
        c = cut.run(start, str(eng.idx[T - 1]), p, record_events=True)
        f_ev = [e for e in full["events"] if (e[1] if e[0] == "fire" else e[2] if e[0] == "entry" else e[3]) <= T - 3]
        c_ev = [e for e in c["events"] if (e[1] if e[0] == "fire" else e[2] if e[0] == "entry" else e[3]) <= T - 3 and e[-1] != "end"]
        same += f_ev == c_ev
    return same


def main():
    quick = "quick" in sys.argv[1:]
    hedge = "beta" if "beta" in sys.argv[1:] else "dollar"
    tag = "" if hedge == "dollar" else "_beta"
    close, open_, funding, universe = load()
    eng = Engine(close, open_, funding, universe)
    btc_daily = close[BTC].resample("1D").last().pct_change()
    p = Params(hedge=hedge)
    results = {"hedge": hedge}
    windows = {"2021-2026": ("2021-01-04", "2026-09-23")}
    if not quick:
        windows.update({str(y): (f"{y}-01-01", f"{y + 1}-01-01") for y in range(2021, 2026)})
        windows.update({"2026": ("2026-01-01", "2026-09-23"), "2021-2023": ("2021-01-04", "2024-01-01"),
                        "2024-2026": ("2024-01-01", "2026-09-23")})
    print(f"== X14 ({hedge} hedge)", flush=True)
    for name, (a, b) in windows.items():
        res = eng.run(a, b, p)
        m = metrics(res, btc_daily, p)
        results[name] = m
        print(fmt(name, m), flush=True)
        if name == "2021-2026":
            mean, lo, hi = monthly_ci(res["equity"])
            results["monthly_ci"] = {"mean%": mean, "lo": lo, "hi": hi}
            print(f"  monthly mean {mean:+.2f}%  bootstrap 95% CI [{lo:+.2f}, {hi:+.2f}]", flush=True)
            res["episodes"].to_csv(OUT / f"x14_episodes{tag}.csv")
            res["legs"].to_csv(OUT / f"x14_legs{tag}.csv", index=False)
            res["equity"].to_csv(OUT / f"x14_equity{tag}.csv")
    if quick:
        return
    print("== fee ×2", flush=True)
    m = metrics(eng.run("2021-01-04", "2026-09-23", Params(hedge=hedge, fee=0.001)), btc_daily, p)
    results["fee2"] = m
    print(fmt("fee2", m), flush=True)
    print("== grid: breadth fraction × hold, 2021-2026 return", flush=True)
    grid = {}
    for bf in (0.05, 0.10, 0.15):
        row = []
        for hold in (144, 288, 576):
            r = metrics(eng.run("2021-01-04", "2026-09-23", Params(hedge=hedge, breadth_frac=bf, breadth_min=max(3, int(np.ceil(bf * 60))), hold=hold)), btc_daily, p)
            grid[f"b{bf}_h{hold}"] = r["return%"]
            row.append(f"b{bf:.2f} h{hold // 12}h: {r['return%']:+6.1f}% (DD {r['maxDD%']:.0f}%, n {r['episodes']})")
        print("  " + " | ".join(row), flush=True)
    results["grid"] = grid
    print("== look-ahead self-check", flush=True)
    same = lookahead_check(eng, "2021-01-04", "2026-09-23", p, n=50)
    results["lookahead_same"] = same
    print(f"  {same}/50 truncated reruns identical", flush=True)
    (OUT / f"x14_results{tag}.json").write_text(json.dumps(results, indent=1, default=str))


if __name__ == "__main__":
    main()
