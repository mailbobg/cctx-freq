# Review loop, frozen before new trial outcomes — 2026-10-04

Scope: 13 configured paper bots besides the newly started E0V1ENWait3 cohort and the stopped live account. Review configurations, execution, economic mechanism, historical failures and forward records before new experiments. Do not activate research variants or alter an ongoing cohort's trading rule silently.

Selection default, pending the optional user risk answer: net annualized return >12%, marked equity drawdown <=5%, positive aggregate return at doubled costs, credible cross-year evidence. Historical screens cannot establish future profitability. All previously inspected 2017–2026 history is reused; no untouched historical OOS claim. A candidate requires an independent forward cohort before adoption. Failure is retained and cannot be repaired by changing the threshold after seeing the result.

Accounting: include initial capital in peaks and returns; mark open positions; charge every buy/sell including partial orders; prohibit negative cash and hidden leverage; sell before buying; no interest on idle cash. Use identical dates, symbols and execution assumptions for each comparison. Report daily-close drawdown as daily-close drawdown; it understates intraday risk.

Engineering defects are separate from alpha: X14 disabled the Freqtrade dispatcher for its own custom exits. Restore dispatch and verify the original 24h / 25% loss rules without changing their numbers. Keep all three X14 bots stopped.

## Trial R1: portfolio risk limit on frozen R2b

Hypothesis: R2b's independent per-coin sizing misses BTC/ETH correlation. Scaling its existing weights together to a fixed 10% annualized portfolio volatility target may meet a small drawdown mandate without introducing a new predictor.

One change: use 30 completed daily log-return observations to estimate covariance; multiply the frozen R2b weights by min(1, 0.10 / estimated portfolio volatility). Preserve 30-day momentum, SMA200 half-size filter, Monday decision using Sunday close, 60% per-coin volatility target, 50% per-coin cap and 5pp rebalance band. No volatility target sweep. This is a new mandate screen, not a revival of the failed C 40% covariance candidate under its old return-retention gate.

Data: already cached `bench/events/c_daily.parquet`; common windows 2018-03-05–2020-12-31 and 2021-01-01–2026-09-21. Per-side fee 0.1% plus 0.02% slippage allowance; stress doubles both to 0.24% total per side. Common 1% cash reserve. Closed daily prices for signals; next Monday open for execution. Compare C, R2b and R2b risk limit in the same cash-constrained simulator.

Controls: verify prefix causality and matched implementation of R2b signals; initial-capital fees must affect return; cash cannot be negative even when targets sum to one. If target allocation changes by less than 5pp, retain the existing band rather than tuning it to rescue performance.

Falsification: candidate misses either return or drawdown default gate in either window, loses at doubled cost, or needs retuned momentum/volatility/band. Stop this version after the first full screen; retain the result. A positive screen remains research-only until real execution and new forward data support it.

## Next hypotheses, not yet experiments

- X8: separate the previous combined restriction into a cost-budget-only experiment while preserving loss additions. First audit 2021 concentration and recent 1m detail; 2021 holdout currently has no 1m data. Do not treat 5m holdout as equal-precision evidence.
- Event shorts: separate unlock-pressure information from the generic short-alt/BTC-long control. Frozen L2P and U2 forward cohorts are too young; account for hedge granularity, current versus entry notional, funding and mark-to-market sizing before reading their profits as an edge.
- Funding-spread: expand evidence only if a new sample supports funding persistence exceeding all four leg fees and basis risk. Existing 84-day BTC/ETH sample produced zero cost-qualified opportunities; reducing its fee gate after that failure is not an improvement.

Every loop records hypothesis -> implementation -> deterministic result -> failure explanation -> next evidence requirement. No success quota, no omission of failed trials.

Execution correction after the initial R1/R3 run: the actual C/R2b bot uses its5pp band only for adjustments to an existing positive-target position. New entries and zero-target full exits bypass it. Two behavior regressions fail against the old research engine and pass after correction. Old outputs remain under `superseded_engine_band_all/`; the corrected rerun keeps all strategy numbers and selection thresholds fixed. This is an engine correction, not a new profitable rule.

R1 execution-validation step, frozen before Freqtrade outcomes: implement the exact R1 daily indicator weights as a research-only Freqtrade subclass, inheriting the original R2b callbacks and Monday timing. Validate whole-frame/prefix weight equivalence with the independent research function, fail closed for missing companion prices, then run R2b and the covariance candidate together over both windows using cached daily BTC/ETH,10,000USDT,2slots,0.99tradable ratio and0.12% each-side all-in costs. Repeat unchanged at doubled0.24% side costs to validate the original stress screen in actual execution. Compare actual exported-order daily MTM to the research engine; investigate material discrepancies rather than labeling them rounding. This does not reopen R1's failed5% adoption gate or authorize another bot. The candidate has no configuration under `bots/` and is not activated.

