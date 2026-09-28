# X14: market-neutral flush rebound — long the flushed altcoins, short BTC (pre-registration)

Date: 2026-09-28. Status: draft for approval — no data download, no code, no backtest before this file is agreed.
The variant log at the bottom is append-only; acceptance criteria may not be edited after the first run.

## What this combines, and why now

Two things survived out-of-sample in this project:
- the **flush effect** (X11 event study, `bench/dip_event_study.py`, `bench/holdout_flush.py`): when BTC falls ≥ 0.5 % in an
  hour and ≥ 10 altcoins fall ≥ 4 % more than BTC in that hour, the flushed coins beat BTC over the next 24 h —
  2024–26 +2.2 % (t 4.7), 2021–23 holdout +1.5 % (t 2.6); positive in 2021, 2023, 2024, 2025, 2026, **negative in 2022**;
- the **two-leg, market-neutral execution** from P1 (`bench/p1_pairs.py`: perps, funding, costs, BTC correlation ≈ 0.1).

X11 traded the effect long-only and failed on the market leg: 2025 −30 % and a −33 % day (2025-10-10) when 90 % of equity
sat in one flush. X14 keeps the relative leg and hedges the market leg: long the flushed coins, short BTC for the same
notional, small size per episode.

Payer: forced sellers in a liquidation cascade dump the most volatile coins hardest; whoever supplies liquidity is paid
when the relative gap closes. The BTC short means we are not paid (or punished) for BTC's own direction.

## Honesty note on data

Every year 2021–2026 has been looked at for this effect: the detection rule and the 24 h horizon were chosen on
2024–26 and confirmed on 2021–23. The backtest below therefore tests **whether the known effect survives costs,
hedging, sizing and drawdown limits as a rule**, not whether the effect exists. 2022 is expected to lose (the excess
itself was negative that year); that is written here before running. The only true out-of-sample evidence will be the
forward paper record.

## Rules

**Venue.** Binance USDT-M perpetuals for both legs, 1x, isolated. Costs per leg per side: taker 0.05 % + slippage
0.02 %; funding on both legs every 8 h from the historical series (P1 assumptions).

**Universe (point in time).** Every Monday: the 60 USDT-M perpetuals (BTC excluded) with the highest quote volume over
the previous 30 days, ≥ 30 days of history, non-crypto perps and stablecoins excluded (Binance `underlyingType`,
as in P1). Breadth is measured on this universe.

**Detection (every closed 5-minute candle, perp prices).** `btc_1h` = BTC log return over the last 12 candles;
`x_1h(i)` = coin i's log return over the same 12 candles minus `btc_1h`; `breadth` = number of coins with
`x_1h ≤ −4 %`. A **flush** fires when `btc_1h ≤ −0.5 %` and `breadth ≥ max(6, 10 % of the universe)` (the X11 rule was
10 of 116 spot pairs = 8.6 %; 10 % of 60 = 6). Cooldown 4 h after a fire. Identical to X11 apart from the universe.

**Trade, at the open of the next 5-minute candle.**
- Long the 5 most-dropped coins (lowest `x_1h`), 4 % of equity each (20 % gross long per episode).
- Short BTC perp for the sum of the long notionals (1 : 1 dollar hedge).
- Exit all legs of the episode 24 h later at the open. No take-profit, no signal exit.
- Disaster stop on a long leg at −25 % from its entry (alt price, not spread); the matching share of the BTC short is
  closed at the same time.
- At most 2 episodes open at once (≤ 40 % long + 40 % short = 80 % gross). A third flush during that time is skipped.

Explicitly not done: regime filters, beta estimation (main spec), scaling by drop size, re-entry, leverage, spot legs.

## Validation

Engine: a basket extension of `bench/p1_pairs.py` (one BTC hedge leg per episode). Data: 5m perp klines for the weekly
universes and BTC from data.binance.vision (`futures/um` monthly 5m), 2020-10 → 2026-09-23; funding from P1.
1. Look-ahead self-check: detection and trades on data truncated at 50 random timestamps must equal the full run's
   decisions before those times (as in P1; the detector is backward-looking by construction).
2. Per calendar year 2021 … 2026 (each starting flat at 10,000 USDT) and the halves 2021–2023, 2024–2026.
3. Reported per window: return, MTM max drawdown, episodes, trades, win rate per episode, P&L split long leg / BTC leg /
   funding / costs, daily-return correlation with BTC, P&L without the 3 best episodes, stopped-leg P&L, P&L by year.
4. Fees ×2.
5. Neighbourhood, fixed now, reported only: breadth {5 %, 10 %, 15 %} × hold {12 h, 24 h, 48 h} — 9 cells.

## Acceptance criteria (all must hold)

1. 2021–2026 compounded return > 0, both halves > 0, and at least 5 of the 6 calendar years > 0.
2. Bootstrap 95 % CI of mean monthly return excludes 0.
3. MTM max drawdown ≤ 15 % in every year (X11's −33 % is the failure this design must fix).
4. |daily-return correlation with BTC| ≤ 0.3.
5. Fees ×2: 2021–2026 compounded return > 0.
6. P&L without the 3 best episodes > 0.
7. Neighbourhood: ≥ 6 of 9 cells with 2021–2026 return > 0.
8. ≥ 150 episodes over 2021–2026.

Expected (written before running): 40–150 episodes per year, gross ≈ +1.5 … +2.5 % of the long notional per episode
before costs (≈ 0.3 … 0.5 % of equity), so roughly +10 … +25 % per year in good years, 2022 negative, drawdowns of
5–15 %. Most likely failures: criterion 3 (a single episode where the flushed coins keep falling while BTC bounces —
the hedge then loses on both legs) or criterion 2 (edge too small for its noise once costs and funding are in).

## Decision

Pass → a paper bot on the server with its own two-leg runtime (not freqtrade), forward record from its start; the
forward record, not this backtest, decides anything further. Fail → log it; at most one variant, chosen now:
**(a) beta-weighted hedge** — BTC short notional = Σ βᵢ × long notionalᵢ, with βᵢ the 30-day hourly-return beta of coin i
to BTC at the formation Monday (1 : 1 under-hedges high-beta alts). Then close the flush line for good.

## Variants tried (append-only log)

| # | date | change | result |
|---|---|---|---|
