#!/bin/zsh
# Step-1 analysis runs (plan in bench/dryrun_recon.md). Waits for the 1m download, then runs per year.
cd /Users/bobmax/Documents/07-Hero/CCXT-Fr && source .venv/bin/activate
bt() {  # label year timerange extra...
  d=user_data/backtest_results/ct/$1-$2; mkdir -p $d
  freqtrade backtesting -c user_data/config-bench.json -c user_data/config-net.json --enable-protections --timerange $3 \
    --export trades --backtest-directory $d "${@:4}" > user_data/logs/ct-$1-$2.log 2>&1
}
typeset -A TR; TR=(2024 20240101-20250101 2025 20250101-20260101 2026 20260101-20260923)
for y in 2024 2025 2026; do
  ( bt c5 $y $TR[$y] --strategy-list E0V1ENPFCT E0V1ENX12CT;
    bt c5fee2 $y $TR[$y] --fee 0.002 --strategy-list E0V1ENPFCT E0V1ENX12CT ) &
  sleep 20
done
for y in 2024 2025 2026; do
  bt detail $y $TR[$y] --timeframe-detail 1m --strategy-list E0V1ENPF E0V1ENX12 E0V1ENPFCT E0V1ENX12CT &
  sleep 20
done
wait
echo ct runs done