Historical state isolation: NFI's persisted profit-target cache must never seed a backtest. X7PF/X8PF use a fresh memory cache per historical instance and per bot_start; dry/live persistence stays unchanged. This correction precedes any admissible new R2 outcome. A future-dated cache entry was found while the aborted old R2 job simulated2024; its effect size is unknown, so that job is not used to select or reject a strategy.

## Trial R2: isolate X8's single-name cost cap

Frozen before execution: preserve original X8PF entries, exits, reductions and loss additions, but cap initial and subsequent invested cost in each coin at 5% of realized bot capital. This separates the cost cap from the loss-addition ban in the failed September 27 combined variant. The 5% cap follows the default single-name catastrophic-loss budget; it is not a 5% portfolio drawdown guarantee. Several names can lose together. No cap sweep and no new profit/stop threshold.

First screens: 2024 on the existing 116-pair static universe with full 1m detail, then 2021 on its historical 120-pair universe with available 5m data. Both include the same volume-share cap as baseline, 6 slots, 10,000 USDT, protections and 0.12% all-in side cost. The 2021 precision mismatch is explicit; a pass must be rerun with finer data before promotion. If either first screen has net <=0, CAGR <=12% or daily-close MTM drawdown >5%, reject this version, do not tune its cap to the result. If both pass, expand to the remaining years and double costs. Baseline and candidate must be rerun together; past profit headlines are not matched controls.

## Trial R3: daily loss-budget overlay on R1

Frozen before outcomes: R1 missed the default drawdown threshold despite profitable long-period returns. This tests a different mechanism, daily risk reduction based on remaining account loss budget; it does not sweep a lower covariance target.

Keep R1 signals, covariance target and weekly 5pp band. Every daily open, using equity at the executable open and a peak updated only from previous daily closes, compute gross allocation ceiling = min(1, 3 * max(equity -0.952 * peak, 0) / equity). Scale desired holdings together down to that ceiling after the ordinary weekly band calculation. Protective reductions may override the band; new/additional investments still occur on Monday only. No resets of the peak/floor after a loss. Flat cash cannot regain risk budget by itself. The multiplier3 and95.2% reference floor are fixed; the0.2pp buffer acknowledges costs. Neither protects against gaps nor guarantees intraday drawdown.

Same two windows, same cash/cost engine and doubled costs as R1. The disabled overlay must exactly reproduce R1's curve; prefix execution must reproduce the past unchanged. Reject if either window misses CAGR>12% or daily DD<=5%, or if double costs make the whole window negative. Do not add monthly floor resets or change the multiplier after seeing results.

## Trial R4: daily volatility reductions, weekly signal and additions

Frozen before execution: R1 samples risk and trades only weekly, leaving holdings to drift beyond its covariance budget between Mondays. Test the scheduling failure directly rather than lowering the volatility target. Preserve corrected R1 entry signals, weekly positive-target band,10% covariance risk target and Monday additions. Each daily open, compute desired Monday holdings (or keep current holdings on other days), then scale those desired holdings together down if their estimated annualized volatility exceeds10%, using the covariance known at the preceding daily close. Daily protection can only reduce risk; no off-Monday additions and no loss-floor reset. Gross exposure cannot exceed100%. Protective trims bypass the ordinary adjustment band.

Same two reused windows,0.12% and doubled0.24% each-side costs, no cash interest. The disabled overlay must reproduce corrected R1 exactly; truncated historical input must reproduce the same execution prefix. The first complete screen rejects the version if either window misses net CAGR>12% / daily MTM DD<=5%, or aggregate doubled-cost returns turn negative. No new volatility target, covariance length or rebalance-band sweep after the outcome. A pass still requires independent forward evidence and exchange execution reconciliation.

## Trial R5: new venue data, fixed broader funding universe

Frozen before data collection: BTC,ETH,SOL,XRP,BNB,ADA,DOGE,AVAX on Bybit/OKX USDT perpetuals;2026-07-05–2026-10-04 UTC, no replacement of an unavailable instrument after seeing its rates. This is a necessary-condition expansion of the prior funding-only screen, not a new untouched holdout. BTC/ETH overlap the earlier sample, and the other assets were selected as common major markets after other strategy studies.

Keep the original7-day trailing realized-funding forecast, Monday01UTC decision (one-hour rate-availability assumption), next7-day holding horizon and38bp per-leg-notional hurdle: four5bp fees + four2bp adverse fills +10bp basis allowance. Direction uses only rates settled by Monday00UTC. Preserve the original complete8h UTC history validator; any different clock, missing history or unavailable instrument is flagged as unreviewed input, never zero-filled or silently dropped. Raw API pages and hashes must be saved; a repeated run uses the saved pages.

If no entries exceed the unchanged cost hurdle, reject this horizon/universe screen. If entries exist, they do not prove profit: obtain actual venue mark prices and executable quotes, use matched base quantities, calculate basis P&L and mark-weighted funding, all four costs and both venues' collateral. Require at least20 completed cost-qualified cycles before a profit/risk verdict; too few is insufficient evidence. Do not lower the gate or add assets to force opportunities.
