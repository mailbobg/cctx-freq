#!/bin/bash
# aq_loop tick: run the next pending queue item (4 backtests in docker),
# evaluate gates, log to results-aq.tsv, advance queue.json.
# One tick ~= 10-20 min on the Vultr box. Cron every 30 min with a lock.
# Manual: ssh server -> /opt/ccxt/user_data/bench/aq_loop/loop.sh
set -u
UD=${RESEARCH_USER_DATA:-/opt/ccxt/user_data}
LOOP=$UD/bench/aq_loop
# All cron and manual entries acquire the shared gate before running this worker.
GATE="$UD/bench/research_scheduler/scheduler.py"
if [[ "${RESEARCH_GATE_LEASE:-}" != "aq_loop" ]]; then
  exec python3 "$GATE" --user-data "$UD" run aq_loop
fi
checkpoint() { python3 "$GATE" --user-data "$UD" checkpoint aq_loop; }
progress() { python3 "$UD/bench/research_scheduler/progress.py" "$UD" aq_loop "$@" || true; }
checkpoint || exit 0

LOCK=/tmp/aq_loop.lock
PY=python3

if [[ -e $LOCK ]]; then
  LPID=$(cat $LOCK 2>/dev/null)
  if [[ "$LPID" != "$$" ]] && kill -0 "$LPID" 2>/dev/null; then
    echo "$(date -u +%FT%TZ) SKIP locked by $LPID"
    exit 0
  fi
  # stale lock (dead pid) or our own re-entry: proceed
fi
echo $$ > $LOCK
trap "rm -f $LOCK" EXIT

# 24h master switch (panel Loops card): no RUN file = paused, tick exits quietly
if [[ ! -f $LOOP/RUN ]]; then
  echo "$(date -u +%FT%TZ) PAUSED (no RUN file, enable from panel Loops card)"
  exit 0
fi

