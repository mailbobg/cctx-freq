# funding-spread FreqUI connection

User correction: the 8086 service must appear as a bot in FreqUI. Previous delivery
only registered the custom runtime in panel 8090 and explicitly excluded it from
the native FreqUI login inventory. This design supersedes that exclusion.

Use a thin UI/API adapter over the existing paper engine. Preserve public-GET-only
exchange access, accounting/database, pairing, entry gates and simulation mode.
Reuse local Basic credentials and existing JWT secret, with scoped access/refresh
tokens. Permit CORS only for loopback UI origins. Serve no exchange key/order path.

Root 8086 redirects to 8089 selecting funding-spread. Keep original raw portfolio
page at /portfolio. Register the service in the existing FreqUI autologin inventory;
the panel card provides both FreqUI and portfolio links.

FreqUI presents two actual paper legs, each with direction, venue, quantity, fees
and funding; at most two legs = one portfolio. Panel 8090 retains portfolio counts,
aggregate PnL and its existing liquidation flow. A UI request to close either leg
closes the entire paired position. Reject partial/limit exits and manual entries.
Stop/pause stops new entries while existing paired risk management continues.

Expose only the older compatible API subset (2.0): REST polling, config, balances,
leg trades/history, profit, daily breakdown, whitelist/locks and supported controls.
Do not advertise native strategy backtesting, optimization or websocket features.
Strategy info identifies the independent CrossVenuePaper runtime. Market charts
are not provided; no exchange candle data is invented by this adapter.

Tests: token login/refresh/type/expiry and audience, CORS, required UI bootstrap
schema, equal leg quantities/directions, aggregate PnL/balances consistency,
portfolio count unchanged, paired force exit, unsupported action refusal, login
inventory includes paper without changing native launches. Actual FreqUI browser
selection, dashboard/trades/balances/config, local API and original bot PIDs are
the completion evidence. Work remains on isolated codex/cross-venue-paper branch.

## Completion evidence

48 compatibility/accounting/control tests passed before the final independent
read-only review. The review identified four Important and one Minor issue;
one regression fix pass addressed all of them, without requesting a re-review.

- Ruling: freeze exit targets before public price requests and recheck the same
  portfolio under lock after requests finish. A specific stale target returns
  409; an all-exit request excludes later replacement portfolios. Otherwise a
  click on an old paper leg can close an unrelated newer portfolio.
- Ruling: daily opening balances include realized profits before the requested
  window. Querying one day or 31 days must give the same rate for the same day.
- Ruling: native history contains closed legs only, newest close first; open
  legs live exclusively in status. Otherwise FreqUI merges duplicate trades.
- Ruling: performance and best pair use grouped PnL divided by grouped entry
  capital. This offsets opposing legs and handles different invested amounts.
- Ruling: expectancy ratio uses mean win/loss and win rate, or unavailable when
  there are no losses. Average duration follows native timedelta formatting.

The four added regression cases failed before the fixes, then passed. Final
suite: **52/52 passing**. Actual funding-spread restart, pause and start succeeded
on 8086. Scoped bearer requests to config, balances, profit, count, status,
history, daily, whitelist, locks and performance all returned HTTP 200.

The real FreqUI browser lists funding-spread alongside e0v1en, c-hold and
unlock-short, selects it and renders dashboard, trades, balance and configuration.
Reload preserves selection; configuration reports CrossVenuePaper 1.1 / Dry-Run.
Combined virtual balance is 10,000 USDT, with zero portfolios because BTC and ETH
estimated funding currently fails the cost gate. This is no profitability claim.

Installed funding PID: 21408 at verification. Original bot PIDs remain 61179,
53915 and 7126. Previously disabled x7/x8/x12 remain disabled. Runtime evidence
contains no tokens or passwords and is saved in the installed checkout at
bench/cross_venue_20260927/frequi-runtime-final.json. Only reviewed adapter changes
were selectively installed; unrelated working-checkout edits were preserved.
