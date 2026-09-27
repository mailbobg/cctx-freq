# X13: E0V1EN with a resting take-profit order (pre-registration)

Date: 2026-09-27. Status: draft for approval; no strategy code and no backtest before this file is agreed.
The variant log at the bottom is append-only; acceptance criteria may not be edited after the first run.

## Where this comes from

`bench/dryrun_recon.md` (2026-09-27): E0V1EN's backtest edge comes from its built-in trailing stop (+3% offset,
0.2% trail) filling at intra-candle highs. A live bot polls the bid every few seconds and misses short spikes.
Per-year 2024 / 2025 / 2026, 118 pairs, 1m detail: built-in trailing +57 / +53 / −8.5%; trailing on 1m closes
+12 / +25 / −41%. Gross edge per trade before fees +0.18% (closes) to +0.38% (highs); round-trip fee 0.2%.
Trailing exits are the only large profit bucket (≈ +3.3% each, 25–35% of trades).

Hypothesis: the spikes are real prices that a **resting limit sell on the exchange** can capture, even when a
polling bot cannot. If that is true, most of the gap between "highs" and "closes" is recoverable in live trading.

What has been seen already: all of 2024–26 has been looked at many times for this family, and the 3% level is the
original author's trailing offset. Nothing below is tuned; the backtest cannot be a clean out-of-sample test. The
new, independent evidence is the tick-level fill check (step 3), which has never been run.

## Rules

X13 = `E0V1ENPF` (phantom-fill capped; all entry signals, sizing, slots, protections unchanged) with:
- built-in trailing stop **off**;
- immediately after an entry fills, a **resting limit sell at entry × 1.03** (the author's 3% offset) — in the
  backtest this is `minimal_roi = {"0": 0.03}` with `ignore_roi_if_entry_signal = false`, run with
  `--timeframe-detail 1m` (fills at exactly +3% when a 1m high reaches it);
- all other exits unchanged (`fastk_profit_sell`, `cci_loss_sell`, the two `time_loss` exits, −25% stop): when
  one fires, the resting order is cancelled and the position is sold as before.

Explicitly not changed: RSI / CTI / SMA thresholds, time-loss rules, stop level, slot count. No X12 guard (kept
separate so the effect of the exit change is isolated).

## Validation

1. **Backtest**, per year 2024, 2025, 2026-01-01 → 09-23, 118 pairs (`config-bench.json`), `--enable-protections`,
   1m detail, fee 0.1% per side; also fee 0.2% per side.
2. `evaluate.py` (per-year, pooled bootstrap CI, lottery test) and `mtm.py` (mark-to-market drawdown).
3. **Tick-level fill check** (the deciding test). From the backtest's take-profit exits, draw a random sample of
   300 (seed 0, stratified 100 per year). For each, download Binance `aggTrades` for that pair and day(s) from
   data.binance.vision and look at every trade from **entry time + 60 s** (order-placement latency) to the end of
   the minute in which the backtest says the order filled:
   - **filled** if any trade printed at a price **strictly above** the target (price-time priority: a buy above
     our ask means every ask up to that price, ours included, was taken);
   - **uncertain** if the highest print equals the target exactly (depends on queue position) — counted as not
     filled;
   - **not filled** otherwise.
   Report the fill rate overall and per year, and the median time between entry and first print above target.
4. Reported, not a criterion: the same backtest at fee 0.075% per side (Binance with BNB discount).

## Acceptance criteria (all must hold)

1. Each of 2024, 2025, 2026-to-date > 0 (fee 0.1%, 1m detail).
2. Pooled bootstrap 95% CI of 2024–26 profit excludes 0.
3. Pooled profit with the best 3 months removed > 0.
4. Mark-to-market drawdown ≤ 25% in every year.
5. Fee 0.2% per side: compounded 2024–26 > 0.
6. **Tick fill rate ≥ 90%** (strictly-above rule), and ≥ 85% in each year.

Expected (written before running): backtest between the "highs" and "closes" results above — roughly +30…+45%
in 2024 and 2025, around 0 to −20% in 2026, so criterion 1 is the most likely backtest failure. Fill rate: high
for liquid pairs; the risk is thin alts where a spike is one or two prints.

## Decision

Pass → X13 goes on the dry-run (replacing X12 on port 8082; X12's record archived). The dry-run cannot confirm
spike fills either (freqtrade's dry-run also fills limit orders by polling), so a pass qualifies X13 for the small
live account, where the resting order is exercised for real. Live implementation (resting sell placed via
`custom_exit` + `custom_exit_price` right after entry, long exit timeout, bot-side −25% stop because Binance spot
cannot hold a stop order and a limit sell on the same balance without OCO) gets its own review before any money.

Fail → log it; at most one further variant, chosen now: **(a) same rules plus a regime filter — no new entries
while BTC/USDT's daily close is below its 200-day simple moving average** (motivated by the holdout finding that
flush rebounds fail in bear years, 2022, and by 2026 being negative in every exit variant). Then close the
E0V1EN line.

## Variants tried (append-only log)

| # | date | change | result |
|---|---|---|---|
| 1 | 2026-09-27 | as specified (1m detail, per year, 118 pairs, protections) | **FAIL (criteria 1 and 5).** Fee 0.1%: 2024 +23.5% / 2025 +31.7% / **2026 −32.1%** (1118 / 802 / 888 trades). Fee 0.2%: −13.3 / +3.9 / −49.8% ❌. Fee 0.075% (BNB, reported only): +33.8 / +40.9 / −28.5%. **Tick fill check PASS**: 300 sampled take-profit fills, 281 filled (a print strictly above target), 15 uncertain (high = target exactly), 4 not filled → fill rate 93.7% (2024 95% / 2025 95% / 2026 91%); median volume traded above target 6.1× our order; median 21 min from entry to first print above target. The resting order does capture the spikes a polling bot misses — the exit mechanism is sound; the entries lose money in 2026. |
| 2 | 2026-09-27 | pre-registered variant (a): no new entries while BTC daily close < SMA200 (BTC was below it on 20% / 27% / 88% of days in 2024 / 2025 / 2026) | **FAIL (criteria 1 and 5).** Fee 0.1%: 2024 +25.1% / 2025 +21.4% / **2026 −11.5%** (1026 / 536 / 163 trades). Fee 0.2%: −10.6 / +4.8 / −12.9%. Fee 0.075%: +34.8 / +28.0 / −10.6%. The filter cuts 2026's loss (−32% → −11.5%) but the few entries it lets through in 2026 still lose 0.5% each. |
| — | 2026-09-27 | **verdict** | Main spec and the one allowed variant both fail. **E0V1EN line closed** (E0V1EN, X12, X13). Keeper from this line: the resting take-profit mechanism is real (93.7% tick-verified fill rate, 6.1× volume above target) and can be reused. |
