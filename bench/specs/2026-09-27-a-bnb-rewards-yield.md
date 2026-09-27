# A: yield of holding BNB for Binance reward programs (analysis, 2026-09-27)

Data: `events/bnb_rewards.csv` (Binance's own launchpool/HODLer APIs via www.binance.info — `avgPerAsset`,
`totalInvestAmount`; 30 Launchpools + 70 HODLer airdrops 2024-01 → 2026-09, all complete; 4 Megadrops without
per-BNB data, excluded), prices from data-api.binance.vision, BNB perp funding from data.binance.vision.
Per event: value per BNB = reward_per_bnb × token close on listing day (and day 7), as % of BNB's price then.
Result table `events/bnb_rewards_yield.csv`.

| year | events | airdrop value per BNB (day-1 prices) | annualised % of BNB | shorting BNB perp to hedge (funding) | BNB itself |
|---|---|---|---|---|---|
| 2024 | 25 (20 LP, 5 HODLer) | $122 | **24.1%** | −3.3% paid | +124% |
| 2025 | 68 (9 LP, 59 HODLer) | $66 | **8.9%** | −2.1% paid | +22% |
| 2026 to 09-26 | 7 (1 LP, 6 HODLer) | $2 | **0.4%** | +1.8% received | −11% |
| last 12 months | 21 | | **≈1.0%** | | |

Hedged (market-neutral) ≈ 21% (2024) → 7% (2025) → ~2% (2026, mostly funding) before margin/fee drag.
Median event: 0.7% of BNB (2024) → 0.07% (2025) → 0.03% (2026). Selling on day 7 instead of day 1 is worse in 80%
of events (median value ratio 0.83). The biggest payouts were 2024 Launchpools (ALT 2.9%, PIXEL 2.7%, SAGA 2.2%).

Verdict: the payer was real, but the yield has decayed ~50× in two years (more BNB competing — ~15–18M per event —
smaller allocations, far fewer Launchpools; none since 2026-03, no HODLer airdrop since 2026-06). The "177% since
2024" headline was mostly BNB's own price rise. Not worth building on today; worth re-checking only if Binance
restarts large Launchpools (cheap to monitor: one API call).
