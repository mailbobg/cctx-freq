# Dry-run vs backtest reconciliation (append-only)

## 2026-09-27 — E0V1EN spot dry-run, 6 trades 2026-09-26 03:05 → 22:40 UTC

Backtest: `config-e0v1en-dryrun.json`, `--enable-protections`, timerange from 2026-09-25 (a warm-up day is
needed: `24h_change_pct` uses 288 candles but `startup_candle_count` is 200, so a backtest started at the
dry-run start produces no signals for the first ~7 h — the first attempt showed only 2 of 6 trades for that
reason).

| pair | open (UTC) | dry | bt 5m | bt 1m-detail | bot max_rate vs 1m high |
|---|---|---|---|---|---|
| QI | 03:05 | +2.93% trail | +4.85% trail | +3.80% trail 03:16 | 0.003985 vs 0.004033 |
| WAXP | 03:05 | +0.38% cci | +0.82% cci | +0.82% | 0.00700 vs 0.00702 |
| PHA | 06:35 | +0.83% fastk | −2.78% cci 09:55 | −2.78% | 0.0792 vs 0.0812 |
| SPELL | 08:25 | +3.15% trail | +3.35% trail | +3.35% | 0.000105 vs 0.0001051 |
| RARE | 18:50 | **−2.98% cci** | +3.09% trail | +3.09% trail 19:24 | **0.0232 vs 0.0234** |
| AUDIO | 21:25 | +3.83% trail | +4.64% trail | +3.90% trail 21:37 | 0.01556 vs 0.01561 |
| sum USDT | | **+134.95** | +237.74 | +206.89 | |

Findings:
- Signals: 6/6 identical pair and candle. No missed or extra entries.
- Entry price: no systematic slippage (dry vs bt open: −0.44, +0.29, −3.95, +0.10, +0.67, −0.07%).
- Exits: 1m-detail fixes exit timing (QI, SPELL, AUDIO now within a minute of the dry-run) and removes
  ~13% of the 5m backtest's profit.
- Remaining gap = the bot never sees the candle's printed high. It samples the price every few seconds
  (exit side = bid), so its max_rate is below the 1m high on every trade. RARE is the costly case: true
  high +3.6% from entry, bot saw +2.75%, so the +3% trailing offset never activated and the trade ended in
  a −2.98% signal exit instead of a +3% trailing exit (≈ −100 USDT swing).
- Implication: E0V1EN's trailing stop (offset 3%, trail 0.2%) is tuned right at the edge of short spikes;
  even a 1m-detail backtest overstates how often it fires. Six trades are not a measurement — keep
  collecting; compare X12 the same way.

## 2026-09-27 — X12 spot dry-run, 2 trades (bot running since 2026-09-26 14:31 UTC)

| pair | open (UTC) | dry | bt 5m | bt 1m-detail | bot max_rate vs 1m high |
|---|---|---|---|---|---|
| RARE | 18:50 | **−2.98% cci 22:40** | +3.09% trail 19:20 | +3.09% trail 19:24 | 0.02323 (+2.83%) vs 0.0234 |
| AUDIO | 21:25 | +3.83% trail 21:38 | +4.64% trail 21:35 | +3.90% trail 21:37 | 0.01556 vs 0.01561 |
| sum USDT | | **+8.84** | +123.03 | +111.71 | |

Same two trades as E0V1EN in this window (the guard did not bind: 2 entries 2.5 h apart, no stop-loss exits).
Same finding: signals and entries match, 1m-detail fixes AUDIO's exit, and RARE flips from a +3% trailing exit
to a −3% signal exit because the bot's sampled high stayed under the +3% trailing offset. The two bots
sampled different highs for the same coin (0.02320 vs 0.02323) — the sampling is noisy, not a fixed haircut.

## Step 1 analysis plan (written 2026-09-27 before any full run): how much of the edge depends on unseen highs?

Variants (strategies in `strategies/E0V1ENCloseTrail.py`, mixin `close_trail.py`; same offset 3% / distance 0.2%):
- R  = E0V1ENPF / E0V1ENX12 with the built-in trailing stop, `--timeframe-detail 1m` (reference)
- C1 = E0V1ENPFCT / E0V1ENX12CT, `--timeframe-detail 1m` → trailing on 1m closes
- C5 = same, no detail → trailing on 5m closes (what a live X12b would run)
Windows: 2024, 2025, 2026-01-01 → 09-23 as separate runs (1m detail memory), 118 pairs, `config-bench.json`,
`--enable-protections`, fee 0.1%; C5 also at fee 0.2%.
Smoke test on the reconciliation window: C1/C5 reproduce the dry-run's RARE exit (cci_loss 22:40).

