# C2: does deciding more often than weekly improve C? (pre-registration)

Date: 2026-09-27, written before any run. Append-only log at the bottom.

## Question

C (weekly TSMOM30 + 60% vol target on BTC/ETH) beat 50/50 holding on return, Sharpe and bear-year loss but missed
"max drawdown ≤ half of holding's" (56%). The user asks for a higher-frequency version. Hypothesis: re-deciding more
often exits bear markets faster (lower drawdown) at the cost of more turnover and more whipsaws.

## Variants (all rules identical to C except the decision interval; nothing else is tuned)

- **W** (baseline, = C): decide on the Sunday daily close, trade Monday open.
- **D**: decide on every daily close, trade the next daily open.
- **H4**: decide on every 4-hour close, trade the next 4h open.
Signals always use the same *calendar* windows: 30-day return and 30-day realised volatility (for H4: 180 bars,
annualised with √(6·365)). Weight = 0.5 × min(1, 0.60 / σ) when the 30-day return > 0, else 0. Rebalance a coin only
when target and current weight differ by > 5 percentage points. Fee 0.1% per traded notional; also 0.2%.

## Data honesty

2017-09 → 2020-12 was used once already (for C's weekly rule). It is no longer untouched; the only new element is the
decision interval. 2021-01 → 2026-09 is in-sample. There is no clean holdout left for BTC/ETH on Binance.

## Acceptance (a faster variant replaces W in the c-hold bot only if all hold, in BOTH windows)

1. Max drawdown lower than W's.
2. Sharpe ≥ W's.
3. CAGR ≥ 90% of W's.
4. Same three at fee 0.2%.
Reported: trades per year, turnover, share of time invested, worst year, max drawdown vs 50/50 holding.
If neither D nor H4 passes, c-hold stays weekly. No other intervals or parameters are tried.

## Result log (append-only)

| date | variant | result |
|---|---|---|
| 2026-09-27 | D (daily) | **FAIL** in both windows. 2017–20: CAGR +72.4% vs W +79.3%, maxDD −49.8% vs −49.4%, Sharpe 1.42 vs 1.58, 77 trades/yr vs 34. 2021–26: +38.7% vs +44.7%, maxDD −41.2% vs −32.5%, Sharpe 1.08 vs 1.18. Worse at fee 0.2%. |
| 2026-09-27 | H4 (4-hourly) | **FAIL.** 2017–20: CAGR +79.2%, maxDD −45.2% (better), Sharpe 1.51 (< 1.58); 2021–26: +42.6%, maxDD −35.8% (worse), Sharpe 1.17; 144 trades/yr. Fee 0.2%: 2021–26 CAGR +34.1% (< 90% of W), Sharpe 0.99. |
| — | 2026-09-27 | **verdict** | Deciding more often adds turnover (2–4×) and whipsaws without improving risk-adjusted return; the weekly rule stays in c-hold. |
