# Cross-venue funding feasibility snapshot — 2026-09-27

Status: public-data feasibility only. No cross-venue bot, coordinator, account connection or trade was created.

Bybit and OKX public production APIs returned HTTP 200 with success codes. For BTC linear USDT perpetuals,
Bybit nextFundingTime and OKX fundingTime both identify the 2026-09-27 16:00 UTC settlement.
The raw responses and source timestamps are retained in public_api_snapshot.json.

At 2026-09-27 12:37:57 UTC, Bybit quoted 0.005211% and OKX -0.0009477445%.
Shorting Bybit / buying OKX would have a quoted funding spread of 0.0061587445% for that settlement,
about 0.6159 USDT for an illustrative 10,000 USDT notional on each side. These rates are estimates;
the funding comparison is not locked profit and slightly different marked notionals change cash flow.

Illustrative costs only: assuming 0.05% per fill, two entries plus two exits on 10,000 USDT each cost
20 USDT before spread, slippage, margin rebalancing or basis changes. This does not use the user's actual
fee tier. Roughly 33 identical settlements would be needed to recover fees alone; extrapolating this
single quote into persistent return or annual yield is unsupported. Total capital must count both
venues' margin and reserves, not just one leg's notional.

Proposed architecture: one portfolio coordinator, two venue execution adapters, separate margin ledgers,
matched signed base-asset exposure after contract conversion, timestamped funding forecasts and realized
settlements, executable order-book prices, partial-fill recovery, and combined PnL limits. A funding-spread
signal alone is insufficient; net expected funding must cover four fills and basis/leg-risk allowance.

Current local Freqtrade architecture uses one exchange per bot; two standalone bots do not supply the
coordination or portfolio accounting. A separate research/paper service is needed before live deployment.
This note is a feasibility assessment, not an implementation approval or a demonstrated strategy edge.

Official references:
- https://bybit-exchange.github.io/docs/v5/market/tickers
- https://bybit-exchange.github.io/docs/v5/market/history-fund-rate
- https://www.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate

Existing integration work: user_data/bots/unlock-short.json registers UnlockPressureShortV1 in the local
control panel with isolated futures, dry_run=true, 10,000 USDT virtual capital, one open trade and port 8085.
The local panel start operation was attempted; native Freqtrade failed to load fapi.binance.com markets
with ClientConnectorError. The new bot was then stopped through the panel, preserving existing bots.
