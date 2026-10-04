"""One frozen relative-trend trial, with signed perpetual and funding accounting."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from r1_r2b_risk import cap_weights, risk_weights, signals, RiskHoldR2b

HERE = Path(__file__).resolve().parent
DATA = HERE / "r6_data"
ASSETS = ["BTCUSDT", "ETHUSDT"]
WINDOWS = [("early_reused", "2021-02-01", "2023-12-31"),
           ("recent_reused", "2024-01-01", "2026-08-31")]


@dataclass
class PerpLedger:
    balance: float
    quantity: np.ndarray = field(default_factory=lambda: np.zeros(2))
    entry: np.ndarray = field(default_factory=lambda: np.zeros(2))

    def equity(self, mark):
        return self.balance + self.quantity @ (np.asarray(mark) - self.entry)

    def trade_to(self, target, price, cost):
        target, price = np.asarray(target, float), np.asarray(price, float)
        delta = target - self.quantity
        turnover = np.abs(delta) @ price
        realized = 0.
        for i, new in enumerate(target):
            old = self.quantity[i]
            if old and (new * old <= 0 or abs(new) < abs(old)):
                closed = min(abs(delta[i]), abs(old))
                realized += np.sign(old) * closed * (price[i] - self.entry[i])
            if not new:
                self.entry[i] = 0.
            elif new * old <= 0:
                self.entry[i] = price[i]
            elif abs(new) > abs(old):
                self.entry[i] = (abs(old) * self.entry[i] + (abs(new) - abs(old)) * price[i]) / abs(new)
        fee = turnover * cost
        self.balance += realized - fee
        self.quantity = target.copy()
        return fee, turnover, realized

    def fund(self, price, rate):
        payment = -self.quantity @ (np.asarray(price) * np.asarray(rate))
        self.balance += payment
        return payment


def bounded_target(book, requested, trade, mark, cost):
    """Scale both requested legs jointly using post-fill marked collateral."""
    requested, trade, mark = map(lambda x: np.asarray(x, float), (requested, trade, mark))
    before = book.equity(mark)
    if before <= 0:
        raise ValueError("Insolvent collateral before rebalance")

    def feasible(scale):
        target = requested * scale
        delta = target - book.quantity
        after = before + delta @ (mark - trade) - np.abs(delta) @ trade * cost
        return after > 0 and np.abs(target) @ mark <= .99 * after

    if feasible(1.):
        return requested
    if not feasible(0.):
        raise ValueError("Closing costs exhaust collateral")
    low, high = 0., 1.
    for _ in range(50):
        mid = (low + high) / 2
        if feasible(mid):
            low = mid
        else:
            high = mid
    return requested * low


def funding_marks(associated, quantity, rate, opened, high, low, sensitivity):
    associated = np.asarray(associated, float)
    if sensitivity == "central":
        replacement = np.asarray(opened, float)
    elif sensitivity in ("adverse", "favorable"):
        paying = np.asarray(quantity) * np.asarray(rate) >= 0
        use_high = paying if sensitivity == "adverse" else ~paying
        replacement = np.where(use_high, high, low)
    else:
        raise ValueError("Unknown settlement sensitivity")
    return np.where(np.isnan(associated), replacement, associated)


def weights(close, mechanism):
    close = close.sort_index().asfreq("D")
    if mechanism == "cov10_long":
        px = close.rename(columns={"BTCUSDT": "BTC_close", "ETHUSDT": "ETH_close"})
        return risk_weights(px, signals(px, RiskHoldR2b)).set_axis(ASSETS, axis=1)
    if mechanism not in ("relative30", "fixed_pair"):
        raise ValueError("Unregistered mechanism")
    direction = np.sign(np.log(close.iloc[:, 0] / close.iloc[:, 1]).diff(30))
    if mechanism == "fixed_pair":
        direction[:] = 1.
    covariance = np.log(close).diff().rolling(30).cov() * 365
    out = pd.DataFrame(0., index=close.index, columns=ASSETS)
    for day in close.index:
        raw = np.array([.5, -.5]) * direction.loc[day]
        matrix = covariance.loc[day].reindex(index=ASSETS, columns=ASSETS).to_numpy()
        out.loc[day] = cap_weights(raw, matrix, vol_target=.10, gross_cap=1.)
    return out


def simulate(trade, mark, fund, target_weights, start, end, cost,
             mechanism="relative30", sensitivity="central", terminal=True):
    begin = pd.Timestamp(start, tz="UTC")
    finish = pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    hours = pd.date_range(begin, finish, freq="1h", inclusive="left").as_unit("ns")
    days = pd.date_range(begin, finish, freq="D", inclusive="left").as_unit("ns")
    marks = np.stack([mark[a].reindex(hours)[["open", "high", "low", "close"]].to_numpy(float)
                      for a in ASSETS], axis=2)
    traded = np.stack([trade[a].reindex(days)[["open", "close"]].to_numpy(float) for a in ASSETS], axis=2)
    if not np.isfinite(marks).all() or not np.isfinite(traded).all() or min(marks.min(), traded.min()) <= 0:
        raise ValueError("Unreviewed price input")
    rates, associated = [], []
    required_slots = hours[hours.hour % 8 == 0]
    for asset in ASSETS:
        events = fund[asset].set_index("slot")
        if not events.index.is_unique or not required_slots.isin(events.index).all():
            raise ValueError("Missing/repeated funding slot")
        selected = events.reindex(required_slots)
        if not np.isfinite(selected.rate.to_numpy()).all():
            raise ValueError("Unreviewed funding rate")
        rates.append(events.rate.reindex(hours).to_numpy())
        associated.append(events.mark.reindex(hours).to_numpy())
    rates, associated = np.stack(rates, axis=1), np.stack(associated, axis=1)
    desired = target_weights.reindex(days - pd.Timedelta(days=1)).to_numpy(float)
    if not np.isfinite(desired).all():
        raise ValueError("Unknown signal weights")
    book = PerpLedger(10000.)
    hour_rows, day_rows = [], []
    day_funding = day_fees = day_turnover = day_realized = 0.
    for i, timestamp in enumerate(hours):
        d = i // 24
        opened, high, low, closed = marks[i]
        payment = fees = turnover = realized = 0.
        # Payment on existing quantities precedes every Monday decision/fill.
        if timestamp.hour % 8 == 0:
            settlement = funding_marks(associated[i], book.quantity, rates[i], opened, high, low, sensitivity)
            payment = book.fund(settlement, rates[i])
        opening_equity = book.equity(opened)
        opening_gross = np.abs(book.quantity) @ opened
        opening_breach = opening_equity <= .005 * opening_gross
        if timestamp.hour == 0 and timestamp.weekday() == 0:
            request = desired[d] * .99 * opening_equity / traded[d, 0]
            if mechanism == "cov10_long":
                diff_notional = (request - book.quantity) * traded[d, 0]
                active = (book.quantity > 1e-12) & (desired[d] > 0)
                request[active & (np.abs(diff_notional) <= .05 * .99 * opening_equity)] = book.quantity[active & (np.abs(diff_notional) <= .05 * .99 * opening_equity)]
            target = bounded_target(book, request, traded[d, 0], opened, cost)
            fees, turnover, realized = book.trade_to(target, traded[d, 0], cost)
            if np.abs(book.quantity) @ opened > .99 * book.equity(opened) + 1e-7:
                raise AssertionError("Joint post-fee collateral cap violated")
        adverse_price = np.where(book.quantity >= 0, low, high)
        adverse_equity = book.equity(adverse_price)
        adverse_gross = np.abs(book.quantity) @ adverse_price
        equity = book.equity(closed)
        gross_notional = np.abs(book.quantity) @ closed
        envelope_breach = adverse_equity <= .005 * adverse_gross
        close_breach = equity <= .005 * gross_notional
        day_funding += payment
        day_fees += fees
        day_turnover += turnover
        day_realized += realized
        pre_terminal = equity
        if terminal and i == len(hours) - 1:
            fee, traded_total, pnl = book.trade_to([0., 0.], traded[d, 1], cost)
            fees += fee
            turnover += traded_total
            realized += pnl
            day_fees += fee
            day_turnover += traded_total
            day_realized += pnl
            equity = book.equity(closed)
        hour_rows.append((timestamp, equity, book.balance, payment, fees, turnover,
                          gross_notional / pre_terminal if pre_terminal > 0 else np.inf,
                          adverse_equity, adverse_gross, bool(opening_breach or close_breach), bool(envelope_breach)))
        if timestamp.hour == 23:
            day_rows.append((timestamp.floor("D"), equity, pre_terminal, book.balance, day_funding,
                             day_fees, day_turnover, day_realized, book.quantity[0], book.quantity[1]))
            day_funding = day_fees = day_turnover = day_realized = 0.
    daily = pd.DataFrame(day_rows, columns=["date", "equity", "pre_terminal_equity", "balance", "funding",
                    "cost", "turnover", "realized_pnl", "BTC_quantity", "ETH_quantity"]).set_index("date")
    hourly = pd.DataFrame(hour_rows, columns=["date", "equity", "balance", "funding", "cost", "turnover",
                    "gross", "adverse_equity", "adverse_gross", "marked_margin_breach", "envelope_margin_breach"]).set_index("date")
    if terminal:
        gap = book.balance - (10000 + daily.realized_pnl.sum() + daily.funding.sum() - daily.cost.sum())
        if abs(gap) > 1e-6 or np.abs(book.quantity).sum():
            raise AssertionError("Final realized ledger does not reconcile")
    return daily, hourly


def metrics(daily, hourly):
    equity = np.r_[10000., daily.equity.to_numpy()]
    highwater = np.maximum.accumulate(equity)
    hourly_equity = np.r_[10000., hourly.equity.to_numpy()]
    days = len(daily)
    final = equity[-1]
    yearly = {}
    prior = 10000.
    for year, frame in daily.groupby(daily.index.year):
        yearly[str(year)] = (frame.equity.iloc[-1] / prior - 1) * 100
        prior = frame.equity.iloc[-1]
    return {"return_pct": (final / 10000 - 1) * 100,
            "CAGR_pct": ((final / 10000) ** (365 / days) - 1) * 100 if final > 0 else -100.,
            "daily_MTM_DD_pct": float((equity / highwater - 1).min() * 100),
            "hourly_close_DD_pct": float((hourly_equity / np.maximum.accumulate(hourly_equity) - 1).min() * 100),
            "min_hourly_adverse_equity": float(hourly.adverse_equity.min()),
            "marked_margin_breach_hours": int(hourly.marked_margin_breach.sum()),
            "envelope_margin_breach_hours": int(hourly.envelope_margin_breach.sum()),
            "max_gross_pct": float(hourly.gross.max() * 100),
            "fees_USDT": float(daily.cost.sum()), "funding_USDT": float(daily.funding.sum()),
            "realized_price_pnl_USDT": float(daily.realized_pnl.sum()),
            "ledger_reconciliation_USDT": float(final - (10000 + daily.realized_pnl.sum() + daily.funding.sum() - daily.cost.sum())),
            "calendar_year_return_pct": yearly,
            "screen_gate": bool(final > 10000 and ((final/10000)**(365/days)-1) > .12
                                and (equity/highwater-1).min() >= -.05
                                and not hourly.marked_margin_breach.any() and not hourly.envelope_margin_breach.any())}


def main():
    manifest = json.loads((DATA / "manifest.json").read_text())
    for name, sha in manifest["output_sha256"].items():
        if hashlib.sha256((DATA / name).read_bytes()).hexdigest() != sha:
            raise ValueError("Frozen R6 input hash changed")
    trade = {a: pd.read_parquet(DATA / f"{a}_trade.parquet") for a in ASSETS}
    mark = {a: pd.read_parquet(DATA / f"{a}_mark.parquet") for a in ASSETS}
    fund = {a: pd.read_parquet(DATA / f"{a}_funding.parquet") for a in ASSETS}
    close = pd.DataFrame({a: trade[a].close for a in ASSETS})
    target = {m: weights(close, m) for m in ("relative30", "fixed_pair", "cov10_long")}
    for mechanism, w in target.items():
        for cutoff in ("2021-12-31", "2024-12-31"):
            pd.testing.assert_frame_equal(w.loc[:cutoff], weights(close.loc[:cutoff], mechanism))
    results = {}
    for label, start, end in WINDOWS:
        for cost in (.0012, .0024):
            for mechanism, w in target.items():
                sensitivities = ("central", "adverse", "favorable") if label == "early_reused" else ("central",)
                for sensitivity in sensitivities:
                    daily, hourly = simulate(trade, mark, fund, w, start, end, cost, mechanism, sensitivity)
                    key = f"{label}_{mechanism}_cost{cost}_{sensitivity}"
                    result = metrics(daily, hourly)
                    results[key] = result
                    if sensitivity == "central":
                        daily.to_csv(HERE / f"r6_{key}_daily.csv")
                        # Preserve the independent hourly risk audit without large CSV overhead.
                        hourly.to_parquet(HERE / f"r6_{key}_hourly.parquet")
                    print(key, {k: round(result[k], 4) for k in ("CAGR_pct", "daily_MTM_DD_pct", "funding_USDT", "fees_USDT")}, "gate", result["screen_gate"], flush=True)
    sources = {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
               for name in ("R6_PROTOCOL.md", "r6_data.py", "r6_relative.py", "test_r6_engine.py", "test_r6_data.py")}
    sources["../../strategies/portfolio_risk.py"] = hashlib.sha256((HERE / "../../strategies/portfolio_risk.py").read_bytes()).hexdigest()
    (HERE / "r6_results.json").write_text(json.dumps({"captured_utc": datetime.now(timezone.utc).isoformat(),
        "protocol": "R6_PROTOCOL.md", "source_sha256": sources, "input_manifest": manifest,
        "not_untouched_OOS": True, "assumptions": "Cross perpetual ledger; exact actual rates; missing associated funding marks use documented hourly-open central estimate and extremes sensitivity; fixed0.5% maintenance screening; daily-open fills with all-in costs, final daily-close costs. No liquidations modeled as profitable exits.",
        "results": results}, indent=2) + "\n")


if __name__ == "__main__":
    main()
