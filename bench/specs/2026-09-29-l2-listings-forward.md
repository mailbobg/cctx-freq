# L2: short new listings with room for the squeeze — forward test only (pre-registration)

Date: 2026-09-29. Status: requested by the user ("写第一条的预注册方案，放到服务器上前向测试"). Written before any code.
Append-only log at the bottom; criteria may not be edited after the bot starts.

## Why a forward test

L1 (`2026-09-29-l1-short-new-listings.md`) showed a large, stable effect (median new listing −17 % vs BTC over three
weeks, 74 % under-perform) that failed on the tail: a 1x isolated short loses its whole margin when the coin doubles
intraday, which happens in about one listing in fourteen. L2 changes only the risk construction so a squeeze is survived
more often: a smaller short in cross margin with a hard exit at 3× entry instead of liquidation at 2×. Every year of
2021–2026 has already been seen while building L1, so a backtest of L2 on that data can only be descriptive. The verdict
comes from the forward paper record.

## Rules

- **Venue**: Binance USDT-M perpetuals, freqtrade dry-run, **cross margin, 1x**.
- **Events**: a perpetual whose `onboardDate` (exchange market info) is within the last 4 days; crypto only
  (`underlyingType == COIN`, stablecoin bases excluded); not a re-listing or migrated contract (the bot's own hourly
  history of the pair must not start more than 1 day before `onboardDate`).
- **Entry**: short at the first hourly candle open at or after `onboardDate` day (D0, UTC) + 3 days 00:00 — i.e. after
  the close of D0 + 2, as in L1. Entry window 24 h; an event missed (bot down, pair not yet in the whitelist) is skipped
  and logged. One trade per pair ever.
