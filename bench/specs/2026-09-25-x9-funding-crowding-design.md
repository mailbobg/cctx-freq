# X9: funding-rate crowding reversal (design + pre-registration)

Date: 2026-09-25. Status: approved in chat (sections 1 "signal & positions" and trade-frequency estimate);
this file is written before any backtest so the acceptance criteria cannot be adjusted to the results.

## Thesis

On USDT-margined perpetuals, leveraged speculators pay funding to the other side. When funding is extreme
relative to a coin's own history the position is crowded; we take the other side, collect funding, and
profit from the crowding unwinding. This harvests a risk premium and a behavioural reversal; it does not
predict direction. Counterparty: leveraged retail on Binance perps.

## Rules (evaluated once per day at 00:00 UTC, timeframe 1d, Binance USDT perps, 1x, isolated)

Inputs per pair, from the 8h funding-rate series only:
- `F` = mean of the last 3 funding rates (24h), annualised as `F * 3 * 365`.
- `C` = percentile of the trailing 7-day cumulative funding within that pair's trailing 90 days.

Entry:
- short when `F_ann > +30%` and `C > 0.90` and the last day's close is not a new 24h high (price not making a
  fresh high while we short — no entries into an active squeeze);
- long when `F_ann < -10%` and `C < 0.10` and the last day's close is not a new 24h low.

Position sizing: `stake_amount = "unlimited"` with `max_open_trades = 10` → each position ≈ 10% of equity.
Per side at most 5 positions; if one side has no signal the other side still trades but a single side
never exceeds 30% of equity (3 positions). No leverage beyond 1x. No position adjustment, no DCA.

Exit (first hit wins):
- crowding resolved: `C` back inside [0.50 −, 0.50 +] → `0.5` band, i.e. `C < 0.5` for shorts, `C > 0.5` for longs;
- time stop: 7 days after entry;
- hard stop: −8% on the position (freqtrade `stoploss = -0.08`, funding fees included in P&L by the backtester).

Not done on purpose: cross-sectional ranking, long/short ratio or OI (only 30 days of history), grid, DCA.

## Universe

Static list for backtests: the top 80 Binance USDT perps by quote volume on 2026-09-25 that have funding
and candle history since 2024-01-01 (survivorship bias acknowledged; the live bot uses a VolumePairList).

## Validation pipeline (same gauntlet as NFI, in this order)

1. `lookahead-analysis` (≥ 80 signals) → any bias = reject.
2. Backtest 2024-01-01 → 2026-09-23 on Binance with fee 0.05% per side (taker) and funding applied.
3. `user_data/bench/evaluate.py`: monthly table, lottery test, yearly split.
4. `user_data/bench/mtm.py`: mark-to-market drawdown (short positions valued daily).
5. Parameter neighbourhood: F thresholds {20, 30, 40}% × C percentiles {85, 90, 95} → 9 cells; the chosen
   cell must not be an isolated spike (≥ 6 of 9 cells positive after fees).
6. Cross-exchange replication, same period and same rules: Bybit and OKX USDT perps.
7. Fee sensitivity: 0.10% per side.

## Acceptance criteria (pre-registered)

- Net of fees and funding, 2024-01 → 2026-09: profit > 0 with monthly t-stat ≥ 2.0 (33 months).
- Lottery test: profit with the best 3 months removed > 0.
- MTM max drawdown ≤ 20%.
- Neighbourhood: ≥ 6 of 9 cells positive.
- Cross-exchange: sign of the total return agrees on at least 2 of the 3 exchanges.
- Trade count ≥ 300 (so months are not dominated by single trades).

Any criterion failing → the strategy is not deployed to dry-run; the variant count is logged in this file
(multiple-comparison discipline). Passing all → dry-run on Binance futures testnet-free paper mode alongside
NFI X7 / X8, forward record from the start date.

## Variants tried (append-only log)

| # | date | change | result |
|---|---|---|---|
| 1 | 2026-09-25 | as specified above, Binance 63 perps, 2024-01-09 → 2026-09-24, fee 0.05% | **FAIL**: −38.0%, 556 trades, win 28.6%, DD 56%, monthly t < 0, −best3 < 0. Funding leg positive on both sides (+1968 long / +337 short USDT) but price leg −2.7k / −3.7k; 330 of 450 longs and 79 of 106 shorts hit the −8% stop. Shorting crowded longs = shorting momentum (Feb 2024, Feb 2025 squeezes). Thesis as specified is falsified; no threshold tuning attempted. |
| 1b | 2026-09-25 | pre-registered 9-cell neighbourhood (F 20/30/40% × C 85/90/95) | 8 of 9 cells negative (−52% … −16%); the single positive cell F0.40/C0.85 is +2.6% with a 47% drawdown. Negative result is uniform, not a knife edge. Lookahead-analysis also flags 8/80 signals (sub-daily funding vs daily candle alignment) — unresolved, moot given the performance failure. **Line closed.** |
| 2 | 2026-09-25 | **pre-registered before running.** Source: nateemma/strategies `Funding/FundingCarry.py` (validated on his data, 3 eras, era-persistent): long-only funding reversion. 1h candles; entry when the trailing 90-day funding z-score < −1.5 (crowded shorts) and BTC 1h close > SMA(500); exit purely by time after 16h; NO stop (−0.99), ROI off; 10 slots × 10%; Binance USDT perps, 63 pairs, fee 0.05%. Differences from variant 1 that his study says matter: horizon 16h not 7d, no stop, long leg only. Same acceptance criteria as above. | **FAIL**: −5.9%, 3006 trades, win 46.8%, closed-trade DD 36%, MTM DD 40%, monthly t −0.16, −best3 −41%, bootstrap CI [−57%, +48%]. Lookahead 80/0 clean. Funding leg positive again, price leg negative (see decomposition in chat log). nateemma's own caveat applied: on his venue the edge was marginal after fills; on Binance perps with 63 pairs it does not exist at all. **Two variants, both dead; line closed.** |
