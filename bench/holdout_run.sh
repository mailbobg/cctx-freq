#!/bin/zsh
# Holdout backtests (spec: specs/2026-09-26-holdout-2021-2023.md). Usage: holdout_run.sh <label> <fee|-> <strategies...>
cd /Users/bobmax/Documents/07-Hero/CCXT-Fr && source .venv/bin/activate
label=$1; fee=$2; shift 2
for y in 2021 2022 2023; do
  d=user_data/backtest_results/holdout/$label-$y; mkdir -p $d
  extra=(); [[ $fee != - ]] && extra=(--fee $fee)
  freqtrade backtesting -c user_data/config-holdout-$y.json --datadir user_data/data_holdout/binance \
    --strategy-list "$@" --timerange ${y}0101-$((y+1))0101 --export trades --backtest-directory $d $extra \
    > user_data/logs/holdout-$label-$y.log 2>&1 &
done
wait
