# Implementation evidence — 2026-09-27

Spec: `docs/2026-09-27-cross-venue-paper.md`. User explicitly authorized paper-bot installation.
Base: nested user_data repository `2065e75`; isolated branch `codex/cross-venue-paper`.

- Engine, provider, service and panel tests were run RED before implementation, then GREEN.
- 29 tests pass: matched settlement/units, quantity rounding/margin, fee gates,
  freshness, persistent two-leg transactions, settled funding/dedup, pending reconciliation,
  exits, authenticated controls, preserved native command and exclusion from native FreqUI.
- Real production GET probe retrieved BTC/ETH prices and rates at both venues and
  exact historical funding and settlement-minute mark prices. No account credentials used.
- One actual Runtime refresh produced monitoring state, equity 10,000, cash 5,000
  at each venue, no position, and negative net estimates after costs for both assets.
- Binance connection diagnosed from actual Quantumult X Activity: Binance domain rule
  forced DIRECT. Existing `binance.com` host-suffix rule now uses the existing Binance
  policy group. HTTPS ping/exchangeInfo succeeded; unlock-short native API running/dry-run.
- No core Freqtrade or existing strategy behavior changed. Existing spot bots and
  unlock-short API confirmed running in dry-run mode before panel update.

Ruling: use a separate GET-only paper runtime behind the same panel — Freqtrade's native
single-exchange bot does not provide cross-venue atomic portfolio management. This retains
existing bot controls but cannot claim native Freqtrade execution or live partial-fill safety.

Ruling: retain the conservative next-settlement gate — a funding differential does not
demonstrate profit after four fills and basis risk. The service may remain flat for long periods.

Ruling: install only the new files and the reviewed panel changes into the user's existing
checkout — user requested running integration; unrelated existing changes must remain intact.

Final independent review: read-only reviewer examined `2065e75..e73e676`. No Critical;
three Important and one Minor. All four are addressed in one regression-test fix pass.

- Fixed explicit start/restart staying paused after liquidation. RED→GREEN
  `test_explicit_start_and_restart_resume_paper_entries` and actual pause→panel restart→running.
- Fixed frozen settlement grids missing actual changed-interval events. RED→GREEN
  `test_actual_settlement_after_interval_change_is_booked`, empty cancelled-schedule
  reconciliation, paginated event discovery and persisted coverage tests.
- Fixed ETH/prediction faults preventing BTC management and history reconciliation.
  RED→GREEN provider isolation, independent history and BTC paired manual-exit tests.
- Re-graded stale holding status as Important: a paper-risk exit delayed by stale prices
  must be visible on the control panel. RED→GREEN faults at refresh and between polls.
- Final suite: **40/40 passing**, both isolated source and installed user_data checkout.
- Final actual provider probe retrieved both venues' BTC settled event at 1790496000000
  through range discovery and the matching mark-minute candle. Actual Runtime refresh
  healthy, equity 10,000, no position and negative net estimates.
- Installed service on 127.0.0.1:8086; native unlock-short on 8085, panel 8090,
  native FreqUI 8089. Browser confirms both new bot cards running/simulation and the
  cross-venue detail page with live rate/cost tables, independent wallets and no positions.
- Actual stop/offline summary/start persistence checked. Panel restart preserved the
  existing e0v1en PID 61179, c-hold PID 53915 and unlock-short PID 7126.
- Final paper PID 10735; 15 persisted snapshots at verification, 0 positions; Binance
  futures ping HTTP 200. Runtime evidence is stored in the installed checkout under
  `bench/cross_venue_20260927/runtime-final.json` (no API credentials).

Ruling: bound automatic history recovery to 85 days and delay publication checks two
minutes — older/unavailable ranges remain blocked, never inferred to contain no events.
This may require manual reconciliation after prolonged downtime.

Ruling: retain the explicit minute-open funding valuation and simultaneous paper fills
as model assumptions — these are not certified exchange account fees or live fills.
Cost if wrong: paper profitability may overstate real execution results; no live adoption.

Reviewer declined scopes resolved: network repair, current public API, runtime and browser
are checked by the implementer; live execution/alpha remain unproven and expressly outside
this simulation integration. No deferred Minor remains after the risk-status re-grade.

User authorized installation; keep the isolated committed source branch and worktree,
without merging/pushing the unrelated working checkout. Existing user changes preserved.
