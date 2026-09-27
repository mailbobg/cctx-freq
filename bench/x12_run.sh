#!/bin/zsh
# X12 runs (spec: specs/2026-09-27-x12-dipbuy-crash-guard-design.md). Usage: x12_run.sh <label> <extra args...> -- <strategies...>
cd /Users/bobmax/Documents/07-Hero/CCXT-Fr && source .venv/bin/activate
label=$1; shift; extra=(); while [[ $1 != -- ]]; do extra+=($1); shift; done; shift
run() {  # window config datadir timerange
  d=user_data/backtest_results/x12/$label-$1; mkdir -p $d
  freqtrade backtesting -c $2 --datadir $3 --strategy-list "${@:5}" --timerange $4 --enable-protections \
    --export trades --backtest-directory $d $extra > user_data/logs/x12-$label-$1.log 2>&1
}
run 2426 user_data/config-bench.json user_data/data/binance 20240101-20260923 "$@" &
for y in 2021 2022 2023; do run $y user_data/config-holdout-$y.json user_data/data_holdout/binance ${y}0101-$((y+1))0101 "$@" & done
wait
