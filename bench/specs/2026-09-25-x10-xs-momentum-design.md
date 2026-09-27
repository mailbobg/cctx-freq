# X10: weekly cross-sectional momentum on USDT perps, inverse-vol sized (pre-registration)

Date: 2026-09-25. Status: draft for approval; no code and no backtest run before this file is agreed.
Variant log at the bottom is append-only; acceptance criteria may not be edited after the first run.

## Thesis and payer

Coins that outperformed their peers over the last few weeks keep outperforming for about a week, and
laggards keep lagging. Literature (2024–2026): the crypto momentum factor CMOM earns ≈ +2.1%/week post-2020
(t ≈ 3.7) in Borri–Liu–Tsyvinski–Wu (arXiv 2510.14435, rev. 2026-03); the CTREND trend factor (Fieberg et
al., JFQA 2025) survives transaction costs in liquid coins; Grobys (2025) documents momentum crash risk and
shows volatility management is required. Payer: slow-reacting flows (retail chasing after the move has
started, funds rebalancing on lag). This is a relative-strength bet with the market leg hedged out; it does
not require predicting the market's direction. Known failure mode: momentum crashes when the market
reverses violently (shorts squeeze, longs dump) — hence the sizing rules below.

## Rules

Venue: Binance USDT-margined perpetuals, 1x, isolated. Timeframe 1d (decisions at 00:00 UTC).
Rebalance day: every Monday 00:00 UTC (weekly). Positions opened on a rebalance day are held 7 days.

Universe (backtest): `user_data/pairs-x9.json` — the 63 perps in today's top-200 by volume that have
candle history since 2024-01-01 (survivorship bias acknowledged: coins that died or delisted are
missing, which flatters the long leg; see validation step 8).

