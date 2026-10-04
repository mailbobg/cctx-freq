# Review and reflection ledger — 2026-10-04

Research in progress; no new strategy has been adopted. Default screen is annualized net >12% and marked drawdown <=5%, pending the user's optional risk answer. Already used history is reused research data. It is not fresh OOS.

## Configured bots: all 13 other configurations

| Bot | Mechanism and evidence | Reflection and action |
|---|---|---|
| c-hold | Weekly BTC/ETH momentum plus per-coin volatility sizing; corrected simulation R1: recent CAGR 43.84%, daily DD 32.33% at 0.12% side cost | Profitable history primarily takes crypto trend risk. Correlation and weekly delays prevent a small drawdown mandate. Keep stopped reference. |
| c-hold-sma | R2b adds half size below SMA200; corrected R1: recent CAGR 39.72%, DD 25.15%; early CAGR 55.00%, DD 38.96% | Better loss control than C in matched windows, still large drawdown. First fresh Monday decision is Oct 5. Preserve frozen forward cohort. |
| x7 | Large NFI spot rule set with recovery additions; snapshot only 3 closed trades and one open position, bot stopped | Too little forward evidence. Exit accounting, concentration and trade attribution matter more than win rate. X8 has more audited recent evidence; do not duplicate or restart X7 based on tiny closed profit. |
| x8 | Custom exits, built-in trailing disabled. Clean matched2024 net+26.89%, daily DD19.36%, primary5m-open DD24.54%. Older1m2025/26 headlines+128.37/+40.85% and2021 DATA concentration route research but predate the new historical-cache isolation | Profitable historical high-frequency reference with excessive tail risk. The5% cap-only version returns+4.98% with13.51% primary5m-open DD and is rejected. Older combined cap+loss-add ban also failed. Remains stopped. |
| x12 | E0 entry-frequency guard; historical headlines rely on optimistic built-in trailing fills. Close-based trailing variants compounded negative; stopped bot has negative realized and open P&L | Guard can space entries but cannot create an entry edge. Reject old headline as adoption evidence. Retain honest fill-model result, no further trailing parameter sweep. |
| x14 | Alt flush rebound long + BTC short, 24h exit. Main independent engine 2021–26 +143%, but 2025 -19.4%, 2026 -4.1%, total DD27.7% | Relative rebound faded, beta hedge also failed. Confirmed custom exits disabled in class/config; fix restores specified execution. Keep stopped. |
| x14-bybit | Same X14 rules on another venue, stopped; no forward closed record | Venue diversification does not repair a losing relative leg. Same dispatch defect fixed. |
| x14-bitget | Same X14 rules on another venue, stopped; no trades | Same failure shape, not an independent alpha hypothesis. Same dispatch defect fixed. |
| unlock-short | APT/TIA scheduled vesting divided by trailing volume; 40% short. Incremental study net +23.62%, hourly DD36.46%; doubled fees +10.63%, 2024 negative, ranking increment CI includes zero | Much of gain may be generic short-alt exposure. Small two-coin scope and short-tail risk fail mandate. Keep the previously authorized frozen paper record; no tuning based on empty forward history. |
| unlock-xs | U2 broad score >=0.02, top6, 5% short each + BTC long; broad historical +59%, DD6%, random control +28%, reused history | Separate supply pressure from generic alt underperformance, static schedule's information availability and historical/live sizing differences. No closed cohort yet. Preserve Monday Oct5 first decision; not a demonstrated profitable deployment. |
| l2-listings | Post-listing short2.5% + BTC long, 21d/3x-price exit; old dataset total +19.2% but DD39.3%, 2025/26 negative | Short-squeeze losses overwhelm many small wins. Need >=40 actual new events under frozen specification. Currently stopped, no forward trades. |
| l2p-listings | L2 extends to PREMARKET contracts; first CT short + BTC hedge open on Oct4 | PREMARKET is a different economic mechanism. Only one event and no close, cannot infer edge. Initial entry-cost hedge coverage 169.53/247.43 =68.5%; BTC lot size and half-event adjustment band leave material mismatch. Current-dollar neutrality is also not maintained by comparing cost stakes. Report as an implementation limitation; do not quietly change this cohort's hedge rule. |
| funding-spread | Separate two-venue matched-base paper ledger, realized funding reconciliation and four-fill cost gate. Stopped with no trades; existing 84-day BTC/ETH sample had zero qualifying opportunities | Low rate differential cannot cover four fees and basis/leg risk. No-trade is a valid gate result, not a missing robot integration. Only investigate another holding horizon/universe after obtaining new independent data; never lower cost gate just to force trades. |