- **Size**: 2.5 % of total equity per event (half of L1), at most 10 events open (25 % gross short).
- **Hedge**: one BTC long whose notional is kept equal to the sum of the open coin shorts (adjusted when it drifts more
  than half an event's size; closed when no short is open).
- **Exit**: after 21 days; or immediately when the price reaches 3× entry (market order; worst case ≈ −5 % of equity per
  event, the same as L1's liquidation, but the coin must triple rather than double).
- **Costs**: freqtrade's dry-run fees (taker 0.05 %), market orders; funding as simulated by freqtrade.

## In-sample reference (descriptive, not a criterion)

The L1 engine with these rules (size 2.5 %, exit on the daily high reaching 3×, fill at 3×, no liquidation) on 2021-02 →
2026-08, reported once in the log below before the bot starts. It sets expectations only.

## Forward acceptance (judged at the first review date with ≥ 40 closed events, not before 2027-03-31)

1. Net P&L of the bot (coin shorts + BTC hedge + funding + fees) > 0.
2. Mean coin return minus BTC return per closed event ≤ −5 % (the effect is still there).
3. Bot equity max drawdown ≤ 10 % (mark-to-market, freqtrade profit incl. open trades).
4. 3× exits ≤ 15 % of closed events.

Kill switch (any time): drawdown > 15 % of starting equity → stop the bot and log it; that is a fail.

Pass → a small live allocation with the same rules after a separate review of live order handling (freqtrade dry-run
does not simulate cross-margin liquidation or funding exactly). Fail → the new-listing line is closed.

## Log (append-only)

| # | date | entry | result |
|---|---|---|---|
| 0 | 2026-09-29 | **in-sample reference (descriptive only)**: L1 engine with L2 rules (`python user_data/bench/l1_listings.py l2`) | 2021-02 → 2026-08: +19.2 %, MTM maxDD −39.3 %, 462 events. Years 2021 +4.5 / 2022 +7.1 / 2023 +11.7 / 2024 +12.8 / **2025 −7.0 / 2026 −9.1 %**; halves +25.0 % / −4.6 %. 22 exits at 3× (−16,377) vs 437 time exits (+18,245); funding −2,979. Weaker than L1's no-stop variant; 2025–26 negative in-sample. Written down before the bot starts so the forward result is read against it. |
| 1 | 2026-09-29 | freqtrade logic check (`strategies/ShortNewListingsL2.py`, backtest 2026-06-15 → 09-25 on O, CAP, GRAM, DATAIP, GRVT + BTC; backtest subclass with startup_candle_count 1 because freqtrade trims the first startup candles of every pair, which removes a new listing's entry window — live trading does not trim) | Entries on D0 + 3 00:00 for all 5, 21-day exits, BTC hedge opened and closed with the shorts, 3× exit hit on CAP in the run before `use_exit_signal` was enabled (−201 % of stake ≈ −5 % of equity, as designed). Fixes found: custom_exit needs `use_exit_signal: true`; the hedge signal must be on every candle. Not a performance number (5 events). |
| 2 | 2026-09-29 | **amendment before the first event → L2P** (user's request): Binance `PREMARKET` perpetuals (`contractType TRADIFI_PERPETUAL`, sub-types Pre-IPO / TradFi: today OPENAI, ANTHROPIC, MOONSHOT, OURA) join the event set; strategy `ShortNewListingsL2P`, bot `l2p-listings` (port 8092) replaces `l2-listings` (stopped with 0 trades). Rules, sizing and acceptance unchanged. Caveats written now: these are pre-IPO company contracts, not pre-TGE tokens, so L2's payer (airdrop/VC/farmer supply) does not apply to them and there is no in-sample evidence for them (L1 kept COIN only); expected 0–2 events a month. The review reports COIN and PREMARKET events separately, and criteria 1–4 are also checked on the COIN events alone so the original L2 question is still answered. | — |
| 3 | 2026-09-29 | user's request: `l2-listings` (COIN only, the original L2) restarted on port 8093 and runs **alongside** `l2p-listings`, each with its own 10,000 USDT dry-run wallet. L2 is judged on the `l2-listings` record; L2P (COIN + PREMARKET) on `l2p-listings`. Both started before any event. | — |
| 4 | 2026-09-29 | execution fix, no rule change, before any event: live bots raised the BTC hedge signal on every loop and freqtrade fetched an order book and logged a rejected BTC entry every 5 s (~2,500 log lines and constant CPU per bot in 2 h). Live/dry-run now signals the hedge only while shorts are open; a first short is hedged at the next hourly candle (≤ 1 h unhedged). Backtests unchanged. Status check: no crypto or pre-market listing had its entry window since the bots started (last crypto listings 哈基米/PONS 2026-09-06, MARSCOIN 09-01, 牛来 08-30; pre-market MOONSHOT 09-22, OURA 09-23, windows closed 09-25/26). | — |
| 5 | 2026-10-01 | `l2p-listings` stopped by hand at 03:56 UTC to free server memory for U2 (`l2-listings` was already stopped since 09-30 02:09). Latest listings: 哈基米/PONS 09-06, MARSCOIN 09-01, 牛来 08-30 (crypto), MOONSHOT 09-22, OURA 09-23 (pre-market); none had its entry window (D0 + 3 days, 24 h) after 09-29 14:15, so no event was missed. If a new listing's window opens while both bots are stopped, that event is lost to the forward record; the review counts only events the bot was running for. | — |
| 6 | 2026-10-01 | **live-path test on the server finished** (`bench/l2live/`, a dry-run container with PONS treated as listed 3 days ago and a 90-minute hold; production entry, sizing, hedge and exit code). The first attempt on 09-30 never ran (test config lacked `initial_state: running`, freqtrade defaults to stopped; earlier still the config was hidden by `.gitignore` `config.json`). | Entry: short signal for PONS at 03:21 UTC, 247.0 USDT (2.5 % of the wallet). BTC hedge entered at the next hourly candle, 04:00:06, as designed. Exit: 90 minutes later PONS closed `time_21d`, the BTC hedge closed `hedge_flat` the same second. Caveat found: BTC's order step (0.001 BTC ≈ 84 USDT) makes the hedge lumpy: a 247 USDT short got a 167.5 USDT hedge (−32 %); with several shorts open the relative error is smaller. The review reports hedge coverage. |
| 7 | 2026-10-01 | execution setting, no rule change, no event yet: `process_throttle_secs` 5 → 30 for `l2p-listings` (and for `unlock-xs`, see its spec). Measured over 2 h the idle bot used 40 % of a core (300 pairs checked every 5 s) while sharing a 2-core server with the real-money e0v1en. Consequence: the 3× exit and the 21-day exit are checked every 30 s instead of every 5 s, so a stop can fill up to 30 s later than before. Entries are unaffected (they happen on the hourly candle). | — |