Reading (fixed now):
- "Edge survives" if C1 and C5 are each > 0 in every one of the three years, and the pooled bootstrap CI of
  2024–26 excludes 0. Then write the X12b spec (C5 rule for live).
- "Edge mostly an artefact" if C5 2024–26 < 25% of R's, or any year ≤ 0 → the dip-buy family's backtests are
  re-judged before anything else.
- Anything in between: report and decide with the user.

## Step 1 result (2026-09-27) — per-year runs 2024 / 2025 / 2026-01→09-23, 118 pairs, protections on, fee 0.1%

| variant | strategy | 2024 | 2025 | 2026 | compounded | pooled CI | avg/trade | trailing exits |
|---|---|---|---|---|---|---|---|---|
| 5m built-in trailing (2026 only) | E0V1EN | | | +5.0% | | | | |
| R: built-in trailing, 1m detail | E0V1EN | +57.0 | +53.1 | **−8.5** | +119.9% | [+17, +182] | +0.18% | 35% |
| R | X12 | +40.9 | +43.0 | −6.3 | +88.8% | [0, +156] | +0.16% | 35% |
| C1: trailing on 1m closes | E0V1EN | +12.0 | +25.3 | **−40.7** | −16.9% | [−75, +70] | −0.02% | 25% |
| C1 | X12 | +6.4 | +20.1 | −36.7 | −19.1% | [−83, +60] | −0.03% | 25% |
| C5: trailing on 5m closes | E0V1EN | +11.2 | +20.5 | **−46.8** | −28.7% | [−96, +59] | −0.06% | 14% |
| C5 | X12 | +7.7 | +13.8 | −44.0 | −31.5% | [−96, +49] | −0.07% | 14% |
| C5, fee 0.2% | E0V1EN / X12 | −17.5 / −18.6 | −6.3 / −10.7 | −57.5 / −55.1 | −67% / −67% | CI < 0 | | |

1m data coverage checked: all 118 pairs ≥ 97% in 2026. The earlier "+66.5% in 2026" was compounding inside a
continuous 2024→26 run; a fresh 2026 run at 5m is +5.0%.

**Verdict by the pre-registered reading: "edge mostly an artefact"** — C5 compounded is negative (< 25% of R) and
2026 ≤ 0 in every variant, even R. Live sits between R (bot sees 1m highs) and C1 (bot sees only closes); the
6-trade reconciliation put the dry-run between them (dry +135 vs R +207 vs C1 +79 USDT). Taking the midpoint,
2024–26 is roughly flat and 2026 is clearly negative. The dip-buy family's backtest edge (E0V1EN, X12, and very
likely Elliotv8 and the 2021–23 holdout numbers, all measured with 5m built-in trailing) came largely from
trailing-stop fills at intra-candle highs. X12 is no better than E0V1EN under honest exits. No live money for
this family; the two dry-runs continue only as measurement.

## X8 precision check (2026-09-27) — per-year, 116 pairs, X8PF, protections on, fee 0.1%

X8 has freqtrade's built-in trailing stop off (`trailing_stop = False`); its exits are custom (profit targets,
signal exits, DCA/grinding), evaluated per candle at the open price. So the relevant check is 5m vs 1m detail
(1m ≈ the live bot's evaluation frequency); no close-based variant is needed.

| | 2024 | 2025 | 2026 | compounded |
|---|---|---|---|---|
| 5m | +45.7% (CI [+15, +74], −best3 −3.3%, MTM DD −13.2%) | +209.7% | +40.5% | ≈ +534% |
| 1m detail | **+29.3% (CI [−0.3, +53], −best3 −11.8%, MTM DD −19.0%)** | +128.4% (CI [+102, +153]) | +40.9% (CI [+32, +51]) | ≈ +316% |

Reading: every year stays positive and 1m keeps ~60% of the 5m result → the edge is **not** a precision artefact
(unlike E0V1EN). 2024 is weak at 1m (CI touches 0, fails the lottery test); 2025–26 are strong but in-sample for
X8 (the NFI author tunes weekly on recent data). Largest loss bucket in both: `exit_bad_trade_abandon` (19 trades,
≈ −5.4k USDT). Holdout 2021–23 result (5m) remains: every year positive, fails on 2021 MTM DD −41%.