ITEM=$($PY -c "
import json
q = json.load(open('$LOOP/queue.json'))
pend = [i for i in q['items'] if i['status'] == 'pending']
print(pend[0]['id'] if pend else 'NONE')
")
if [[ $ITEM == NONE ]]; then
  # queue drained: ask the LLM advisor for the next batch (max 5 refills)
  REFILLS=$($PY -c "import json; print(json.load(open('$LOOP/queue.json')).get('refills', 0))")
  if [[ $REFILLS -lt 5 ]]; then
    echo "$(date -u +%FT%TZ) queue drained, asking advisor (refill $((REFILLS+1))/5)"
    checkpoint || exit 0
    progress advising
    $PY $LOOP/advise.py $LOOP/queue.json >> $LOOP/cron.log 2>&1
    $PY -c "
import json
p = '$LOOP/queue.json'
q = json.load(open(p)); q['refills'] = q.get('refills', 0) + 1
json.dump(q, open(p, 'w'), indent=1)"
    exec "$0"  # re-enter: process the refilled queue (or exit if still empty)
  fi
  # 24/7: refill caps reset instead of halting (history kept, advisor sees all tried)
  echo "$(date -u +%FT%TZ) queue drained, refills exhausted -> reset to 0, keep running 24/7"
  $PY -c "
import json
p = '$LOOP/queue.json'
q = json.load(open(p)); q['refills'] = 0
json.dump(q, open(p, 'w'), indent=1)"
  exec "$0"
fi
echo "$(date -u +%FT%TZ) item $ITEM"
progress preparing --item "$ITEM"

# build a temp config = base + aq block
$PY - "$LOOP/queue.json" $ITEM > $LOOP/cfg-$ITEM.json << 'EOF'
import json, sys
q = json.load(open(sys.argv[1]))
item = next(i for i in q["items"] if i["id"] == sys.argv[2])
cfg = {
  "max_open_trades": 5, "stake_currency": "USDT", "stake_amount": "unlimited",
  "tradable_balance_ratio": 0.99, "timeframe": "1h", "dry_run": True,
  "dry_run_wallet": 10000, "trading_mode": "spot",
  "unfilledtimeout": {"entry": 10, "exit": 10, "exit_timeout_count": 0, "unit": "minutes"},
  "entry_pricing": {"price_side": "other", "use_order_book": False, "order_book_top": 1, "price_last_balance": 0.0},
  "exit_pricing": {"price_side": "other", "use_order_book": False, "order_book_top": 1},
  "exchange": {"name": "binance", "key": "", "secret": "",
    "pair_whitelist": ["BTC/USDT", "SOL/USDT", "AVAX/USDT"], "pair_blacklist": []},
  "pairlists": [{"method": "StaticPairList"}],
  "fee": 0.001, "dataformat_ohlcv": "feather",
  "internals": {"process_throttle_secs": 5},
  "aq": item["aq"],
}
print(json.dumps(cfg))
EOF

STRAT=$($PY -c "import json; print(json.load(open('$LOOP/queue.json'))['strategy'])")
PAIRS="BTC/USDT SOL/USDT AVAX/USDT"
OK=1
JOBS=2  # parallel backtest windows per item (each docker capped 1500m); raise only with RAM headroom
run_win() {
  local TR=$1
  checkpoint || { echo "$ITEM $TR SKIP-gate"; echo "$TR" >> $LOOP/.fail-$ITEM; return 0; }
  progress backtesting --item "$ITEM" --strategy "$STRAT" --window "$TR"
  timeout 1500 docker run --rm -m 1500m \
    -v /opt/ccxt/user_data:/freqtrade/user_data -w /freqtrade \
    ccxt-freqtrade:local backtesting \
    --userdir /freqtrade/user_data \
    -c /freqtrade/user_data/bench/aq_loop/cfg-$ITEM.json \
    --datadir /freqtrade/user_data/data_aq/binance \
    --strategy $STRAT --timeframe 1h --timerange $TR -p $PAIRS \
    --backtest-directory /freqtrade/user_data/bench/aq_loop/results/$ITEM-$TR \
    > $LOOP/log-$ITEM-$TR.log 2>&1 || echo "$TR" >> $LOOP/.fail-$ITEM
}
rm -f $LOOP/.fail-$ITEM
for TR in 20210101-20211231 20220101-20221231 20230101-20251231 20210101-20251231; do
  while [[ $(jobs -r | wc -l) -ge $JOBS ]]; do sleep 15; done
  run_win $TR &
done
wait || true
if [[ -f $LOOP/.fail-$ITEM ]]; then echo "$ITEM FAILED windows: $(tr '\n' ' ' < $LOOP/.fail-$ITEM)"; OK=0; fi
rm -f $LOOP/.fail-$ITEM
# collect in window order (touch keeps mtime order for evaluate's windowing)
for TR in 20210101-20211231 20220101-20221231 20230101-20251231 20210101-20251231; do
  for f in $LOOP/results/$ITEM-$TR-*.zip $LOOP/results/$ITEM-$TR-*.meta.json; do
    [[ -e $f ]] || continue
    mv "$f" $LOOP/results/$ITEM/
    touch $LOOP/results/$ITEM/$(basename "$f")
  done
done

TS=$(date -u +%FT%TZ)
if [[ $OK -eq 1 ]]; then
  # freqtrade drops result files next to --backtest-directory using its
  # basename as filename prefix; collect this item's files into its subdir
  mkdir -p $LOOP/results/$ITEM
  mv $LOOP/results/$ITEM-*.zip $LOOP/results/$ITEM/ 2>/dev/null
  mv $LOOP/results/$ITEM-*.meta.json $LOOP/results/$ITEM/ 2>/dev/null
  progress evaluating --item "$ITEM" --strategy "$STRAT"
  EVAL=$($PY $LOOP/evaluate.py "$LOOP/results/$ITEM" $STRAT "$LOOP/queue.json" $ITEM)
  echo "EVAL: $EVAL"
  if [[ -z $EVAL ]]; then
    $PY - "$ITEM" << 'EOF'
import json, sys
p = '/opt/ccxt/user_data/bench/aq_loop/queue.json'
q = json.load(open(p))
for i in q["items"]:
    if i["id"] == sys.argv[1]:
        i["status"] = "failed"
        i["error"] = "evaluate crashed (see cron.log), no metrics"
json.dump(q, open(p, "w"), indent=1)
EOF
    echo "$TS $ITEM ERROR-eval"
    rm -f $LOOP/cfg-$ITEM.json
    exit 0
  fi
  VERDICT=$(echo $EVAL | $PY -c "import json,sys; print(json.load(sys.stdin)['verdict'])")
  SHARPE=$(echo $EVAL | $PY -c "import json,sys; ms=json.load(sys.stdin)['metrics']; print(round(min(m['sharpe'] for m in ms.values()),3))")
  DD=$(echo $EVAL | $PY -c "import json,sys; ms=json.load(sys.stdin)['metrics']; print(round(max(m['dd'] for m in ms.values()),2))")
  $PY - "$ITEM" "$VERDICT" "$SHARPE" "$DD" << 'EOF'
import json, sys
p = '/opt/ccxt/user_data/bench/aq_loop/queue.json'
q = json.load(open(p))
verdict = sys.argv[2]
if sys.argv[1] == 'base':
    verdict = 'BASELINE'  # reference run: gates don't apply to itself
for i in q["items"]:
    if i["id"] == sys.argv[1]:
        i["status"] = "done-" + verdict.lower()
        i["robust_sharpe"] = float(sys.argv[3])
        i["worst_dd"] = float(sys.argv[4])
if verdict == "PASS":
    cur = q.get("champion")
    q["champion"] = sys.argv[1]  # latest passer (totals compared at review)
json.dump(q, open(p, "w"), indent=1)
with open('/opt/ccxt/user_data/bench/aq_loop/results-aq.tsv', 'a') as f:
    import os as _os
    if _os.path.getsize('/opt/ccxt/user_data/bench/aq_loop/results-aq.tsv') == 0:
        f.write("round\tevent\tstrategy_name\tsharpe\tmax_dd\tnote\n")
    f.write(f"{sys.argv[1]}\tevolve\tE0V1ENMTF1H-var\t{sys.argv[3]}\t-{sys.argv[4]}\t{sys.argv[1]} aq sweep {verdict}\n")
EOF
  echo "$TS $ITEM $VERDICT"
else
  $PY - "$ITEM" << 'EOF'
import json, sys
p = '/opt/ccxt/user_data/bench/aq_loop/queue.json'
q = json.load(open(p))
for i in q["items"]:
    if i["id"] == sys.argv[1]:
        i["status"] = "failed"
json.dump(q, open(p, "w"), indent=1)
EOF
  echo "$TS $ITEM ERROR"
fi
rm -f $LOOP/cfg-$ITEM.json
