# X14-V: does the hedged flush rebound still work on less efficient venues? (pre-registration)

Date: 2026-09-28. Status: draft for approval — no data download, no code changes beyond parametrising the data root,
no backtest before this file is agreed. Append-only log at the bottom; criteria may not be edited after the first run.

## Question

X14 on Binance USDT-M perps: +22 … +54 % every year 2021–2024, robust across the 9-cell neighbourhood, then
−19 % in 2025 and −4 % in 2026 (spec `2026-09-28-x14-hedged-flush-design.md`). Hypothesis: the effect did not
disappear from crypto, it disappeared from Binance, which since 2025 is dominated by professional liquidity providers
and a faster liquidation engine. On a venue with more retail flow and fewer professional takers, the same forced
sellers should still be paying for liquidity in 2025–26.

Venues (chosen for free, deep 5m history via public APIs, verified 2026-09-28):
- **Bitget USDT-M perpetuals** — retail/copy-trading heavy, mid-tier: the "less efficient" candidate;
- **Bybit USDT linear perpetuals** — second-tier by volume: the control (if it also failed in 2025–26, the change is
  market-wide, not Binance-specific).
Hyperliquid was the first choice but has no free candle history before 2025-07 (S3 archive is requester-pays and
holds L2 books only); it may be tested later on the 14 months that are available.

## Rules

Identical to X14's main spec (detection, sizing, 24 h exit, −25 % leg stop, ≤ 2 episodes, dollar hedge), applied
per venue with venue prices, venue funding and venue costs:
- costs per leg per side: taker fee 0.055 % (Bybit) / 0.06 % (Bitget) + slippage 0.03 % (thinner books than Binance);
- universe: weekly top-60 perpetuals by 30-day quote volume on that venue (BTC excluded from longs, stablecoin and
  non-crypto bases excluded), ≥ 30 days of history;
- window: 2022-01-01 → 2026-09-23 (both APIs start in 2022 for 5m history).
Nothing is tuned per venue. The engine is `bench/x14_flush.py` with a different data root.

## Validation (per venue)

Per calendar year 2022 … 2026, halves 2022–2023 / 2024–2026, full window; fee ×2; the 9-cell neighbourhood;
look-ahead self-check 50/50; P&L split long / hedge / funding; BTC correlation.

## Acceptance (per venue; all must hold)

1. **2025 > 0 and 2026 > 0** — the discriminating test; each at 1m-free 5m resolution as in X14.
2. At least 4 of the 5 calendar years > 0 and both halves > 0.
3. Bootstrap 95 % CI of mean monthly return excludes 0.
4. MTM max drawdown ≤ 15 % in every year.
5. |BTC correlation| ≤ 0.3.
6. Fee ×2: full-window return > 0.
7. P&L without the 3 best episodes > 0.
8. Neighbourhood ≥ 6 of 9 cells > 0.
9. ≥ 120 episodes over the window.

Expected (written before running): Bybit behaves like Binance (2022–24 positive, 2025–26 flat or negative);
Bitget positive in 2025–26 if the venue-efficiency hypothesis is right. Most likely failure: Bitget also negative in
2025 (then the decay is market-wide and the flush line stays closed), or Bitget passes returns but fails drawdown
(thinner books → deeper cascades, more −25 % stops).

## Decision

Bitget passes → paper bot on Bitget (freqtrade supports it; a dry-run there needs no account) and, after a
forward record, a small live test on that venue. Bybit alone passes → same on Bybit. Neither → the flush line is
closed for all venues; no further variants (none pre-registered).

## Log (append-only)

| # | date | venue | result |
|---|---|---|---|
