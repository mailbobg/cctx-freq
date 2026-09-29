# L1: short newly listed Binance USDT-M perpetuals, BTC-hedged (pre-registration)

Date: 2026-09-29. Status: approved by the user ("可以") before any data download, code or backtest. Append-only log at
the bottom; criteria may not be edited after the first run.

## Who pays

After a new token gets a Binance perpetual, supply reaching the market is one-sided: airdrop recipients, early
investors, market makers' loan inventory and launchpool farmers sell, while the listing hype pulls in late buyers.
New listings under-performing the market over the following weeks is one of the more consistently reported crypto
patterns. `unlock-short` bets on the same kind of supply for token-unlock events only; new listings give many more
events. BTC long of equal size removes the market direction.

Main risk: the occasional listing that squeezes 2–10× (low float, heavy short interest, negative funding that the
short pays).

## Data

- Prices: `data_p1/daily.parquet` (every USDT-M perpetual incl. delisted). Listing date = first daily bar (D0, a
  partial day).
- Funding: the same new download as F1 (`bench/fund_fetch.py`, monthly archives for every symbol used).
- Exclusions: non-crypto (`underlyingType != COIN`), stablecoins/index products, and re-listings / renames: a symbol
  whose base (after stripping a leading 1000/1000000/1M) already had a perpetual earlier is not a new listing.
- Events: D0 from 2021-02-01 to 2026-07-31 (so every event has its full holding window before 2026-08-31).

## Rules

- **Entry**: short the new coin at the daily close of D0 + 2 (48–72 h after listing), long BTC with equal notional.
- **Size**: 5 % of equity per leg per event; at most 10 events open at once (50 % gross per side); an event arriving
  when 10 are open is skipped.
- **Exit**: daily close of entry + 21 days; or disaster stop when the coin's daily close ≥ 1.5 × entry (both legs closed
  at that close). A coin whose data ends is closed at its last close.
- **Costs**: coin leg taker 0.05 % + slippage 0.10 % per side (new listings are thin); BTC leg 0.05 % + 0.02 %.
  Funding settlements on both legs (the short pays negative funding).

## Validation

One continuous mark-to-market run (daily), 10,000 USDT. Per calendar year 2021 … 2026 and halves 2021–2023 /
2024–2026; P&L split coin leg / BTC leg / funding; distribution of 21-day coin excess returns over BTC (share of events
where the coin beat BTC, share that doubled); fee ×2; BTC daily correlation; P&L without the 3 best events; look-ahead
self-check (event list and entries recomputed on data truncated at 50 random dates must be identical up to the cut).
Neighbourhood grid (fixed now): entry delay {1, 2, 5 days} × hold {14, 21, 28 days} — 9 cells.

## Acceptance (all must hold)

1. Both halves > 0 and at least 5 of 6 calendar years > 0.
2. Bootstrap 95 % CI of mean monthly return excludes 0.
3. Mark-to-market max drawdown ≤ 15 % in every year.
4. |BTC daily correlation| ≤ 0.3.
5. Fee ×2: full-window return > 0.
6. P&L without the 3 best events > 0.
7. Grid ≥ 6 of 9 cells > 0.
8. ≥ 150 events traded.

Expected (written before running): 150–400 events; median 21-day excess return of the coin vs BTC −10 … −20 %; a
right tail of squeezes; 2021 (alt season) the weakest year. Most likely failure: 1 (a listing-mania year), 3, or 6.

## Decision

Pass → paper bot on the server (futures; new listings detected from exchangeInfo, entry after the delay). Fail → log
it; one variant, chosen now: **(a) no disaster stop, size scaled so each event risks the same volatility (5 % ×
median / own 2-day post-listing range, capped at 5 %)**. Then the line is closed.

## Log (append-only)

| # | date | change | result |
|---|---|---|---|
| — | 2026-09-29 | **engine corrections made while reading results — disclosed in full.** (1) Ties between same-day listings competing for the last slot were broken by an unstable sort (look-ahead check 9/50); now symbol order. (2) The first engine let a short leg lose many times its notional (COAI 2025-09/10: 0.21 → 7.8, −99,684 on a 693 leg) — impossible under the 1x isolated margin every futures spec here uses; added liquidation. (3) Liquidation first checked on daily closes only; new listings wick far above the close, so it now triggers when the **daily high** reaches 1.95× entry (maintenance margin of new listings), losing the whole margin (daily highs via `bench/hl_fetch.py`, closes match daily.parquet 100 %). Intermediate results, for the record: after (1) main −5.2 %, variant +210.5 %; after (2) main +42.8 %, variant +202.8 % (all 8 criteria passed); after (3) the rows below. (3) is the realistic model and the only one judged. | — |
| 1 | 2026-09-29 | **main spec** (engine `bench/l1_listings.py`) | **FAIL on 1, 2, 3.** 2021-02 → 2026-08: +12.6 %, MTM maxDD −57.9 %, 482 events (591 listings found; 109 skipped at 10 open). Years: **2021 −13.6** / 2022 +17.3 / 2023 +11.0 / 2024 +31.6 / **2025 −14.8 / 2026 −10.7 %**; halves +12.4 % / +0.2 %. CI [−1.34, +2.43]. Fee ×2 +7.3 %. Grid 9 of 9 > 0. BTC corr −0.00. Exits: 382 time +55,097; 62 close-stops −24,448; 35 liquidations −29,469. Event study (all 591 listings, 21 days after D0+2): median coin − BTC −16.8 %, 74 % under-perform BTC, 8.3 % beat it by > 50 %, 3.7 % by > 100 %. Look-ahead 50/50. |
| 2 | 2026-09-29 | pre-registered variant (a): no stop, volatility-scaled size | **FAIL on 1, 2, 3.** +72.4 %, maxDD −24.9 %, 476 events. Years: **2021 −5.6** / 2022 +18.2 / 2023 +11.1 / 2024 +28.5 / 2025 +13.2 / **2026 −4.4 %** (4 of 6); halves +23.9 % / +39.1 %. CI [−0.31, +2.34]. MTM maxDD per year −11.3 / −5.2 / −13.1 / −14.0 / **−22.2 / −18.9 %**. Passes 4 (corr −0.03), 5 (fee ×2 +66.5 %), 6 (without top-3 +5,590), 7 (grid 9 of 9 > 0, +27 … +72 %), 8. Exits: 406 time +37,797 vs 67 liquidations −30,698; funding −3,148 (new listings' shorts pay). |

## Verdict (2026-09-29)

Fail, but the closest of this round. The under-performance of new listings is large and stable (median −17 % vs BTC in three
weeks, three quarters of listings) and every grid cell of both rules is positive over the window; what fails is the tail: a
1x short loses its whole margin in the one listing in fourteen that doubles intraday, and 2021 and 2026 end negative with the
2025 drawdown above 15 %. Per the spec the line is closed. Anything further (e.g. a smaller short per event with more margin
so liquidation sits at 3×, or options instead of shorts) would be a new spec and could only be tested forward, since
2021–2026 has now been seen.
