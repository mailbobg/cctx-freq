# Strategy fixes and risk experiments — 2026-09-27

## Implemented fixes

- `RiskHoldC`: use cash plus marked holdings for sizing; current callback price for the active pair, bid quotes for other positions in dry/live mode, analyzed closed prices in historical mode. Missing quotes skip sizing. Partial reductions now convert sale value into Freqtrade's cost-basis stake units. Do not repeat a filled rebalance within the decision day; retain existing weekly signal and band.
- `bots/common.json`: stoploss, emergency_exit and force_exit are market orders. Ordinary entry/exit remain limit. Bot-side stoploss remains bot-side; this change is not a claim of exchange-hosted protection.
- `E0V1EN`: 400 warmup bars cover the 288-bar return; earlier 200 was insufficient.
- `bench/mtm.py`: reconstruct order notional from amount × fill price and charge each side's fee once. Exported backtest `cost` already includes an entry-style fee, even for sells. Reject missing valuation data and unsupported short/leveraged accounting. Include initial capital in the drawdown peak.
- `bench/evaluate.py`: label legacy IID-trade interval, add 7/30-day portfolio realized-P&L block intervals and entry-tag attribution, retain zero-trade months. `summarize_backtests.py` separately computes actual daily MTM intervals and verifies final P&L reconciliation.

## Candidate decisions

Parameters and adoption gate were fixed in `PLAN.md` before outcomes were inspected. No parameter sweep. All windows are reused history, not untouched OOS.

### C-hold covariance cap — rejected as replacement

Daily historical closes/opens, weekly decisions, common 1% reserve, same 5pp rebalance band, no negative cash. This is a matched portfolio simulator, not an end-to-end Freqtrade backtest. Inputs: cached `events/c_daily.parquet`.

| Window | Single-side fee | Baseline return / max DD | Candidate return / max DD |
|---|---|---|---|
| 2017-09-18 → 2020-12-31 | 0.1% | +575.34% / -49.06% | +430.70% / -39.87% |
| 2017-09-18 → 2020-12-31 | 0.2% | +554.83% / -49.72% | +416.00% / -40.57% |
| 2021-01-01 → 2026-09-21 | 0.1% | +713.57% / -32.21% | +440.98% / -28.33% |
| 2021-01-01 → 2026-09-21 | 0.2% | +665.86% / -32.82% | +412.61% / -28.89% |

Candidate retains only 62–75% of baseline cumulative return, below the preregistered 90% gate. Later-window Sharpe also declines (1.18 → 1.16 at 0.1%). Kept as a research file, not selected for running bots. Original research figures differ slightly because this matched simulator enforces the shared cash reserve and fee affordability for both sides.

### Dip bounded-loss candidate — rejected

2026-01-01 → 2026-09-23, all 118 locally available spot pairs, one-minute detail, protections enabled, 0.1% per side, 10,000 USDT starting capital. Compare E0V1ENX13 fixed-ROI baseline with the same entry rule plus 10% stop and unconditional 24h exit. Both use corrected 400-bar warmup and identical order settings.

| Strategy | Trades | Return | Daily MTM max DD |
|---|---:|---:|---:|
| E0V1ENX13 | 891 | -31.82% | -36.46% |
| E0V1ENBounded | 910 | -37.44% | -42.42% |

Shorter holding time (3h44 → 1h51 average) and lower peak daily exposure did not improve return or drawdown. No additional tuning or expanded tests are justified by this failed screen; not deployed. This is a comparison against fixed-ROI X13, not a claim about the original trailing-stop baseline.

### X8 budget candidate — rejected

2026-01-01 → 2026-09-23, all 116 configured spot pairs, one-minute detail, 0.1% per side, 10,000 USDT. Both strategies use the same liquidity cap and corrected emergency/stop order settings; this is a fresh matched rerun, not a comparison to an archived headline.

| Strategy | Trades | Return | Daily MTM max DD | Max daily exposure |
|---|---:|---:|---:|---:|
| NostalgiaForInfinityX8PF | 177 | +39.95% | -2.93% | 27.61% |
| NostalgiaForInfinityX8Budget | 174 | +20.18% | -3.91% | 35.65% |

Candidate retains 50.5% of baseline return; drawdown and peak daily exposure both worsen. Average holding duration increases from 1d3h30 to 2d0h58. The result rejects this particular combination of budget/add restrictions: restricting additions can extend losing holdings and keep portfolio capital tied up. It does not prove every possible restriction is harmful. No promotion and no further parameter search; the failed first screen does not justify expanding to other years/double fees.

## Final adoption decision

All three candidate changes fail the predeclared adoption gate and remain research-only. Keep the original strategy selection; retain the verified sizing, exit-configuration, warmup and evaluation fixes. C-hold/E0V1EN bug fixes are active in their existing dry-run processes; no new strategy was activated. X8 remains stopped. All launched research jobs completed (exit 0); no task-owned backtest is left running.

## Validation and runtime

17 offline unittest regressions pass. They cover cost-basis partial exits above/below entry price, MTM sizing, missing prices, historical-vs-live data boundary, rebalance deduplication, covariance sizing/no lookahead, candidate holding deadline, budget add restrictions with preserved reductions, fee accounting and timezone-safe daily grouping. The venv lacks pytest; an attempted pytest invocation failed with `No module named pytest`. No claim is made that the upstream Freqtrade suite passed.

Resolved strategy/config loading and Python compilation also pass. C-hold and E0V1EN were reloaded after the fixes; their APIs confirm running dry-run mode and market stop/emergency exits. X8 was already stopped when runtime activation was checked and was left stopped. Runtime snapshot is in `runtime_verified.json`. E0V1EN's log showed a Binance WebSocket remote-close 1008 after reload; subsequent heartbeats continued. Network transport was not changed by this task.

Existing X8 2024–2026 exports were re-audited with corrected MTM accounting; final cash/holdings P&L matches exported trade totals within 0.000001 USDT. The 2024 daily-MTM block intervals include zero (7d approximately -16.6% to +69.6%, 30d -21.3% to +83.9%). Intervals are exploratory and do not adjust for repeated strategy selection.

## Files and reproduction

- `originals/`: pre-edit source/config backups (no credentials).
- `changes.patch`: reviewable local source changes; these paths remain ignored by the repository, so `git status` does not display them.
- `c_results.json` and CSV curves: full C comparison outputs.
- `*_summary.json` and `*_equity.csv`: spot backtest diagnostics.
- `source_hashes.json`: strategy code fingerprints for this experiment.
- Run tests: `.venv/bin/python user_data/bench/test_review_risk.py`.
- Reproduce C: `.venv/bin/python user_data/bench/review_20260927/compare_c.py`.
- Reproduce dip/X8: `.venv/bin/python user_data/bench/review_20260927/run_comparison.py dip` (or `x8`), optional `--fee 0.002` and `--timerange`.

Risk limitations: daily MTM misses intraday extremes; one-minute OHLCV does not model order-book queue/impact. Some local informative daily datasets end on September 21 while the Freqtrade run ends on September 23. Dry-run does not prove live execution quality. Candidate tests do not establish future profitability.
