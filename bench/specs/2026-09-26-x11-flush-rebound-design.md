# X11: buy the altcoins hit hardest in a broad market flush (pre-registration)

Date: 2026-09-26. Status: draft for approval; no strategy code and no backtest before this file is agreed.
The variant log at the bottom is append-only; acceptance criteria may not be edited after the first run.

## Where this comes from, and what has already been seen

Event study `user_data/bench/dip_event_study.py` (116 Binance spot pairs, 2024-01 → 2026-09, 5m):
- a single altcoin crashing on its own (BTC flat) keeps falling — information, not panic;
- altcoins dropping during a mild market dip rebound only as much as BTC does;
- **when ≥ 10 altcoins drop sharply against BTC inside the same 4 hours while BTC itself falls > 0.5%,
  the losers rebound**: 153 independent episodes, 24h excess over BTC +2.2% (t 4.7), positive in each of
  2024 / 2025 / 2026 (+3.0 / +1.7 / +1.1%), win rate 65–84%, median 4h adverse excursion −1.9%.

Payer: forced and panicked sellers in a broad sell-off (liquidations, stop cascades, de-risking), who sell
the most volatile altcoins hardest and hand a discount to whoever provides liquidity.

Honest caveat on out-of-sample status: the event study already looked at all of 2024-01 → 2026-09, and the
breadth cut (≥ 10 coins) was chosen after looking. **No part of the backtest window is a clean holdout.**
The backtest can only confirm that the rules, as coded, reproduce the event-study effect after costs. The
only true out-of-sample evidence will be the dry-run from its start date, and the effect is visibly
decaying year over year (2024 > 2025 > 2026).

## Rules

Venue and universe: Binance spot, long only, the 116 altcoins in `user_data/pairs-bench-full.json`
(BTC/USDT is the reference, not traded). Timeframe 5m; every decision uses closed candles only.

Flush detection, evaluated on every closed 5m candle t:
- `btc_1h` = BTC log return over the last 12 candles.
- `x_1h(i)` = log return of altcoin i over the last 12 candles minus `btc_1h`.
- `breadth` = number of altcoins with `x_1h ≤ −4%`.
- A **flush** fires at t when `btc_1h ≤ −0.5%` and `breadth ≥ 10`.
- After a flush fires, no new flush can fire for 4 hours (48 candles): one episode per 4h, matching the
  event study's clustering.

Entry, at the open of candle t+1: buy the altcoins with `x_1h ≤ −4%` at t, the most-dropped first, at most 10
of them. Each gets a fixed 9% of equity (so at most 90% invested). Skip a coin already held.

Exit: purely by time, 24 hours after entry (at the first candle open ≥ entry + 24h). No ROI, no trailing stop,
no signal exit. Disaster stop −25% (not a signal; the event study's median 4h adverse move is −1.9%).

Costs: fee 0.1% per side (spot taker) — a 0.2% round trip.

Explicitly not done: any filter on RSI, volume, coin age or regime; position scaling by drop size;
re-entry during an open episode; leverage; shorts.

## Expected behaviour (written before running)

- ≈ 150 episodes × up to 10 coins ⇒ roughly 800–1,500 trades over 2024-01 → 2026-09.
- Per trade ≈ +1.5 … +3% gross at 24h (event-study cluster means are +2.5 … +4.5% raw) minus 0.2% cost.
- Year pattern: 2024 strongest, 2026 weakest.
- Most likely failure: the event study measured "mean over coins in a cluster"; picking the 10 worst
  drops might select the ones with news (idiosyncratic) that keep falling, or the equity curve is carried
  by a handful of crash days (lottery).

## Validation pipeline

1. Cross-sectional look-ahead check, custom: re-run the flush detector on data truncated at each of 50
   random flush times and confirm the same flush fires with the same coin list (freqtrade's
   `lookahead-analysis` cannot test cross-sectional rules — it loads one pair; see X10).
2. Backtest 2024-01-01 → 2026-09-23, fee 0.1%.
3. `evaluate.py`: monthly table, lottery test, bootstrap CI, yearly split.
4. `mtm.py`: mark-to-market drawdown.
5. Neighbourhood, pre-registered 3 × 3: breadth {5, 10, 20} × hold {4h, 12h, 24h}.
6. Fee sensitivity: 0.2% per side.
7. Replication of the effect (event study only, not the strategy) on OKX spot for the same period and
   the pairs listed there — required before any live money, not before dry-run.
8. Correlation of daily returns with the E0V1EN backtest (to know whether X11 is the part of E0V1EN that works).

## Acceptance criteria (pre-registered; all must pass for a dry-run)

- Net profit > 0 over the full window, and each calendar year (2024, 2025, 2026-to-date) > 0.
- Lottery: profit with the best 3 months removed > 0.
- Bootstrap 95% CI of total profit excludes 0.
- MTM max drawdown ≤ 20%.
- Neighbourhood ≥ 6 of 9 cells positive.
- Fee 0.2% per side still > 0.
- Custom look-ahead check: 50 of 50 identical.
- Trade count ≥ 300.

Pass → dry-run on Binance spot (port 8083), forward record from its start date; that record, not this
backtest, decides anything further. Fail → log it; at most one further pre-registered variant, chosen now:
(a) hold 4h instead of 24h (the shortest horizon where the excess was already positive in every year).
Then close the line.

## Variants tried (append-only log)

| # | date | change | result |
|---|---|---|---|
| 1 | 2026-09-26 | as specified above; 116 pairs, 2024-01 → 2026-09-23, fee 0.1% | Custom look-ahead 50/50 identical ✅. 80 flush episodes (2024 44, 2025 29, 2026 7), 632 trades ✅. **+205.8%**, win 60.8%, bootstrap CI [+73%, +341%] ✅, −best3 +23.6% ✅, monthly t 1.44. **Yearly 2024 +206.8% / 2025 −29.6% / 2026 +28.6% → FAIL** (each year must be > 0). **MTM max DD −32.7% (2025-10-10) → FAIL** (≤ 20%). Neighbourhood and fee test: rows below. |
| 1b | 2026-09-26 | pre-registered 3×3 neighbourhood (breadth 5/10/20 × hold 4/12/24h) + fee 0.2% | Neighbourhood 8 of 9 positive ✅ (B5: +68/+192/+259%; B10: +41/+113/+206%; B20: −10/+113/+99%) — clean gradient, longer hold better. Fee 0.2% → +173% ✅. |
| 2 | 2026-09-26 | pre-registered variant (a): hold 4h | **FAIL**: +41.1%, 810 trades, CI [−9%, +90%] (includes 0), −best3 −19.6%, 2025 −30.6%, MTM DD −31.1% (2025-10-10). |
| — | 2026-09-26 | **verdict** | Main spec fails 2 of 8 criteria (2025 −29.6%, MTM DD −32.7%), variant fails 4. **Line closed per spec.** The effect is real in aggregate (neighbourhood 8/9, gradient, fee-robust, CI excludes 0 for the 24h spec) but lives mostly in 2024, and a single cascade (2025-10-10) wipes a year. Concentrating 90% of equity into one flush is the design flaw the spec did not allow us to fix. |
