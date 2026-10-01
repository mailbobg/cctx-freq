# U1: cross-sectional token-unlock pressure short, BTC-hedged (pre-registration)

Date: 2026-10-01. Status: requested by the user ("两个都做": widen unlock-short beyond APT/TIA). Written before any code,
return or backtest on this question. Append-only log at the bottom; criteria may not be edited after the first run.

## Who pays

Holders who receive tokens on a known date (investors, team, airdrop recipients) sell part of them into the market
around that date, and liquidity providers widen spreads or step back ahead of the supply. The schedule is public, so the
price may already discount it; the question is whether a large unlock relative to normal trading volume still leaves a
short-horizon edge after costs. The existing `unlock-short` bot (UnlockPressureShortV1, Codex's design) trades the same
idea on two coins (APT, TIA), one position a week; two coins cannot produce a reliable statistic. U1 tests the idea on
every Binance perpetual that has a known schedule.

**The main confound, written first:** altcoins as a group lose to BTC over 2022–2026, so shorting any basket of altcoins
against BTC makes money regardless of unlocks (L1, F1 and P2 all show it). U1 therefore has to beat a *control* made of
random coins with no unlock in the same weeks (below); beating zero is not enough.

## What has been seen already (honesty note)

- The unlock table `bench/events/unlocks.parquet` (41,057 rows, 344 protocols, categories, DefiLlama emissions data
  collected 2026-09-25; the API is paid now, so it cannot be refreshed) and a mapped table + an event study
  (`unlock_event_study.parquet`, returns around 7,791 events) were built earlier in this project. Their results have **not**
  been read for this design; only column names, row counts and event counts per year/category were looked at.
- The APT/TIA bot's design came from earlier work that was not audited here.
- Binance perpetual prices 2021–2026 were used for other strategies (not for unlock timing).
- Schedule vintage: the table holds the schedules as of 2026-09-25, not as published at each past date. Revisions of past
  schedules would leak the future; the placebo/control below limits, but does not remove, that risk.

## Data

- Events: `unlocks.parquet` joined to `unlock_symbol_map.parquet` (344 protocols → 188 with a Binance USDT-M perpetual; 146
  have mapped events). Event day = `ts` floored to the UTC day.
- Included categories (fixed now): `insiders`, `privateSale`, `publicSale`, `airdrop` (discrete holder unlocks). Excluded:
  `farming`, `staking`, `liquidity` (continuous reward streams), `ecosystem`, `noncirculating` (treasury/unclassified), `Uncategorized`
  (unknown provenance), and all others.
- Prices and volume: `data_p1/daily.parquet` (1d close + quote volume, all USDT-M perps incl. delisted); daily high/low
  for the mapped perps from the data.binance.vision monthly 1d archives (new download, `bench/uh_fetch.py`);
  funding from `data_f1/funding.parquet`. Non-crypto perpetuals (`underlyingType != COIN`) and stablecoin/index bases excluded.
- Window: first Monday 2022-01-03 → 2026-08-31 (funding archive end).

## Rules

- **Decision**: every Monday 00:00 UTC, using data through the Sunday close only (prices, volumes) plus the public schedule.
- **Universe**: top 100 perpetuals by 30-day quote volume, ≥ 60 days of history, BTC excluded from the short side.
- **Score**: S = Σ over included-category events dated in (Monday 00:00, Monday + 14 d] of tokens × Sunday close, divided by
  (14 × mean daily quote volume of the previous 30 days). Unlock value measured against two weeks of normal volume.
- **Selection**: perpetuals with S ≥ 0.25; the 6 highest S (ties by symbol). Fewer, or none, in thin weeks.
- **Positions**: short each selected coin at 5 % of equity (≤ 30 % gross), plus a BTC long equal to the total short
  notional. Positions are re-formed every Monday at the Sunday close price; only differences are traded (a coin still
  selected stays open; a coin that falls out is covered). 1x, isolated.
- **Liquidation**: a short is liquidated, losing its whole notional, when the daily high reaches 1.95× its entry price.
  A coin whose data ends is closed at its last close.
- **Costs**: taker 0.05 % + slippage 0.05 % per side on altcoin legs, 0.05 % + 0.02 % on BTC; funding on every leg
  (shorts receive positive funding), from the funding archive.

## Control (the confound)

Same weeks, same universe, same number of shorts k_w as the real selection of that week, but coins drawn at random from
those with **no included-category unlock in (Monday − 14 d, Monday + 14 d]**, equal weight 5 %, BTC hedge, same costs and
liquidation rule. 50 draws with seeds 0–49; the control is their mean weekly return. Weeks with no real selection have no
trade in either arm.

## Validation

One continuous mark-to-market run on daily closes (10,000 USDT). Per calendar year 2022 … 2026 and halves 2022-01 →
2024-06 / 2024-07 → 2026-08. Reported: return, MTM max drawdown, coin-weeks, win rate by coin-week, P&L split short legs /
BTC hedge / funding / costs, BTC correlation, liquidations, P&L without the 3 best coins, P&L by unlock category.
Fee ×2. Neighbourhood grid (fixed now): S threshold {0.1, 0.25, 0.5} × k {3, 6, 10} — 9 cells. Look-ahead self-check: 50
random Mondays; selection recomputed with prices truncated at that Monday must equal the full run's selection (the schedule
is public in advance and is not truncated).

## Acceptance (all must hold)

1. Full window, both halves, 2025 and 2026 each net > 0; and among calendar years with ≥ 20 traded coin-weeks at least 80 %
   positive.
2. Bootstrap 95 % CI of the mean **weekly return difference real − control** excludes 0 (5,000 resamples of weeks).
3. MTM max drawdown ≤ 15 % in every year.
4. |BTC daily correlation| ≤ 0.3.
5. Fee ×2: full-window return > 0.
6. P&L without the 3 best coins > 0.
7. Grid ≥ 6 of 9 cells with full-window return > 0 **and** real − control > 0.
8. ≥ 150 traded coin-weeks over the window.

Expected (written before running): selections are rare in 2022–23 (few schedules in the table), 100–300 coin-weeks a year
from 2024; real beats zero mostly because alts bleed against BTC, with the control capturing most of it. Most likely
failures: 2 (real ≈ control) and 3 (a squeeze on a low-volume coin, liquidation = −5 % of equity per event).

## Decision

Pass → a paper bot on the server, weekly Monday entries from the stored schedule file (2,629 future included-category
events across 64 perps are already in the table; it must be refreshed by hand because the source API is paid), forward
record before any real money. Fail → log it; one variant, chosen now: **(a) categories `insiders` and `privateSale` only**,
everything else unchanged. Then the line is closed and the APT/TIA bot is judged on its own forward record.

## Log (append-only)

| # | date | change | result |
|---|---|---|---|
