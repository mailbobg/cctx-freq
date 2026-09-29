# F1: cross-sectional funding carry on Binance USDT-M perpetuals (pre-registration)

Date: 2026-09-29. Status: approved by the user ("可以") before any data download, code or backtest. Append-only log at
the bottom; criteria may not be edited after the first run.

## Who pays

Leveraged longs who crowd into a coin pay funding every 8 h (sometimes 4 h or 1 h) to whoever is short. Crowding also
tends to precede under-performance (the late buyers are the marginal price setters). Short the coins with the highest
funding, long the coins with the lowest (often negative) funding, dollar-neutral: collect the funding spread and any
price reversal of the crowding. Literature: the carry factor in crypto perpetuals (funding / basis sorts). This is a
relative-value trade on a different mechanism from every price-signal strategy tested so far.

Main risk: short squeezes on the high-funding side (the crowd is right for a while longer, the coin doubles).

## Data

- Prices and volumes: `data_p1/daily.parquet` (1d klines of every USDT-M perpetual incl. delisted, data.binance.vision).
- Funding: monthly `fundingRate` archives from data.binance.vision for every symbol that enters the universe
  (new download, `bench/fund_fetch.py`), 2020-12 → 2026-08.
- Non-crypto perpetuals (stocks, commodities, FX, indexes) excluded by `underlyingType != COIN` (exchangeInfo snapshot)
  and the stablecoin/index lists of `p1_fetch.py`.
- Window: 2021-02-01 → 2026-08-31 (funding archives end 2026-08).

## Rules

- **Universe** (every Monday 00:00 UTC): the 40 perpetuals with the highest quote volume over the previous 30 days,
  ≥ 60 days of history, exclusions above.
- **Signal**: mean funding rate per settlement over the previous 7 days (settlements strictly before Monday 00:00),
  annualised by the settlement count for display only.
- **Portfolio**: short the 8 highest-signal coins, long the 8 lowest; equal weight within a side; each side 50 % of
  equity (gross 100 %, 1x, net zero dollars). Entries at the Monday 00:00 price (= Sunday's daily close); held one week;
  rebalanced every Monday to the new target weights (only the difference is traded).
- **P&L**: daily close-to-close price P&L plus every funding settlement on held positions (longs pay positive rates,
  shorts receive them). A 1x short is liquidated when a daily close reaches 2× its entry (loss = its notional);
  a coin whose data ends is closed at its last close.
- **Costs**: taker fee 0.05 % + slippage 0.05 % per side on traded notional (mid-caps are thinner than BTC/ETH).

## Validation

Per calendar year 2021 … 2026 and halves 2021–2023 / 2024–2026 measured on one continuous mark-to-market run (daily);
P&L split price vs funding, long side vs short side; turnover; fee ×2; BTC daily correlation; P&L without the 3 best
coins; look-ahead self-check (signal and universe recomputed on data truncated at 50 random Mondays must be identical).
Neighbourhood grid (fixed now): names per side {5, 8, 10} × signal lookback {3, 7, 14 days} — 9 cells.

## Acceptance (all must hold)

1. Both halves > 0 and at least 5 of 6 calendar years > 0.
2. Bootstrap 95 % CI of mean monthly return excludes 0.
3. Mark-to-market max drawdown ≤ 15 % in every year.
4. |BTC daily correlation| ≤ 0.3.
5. Fee ×2: full-window return > 0.
6. P&L without the 3 best coins > 0.
7. Grid ≥ 6 of 9 cells > 0.
8. Funding P&L > 0 over the full window (the mechanism must be the one claimed).

Expected (written before running): funding income +5 … +15 % a year in 2021 and early 2024 (high-leverage bull
phases), small in 2022–23 and 2025–26; price P&L of the short side negative in squeezes. Most likely failure: 3
(squeeze drawdowns) or 1 (years where the high-funding coins keep rising).

## Decision

Pass → paper bot on the server (futures, weekly rebalance, reusing freqtrade with a custom rebalance strategy or the
two-leg runtime). Fail → log it; one variant, chosen now: **(a) inverse-volatility weights within each side (60-day
daily vol), everything else unchanged** — aimed at the squeeze tail. Then the line is closed.

## Log (append-only)

| # | date | change | result |
|---|---|---|---|
