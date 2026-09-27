# C: BTC + ETH risk-managed holding (pre-registration)

Date: 2026-09-27. Status: draft for approval; no 2017–2020 data is loaded before this file is agreed.
The variant log at the bottom is append-only; acceptance criteria may not be edited after the first run.

## Question (different from every earlier spec)

Not "is there alpha", but: **for someone who wants to hold BTC and ETH anyway, does a simple weekly rule keep most of
the return of holding while cutting the deep drawdowns?** The benchmark is the plain holding, not zero.

Payer: none needed — this is risk management, not an edge. What it exploits is a well-documented statistical fact
(volatility clusters, and crypto trends persist over weeks), traded weekly so costs are negligible.

## What has been seen already

TSMOM30 on BTC+ETH was tested on 2021-01 → 2026-09 (loop round 16 and the holdout): +321% vs hold +175%
(2023-03 → 2026-09) and +288% vs +129% (2021–2023), but MTM drawdowns of 28–40% and 2022 −39%. So 2021–2026 is
in-sample for the trend part. **2017-08 → 2020-12 has never been loaded in this project** (Binance ETH/USDT starts
2017-08-17) and is the out-of-sample test here. It contains the 2018 bear market (BTC −73%, ETH −82%) and the
March-2020 crash.

## Rules (fixed now, taken from the earlier TSMOM30 and textbook volatility targeting; nothing tuned)

Assets BTC/USDT and ETH/USDT, Binance spot daily candles. Decision each Monday 00:00 UTC on the Sunday close;
trade at the Monday daily open.
For each coin separately:
- trend: 30-day return > 0 → "on", else weight 0 (TSMOM30, unchanged);
- size when on: weight = 0.5 × min(1, 0.60 / σ30), σ30 = annualised std of the last 30 daily log returns
  (60% annual vol target per coin; each coin at most half of the account);
- rest in USDT (no interest assumed).
Rebalance only when a coin's target weight differs from its current weight by more than 5 percentage points.
Costs 0.1% of traded notional. No intraday stops.

Benchmark: 50/50 BTC/ETH bought at the start and rebalanced to 50/50 every Monday (same costs).
Also reported (decomposition only, not a selection menu): trend without vol sizing; vol sizing without trend.

## Evaluation

Implemented as a plain pandas script (`user_data/bench/c_riskhold.py`) — a weekly, fractional-weight rule does not
need freqtrade. Windows:
- **OOS: 2017-08-21 (first Monday with 30 days of ETH history + warm-up allowed from 2017-08-17) → 2020-12-31**
  — the deciding window;
- in-sample, reported: 2021-01-01 → 2026-09-21.
Daily mark-to-market equity; metrics: CAGR, max drawdown, Sharpe (daily, annualised), worst calendar year, share of
time invested, turnover per year.

## Acceptance criteria (OOS window; all must hold)

1. Max drawdown ≤ 50% of the benchmark's max drawdown.
2. CAGR ≥ 60% of the benchmark's CAGR (and > 0).
3. Sharpe > benchmark Sharpe.
4. In the worst benchmark calendar year (expected 2018), loss ≤ half of the benchmark's loss.
5. Costs 0.2% per trade: criteria 1–3 still hold.
And in-sample 2021–2026, reported with the same metrics; failing there does not override an OOS pass, but it is
logged.

## Decision

Pass → a weekly "what to hold" signal on the control panel (BTC %, ETH %, USDT %), executed by hand or by a small
bot; no money until the user decides. Fail → at most one variant, chosen now: (a) 20-week (140-day) trend instead of
30-day, same sizing. Then close.

## Variants tried (append-only log)

| # | date | change | result |
|---|---|---|---|
| 1 | 2026-09-27 | as specified (TSMOM30 + 60% vol target, 5pp band). Start clarified: first Monday with 30 days of history is 2017-09-18 (strategy and benchmark both start there) | **FAIL on criterion 1 only (narrowly).** OOS 2017-09-18 → 2020-12-31, fee 0.1%: strategy +581.6% / CAGR +79.3% / maxDD −49.4% (peak 2018-01-13 → trough 2019-02-14) / Sharpe 1.58 / 2018 −33.8%; benchmark 50/50 +412.6% / CAGR +64.4% / maxDD −87.8% / Sharpe 1.03 / 2018 −76.4%. Criteria: 1 maxDD ≤ 50% of bench → 56% ❌; 2 CAGR ✅ (above bench); 3 Sharpe ✅; 4 2018 loss ≤ half ✅. Fee 0.2%: same verdicts (maxDD −50.1%). In-sample 2021-01 → 2026-09: all four pass (+725% vs +210%, maxDD −32.5% vs −76.3%, Sharpe 1.18 vs 0.63, 2022 −17.1% vs −65.2%). Decomposition: trend alone OOS CAGR +95.7% / maxDD −64.1%; vol sizing alone adds little. |
| 2 | 2026-09-27 | pre-registered variant (a): 140-day trend | **FAIL.** OOS CAGR +37.1% (start 2018-01-08, bench +8.0%), maxDD −55.6% (2019-06 → 2020-03-12) ❌; in-sample worse than holding (Sharpe 0.54 vs 0.63, 2022 −38.6%). |
| — | 2026-09-27 | **verdict** | Closed per spec: the main rule misses criterion 1 (drawdown 56% of the benchmark's vs ≤ 50% required) and the variant fails more. Factually, the main rule beat holding on return, Sharpe and bear-year loss in both windows; it did not halve the worst drawdown in 2018 (whipsaws from ~42% invested). No further variants. |
