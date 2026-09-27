# X12: E0V1EN dip-buy with a crash guard (pre-registration)

Date: 2026-09-27. Status: draft for approval; no strategy code and no backtest before this file is agreed.
The variant log at the bottom is append-only; acceptance criteria may not be edited after the first run.

## Where this comes from, and what has already been seen

Holdout test (`specs/2026-09-26-holdout-2021-2023.md`): the 5m dip-buy family kept its edge on never-seen
data — E0V1ENPF every year positive incl. 2022 (+107% while the universe fell −79%), average profit per
trade +1.10 / +0.35 / +0.68% (2021/22/23) vs +0.45% in 2024–26. It failed only on mark-to-market drawdown:
2022 −31.9% (limit 25%). Elliotv8PF failed the same way, −61.8%.

Anatomy of the worst days (looked at before writing this file, so these episodes are in-sample for X12):
- **E0V1EN, FTX, 2022-11-08:** six entries between 17:05 and 17:20 (SOL, SUSHI, ALGO, SRM, AR, CHZ), all
  six stopped at −25% within ~2h. Cause: the whole book filled inside one crash leg.
- **Elliotv8, LUNA, 2022-05-10 → 13:** LUNA bought and stopped at −32% more than 20 times in four days
  (≈ −5,000 USDT). Cause: immediate re-entry into a coin that just hit its stop.
- (X8's worst, May 2021, is DCA bag-holding — a different mechanism, out of scope here.)

Found while preparing this file: E0V1EN declares `CooldownPeriod` (96 candles = 8h per pair after any
exit) in its `protections`. The dry-run applies it; backtests only do with `--enable-protections`, which none
of ours used. **All E0V1EN backtest numbers so far are without the cooldown the dry-run actually runs.**

Payer (unchanged from the family): forced and panicked sellers pay liquidity providers. The guard does not
try to tell a rebounding flush from a collapse — the X11 holdout showed that cannot be done from the flush
itself (2021/2023 positive, 2022 negative). It only limits how much of the book one crash leg can take.

## Rules

X12 = `E0V1ENPF` (phantom-fill capped, all signals, exits, stop −25% and parameters unchanged) plus:

- **A. Portfolio entry-rate cap:** at most **2 new entries in any rolling 60 minutes** across all pairs
  (counted over open and already-closed trades by open time). Implemented in `confirm_trade_entry`. When
  more signals fire in the same candle than the cap allows, freqtrade's pair iteration order decides which
  are taken — the same in backtest and live.
- **B. Per-pair stop lock:** after a `stop_loss` exit, that pair is locked for **24 hours**
  (freqtrade `StoplossGuard`, `only_per_pair: true`, `trade_limit: 1`, `lookback_period_candles: 288`,
  `stop_duration_candles: 288`), in addition to E0V1EN's existing 8h `CooldownPeriod`.
- All runs with `--enable-protections`.

Explicitly not done: any market-regime filter (BTC trend, breadth), any change to entry/exit signals, stop
level, slot count or sizing, any portfolio-level drawdown stop.

## Runs

Universe and windows (identical to earlier runs, so everything is comparable):
- 2024-01-01 → 2026-09-23, 118 pairs (`config-bench.json`, the dry-run's setup), one continuous run.
- 2021, 2022, 2023: the holdout universes and per-year runs (`config-holdout-<Y>.json`).

For each window:
1. **Base:** E0V1ENPF with `--enable-protections` (= what the dry-run really does).
2. **X12.**
3. **De-leveraged control:** Base with `tradable_balance_ratio` = 0.99 × k, where k = X12's worst MTM
   drawdown over the six windows ÷ Base's worst. Computed mechanically after runs 1–2. It answers: does the
   guard do better than simply trading smaller?

Fee 0.1% per side; fee test 0.2% per side for X12. Evaluation with `evaluate.py`, `mtm.py`,
`holdout_combine.py`.

## Acceptance criteria (pre-registered; all must pass)

1. 2024–26: net > 0, each calendar year > 0, profit without the best 3 months > 0, bootstrap 95% CI
   excludes 0.
2. Every one of the six windows (2021, 2022, 2023, 2024, 2025, 2026-to-date): return > 0.
3. **Worst MTM drawdown over all windows ≤ 25%.**
4. X12's compounded return 2021 → 2026 (chaining the four runs) > the de-leveraged control's.
5. Fee 0.2% per side: 2024–26 net > 0.

Honesty note: criterion 3 is tested on the very episodes that motivated the guard, so passing it is
expected and proves little by itself. Criteria 2 and 4 are the informative ones (does the guard cost the
edge; does it beat trading smaller). The only true out-of-sample evidence will be the dry-run record.

Expected (written before running): X12 loses 20–40% of Base's return in 2021 and 2024–26 (missed
simultaneous rebounds), and cuts 2022's drawdown to ~15–20%. Most likely failure: criterion 4 — crash legs
are also the best entries in bull years, so capping them may cost as much as trading smaller.

## Decision

Pass → X12 replaces E0V1EN on the 8082 dry-run (new sqlite; the E0V1EN dry-run record is archived), and
becomes the candidate for the small live account to measure fill cost. Fail → log it; at most one further
variant, chosen now: **(a) cap 3 entries per 4 hours** instead of 2 per 60 minutes (slower spreading). Then
close the line; E0V1EN stays on the dry-run as the slippage probe.

Side result reported regardless: Base vs the earlier no-protection numbers (how much the 8h cooldown the
dry-run runs changes the backtest).

## Variants tried (append-only log)

| # | date | change | result |
|---|---|---|---|
| 1 | 2026-09-26 | as specified; all runs --enable-protections | **PASS, 5 of 5.** (1) 2024–26 +294.6%, CI [+103, +473], −best3 +189%, 2024 +81.5 / 2025 +143.0 / 2026 +70.1% ✅. (2) all windows > 0: 2021 +9,265 / 2022 +49.8 / 2023 +93.8% ✅. (3) worst MTM DD −19.8% (2022-11-09; in-sample by construction) vs Base −32.3% ✅. (4) k = 19.8/32.3 = 0.613 → control tradable_balance_ratio 0.607 (its worst MTM DD −20.4%, matched); chained 2021→26: X12 +107,159% vs control +20,893% ✅ — X12 beats the control in every single window (2021 9,265 vs 4,532; 2022 49.8 vs 13.2; 2023 93.8 vs 62.1; 2024–26 294.6 vs 147.1). (5) fee 0.2%: 2024–26 +61.9% ✅ — but thin: 2026 −26.0%, CI [−53, +173] includes 0, 2022 +4.2%. |
| — | 2026-09-26 | side result: Base with vs without the 8h CooldownPeriod | The cooldown the dry-run really runs cuts E0V1ENPF 2024–26 from +1,139% to +351.6%, 2022 from +107.2% to +17.0%, 2021 from +32,587% to +15,822%. All earlier E0V1EN figures (bench, loop, survivorship) were without it and overstated by this much. |
| — | 2026-09-26 | **verdict** | Pass → per spec X12 replaces E0V1EN on the 8082 dry-run. Execution cost remains the deciding unknown: at 0.2% per side the edge is near zero in 2026 and 2022. |
