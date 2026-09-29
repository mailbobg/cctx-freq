# P2: replication of "量化韭菜TOM" pair_trading_run_V6 (pre-registration)

Date: 2026-09-29. Status: approved by the user ("测试") before any code or backtest. Append-only log at the bottom;
criteria may not be edited after the first run.

## Source

GitHub `NTZ12345678/freqtrade_chinese_discord`, folder `策略相关/配对套利/` (commit `4e4ece4`, read 2026-09-29):
`pair_trading_get_para_V6.py` (formation) and `pair_trading_run_V6.py` (trading). The author shows a live dry-run
2026-02-22 → 04-20 (~1,080 USDT account: closed +135 USDT, incl. open positions +75 USDT, 1,119 trades) and one
out-of-sample backtest of an older version (params 2024-08 → 11, run 2024-11 → 2025-03: +87.6 %, DD 34.4 %, worst
trade −520 %, longest hold 81 days).

How it differs from our closed P1: 5m bars instead of 1h; a narrow band (enter |z| > 1, exit |z| < 0.8); selection by
ADF statistic < −6 and half-life < 500 bars; **no stop** (stoploss −100, the z > 3 stop is commented out). P1 lost
because stops fired far more often than spreads converged; this design never stops, so the question is whether the
losers held to reversion cost less than the stops did — measured mark-to-market, not on closed trades.

## Who pays

Same hypothesis as P1: short-lived dislocations between close substitutes, paid by traders moving one coin in a hurry.
The tail risk is a spread that never reverts (delisting, hack, token unlock, narrative split); without a stop that
loss is taken in full.

## Rules (faithful to V6 with `use_whitelist = False`; implementation readings marked *)

**Data.** Binance USDT-M perpetuals, `data_x14/5m` closes/opens, weekly top-60 universe by 30-day quote volume
(`data_x14/universe.json`, delisted included, BTC included), funding from `data_p1/funding.parquet`.

**Walk-forward.** Formation window 90 days of 5m closes ending at the window start; trading window the following
90 days; windows start 2021-04-01 and roll every 90 days to 2026-09-23. (*The author's own OOS log used 3 months of
parameters followed by 4 months of trading; 90/90 is fixed here.)

**Formation** (per window, universe = the Monday list at or before the window start, coins with ≥ 90 % of the
formation candles and a price at the window start). For every ordered pair (y, x): OLS y = c + γ·x on raw closes
(as the code does, not logs); residual z; z_mean, z_std; ADF on z with maxlag 1, constant, no autolag; half-life =
−ln 2 / b from Δz = a + b·z_{t−1}. Keep pairs with ADF p ≤ 0.01 and ADF stat < −6, half-life < 500 candles,
*γ > 0 (the run code refuses to size γ ≤ 0; a slot given to such a pair would be dead). Sort by half-life ascending;
take greedily, skipping any pair that shares a coin with one already taken, until 6 pairs (max_open_trades 12 / 2).

**Trading** (closed 5m candle → fill at next candle's open). zscore = (y − γx − c − z_mean) / z_std with frozen
parameters.
- Entry when zscore > 1: short y, long x; zscore < −1: long y, short x. Sizing "unit neutral": qty_x = γ·qty_y,
  qty_y·p_y + qty_x·p_x = budget, budget = realised equity / number of selected pairs, 1x per leg.
- Exit both legs when |zscore| < 0.8, or when the pair's open P&L > 1 % of realised equity and the position is older
  than 1 day.
- No stop. *A 1x short leg is liquidated when its price reaches 2× entry (loss = leg notional); the other leg then
  stays open until the pair's exit rule fires. *A coin whose data ends is closed at its last close.
- A coin may hold only one open position (freqtrade rule), at most 12 open legs.
- *Window roll: positions of pairs not re-selected keep running with their old frozen parameters until their exit
  rule fires; no new entries on them. New pairs cannot enter a coin that still has an open leg.

**Costs.** Taker fee 0.05 % + slippage 0.02 % per leg per side (market orders, as the code); funding on open legs.

## Validation

One continuous run 2021-04-01 → 2026-09-23, 10,000 USDT, mark-to-market equity every 5m candle. Per calendar year and
halves (2021-04…2023, 2024…2026-09) measured on that curve (open positions marked, not closed). Reported: return,
MTM max drawdown, pair trades, win rate, average and longest hold, unrealised P&L at the end, funding, BTC daily
correlation, P&L by pair, liquidations. Fee ×2. Neighbourhood grid (fixed now): entry z {1.0, 1.5, 2.0} × formation
{60, 90, 120 days}, exit band 0.8 unchanged. Look-ahead self-check: 50 random truncation times, decisions up to each
must be identical (formation of the window containing the truncation recomputed on truncated data).

## Acceptance (all must hold)

1. Both halves > 0 and at least 5 of 6 calendar years > 0 (2021 is a partial year from April).
2. Bootstrap 95 % CI of mean monthly MTM return excludes 0.
3. MTM max drawdown ≤ 15 % in every year.
4. |BTC daily correlation| ≤ 0.3.
5. Fee ×2: full-window return > 0.
6. P&L without the 3 best pairs > 0.
7. Grid ≥ 6 of 9 cells > 0.
8. ≥ 300 closed pair trades.

Expected (written before running): high closed-trade win rate (> 60 %) and many trades; the MTM curve carries long
stretches of unrealised loss. Most likely failure: 3 (drawdown from a spread that never reverts, like DASH −520 % in the
author's own log) and 1 (2022 or 2025 negative).

## Decision

Pass → paper bot (two-leg, server). Fail → log it; one variant, chosen now: **(a) same rules with the author's own
commented-out stop restored: close both legs when |zscore| > 3.** Then the line is closed.

## Log (append-only)

| # | date | change | result |
|---|---|---|---|
