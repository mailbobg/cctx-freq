# Cov10 independent paper bot

User-authorized paper activation on 2026-10-05 (Asia/Shanghai), following the R35 research selection. This is a new forward account; earlier historical/source failures remain in `bench/review_loop_20261004/`. No real-capital adoption or profitability guarantee follows from activation.

- Bot/config: `cov10`, `user_data/bots/cov10.json`.
- Strategy: `RiskHoldR2bCov10`, exact selected code plus its parent and risk helper frozen in `user_data/strategies/cov10_paper/`.
- Spot BTC/USDT and ETH/USDT, no shorts/leverage, 10,000 USDT simulated wallet, two slots, 99% tradable equity.
- Weekly original R2b timing, 30 completed returns for covariance, fixed 10% estimated annual volatility target, original 5pp band. A volatility target does not guarantee a drawdown ceiling.
- API: server loopback `8099`, forwarded by the existing Mac SSH tunnel. Panel: `http://127.0.0.1:8090/`; FreqUI: `http://127.0.0.1:8089/?bot=cov10`.
- Separate database: `user_data/tradesv3.cov10.dryrun.sqlite`; logs: `user_data/logs/cov10.log`.
- Per-side simulated fee override 0.0012, matching the R1 all-in research cost budget. It includes the 0.02% allowance; it is not a claim about the exchange account's actual fee. Actual paper fill-price differences must be measured separately.
- Keep R1's limit entry/exit and market emergency/forced exits. No forced entries to manufacture trades; a flat account may be correct for the weekly signal.
- The panel adds the existing shared-market-data overlay when starting this Binance dry-run bot. Do not bypass the cache with a second direct polling service.

The frozen directory prevents existing global strategy/helper updates from changing this cohort, and avoids overwriting the server's helper used by other strategies. The three Python files retain byte-identical copies of the selected local source. `manifest.json` records their SHA256 identities. A fresh process must verify that both imported parent/helper paths are from this directory.

After starting through `/api/bots/cov10/start`, verify actual `show_config` dry-run mode, wallet and strategy; both pair-candle histories and informative data; RUNNING state; FreqUI login availability; and the running container's mounted source identities. Activation evidence is retained separately in `bench/cov10_forward_20261005/`. Existing cohorts' databases and rules are not migrated into this account.

## Verified activation

Started the independent server container on 2026-10-05 at 08:53:08 Asia/Shanghai (00:53:08 UTC). Fresh effective-config APIs report `RiskHoldR2bCov10`, `dry_run=true`, spot mode, `RUNNING`, daily candles, and two slots. Both pairs returned 260 analyzed daily candles ending with the completed 2026-10-04 UTC candle. The Sunday decision has an allocation weight of 0.133877 per pair. The first two paper entries filled at 08:53:22 Asia/Shanghai, with approximately 1,325 USDT in each pair and `weekly_on` tags. This is a current startup decision, not a historical replay.

The configured wallet is 10,000 USDT; the API's 9,900 USDT starting-capital field reflects the 99% tradable-balance setting. Initial P&L includes the 0.12% opening and estimated closing fees. It is too early to assess forward profitability.

The FreqUI login registry includes `ccxt.cov10`, and the existing Mac LaunchAgent now persists the 8099 SSH forward. The container uses the shared-market overlay and `unless-stopped` restart policy. The separate SQLite database contains the two paper trades with 0.0012 fee rates on each side. All six previously active containers retained their IDs and start times; all 24 existing non-metadata/non-secret JSON configs and the global risk helper retained their hashes.

Evidence: `bench/cov10_forward_20261005/` contains configuration/source preflights, the deployment/start records, the token-free API snapshot, and before/after server checks.
