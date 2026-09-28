# Strategy incremental evidence implementation plan

> For agentic workers: use superpowers:executing-plans inline; one final independent review.

Goal: distinguish supply-ranking value from short exposure, and test whether observed funding
differences persist sufficiently to pay four executions. Research only; no active bot changes.
Architecture: two independent research probes reuse audited native Freqtrade infrastructure and
public venue funding history. Preserve the previous experiments and their rejected conclusions.
Tech stack: Python, pandas, unittest, native Backtesting, public GET requests.
Spec: this document freezes the numerical protocol before new outcomes are computed.

## Frozen protocol and constraints

Supply: 2024-01-01 to 2026-09-25 UTC; reused history, not untouched OOS. Same 10,000 USDT,
1x leverage, weekly entry opportunities, 166-hour exit and 20% stop as UnlockPressureShortV1.
Compare that unchanged rule against fixed short APT (40%), fixed short TIA (40%), and equal
short APT/TIA (20% each). All controls use exactly the original rule's eligible decision bars;
only asset allocation changes. Run full continuous accounts at 0.10% and 0.20% cost per side.
Use the existing audited cache, offline metadata assumptions, actual historical funding,
common June-22-2026 retrospective data-quality exclusion and hourly reconciliation. Reconstruct
calendar returns from continuous accounts; do not stitch independently restarted year runs.
Report hourly drawdown and daily paired 7/30-day block intervals of rule minus equal short.
Intervals are exploratory after repeated selection. No new parameter search or adoption gate
based on retaining an incumbent's returns. Weak incremental evidence rejects the factor claim.

Funding: 84 complete UTC days [2026-07-05, 2026-09-27), BTC/ETH Bybit and OKX USDT perps.
Monday 01:00 UTC decisions with full past and forward seven-day coverage. Assume a one-hour
historical rate availability delay. Select the short venue from the previous seven days' signed
rate difference; predict that same weekly sum for the next week. Entry requires magnitude above
38 bps of per-leg notional: four 5-bps fees + four 2-bps slippage assumptions + 10-bps basis
allowance. Gross target 80% of total capital, 40% per leg; no leverage increase. Hold seven days;
no orientation selected from future settlements. Actual independent event clocks are integrated,
not fabricated or matched to the wrong payment. Missing/nonfinite/duplicate data or unreviewed
settlement gaps fail the probe. Report forecast/realized rate differences and cost-gate frequency.
This is a necessary-condition carry diagnostic: rates are not mark-weighted cash flows, price
basis PnL is not measured, and there is no executable historical quote or total strategy PnL.
No qualifying entry means do not advance this version to full dual-price simulation. A qualifying
entry still needs that separate simulator before profitability can be evaluated.

## Review focus

- Controls must share the source rule's causal decision opportunities and total planned exposure.
- Short funding, fees and final marked equity must reconcile to native results.
- Cross-year holdings must stay in the continuous account and calendar returns.
- Forecast direction must exclude later funding observations and respect assumed availability.
- Funding pagination, boundary exclusivity, duplicates and coverage must not silently lose events.

## Task 1: supply controls and native experiment

Files: research/incremental_20260927/{controls.py,test_research.py,run_controls.py,PLAN.md,RESULTS.md};
strategies/{UnlockPressure.py,NativeControls.py}. Original strategy is copied byte-identically.

- [x] Write failing tests for matched opportunity times, fixed/equal short exposure and no future dependence.
- [x] Observe RED, implement controls, observe GREEN and original 22-test baseline.
- [x] Run eight native simulations; reconcile every trade and final marked equity; export curves/results.
- [x] Compute continuous calendar returns and paired daily block diagnostics; commit evidence.

## Task 2: funding persistence probe

Files: research/incremental_20260927/{funding.py,run_funding.py,test_funding.py,funding_summary.json}.

- [x] Write RED tests for causal rate selection, entry gate, boundary inclusion, pagination and gaps.
- [x] Implement public history capture and deterministic diagnostic; run GREEN.
- [x] Fetch bounded real history, retain URLs/raw hashes, run the frozen rule and disclose limits.
- [x] Independent final review; one RED-to-GREEN correction pass if necessary; report both verdicts.
