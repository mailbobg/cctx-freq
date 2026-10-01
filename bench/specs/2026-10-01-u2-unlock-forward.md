# U2: forward paper test of the token-unlock pressure short (pre-registration)

Date: 2026-10-01. Status: requested by the user ("做 U2 前向方案"). Written before the bot's first decision.
Append-only log at the bottom; criteria may not be edited after the bot starts. First decision: Monday 2026-10-05 00:00 UTC.

## Why forward

U1 (`2026-10-01-u1-unlock-pressure-cross-section.md`) failed its pre-registered criterion 8: the threshold S ≥ 0.25 selected
20 coin-weeks in 4.7 years. Lower thresholds look better than a random-coin control in every year (S ≥ 0.02: +58.9 % vs
control +28.2 %, 389 coin-weeks), but the threshold was chosen after seeing the counts and the 2022–2026 returns at two other
thresholds, so that result is in-sample and cannot be confirmed on the same data. The only clean evidence left is events
that have not happened yet. The stored schedule holds 2,629 included-category unlock events across 64 perpetuals after
2026-09-25, so a paper bot can run the rule on them as they come.

## Rules (U1's rule at the exploratory threshold, fixed now)

- **Decision**: every Monday 00:00 UTC, on daily candles up to Sunday's close and the stored schedule
  (`strategies/unlock_schedule.csv`, included categories `insiders`, `privateSale`, `publicSale`, `airdrop`).
- **Universe**: freqtrade's `VolumePairList` top 100 USDT-M perpetuals by 30-day quote volume, `AgeFilter` ≥ 60 days listed,
  the 218-entry non-crypto/stablecoin blacklist of the other futures bots. BTC is never shorted.
- **Score**: S = Σ tokens unlocking in (Monday, Monday + 14 d] × Sunday close / (14 × mean daily quote volume of the previous 30 days).
- **Selection**: S ≥ 0.02, the 6 highest. One short per selected coin at 5 % of equity, 1x, isolated; a BTC long of equal total
  notional (kept within half a position of the sum of the open shorts). A coin leaves at the Monday on which it is no longer
  selected; the BTC long closes when no short is open.
- **Costs**: freqtrade dry-run taker fees, market orders, funding as simulated by freqtrade. 10,000 USDT dry-run wallet.
- Selection code: `strategies/unlock_xs_logic.py`; identical to the backtest engine on 85 Mondays
  (`bench/test_u2_parity.py`), including the 60 random ones.

### Where live differs from the U1 backtest (written now, so nobody has to find it later)

1. Quote volume: live candles carry base volume, so quote volume = volume × close; the backtest used the archive's quote volume.
2. Universe ranking: freqtrade's 30-day volume lookback instead of the archive sum; the top-100 set can differ at the margin.
3. Position size is fixed at entry (5 % of equity then); the backtest re-sized to 5 % of current equity every Monday.
4. Entries only in the first 6 hours of Monday's candle; a bot that is down then misses that week.
5. Liquidation: freqtrade's dry-run isolated-margin liquidation price, not the backtest's daily-high rule.
6. The schedule is the 2026-09-25 file and is not refreshed (the source API is paid). New tokens, revised vesting and new
   categories after that date are invisible to the bot; after the last scheduled event it simply stops trading.

## Forward acceptance (judged at the first review with ≥ 40 coin-weeks, not before 2027-03-31)

A coin-week is one short position held over one Monday decision. Expected pace ≈ 80 a year (389 in 4.7 years at this threshold).

1. Net P&L of the bot (shorts + BTC hedge + funding + fees, open positions marked) > 0.
2. **Against a control**: the mean weekly return of the selected coins minus BTC, over the bot's coin-weeks, is lower than the
   mean weekly return minus BTC of the other universe coins (no included unlock in ±14 days) in the same weeks, and the
   95 % bootstrap CI (5,000 resamples of coin-weeks) of that difference excludes 0. The control is computed at review time
   from the same daily candles. If the point estimate is favourable but the CI includes 0, the review is extended once to
   80 coin-weeks and judged on the same rule.
3. Mark-to-market max drawdown ≤ 10 %.
4. At most 1 liquidation per 40 coin-weeks.

Kill switch (any time): drawdown > 15 % of the starting wallet → stop the bot, log it, that is a fail.

Expected (written before the first week): from the in-sample reference, a win rate of about 38 % per coin-week and P&L carried
by a handful of large unlockers (the top three coins were about half of the exploratory total). A first positive month says
nothing; most weeks will have 0–2 shorts and many none.

## Decision

Pass → keep the paper bot running another quarter, then a small real-money test sized at the stake that kept drawdown ≤ 10 %
(separate review of live order handling). Fail → the unlock line is closed for good; `unlock-short` (APT/TIA) is judged on its
own record.

## Log (append-only)

| # | date | entry | result |
|---|---|---|---|
| 1 | 2026-10-01 | before the first decision: **entry-day guard added** (`_entry_day`: positions open on Monday only). The first version allowed an entry in the first 6 hours of any daily candle, so a bot started on a Thursday would have opened the 09-28 week's selection after the fact. Found by reading the code before starting the bot, not from a result. Live-path test on the server (today treated as the Monday, a fake unlock for SUI and ADA): decision, 5 % short sizing (495 USDT on 9,900), BTC hedge (990 USDT = sum of the shorts) worked; the forced-drop exit and hedge close are watched in the same test. | — |
