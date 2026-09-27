#!/bin/zsh
# X8 5m vs 1m-detail, per-year (same method as bench/dryrun_recon.md step 1). Two jobs at a time.
cd /Users/bobmax/Documents/07-Hero/CCXT-Fr && source .venv/bin/activate
job() {  # year precision
  y=$1; p=$2; typeset -A TR; TR=(2024 20240101-20250101 2025 20250101-20260101 2026 20260101-20260923)
  d=user_data/backtest_results/x8detail/$p-$y; mkdir -p $d
  extra=(); [[ $p == 1m ]] && extra=(--timeframe-detail 1m)
  freqtrade backtesting -c user_data/config-bench-nfi.json -c user_data/config-net.json --enable-protections \
    -s NostalgiaForInfinityX8PF --timerange $TR[$y] --export trades --backtest-directory $d $extra \
    > user_data/logs/x8detail-$p-$y.log 2>&1
  echo "done $p $y $(date +%H:%M)"
}
typeset -fx job 2>/dev/null
jobs_list=(2026:5m 2026:1m 2025:5m 2025:1m 2024:5m 2024:1m)
running=0
for j in $jobs_list; do
  job ${j%%:*} ${j##*:} &
  sleep 30
  while [ $(jobs -r | wc -l) -ge 2 ]; do sleep 20; done
done
wait
echo "x8 detail runs done"
