# strat_loop — LLM 全新策略文件进化环

父策略 `E0V1ENMTF1H`，门沿用 `aq_loop/evaluate.py` 同一套。
一批最多 2 个新文件，`refills<=5`，连续 6 fail 标 dead 停。

## 文件

- `queue.json` — `{parent, baseline, windows, pairs, items, champion, refills, consecutive_fail, status}`
  item: `{id, file, strategy, status: pending|done-pass|done-fail|done-fail-validate|done-fail-error,
  why, robust_sharpe, worst_dd, reasons, error, code_sha}`
- `propose.py` — 组 full-context prompt 调 LLM，一次一文件、最多 2 个；
  过 `validate.py` 才写入 `../../strategies/E1V1*.py` 并记 pending，
  不过记 done-fail-validate（代码不进策略目录）。
- `validate.py` — 静态检查：文件名 `E1V1<1-16大写>H`、类名=文件名、V3、1h、
  startup<=400、有 entry/indicators、docstring 有 `Parent:`、禁本地 import/pandas_ta/exec/eval/open/网络。
- `loop.sh` — cron tick：队头跑 4 窗口 docker 回测 → `aq_loop/evaluate.py` 判门 →
  更新 queue/champion/consecutive_fail，写 `results-strat.tsv`。队空且 refills<5
  才调 propose。磁盘>=80% 或 queue dead 直接停。
- `llm.py` 复用 `../aq_loop/llm.py`（zen/go `muse-spark-1.3`）。

## 运行（Vultr）

前置：`aq_loop` 必须已停（同机 2vCPU，两个 loop 并跑会 OOM）。
手动一 tick：`ssh server -> /opt/ccxt/user_data/bench/strat_loop/loop.sh`。
开 cron（10min，和 aq_loop 同节奏）：`crontab -e` 加
`*/10 * * * * /opt/ccxt/user_data/bench/strat_loop/loop.sh >> /opt/ccxt/user_data/bench/strat_loop/cron.log 2>&1`
冒烟（本地）：`python3 validate.py <file>` + winter 小窗回测。
