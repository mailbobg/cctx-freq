# Cross-venue funding paper bot

User request: fix the Binance futures connection and connect the second strategy as a new bot.
The existing authorization is for simulation. Binance routing has been repaired and the native
UnlockPressureShortV1 API confirms dry-run/running.

Implement one paper portfolio on Bybit + OKX, initially BTC/ETH linear USDT perpetuals.
The virtual wallet totals 10,000 USDT, split equally across venues. Use 1x margin and
at most one matched-base-quantity two-leg position, capped at 40% total equity per leg.

Entry uses fresh executable bid/ask quotes, actual contract quantity increments, matching
funding intervals and the same next settlement. Estimate only the next settlement.
Subtract the round-trip bid/ask cost, four fees (5 bp per fill), adverse paper slippage
(2 bp per fill) and a 10 bp basis-risk buffer. Enter only when positive, between 10 minutes
and two hours before settlement. Otherwise remain in cash and report the reason.

Persist both legs together in SQLite. Realized funding requires a historical settled rate
and the corresponding one-minute mark candle open, an explicit valuation approximation.
Do not credit forecasts. Deduplicate events, catch up after restart and block new entries
while any due funding is unaccounted for. Close after settlement has reconciled, a 20%
leg loss or 12-hour maximum hold; stale quotes defer fills and surface a fault.

Only public GET endpoints are available to the provider. The service rejects live mode,
has no API-key or order path, and uses explicit simulated simultaneous fills. This does
not establish live partial-fill, liquidation, fee-tier or exchange custody safety.

Integrate via a separate runtime in the existing panel: independent config/database/port,
start/stop/restart, offline persisted summary and a dedicated portfolio detail page.
Native Freqtrade bots retain their current command; cross-venue service is excluded from
FreqUI logins. Existing API credentials protect control requests on loopback.

Verification: meaningful tests for freshness, interval/settlement mismatch, units, fee gates,
equal quantities, cash/margin, restart, funding signs/dedup/time bounds, pending funding,
exit costs and stale-leg rejection. Provider parses captured real public responses. Verify
production public data, running service API, panel registration/UI and existing bot health.
One final read-only independent review under executing-plans, while runtime checks proceed.

Final reconciliation behavior: discover and paginate the actual settled events within
each venue's position time bounds, then persist an independent coverage watermark.
Funding intervals may change while a position exists; the entry forecast does not define
the historical event schedule. Allow two minutes for event publication; open-position
coverage uses completed minute boundaries. Recent closed positions block new entry until
their full holding interval is covered. History beyond the conservative 85-day recovery
window remains blocked for manual reconciliation instead of being assumed empty.

Isolate public-data failures by symbol and purpose. Fresh BTC prices can manage/close BTC
when ETH data or funding predictions fail. Missing predictions prohibit new entry but
do not prohibit historical reconciliation or exits with valid executable prices. Stale
position prices appear as faults both during refresh and between polling cycles.

Explicit panel start/restart resumes paper entries. Automatic crash recovery preserves
the persisted pause state. Sources for the implemented public contracts:
[Bybit settled funding history](https://bybit-exchange.github.io/docs/v5/market/history-fund-rate)
and [OKX API guide](https://www.okx.com/docs-v5/en/#public-data-rest-api-get-funding-rate-history).
