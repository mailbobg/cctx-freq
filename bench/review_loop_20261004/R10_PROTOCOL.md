# R10: fixed tokenized-gold sleeve, preregistered before gold prices/outcomes

## Hypothesis and critic

R8 filtering and R9 selection left a shared crypto downside path. A separately backed gold asset might diversify that exposure without shorting, leverage or funding income. This is an inference to test, not an established hedge. Paxos describes PAXG as allocated physical gold; custody, token dislocation and exchange execution remain risks. Binance's official listing announcement places PAXG/USDT trading at2020-08-28 12UTC, before all evaluated dates. No XAU proxy or other token will replace PAXG after outcomes.

Primary evidence: https://www.paxos.com/pax-gold ; https://docs.paxos.com/guides/stablecoin/paxg ; https://www.binance.com/en/support/announcement/detail/a8971e79c00f4350b1ab27c449e1f049 . These establish asset identity/availability, not profitability, present liquidity or guaranteed backing recovery.

## One frozen change

Retain BTC/ETH RiskHoldR2b raw signals (30-day momentum, SMA200 soft condition, original individual cap0.5). Add constant raw PAXG weight0.5, independent of its price momentum. Apply the existing30-day daily log-return covariance,365 annualization,10% volatility target and1.0 gross cap jointly to three assets. PAXG raw0.5 is a fixed diversified sleeve choice, not an optimized weight. Unknown/nonfinite covariance creates zero targets; do not forward-fill prices. No volatility target, threshold, lookback, band, stop, cash yield, leverage or universe sweep.

## Inputs and execution

Use unchanged frozen BTC/ETH spot archives from R8/R9 and2020 cached warmup. Acquire checksummed official Binance PAXGUSDT1d spot archives2020-09-01 through2026-08-31. Validate full calendar/OHLC/activity and the2025 microsecond timestamp schema. Preserve raw sources and output hashes. Genuine missing archive days require actual source repair or an explicit unavailable verdict, never interpolated prices.

Monday OPEN execution uses previous completed Sunday targets, original1% target reserve, original5pp adjustment band only when a positive existing holding has a positive target, sell-before-buy ordering and real available cash. Price zero/unknown cannot trade. End at final day's CLOSE with all closing costs. Generalizing the spot ledger to three assets must reproduce every two-asset R8 baseline equity/cash/exposure/fee/turnover curve to1e-12 before outcomes. Retain actual units per asset for risk diagnostics and accounting, rather than assigning all invested value to the worst coin.

Fixed early reused window2021-02-01–2023-12-31 and recent reused window2024-01-01–2026-08-31. Base all-in cost allowance0.0012 per side, doubled-cost0.0024 per side, all three assets equally; no Paxos creation/redemption costs are assumed for secondary-exchange purchases. A separate preregistered PAXG execution sensitivity uses0.0024 per side for PAXG with BTC/ETH0.0012. Do not interpret candle OPEN as an atomic guaranteed fill. Quote volume/activity checks are necessary but not market-impact proof. Price highs/lows are risk envelopes, not simultaneous multi-asset marks.

## Controls, tests and rejection

Unchanged two-asset Cov10 is the sole matched control. Verify flat round-trip costs, no short/credit cash, unit/P&L reconciliation, disabled-gold exact baseline parity, three-asset covariance gross/vol caps, missing-price blocking, causal weight and genuinely held execution prefixes. Initial equity1 must count as a possible peak. Required base CAGR>12% in BOTH windows, daily account MTM DD≤5%, aggregate profit after doubled costs and no execution/solvency/data failure. A passing research screen still requires actual Freqtrade execution parity, finer risk evaluation and independent forward proof before adoption. Never equate reused historical dates with fresh OOS.

If any necessary gate fails, retain results and reject R10 without PAXG weight, momentum, risk-target or cash substitutions. Do not combine failed R8/R9 features. This research does not authorize activating a new/live bot or modifying a running cohort.
