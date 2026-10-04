"""Frozen R7 price screen; separated collateral, matched base, individual expiry."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r6_relative import PerpLedger
from r7_data import expiry

HERE = Path(__file__).resolve().parent
DATA = HERE / "r7_data"
ASSETS = ["BTCUSDT", "ETHUSDT"]
WINDOWS = [("early_reused", "2021-02-01", "2023-12-31"),
           ("recent_reused", "2024-01-01", "2026-08-31")]
LISTED = {"BTCUSDT_210326": pd.Timestamp("2021-02-03 07:00", tz="UTC"),
          "ETHUSDT_210326": pd.Timestamp("2021-02-04 07:00", tz="UTC")}


class CarryLedger:
    def __init__(self, capital):
        self.initial = capital
        self.spot_cash = capital / 2
        self.spot_quantity = np.zeros(2)
        self.spot_entry = np.zeros(2)
        self.future = PerpLedger(capital / 2)
        self.fees = self.price_pnl = 0.

    def equity(self, spot, future):
        return self.spot_cash + self.spot_quantity @ np.asarray(spot) + self.future.equity(future)

    def repool(self):
        if self.spot_quantity.any() or self.future.quantity.any():
            raise ValueError("Spot gains cannot provide futures margin while held")
        equity = self.spot_cash + self.future.balance
        if equity <= 0:
            raise ValueError("Insolvent total collateral")
        self.spot_cash = equity / 2
        self.future.balance = equity / 2

    def open(self, quantity, spot, future, cost):
        quantity, spot, future = map(lambda v: np.asarray(v, float), (quantity, spot, future))
        if self.spot_quantity.any() or self.future.quantity.any() or (quantity < 0).any():
            raise ValueError("Only a fresh matched-base carry entry is allowed")
        bought = quantity @ spot
        if bought * (1 + cost) > self.spot_cash + 1e-8 or quantity @ future * (1 + cost) > self.future.balance + 1e-8:
            raise ValueError("Separate1x sleeve collateral exceeded")
        self.spot_cash -= bought * (1 + cost)
        self.spot_quantity = quantity.copy()
        self.spot_entry = spot.copy()
        fee, _, pnl = self.future.trade_to(-quantity, future, cost)
        self.fees += bought * cost + fee
        self.price_pnl += pnl

    def close(self, spot, future, cost):
        spot, future = np.asarray(spot), np.asarray(future)
        sold = self.spot_quantity @ spot
        self.price_pnl += self.spot_quantity @ (spot - self.spot_entry)
        self.spot_cash += sold * (1 - cost)
        fee, _, pnl = self.future.trade_to([0., 0.], future, cost)
        self.fees += sold * cost + fee
        self.price_pnl += pnl
        self.spot_quantity[:] = 0.
        self.spot_entry[:] = 0.


def cycles(start, end):
    begin, finish = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    if begin.weekday() != 0:
        raise ValueError("The frozen window must start on Monday")
    expirations = []
    for year in range(begin.year, finish.year + 2):
        for month in (3, 6, 9, 12):
            last = pd.Timestamp(year=year, month=month, day=1, tz="UTC") + pd.offsets.MonthEnd(0)
            friday = last - pd.Timedelta(days=(last.weekday() - 4) % 7) + pd.Timedelta(hours=8)
            if friday > begin:
                expirations.append(friday)
    entry, out = begin, []
    for exp in expirations:
        if entry > finish:
            break
        out.append((entry, exp))
        entry = exp.floor("D") + pd.Timedelta(days=3)
    return out


def finite_price(frame, date, column, context):
    if date not in frame.index:
        raise ValueError("Missing " + context + " price")
    value = float(frame.loc[date, column])
    if not np.isfinite(value) or value <= 0:
        raise ValueError("Invalid " + context + " price")
    return value


def simulate(spot, dated, start, end, cost, allowed=None, terminal=True):
    allowed = ASSETS if allowed is None else allowed
    days = pd.date_range(start, end, tz="UTC", freq="D").as_unit("ns")
    schedule = dict(cycles(start, end))
    book = CarryLedger(10000.)
    held_symbols = [None, None]
    close_day = None
    rows, events = [], []
    for day in days:
        sopen = np.array([finite_price(spot[a], day, "open", "spot") for a in ASSETS])
        sclose = np.array([finite_price(spot[a], day, "close", "spot") for a in ASSETS])
        entry_gross = 0.
        if day in schedule:
            if book.spot_quantity.any():
                raise AssertionError("Overlapping expiry cycles")
            book.repool()
            exp = schedule[day]
            close_day = exp.floor("D") - pd.Timedelta(days=1)
            if terminal:
                close_day = min(close_day, days[-1])
            suffix = exp.strftime("_%y%m%d")
            quantity = np.zeros(2)
            fopen = np.ones(2)
            decisions = []
            sunday = day - pd.Timedelta(days=1)
            for k, asset in enumerate(ASSETS):
                if asset not in allowed:
                    decisions.append("diagnostic_excluded")
                    continue
                symbol = asset + suffix
                if symbol in LISTED and LISTED[symbol] > sunday + pd.Timedelta(days=1):
                    decisions.append("confirmed_not_listed_at_decision")
                    continue
                frame = dated.get(symbol)
                if frame is None:
                    raise ValueError("Unreviewed registered delivery contract " + symbol)
                prior_future = finite_price(frame, sunday, "close", "decision")
                prior_spot = finite_price(spot[asset], sunday, "close", "decision")
                basis = prior_future / prior_spot - 1
                if basis <= .0048:
                    decisions.append("below_fixed_four_fill_hurdle")
                    continue
                if "trades" in frame and (frame.loc[sunday, "trades"] <= 0 or (day in frame.index and frame.loc[day,"trades"] <= 0)):
                    raise ValueError("Zero-trade delivery decision/entry row")
                fopen[k] = finite_price(frame, day, "open", "entry")
                weight = 1 / len(allowed)
                quantity[k] = .99 * min(book.spot_cash * weight / (sopen[k] * (1 + cost)),
                                         book.future.balance * weight / (fopen[k] * (1 + cost)))
                held_symbols[k] = symbol
                decisions.append("enter")
            book.open(quantity, sopen, fopen, cost)
            value = book.equity(sopen, fopen)
            entry_gross = quantity @ (sopen + fopen) / value
            if entry_gross > .99 + 1e-8 or not np.array_equal(book.spot_quantity, -book.future.quantity):
                raise AssertionError("Post-fee budget or matched-base quantity violated")
            events.append({"date": day.isoformat(), "expiry": exp.isoformat(),
                           "entered": decisions, "quantities": quantity.tolist(), "entry_gross": entry_gross})
        fclose, fhigh = np.ones(2), np.ones(2)
        for k, symbol in enumerate(held_symbols):
            if symbol is not None and book.spot_quantity[k] != 0:
                frame = dated[symbol]
                fclose[k] = finite_price(frame, day, "close", "active contract")
                fhigh[k] = finite_price(frame, day, "high", "active contract")
        before = book.equity(sclose, fclose)
        future_marked = book.future.equity(fclose)
        future_adverse = book.future.equity(fhigh)
        future_gross_adverse = np.abs(book.future.quantity) @ fhigh
        concern = bool(book.spot_quantity.any() and future_adverse <= .005 * future_gross_adverse)
        gross = book.spot_quantity @ sclose + np.abs(book.future.quantity) @ fclose
        if day == close_day:
            book.close(sclose, fclose, cost)
            held_symbols = [None, None]
        value = book.equity(sclose, fclose)
        rows.append((day, value, before, book.spot_cash, book.future.balance, future_marked,
                     future_adverse, concern, gross / before if before > 0 else np.inf,
                     entry_gross, book.fees, book.price_pnl))
    frame = pd.DataFrame(rows, columns=["date", "equity", "pre_close_equity", "spot_cash", "future_balance",
              "future_marked_equity", "future_adverse_equity", "unresolved_margin_concern", "gross",
              "entry_gross", "cumulative_fees", "cumulative_price_pnl"]).set_index("date")
    if terminal:
        if book.spot_quantity.any() or book.future.quantity.any():
            raise AssertionError("Missing terminal closing costs")
        gap = frame.equity.iloc[-1] - (book.initial + book.price_pnl - book.fees)
        if abs(gap) > 1e-6:
            raise AssertionError("Carry final ledger does not reconcile")
    return frame, events


def metrics(frame, events):
    equity = np.r_[10000., frame.equity.to_numpy()]
    cagr = ((equity[-1] / 10000) ** (365 / len(frame)) - 1) * 100 if equity[-1] > 0 else -100.
    dd = (equity / np.maximum.accumulate(equity) - 1).min() * 100
    yearly, prior = {}, 10000.
    for year, group in frame.groupby(frame.index.year):
        yearly[str(year)] = float((group.equity.iloc[-1] / prior - 1) * 100)
        prior = group.equity.iloc[-1]
    return {"CAGR_pct": cagr, "return_pct": float((equity[-1]/10000-1)*100), "daily_trade_price_DD_pct": float(dd),
            "unresolved_margin_days": int(frame.unresolved_margin_concern.sum()),
            "min_future_adverse_equity": float(frame.future_adverse_equity.min()),
            "max_gross_pct": float(frame.gross.max()*100),
            "fees_USDT": float(frame.cumulative_fees.iloc[-1]),
            "realized_price_pnl_USDT": float(frame.cumulative_price_pnl.iloc[-1]),
            "ledger_reconciliation_USDT": float(equity[-1] - (10000 + frame.cumulative_price_pnl.iloc[-1] - frame.cumulative_fees.iloc[-1])),
            "entered_asset_cycles": sum(e['entered'].count('enter') for e in events),
            "calendar_year_return_pct": yearly,
            "necessary_screen_pass": bool(cagr > 12 and dd >= -5 and not frame.unresolved_margin_concern.any()),
            "not_risk_certified": True}


def main():
    manifest = json.loads((DATA / "manifest.json").read_text())
    for name, expected in manifest["output_sha256"].items():
        if hashlib.sha256((DATA/name).read_bytes()).hexdigest() != expected:
            raise ValueError("R7 frozen price input changed")
    spot = {a: pd.read_parquet(DATA/f"{a}_spot.parquet") for a in ASSETS}
    dated = {p.stem: pd.read_parquet(p) for p in DATA.glob("*USDT_*.parquet")}
    results, decisions = {}, {}
    for label,start,end in WINDOWS:
        for cost in (.0012,.0024):
            for name,allowed in (("equal_pair",ASSETS),("BTC_control",["BTCUSDT"]),("ETH_control",["ETHUSDT"])):
                curve, events = simulate(spot,dated,start,end,cost,allowed)
                key=f"{label}_{name}_cost{cost}"
                result=metrics(curve,events)
                results[key],decisions[key]=result,events
                curve.to_csv(HERE/f"r7_{key}_daily.csv")
                print(key,{k:round(result[k],4) for k in ('CAGR_pct','daily_trade_price_DD_pct','fees_USDT','min_future_adverse_equity')},'margin_concern_days',result['unresolved_margin_days'],'necessary_pass',result['necessary_screen_pass'],flush=True)
    sources={n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in ('R7_PROTOCOL.md','r7_data.py','r7_dated.py','test_r7_data.py','test_r7_engine.py','r6_relative.py')}
    (HERE/'r7_results.json').write_text(json.dumps({'captured_utc':datetime.now(timezone.utc).isoformat(),
        'source_sha256':sources,'input_manifest':manifest,'results':results,'decisions':decisions,
        'limitations':'Necessary traded-price profitability/collateral screen only; separated wallets, no cash yield or perpetual funding, price envelopes are not exact mark liquidations. Reused history, no activation.'},indent=2)+'\n')


if __name__ == '__main__':
    main()
