#!/bin/zsh
# Parameter neighbourhood for X9: 3 funding thresholds × 3 crowding percentiles = 9 backtests.
# Each cell writes its own result zip; summarise with evaluate.py afterwards.
# Usage: user_data/bench/x9_neighbourhood.sh [timerange]   (default 20240101-)
set -u
cd "$(dirname "$0")/../.."
TR=${1:-20240101-}
mkdir -p user_data/bench/x9_cells
for F in 0.20 0.30 0.40; do
  for C in 0.85 0.90 0.95; do
    LOW=$(python3 -c "print(round(1-$C,2))")
    CELL="F${F}_C${C}"
    cat > "user_data/bench/x9_cells/$CELL.json" <<EOF
{"x9": {"short_funding_ann": $F, "long_funding_ann": -$(python3 -c "print(round($F/3,3))"),
        "short_crowding_pct": $C, "long_crowding_pct": $LOW}}
EOF
    echo "===== cell $CELL start $(date '+%H:%M:%S')"
    .venv/bin/freqtrade backtesting -c user_data/config-x9-bench.json -c "user_data/bench/x9_cells/$CELL.json" \
      -s FundingCrowdingX9 --timerange "$TR" --fee 0.0005 --cache none --export trades \
      --export-filename "user_data/bench/x9_cells/$CELL.json.result" \
      --logfile "user_data/logs/x9-cell-$CELL.log" 2>&1 | grep -A6 "STRATEGY SUMMARY" | tail -3
  done
done
echo "===== all cells done $(date '+%H:%M:%S')"
