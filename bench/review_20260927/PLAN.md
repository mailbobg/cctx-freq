# 2026-09-27 strategy risk revision

Authorized: fix reviewed defects and optimize the current strategies. Preserve original entry rules and do not fit parameters to historical profit. Original changed files are saved in originals/ (no credentials).

Fixes: mark-to-market sizing and cost-basis partial exits in RiskHoldC; market emergency/forced/stop exits in shared bot config; enough E0V1EN history for its 288-bar return.

Three research candidates (not automatic replacements):
- RiskHoldCPortfolio: same weekly signals; scale both weights down together if 30-day covariance implies annual portfolio volatility above 40%, or gross allocation above 80%. No leverage. Retain 5pp rebalance band.
- E0V1ENBounded: same entry and custom exits as E0V1ENPF; disable intrabar trailing, use 3% ROI, 10% hard stop and unconditional 24h maximum holding. This reopens the closed family only as an explicitly authorized risk experiment, not a new claim of alpha.
- NostalgiaForInfinityX8Budget: preserve X8PF signals and exits; limit invested cost per position to 15% of the bot's realized capital budget and refuse additions below -5% current profit. A budget limit does not cap market value after appreciation and is not a hard portfolio drawdown limit.

No parameter sweep. Compare each with its own matched baseline. All historical windows already examined are in-sample. Daily covariance is estimated from closed candles only. C: full cached 2017-2026 history, 0.1% and 0.2% costs; daily/MTM net returns. Dip/X8: 1-minute detail matched local backtests, protections on, common costs/config/universe. A shortened execution check must never be presented as proof of improvement across years.

Adoption gate: lower MTM drawdown, retain >=90% of positive baseline return, no negative year introduced, and repeat at double fees over the full relevant history. Otherwise keep as research candidate. Test passing verifies behavior, not profitability.

Evaluation improvement: contiguous daily portfolio-P&L block bootstrap (7 and 30 days); report enter-tag profit attribution. Treat these intervals as sensitivity diagnostics, not proof after repeated model selection. Existing trade-IID intervals must be explicitly labeled as such.

Activation: fix files, validate resolved configs first. New research variants are not switched into running bots. Existing processes need a controlled reload to adopt bug fixes; record actual runtime state separately.
