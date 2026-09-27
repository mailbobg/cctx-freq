#!/bin/zsh
# X13 backtests (spec: specs/2026-09-27-x13-resting-tp-design.md). Per year, 1m detail, three fee levels.
cd /Users/bobmax/Documents/07-Hero/CCXT-Fr && source .venv/bin/activate
job() {  # year fee label
  typeset -A TR; TR=(2024 20240101-20250101 2025 20250101-20260101 2026 20260101-20260923)
  d=user_data/backtest_results/x13/$3-$1; mkdir -p $d
  freqtrade backtesting -c user_data/config-bench.json -c user_data/config-net.json -c user_data/config-x13.json \
    --enable-protections -s ${STRAT:-E0V1ENX13} --timerange $TR[$1] --timeframe-detail 1m --fee $2 \
    --export trades --backtest-directory $d > user_data/logs/x13-$3-$1.log 2>&1
  echo "done $3 $1 $(date +%H:%M)"
}
for spec in 2026:0.001:fee10 2025:0.001:fee10 2024:0.001:fee10 2026:0.002:fee20 2025:0.002:fee20 2024:0.002:fee20 2026:0.00075:fee075 2025:0.00075:fee075 2024:0.00075:fee075; do
  IFS=: read y f l <<< "$spec"
  job $y $f ${PREFIX:-}$l &
  sleep 20
  while [ $(jobs -r | wc -l) -ge 3 ]; do sleep 20; done
done
wait
echo "x13 runs done"
