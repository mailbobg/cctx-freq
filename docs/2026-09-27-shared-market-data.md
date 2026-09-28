# Shared Binance public REST data

User requested fewer duplicate Binance requests across bots. Existing native
bots are separate processes with separate CCXT objects and in-process caches;
using the same credentials does not share data or throttle across processes.
Current workloads: e0v1en spot 5m/118 pairs (websocket enabled), c-hold spot 1d
BTC/ETH, unlock-short USD-M 1h APT/TIA. Funding-spread uses Bybit/OKX, not Binance.

Ruling: add an on-demand public REST gateway to the existing loopback panel,
instead of changing strategy DataProviders or indicators. Freqtrade producer
mode shares analyzed frames and would require strategy changes. A new central
websocket collector is disproportionate to the present overlap and is deferred.

Native launch adds a final config overlay for public CCXT URLs only. Both sync
and async CCXT calls use it; websocket and private/order endpoints stay direct.
Panel price/day-candle reads use the same gateway object without loopback HTTP.

Scope: automatic overlay applies to Binance dry-run bots only. Normal private
API bases and public OHLCV websocket URLs remain unchanged. CCXT also puts some
key-bearing aliases (historicalTrades, userDataStream, listenKey) under public
base names; these aliases are unsupported and refused by the gateway. Live
launches keep direct exchange URLs until method-level routing supports them.

Use fixed Binance spot/USD-M origins and a strict read-only endpoint allowlist.
Reject signed/private/key-bearing requests. Keys include market, path and all
query parameters. One in-flight upstream call per key; bounded LRU storage and
pooled HTTP sessions. No stale-on-error fallback. Failed responses are not cached.
429/418 block further calls in the affected market through Retry-After.

Freshness: exchangeInfo 300s; bounded past-only kline responses 300s; live klines
2s but never across their final candle close; funding history 5s; quote/clock
reads have no retained cache (concurrent callers still share the in-flight call).
All timestamps, prices and bodies remain the exchange's raw response.

Expose public-only request/hit/upstream/coalescing/error counters. Verify empty
and malformed responses, query separation, simultaneous clients, expiry/candle
boundary, upstream failure, rate-limit backoff and private-request rejection.
Then check real sync+async CCXT, restart the three currently running Binance bots
sequentially after confirming dry-run, and preserve databases and disabled bots.

Baseline: isolated source ee0d39d, 52 tests pass. Installed checkout has unrelated
user edits. Work uses existing isolated worktree on codex/shared-market-data;
selective installation must check old file content before replacing it.

Sources: https://www.freqtrade.io/en/stable/producer-consumer/ and
https://developers.binance.com/en/docs/products/spot/rest-api .

## Verification and review

- Tests were run RED before gateway/launcher implementation, then GREEN. Final
  relevant suite: 68 tests, including concurrent clients, no retained quote
  cache, candle boundaries, invalid responses, failed requests, bounded memory,
  unsigned/loopback restrictions and config URL isolation.
- Independent read-only review of 951b199 found no Critical and two Important:
  queued requests bypassed backoff, and the overlay ignored custom PANEL_PORT.
  Both were reproduced RED and fixed in one pass. The credential-bearing public
  aliases boundary is addressed by a dry-run-only launcher and explicit scope.
- Ruling: publish backoff while still holding the network slot, and recheck
  after acquiring a slot. Queued requests must not evade Retry-After. Do not
  retain the expired 418 status when a later response is only a 429.
- Ruling: slow public network calls use asyncio's worker pool; panel control
  routes keep their independent AnyIO workers. A 40-client outage regression
  originally exhausted control workers and now leaves controls responsive.
- Ruling: custom ports receive an atomically generated, credential-free overlay
  in user_data/logs. The default deployment continues to use the reviewed
  bots/shared-market-data.json. Live and other-exchange launches stay direct.
- Real temporary gateway verified synchronous spot BTC 5m, synchronous USD-M
  APT 1h and asynchronous spot ETH 1d CCXT clients. Private and public OHLCV WS
  URL values were unchanged after applying the merged configuration.
- Eight real simultaneous clients requesting the same exchangeInfo resulted
  in one Binance request, both in the temporary probe and installed gateway.
  This is a same-query measurement, not an overall 87.5% traffic claim.
- e0v1en, c-hold and unlock-short were restarted sequentially in dry-run mode.
  During deployment x7/x8/x12 were found running too (their state had changed
  since the initial snapshot); they were also integrated sequentially. No bot
  was deliberately started from an observed stopped state by this work.
- Strategy, trading mode and database configurations were preserved. Native
  API calls return analyzed candles. Funding PID 21408 remains running and its
  Bybit/OKX public-data pipeline is independent of this Binance gateway.

The installed checkout keeps credential-free runtime evidence at
bench/shared_market_data_20260927/runtime-final.json. Source is isolated on
codex/shared-market-data; unrelated installed working-checkout edits remain.
All six running Binance simulation bots use the overlay; FreqUI lists all seven
running bots. Public OHLCV websocket connections remain per native process.

During deployment, x8/x12 hit SQLAlchemy QueuePool exhaustion and CoinGecko 429
errors while FreqUI was polling. The supervisor recovered both; final native
candle checks and subsequent RUNNING heartbeats succeeded (PIDs 24648/24675).
This is a separate native RPC/fiat resource issue, not a Binance rate-limit
response. No root-cause fix for that separate fault is claimed here. Native
fiat conversion and database code have not been changed by this work.