Stopped-bot open P&L from the panel can use stale prices; snapshot `runtime_snapshot.json` is runtime context, not a comparable performance test. Forward record gates in `bots/verdicts.json` remain relevant for their cohorts but do not certify the historical strategy.

## Confirmed engineering corrections

1. **X14 exit dispatcher:** `use_exit_signal=False` prevented `custom_exit` entirely. Regression exercises real `should_exit`, fails before fix, passes after enabling class and all three configs. Local commit `16d3a58`, deployed to server; all three bots remain stopped. No numeric rule or historical alpha claim changed. Freqtrade documents the custom-exit gate in its [callback documentation](https://www.freqtrade.io/en/2024.3/strategy-callbacks/); the current local dispatcher confirms it.
2. **PhantomFillMixin reductions:** negative callback stake is cost basis. Actual sale is `abs(stake) * trade.amount / trade.stake_amount * current_rate`. A 100-cost holding worth300 sold300 despite a100 quote-volume cap. Regression reproduced300 versus100; fix converts quote volume to cost-basis stake before clamping. Also checks leveraged quantities and underwater positions. The [Freqtrade callback documentation](https://docs.freqtrade.io/en/latest/strategy-callbacks/) explains partial-exit units. First R2 process terminated before outcome; rerun uses corrected helper. This can reduce historical profits; it cannot establish alpha.
3. **Historical C/R2b audit:** old standalone research engines allowed fees to push cash negative and some metrics normalized away the first day. R1 uses a common fee-affordable cash engine, sells before buys, includes initial capital, and invokes actual current strategy indicators. No existing live indicator rule changed.
4. **NFI historical profit-target state:** X7PF/X8PF used the ordinary persistent cache constructor. A regression proves a future-dated on-disk target is loaded into a historical instance. The shared bench cache contained POND/USDT with2026-07-10 timestamp while R2 simulated2024; this proves contaminated state was available, not that it improved that specific result. The incomplete job was terminated and excluded. Historical PF wrappers now create fresh memory caches and reset on bot_start; dry-run persistence is tested unchanged. Clean R2 completed with exact source hashes in `r2_clean_manifest.json`.

Exit/volume corrections are deployed as local commits16d3a58 anddb6de6d. The only running dependency flagged by deployment is e0v1en's shared volume helper; its actual API reports `position_adjustment_enable=False`, so the changed reduction method is inactive there. Its process keeps the earlier imported module until the next authorized restart, with unchanged active sizing behavior. Stopped NFI/X12 classes will load the corrected helper at their next authorized start. No other active cohort was reconfigured or restarted. Latest current-source suite:139 passed,16 subtests; X14 and volume-reduction regressions also pass in the actual server image.

## R1 result and reflection

Frozen R2b plus 10% annualized covariance risk limit, same weekly decision and 5pp band. One matched cash simulator, common 1% reserve, fee+slip0.12% / doubled0.24% each side. All signal/covariance prefix and execution-prefix checks pass; no negative cash; first fees reduce initial capital. Complete outputs in `r1_results.json` and curves.

| Window | Original R2b CAGR / daily DD | Risk limit CAGR / daily DD | Risk limit at doubled cost |
|---|---|---|---|
| 2018-03-05–2020-12-31 | 55.00% /38.96% | 16.25% /10.98% | 15.81% /11.14% |
| 2021-01-01–2026-09-21 | 39.72% /25.15% | 12.99% /7.56% | 12.53% /7.77% |

**Fails default drawdown gate in both windows.** Profitable over both whole windows and at doubled costs, but 2018 is negative; no promise of positive annual return. Ex-ante volatility is an estimate, not a drawdown floor. Weekly weight drift and a5pp band are material at low allocations. Do not lower the volatility target until the backtest passes; retain this version and investigate a different causal failure only with a new preregistered hypothesis. Original R2b forward cohort is unchanged.

## Verification limits

139 applicable tests passed across current bench, cross-venue service and panel, plus16 subtests. This includes the new engine, Freqtrade weight and funding-data regressions. Two existing FastAPI/Starlette deprecation warnings. Upstream bare `pytest -q` cannot collect because `xdist` is absent; no upstream-suite pass is claimed. Bench-wide discovery also tried to collect an archived panel backup whose static directory was not saved; exclude that archival backup for the current-source suite. X14's three exit tests also pass inside the actual server image.

R2 X8 full-year1m matched job completed successfully. Final balances reconcile within0.01USDT for both strategies; the cap-only candidate fails both return and drawdown gates.

## All strategy source classes, beyond the robot configs

Initial source catalog found64 IStrategy descendants, including the `_Daily` base and SampleStrategy template. CLI class discovery succeeded for those; this does not verify exchange initialization or profitability. The refreshed `strategy_catalog.json` now includes the new, actually backtested Cov10 subclass:65 descendants and217 existing export-stat rows, with no archive-read errors and no remaining source-default disabled-custom-exit warning. The initial catalog remains in commit37bbab0. Catalog profits below use their historical execution assumptions and closed-trade accounting, so they only route research.

| Unconfigured family/classes | Existing evidence and disposition |
|---|---|
| Scalp, SmoothScalp, ReinforcedSmoothScalp, BinHV45 | 2025Sep–Nov1m net -99.7/-74.8/-35.6/-55.0%, respectively. Tiny take-profits do not cover turnover/tail losses. Exclude these implementations. |
| CombinedBinHAndCluc, ClucMay72018, NASOSv5 | 2024–26 old5m net -99.5/-99.7/-76.2%. No positive baseline to optimize; exclude. |
| SMAOffsetProtectOptV1, NotAnotherSMAOffsetStrategy, ClucHAnix_5m, MultiMA_TSL3 | Old5m positive +210.5/+45.6/+270.8/+60.2%, but closed-trade DD38.6/57.7/46.0/25.8%. Trailing or custom profit-dependent stop models need finer/tick-aware validation. None passes risk gate even using understated closed-trade risk. MultiMA_TSL3a and Cluc5mDCA have no independent export; inherited signals/extra exits or DCA are not independent profitable discoveries. |
| Elliotv8 / Elliotv8PF | Tight trailing0.1% after2% activation; source comments prescribe different live/backtest modes. Huge old5m returns are not trustworthy evidence of the same executed rule. 2022 holdout DD63.7% at original fees; doubled fees lost12.4%. Exclude current version from adoption. |
| OversoldReversion | Two existing1h studies net11.9/30.2%, closed DD21.5/22.8% across2024–26. Deep oversold signals can still continue falling. No strict mandate pass; claimed vector expectation is not Freqtrade execution proof. |
| EmaCross, EmaCrossFunding, ZScoreTrendSentinel, BBBreakoutBTC, TurtleCryptoS2 | Prior loop shows weak current-period return/cost or large drawdown; long-beta contributions dominate, short contributions often lose. BBBreakout default holding deadline repaired, old effective configs already had exits enabled. Do not revive by tuning indicator periods. |
| ConnorsRSI2, TurnOfMonth, NDownDays, GoldenCross, EMACross5_40, HiLo10, PriceVsSMA200, TSMOM30, VolTargetSMA200 | Full prior loop/holdout retained. NDownDays had daily DD15.8% and CI includes zero; EMA5/40 had DD38.5% and weaker large-cap replication; TSMOM30 failed2022 holdout. These are trend/beta or intermittent dip exposure, not proven low-risk net edges. |
| FundingCrowdingX9, FundingReversionX9b, XSMomentumX10 | Initial net -46.8/-5.9/-28.6%, closed DD54.3/36.3/41.2%. Funding extremes/reversal and cross-sectional momentum fail the original cost/tail criteria. Repair source default deadlines without claiming the old actual backtests used disabled exits. |
| FlushReboundX11 | Old long-only rule had concentrated crash losses. Independent X14 hedge also failed2025/26. Repair source default24h deadline, preserve closed-line verdict. |
| E0V1ENX13, X13R, VEI, CloseTrail, Bounded, PF and delayed/confirmation variants | Grouped with the original E0 family; prior realistic fill/cost failures retained, not silently reopened as other independent strategies. New Wait3 forward cohort was already authorized separately and is excluded from this loop's adoption. |
| RiskHoldCPortfolio, X8Budget | Prior covariance40% / combined budget15%+loss-add ban failed their own frozen replacement gates. New R1/R2 are separate recorded hypotheses, not retroactive passes. |
| UnlockPressureLSV1, FixedPairControlV1, UnlockXS, L2/L2P, HedgedFlushX14, RiskHoldR2b | Independent simulators/control studies and frozen forward records described above. Lack of a Freqtrade archive is not absence of research; no fresh-cohort profitability claim. |

Additional source-default defect: BBBreakoutBTC, FlushReboundX11, FundingReversionX9b and XSMomentumX10 declared `use_exit_signal=False` while implementing time exits. Four dispatcher regressions fail with these defaults and pass after repair. **Existing archived effective configs explicitly had `use_exit_signal=True`, so the old tested rules were not invalidated by this source-default defect.** It matters when these classes are used with configs omitting the override. No configured running bot uses these four classes.

## R3 result and reflection

Daily loss-budget overlay on R1, no floor resets. Disabled-overlay curve reproduces R1 exactly; execution-prefix test passes.

| Window | CAGR / daily DD | At doubled costs |
|---|---|---|
| 2018-03-05–2020-12-31 | +2.70% /4.45% | +2.33% /4.48% |
| 2021-01-01–2026-09-21 | +3.86% /4.29% | +3.40% /4.34% |

**Rejected for return.** Corrected execution produces positive aggregate net returns in both windows, including doubled costs, and daily DD below5%; returns remain far below12%. The fixed loss budget restricts allocation after losing periods. Removing the research engine’s erroneous entry/off band allows new signals to invest again, so the earlier claim of near-complete cash lockout does not describe the corrected result. This is protective sizing, not a better entry predictor. Do not add ad-hoc peak resets or retune the multiplier after observing this failure.

## Further execution audit

The R1/R3 research engine incorrectly applied the5pp adjustment band to first entries and zero-target full exits. Real C/R2b entry/exit signals bypass that band. Regressions reproduced a missing3% initial entry and a4.46% position failing to clear on its off signal; a small active adjustment remained correctly ignored. Corrected execution passes all three tests and retains the superseded code/results under `superseded_engine_band_all/`. Current tables use the corrected rerun, with no rule or adoption threshold change. The real forward R2b bot was not altered. The historical-cache fix was also deployed and its two regressions pass inside the actual server image.

R2 liquidity assumptions are still per-order caps based on last completed5m quote volume. They do not enforce a cumulative bucket across successive1m adjustments, price impact, or a full-exit volume limit. Thus even a positive R2 screen would remain conditional on stronger execution evidence. The historical static universe and today’s NFI source/coin lists also cannot constitute untouched point-in-time OOS.

R4 now tests one separate scheduling mechanism: daily covariance-based risk reductions, while signals and additions remain weekly. Its protocol was saved before execution.

## R4 result and reflection

| Window | Net CAGR / daily MTM DD | Doubled costs |
|---|---|---|
| 2018-03-05–2020-12-31 | 13.72% /9.74% | 13.28% /10.05% |
| 2021-01-01–2026-09-21 | 11.07% /7.16% | 10.61% /7.35% |

**Rejected.** Daily covariance reductions modestly reduce drawdown relative to R1 but sacrifice more return. Recent net CAGR falls below12%, and neither window reaches5% daily DD. Disabled-overlay, independently recomputed execution-prefix and no off-Monday-buy checks pass. Retain R1 as the more useful historical profitability lead; do not retune R4's volatility target, band or observation length.

A separate diagnosis asks whether waiting until Monday after a zero-weight signal is the main loss source. The actual Cov10 holdings show120 early and236 recent non-Monday asset-days held while the preceding completed signal is zero. Open-to-close P&L on those days totals+78.69 and+1,050.28USDT; within each worst-drawdown episode it is-98.51 and+468.88USDT. These figures attribute existing holdings only; they exclude overnight gaps and cannot predict a replacement exit rule's complete P&L. They do not support a claim that daily signal exits would cure the drawdown. Retain `r1_delayed_exit_attribution.json`; do not launch that change as an optimization without stronger causal evidence.

## R1 actual Freqtrade execution validation

New class `RiskHoldR2bCov10` implements the exact R1 weights and inherits unchanged R2b callbacks. Two tests compare its full and truncated indicators with the independent R1 weights, including companion data containing later rows, and check missing-companion fail-closed behavior. It is research-only, with no `bots/` config or activation. Public daily OHLCV was retrieved into `r1_data/`; every open/close equals the previously frozen research input. Raw pages, hashes and provenance are saved.

A later missing-data regression fails against this candidate's original implementation: dropping incomplete joined dates compresses a two-day return into one daily covariance observation. Retain a regular daily calendar, including missing prices, and require30 consecutive complete returns before allowing portfolio risk again. Both companion-only and joint calendar gaps now fail closed; the four strategy regressions pass, including exact full/prefix weight equality with the frozen R1 on the original continuous inputs. This guard does not change the historical weights or profit table. `r1_missing_calendar_guard.json` records archived and corrected source hashes. The candidate remains local and inactive.

| Actual Freqtrade window | Cov10 net CAGR / daily MTM DD | Doubled costs |
|---|---|---|
| 2018-04-04–2021-01-01 | 17.70% /10.98% | 17.25% /11.14% |
| 2021-01-01–2026-09-21 | 12.76% /7.56% | 12.30% /7.77% |

Early Freqtrade starts a month later because the unchanged230-day warmup exceeds available data before March5. These are execution-validation dates, not a replacement for the original full risk screen. Every exported-order final balance reconciles within0.01USDT. After matching dates and Freqtrade's final-open liquidation, Cov10's maximum curve difference from research is below0.07% of initial capital in all four runs. The apparent recent profit gap mostly came from marking the final day at its close in research while Freqtrade force-closes at that day's open. The same historical rule remains profitable at both cost levels; neither historical window passes5% risk, and previously reused dates are not fresh OOS.

Actual baseline R2b has larger maximum differences from the simple research engine, up to4.18% of initial capital. The research engine sizes both coins at current opens, sells first, and applies0.99 target scaling. The bot values a companion at its last analyzed close and also has Freqtrade's available-stake cap based on cost stakes/free cash and pair processing. The simple target reserve is not a guarantee of1% free cash after the adjustment band. These are execution constraints; do not dismiss this baseline gap as quantity rounding. Actual Freqtrade is the current execution evidence for both classes. Closed-trade recent Cov10 DD5.20% understates its7.56% marked daily DD.

## R5 broader funding-rate screen

Fixed BTC/ETH/SOL/XRP/BNB/ADA/DOGE/AVAX, Bybit versus OKX, July5–Oct4, original7-day forecast/hold and38bp hurdle. All16 histories pass the complete8h UTC validator, totaling4,368 realized settlement records. Across88 complete coin-weeks there are **zero cost-qualified entries**. Maximum forecast is13.03bp (ADA), below even the assumed four execution fees alone20bp. No threshold/universe sweep follows this result. The raw-page replay with network access disabled reproduces every weekly row. This is a necessary funding-only screen, not basis-inclusive P&L; no funding bot rule changed.

A diagnostic ceiling also checks whether merely holding longer could overcome the return target. Assume constant matched leg notionals,1x positions, half of account collateral at each venue, zero trading costs, no basis P&L, and perfect hindsight. Choosing the better direction separately at each of273 settlements gives a fixed-asset funding component of1.53%–2.51% linear annualized across this91-day sample. An even less executable oracle that also chooses the best of all eight assets at every settlement reaches5.46%. This is a sample funding-only upper bound under those assumptions, not total return, CAGR or a forecast. It offers no evidence for a12% funding return from a longer unleveraged holding horizon; leverage introduces a separate collateral/liquidation mechanism and is not silently added to rescue this result. Saved in `r5_funding_upper_bound.json`.

## Other independent research mechanisms

Review also covers earlier standalone studies without strategy classes: P1 correlation pairs lost59.9% /daily marked DD64.3%; cointegration selection still lost25.1% /DD35.3%. P2's96% closed win rate hides62.2% MTM DD and a negative later half; restoring its spread stop lost63.6%. F1 funding sorts have positive old aggregate results but negative2024/25/26 and63% DD. Their individual specs retain failed controls and execution caveats; no reopening by tuning here. The Sept26 BNB rewards snapshot showed reward-yield decay and a large unhedged BNB-price contribution, so it is not a current low-risk carry candidate. Rechecking that payer requires new reward events, not annualizing its old payout peak.

R2 data identity/coverage snapshot:696 expected files across116 pairs and six timeframes are present, with hashes and endpoints saved. No cached source file had a modification time after the clean run began. Endpoint presence does not prove uninterrupted candles or point-in-time coin availability. All clean source/config hashes still match the execution manifest.

Interior timestamp audit (`r2_data_quality.json`) finds one affected symbol, STRAX/USDT, in all six timeframes:11,820 missing1m bars and seven missing daily candles during the March2024 suspension. A raw daily open-to-prior-close scan outside[0.5,2.0] finds only its0.1 ratio on March28. The [official Binance announcement](https://www.binance.com/en/support/announcement/detail/2e6eb8e644664a6ca7b6659a226c41b0) identifies this as a1-old-to10-new token redenomination and suspension from March20 03UTC to March28 08UTC. It is not an ordinary90% economic loss. Freqtrade fills the gap with flat candles; its simulation does not itself multiply token holdings for this swap. The final export therefore requires a STRAX trade/exposure audit before an economic verdict. No shared candle cache was edited during the current run, and no affected symbol was silently removed after seeing a performance outcome.

The export contains just one STRAX trade per version, both December28 00:10–00:45UTC, earning5.94/5.02USDT. Neither held STRAX through the swap nor traded it during suspension. Their worst daily/intraday losses occurred July/August, before that first STRAX trade, so the failed drawdown screen is not a spurious90% position loss from redenomination. Recursive daily indicator effects have not been fully excluded; this dataset must not certify an adopted winner. Removing the tiny positive December trade alone cannot rescue either failed risk/return gate. Do not rerun a tuned universe to force a pass.

## R2 completed result and reflection

Clean historical target state, identical116-pair universe, six slots,1m trade detail and0.12% each-side cost,2024-01-03–2025-01-01 after unchanged startup. Sources/configs still match the execution hashes. Both exported-order final balances reconcile within0.01USDT, with positive daily cash.

| Strategy | Net return / CAGR | Daily marked DD | Primary5m-open wallet DD | Max daily exposure / single-name marked weight |
|---|---|---|---|---|
| Original X8PF | +26.89% /26.99% | 19.36% | 24.54% | 80.11% /19.42% |
| X8 cost cap5% | +4.98% /5.00% | 10.69% | 13.51% | 26.26% /5.19% |

The primary5m-open capture supplements daily risk; it is not every1m fill or intrabar high/low. Local source confirms balances are captured before that pair trades, with prices at main-candle opens. Add final liquidation balance at the end; keep daily/closed/wallet definitions distinct. Daily endpoints miss the August5 intraday trough. All figures are reused-history simulation results with cost allowances, not live executions or an OOS claim.

**Reject R2 after the first screen.** It preserves positive aggregate return but sacrifices81.5% of original net profit and still far exceeds5% risk. Its259 closed trades win92.7%, yet19 losers total-2,079.96USDT versus240 winners+2,577.76USDT: the average loss is about10.2 times the average win. Total assumed transaction costs389.86USDT consume a substantial part of gross trading gains. A cap on outstanding cost controls one holding's size; it does not constrain cumulative realized losses across repeated partial sales/rebuys or correlated losing names. Do not call this a profitable improvement merely because the cap worked. No2021, additional-year, double-cost or cap sweep follows the preregistered failure. Full outputs: `r2_results.json`, order-based curves/holdings and `r2_additional_diagnostics.json`.

## Selection checkpoint

All13 other bot configs and source families have been reviewed. R1/Cov10 remains the strongest reproducible historical profitability lead: actual Freqtrade profit over two multi-year windows and at doubled costs, with materially lower risk than its original R2b baseline. It fails the frozen5% default risk threshold in both periods; no strategy is selected as an adopted mandate-compliant winner. R2, R3, R4 and R5 are retained failures. The research candidates have no running bot configuration. The runtime checkpoint confirms existing frozen cohorts remain intact and the live account stays stopped. Fresh forward records are necessary before claiming a deployable edge; repeated selection on these same historical dates cannot provide them.

## R6 signed relative-trend result and reflection

The protocol and tested engine were committed as `b8931e0` before the first performance screen. Official checksummed hourly mark archives contain real missing days; the corresponding checksummed daily archives repair them with matching overlaps, never interpolation. Both assets now have50,400 hourly mark candles and6,300 actual funding events. All6,386 absent API-associated funding prices are explicit; central settlement uses the hour's mark OPEN and early-window adverse/favorable sensitivity uses that hour's high/low. Cached trade OHLC warmup starts2020-01-01; its high/low/close match the prior frozen dataset exactly. Input URLs, raw bodies, checksums, hashes and coverage are retained under `r6_data/`.

| Frozen mechanism | 2021-02–2023-12 CAGR /daily MTM DD | 2024-01–2026-08 CAGR /daily MTM DD | Double-cost CAGR early /recent |
|---|---|---|---|
| Relative30 BTC/ETH pair | -1.62% /23.94% | -1.05% /28.62% | -3.84% /-2.83% |
| Fixed long BTC /short ETH control | -2.28% /25.40% | -0.05% /20.97% | -2.76% /-0.61% |
| Cov10 long-exposure **perpetual** control | +9.85% /7.02% | +11.67% /7.69% | +9.50% /+11.08% |

The Cov10 control here includes actual perpetual funding and different futures prices/windows; it is not a revision of R1's validated spot results. All controls use the same signed perpetual ledger, post-fee joint budget and final closing fees. Final balances reconcile to realized price P&L plus funding minus fees within5e-11USDT. No hourly marked or adverse-envelope maintenance breach occurs at the assumed0.5% maintenance rate; this does not cure the failed return/drawdown screens or establish exact historical exchange-tier liquidation proof.

**Reject R6 without tuning.** The price-plus-funding income of the costed Relative30 run is only221.63/131.74USDT against685.75/410.08USDT costs (10,000 initial balance). Those decompositions are not a zero-cost counterfactual. Early-window settlement sensitivities span CAGR-1.66% to-1.58% and cannot change the verdict. Directional results also alternate:2021+14.44%,2023-17.78%,2024-26.13%,2025+19.43%,2026 partial+10.20%; a favorable year is not durable cross-period edge. Estimated10% annual risk is a volatility target, not a5% drawdown constraint: realized account volatility is10.68%/11.86%, yet prolonged loss sequences produce far larger drawdowns. Rebalance gross cap99% drifts to129.28%/124.30% between Mondays as prices and collateral move; maximum absolute daily net-dollar drift is7.69%/7.71%. Equal entry dollars do not make the pair continuously neutral. Preserve `r6_results.json`, `r6_diagnostics.json` and central daily/hourly curves; no actual Freqtrade run or new bot is warranted by this rejection.

## Execution defects found while checking R6

Binance's local cross-margin calculation previously used `amount*(mark-open)` for every other trade, including shorts. Their losses were credited as collateral and their profits debited. Root commit `d52cfaa0a` applies each other position's direction. Nine focused cases cover own/other long/short in dry/live modes and an unchanged historical open-price fallback. The fix is local; the running server image has not been restarted or replaced. It does not alter historical alpha because that backtest fallback makes other unrealized P&L zero.

The shared risk helper previously used net weight for its gross cap. Signed(+2,-2) escaped a0.8 gross cap despite4 gross risk; it now uses the sum of absolute weights. Existing long-only sizing is unchanged, proven by full-history/prefix Cov10 tests. Two regression cases cover offsetting and positive weights. Current-source bench, cross-venue and panel suite:165 tests plus16 subtests pass, with two existing dependency deprecation warnings. These are accounting/risk fixes, not profitability evidence; no running strategy rule or cohort was changed.
