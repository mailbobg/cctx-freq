# P1: crypto pairs trading on Binance USDT-M perpetuals (pre-registration)

Date: 2026-09-28. Status: draft for approval — no data download, no code, no backtest before this file is agreed.
The variant log at the bottom is append-only; acceptance criteria may not be edited after the first run.

## Why this, and who pays

Everything we tried so far is long-only on altcoins, and every one of those loses in bear years (2022, 2026). The one
effect that replicated on both 2024–26 and the 2021–23 holdout is relative: after a sharp drop *relative to BTC*,
the coin tends to recover the gap (X11 flush study: +1.5–2.2% excess over BTC within 24 h) — but trading it long-only
also carried the market's direction, which is what lost money in 2022.

Pairs trading isolates the relative part: long the coin that fell behind, short the partner that did not, so the
market direction cancels. Payer: traders forced or in a hurry to move one specific coin (liquidations, unlock and
listing flows, rotation between similar coins) push its price away from close substitutes; whoever takes the other
side of that temporary gap is paid when it closes. Academic basis: distance-method pairs trading (Gatev, Goetzmann &
Rouwenhorst 2006) and its crypto replications; the anomaly is published, so decay is expected and priced in below.

Main risk: the gap does not close because something structural changed (LUNA/UST, FTT, delistings, a hack). That is
the tail this design must survive.

## What has been seen already (honesty note)

The Binance *spot* 2021–2026 data has been used for other strategies, but never for relative-value / pair spreads,
and no perpetual-futures price series for 2021–2023 has been loaded in this project. Nothing below is tuned on data:
every parameter is fixed now from the textbook method. Both 2021–2023 and 2024–2026 must pass on their own.

## Rules

**Venue and costs.** Binance USDT-M perpetuals, isolated margin, 1x per leg (no leverage). Costs per leg per side:
taker fee 0.05% + slippage 0.02%; funding paid/received every 8 h on both legs from the historical funding series.

**Universe (point in time, delisted included).** At each formation date: the 20 USDT-M perpetuals with the highest
quote volume over the previous 30 days, excluding stablecoin/fiat bases and index/leveraged products, each with at least
90 days of history. Data: data.binance.vision `futures/um` monthly 1h klines and funding rates, 2020-10 → 2026-09.

*Scope clarification added 2026-09-28 before any backtest run:* Binance now lists perpetuals on tokenized stocks,
commodities and FX (MSTR, QQQ, XAU, XAG, CL, SNDK, SK Hynix …). These are not crypto and are excluded by Binance's
own `underlyingType` field (keep only `COIN`; 13 symbols dropped from the raw top-20 lists, all in 2025–26).
Symbols delisted before that field existed are all crypto and stay. The monthly 1d archives end at 2026-08-31, so the
three formation Mondays 2026-09-07/14/21 carry forward the 2026-08-31 universe (past information only).

**Formation (every Monday 00:00 UTC).** For every pair of the 20 coins (190 pairs), over the previous 60 days of hourly
closes: normalise each price to 1 at the window start; distance = sum of squared differences of the two normalised
series (Gatev's distance method). Keep the 5 pairs with the smallest distance, provided their hourly log-return
correlation over the window is ≥ 0.7. Hedge ratio β = OLS slope of log(A) on log(B) over the window.

**Trading (hourly, on closed candles, until the next formation).** Spread s = log(A) − β·log(B); z = (s − mean) / std with
mean and std from the formation window (frozen for the week).
- Entry when |z| ≥ 2: short the rich leg, long the cheap leg, dollar amounts in the ratio 1 : β (normalised so the pair
  uses 20% of equity gross: 10% per leg for β = 1).
- Exit when z crosses 0.
- Stop when |z| ≥ 4, or after 7 days in the trade, or when the pair drops out of the next formation while open
  (closed at that Monday's formation).
- At most one open position per pair, at most 5 pairs open (≤ 100% gross, 1x). A coin may appear in several pairs.
- Entries and exits at the open of the next hourly candle after the signal candle closes.

Explicitly not done: cointegration tests, Kalman or rolling hedge ratios, volatility filters, regime filters, per-pair
parameter tuning, leverage.

## Validation

Implemented as a stand-alone event-driven backtest script (`bench/p1_pairs.py`) — freqtrade cannot hold two linked legs;
a live version would reuse the two-leg paper runtime that already exists for funding-spread (`cross_venue/`).
1. Look-ahead self-check: rerun the formation and signal code on data truncated at 50 random timestamps; decisions must
   be identical.
2. Per calendar year 2021 … 2026 (each year starts flat with 10,000 USDT), plus the two halves 2021–2023 and 2024–2026.
3. Reported for each: return, max mark-to-market drawdown, trades, win rate, average holding time, share of P&L from
   funding, daily-return correlation with BTC, P&L by pair (top 3 pairs' share), P&L of stopped-out trades.
4. Fees ×2 (0.10% + 0.02% per leg per side).
5. Robustness grid, fixed now, reported and judged only as "does the result survive nearby settings":
   entry z {1.5, 2, 2.5} × formation window {30, 60, 90 days} — 9 cells.

## Acceptance criteria (all must hold)

1. Both halves (2021–2023 and 2024–2026) net > 0, and at least 5 of the 6 calendar years > 0.
2. Bootstrap 95% CI of mean monthly return (2021–2026) excludes 0.
3. Max mark-to-market drawdown ≤ 15% in every year.
4. |correlation of daily returns with BTC| ≤ 0.3 over 2021–2026 (it must actually be market-neutral).
5. Fees ×2: 2021–2026 compounded return > 0.
6. P&L with the 3 best pairs removed > 0 (not carried by one relationship).
7. Robustness grid: ≥ 6 of 9 cells with 2021–2026 compounded return > 0.
8. At least 150 closed pair trades over 2021–2026.

Expected (written before running): 100–250 pair trades per year; average net per trade +0.3 … +1% of the pair's gross;
funding a small net cost; the worst losses in 2022 (LUNA May, FTX November) from stopped spreads. Most likely failure:
criterion 1 or 3 — a structural break costs more than a year of reversions — or criterion 2 because the edge is small
relative to its noise.

## Decision

Pass → a two-leg paper bot (reusing `cross_venue/` engine, Binance USDT-M only) on the server, forward record from its
start; small live money only after a separate review of the live two-leg execution. Fail → log it; at most one variant,
chosen now: **(a) same rules, but pair selection by cointegration instead of distance** — keep the 5 pairs with the
lowest Engle–Granger p-value (must be < 0.05) on the 60-day hourly log prices, correlation filter unchanged. (A
"same-sector" variant was considered and rejected: point-in-time sector labels for 2021–2026 are not available, and
today's labels would leak the future.) Then close the line.

## Variants tried (append-only log)

| # | date | change | result |
|---|---|---|---|