Signal, computed on each rebalance day from daily closes:
- `mom` = close[t−1] / close[t−22] − 1 (21-day return, skipping the most recent day to avoid the
  1-day reversal; t−1 = yesterday's close, the last complete candle).
- `vol` = std of daily log returns over the last 30 days, annualised.
- Rank all universe coins by `mom`. Long the top 20% (≈ 12 coins), short the bottom 20%.

Sizing (inverse volatility, market-neutral by notional):
- Within each side, weight_i ∝ 1/vol_i, normalised so that each side's total notional = 45% of equity
  (gross 90%, net ≈ 0). Per-position cap 8% of equity. No leverage beyond 1x.
- Implemented via `custom_stake_amount`; `max_open_trades` = 24.

Exit: at the next rebalance day (7-day time exit via `custom_exit`). A coin that is still in the same
quintile is re-entered as a new trade (so turnover, fees and funding are booked honestly). Disaster stop
`stoploss = −0.25` (rarely hit at 1x on a 7-day hold; not a signal). No ROI, no trailing, no DCA.

Costs: fee 0.05% per side (taker), funding applied by the backtester from the downloaded funding history.

Explicitly not done: parameter search on the lookback or quantile before the neighbourhood check;
BTC/ETH exclusion; any ML; leverage.

## Expected behaviour (written before running)

- ≈ 24 entries per week ⇒ ≈ 1,100–1,300 trades over 2024-01 → 2026-09 (≈ 140 weeks).
- Correlation of weekly strategy returns with BTC weekly returns |ρ| ≤ 0.3 (it is meant to be neutral).
- If the literature holds on this venue: positive net return, Sharpe 0.8–1.5, one or two crash weeks of
  −5% to −10% around violent market reversals (e.g. 2024-08-05, 2025-02, 2025-10-10). Fees + funding cost
  roughly 0.1–0.2%/week gross turnover ≈ 5–10%/yr — a real hurdle.
- If it fails, the most likely picture: the short leg loses in squeezes faster than the long leg earns
  (the same asymmetry that killed X9).

## Validation pipeline (same gauntlet, in this order)

1. `lookahead-analysis` (≥ 80 signals) → any bias = reject. The cross-sectional rank uses other pairs'
   data; the check must confirm only complete candles are used.
2. Backtest 2024-01-01 → 2026-09-23, fee 0.05%, funding on. Also report the long and short legs separately.
3. `evaluate.py`: monthly table, lottery test, bootstrap CI, yearly split.
4. `mtm.py`: mark-to-market drawdown including funding.
5. Neighbourhood, pre-registered 3 × 3: lookback {14, 21, 28} days × quantile {20%, 25%, 33%}; the
   chosen cell (21, 20%) must not be an isolated spike: ≥ 6 of 9 cells positive after fees.
6. Fee sensitivity: 0.10% per side.
7. Cross-exchange replication, same period and rules: Bybit and OKX USDT perps (data to be downloaded).
8. Survivorship check: rerun on the raw 103-pair list, coins entering when their data starts. The
   result must keep its sign.
9. Correlation to BTC weekly returns reported (not a gate, but |ρ| > 0.5 means the hedge failed).

## Acceptance criteria (pre-registered; all must pass)

- Net profit > 0 with monthly t-stat ≥ 2.0 over the 33 months.
- Lottery: profit with the best 3 months removed > 0.
- Bootstrap 95% CI of total profit excludes 0.
- MTM max drawdown ≤ 25%.
- Neighbourhood ≥ 6 of 9 cells positive.
- Cross-exchange: sign of the total return agrees on ≥ 2 of 3 exchanges.
- Survivorship rerun keeps the sign.
- Trade count ≥ 500.

Pass → dry-run on Binance futures (port 8083) alongside the existing three bots, forward record from the
start date. Fail → log below, at most two further pre-registered variants (candidates, in this order:
(a) portfolio-level vol targeting at 20% annualised instead of per-coin inverse vol; (b) 7-day skip on the
lookback, i.e. close[t−8]/close[t−29]); then close the line.

## Variants tried (append-only log)

| # | date | change | result |
|---|---|---|---|
| 1 | 2026-09-25 | as specified above, 63 Binance perps, 2024-01-09 → 2026-09-24, fee 0.05%, funding on | **FAIL**: −28.6%, 2391 trades (all entries Monday, 89% held 7d, stakes 2–5% each, weekly long/short notional ratio 0.98 → implementation as specified). Long leg −22.7%, short leg −6.0%; 239 stop-losses. Bootstrap CI [−80%, +24%], −best3 −47%, MTM DD 41.6%. **Thesis check: equal-weight weekly long-minus-short return = −0.01%/week, t = 0.00, 49% of 142 weeks positive — no cross-sectional momentum at this horizon in this universe, before costs.** Lookahead and 9-cell neighbourhood: see rows below. |
| 1a | 2026-09-25 | `lookahead-analysis` | Flags 25/80 entries, 39/80 exits on `mom_rank`/`weight`. **Tool artefact, not a leak**: its cut-off re-runs load only the pair under test (`lookahead.py` lines 149/160), so the cross-sectional rank degenerates to a one-coin universe. The strategy uses only backward shifts/rollings per row; the panel is rebuilt from the data actually loaded. freqtrade's lookahead tool cannot validate cross-sectional strategies; a cross-sectional check would need a custom cut-off test. Left unresolved because the performance result is already negative and a leak could only have made it look better. |
| 1b | 2026-09-25 | pre-registered 9-cell neighbourhood, lookback {14,21,28} × quantile {20,25,33}% | 5 of 9 positive (criterion ≥ 6 → **FAIL**): L14 +11.3/+7.0/+7.1, L21 **−28.6**/+10.3/−12.5, L28 +6.5/−2.4/−9.5. No gradient in either axis; neighbouring cells flip sign (L21: −29 → +10 → −12). This is the noise fingerprint: a zero-mean signal with the chosen cell as an unlucky draw. Consistent with the thesis check (weekly L−S = 0). Variants (a)/(b) not run: both change sizing/window, neither can create a signal where the sort itself is flat. **Line closed** unless the universe changes (literature effect lives in small/illiquid coins outside our tradeable set). |
