#!/bin/bash
# strat_loop tick: run the next pending strategy file (4 backtests in docker),
# evaluate gates (reuse aq_loop/evaluate.py), log, advance queue.json.
# 24h master switch: panel Loops card toggles $SLOOP/RUN (no file = paused).
# One tick ~= 10-20 min on the Vultr box. Cron every 10 min with a lock.
# Manual: ssh server -> /opt/ccxt/user_data/bench/strat_loop/loop.sh
# NOTE: do NOT run together with aq_loop on the 2vCPU box (docker OOM).
set -u
UD=${RESEARCH_USER_DATA:-/opt/ccxt/user_data}
LOOP=$UD/bench/aq_loop
SLOOP=$UD/bench/strat_loop
# All cron and manual entries acquire the shared gate before running this worker.
GATE="$UD/bench/research_scheduler/scheduler.py"
if [[ "${RESEARCH_GATE_LEASE:-}" != "strat_loop" ]]; then
  exec python3 "$GATE" --user-data "$UD" run strat_loop
fi
checkpoint() { python3 "$GATE" --user-data "$UD" checkpoint strat_loop; }
progress() { python3 "$UD/bench/research_scheduler/progress.py" "$UD" strat_loop "$@" || true; }
checkpoint || exit 0

LOCK=/tmp/strat_loop.lock
PY=python3
MAX_REFILLS=5
MAX_CONSEC_FAIL=6

if [[ -e $LOCK ]]; then
  LPID=$(cat $LOCK 2>/dev/null)
  if [[ "$LPID" != "$$" ]] && kill -0 "$LPID" 2>/dev/null; then
    echo "$(date -u +%FT%TZ) SKIP locked by $LPID"
    exit 0
  fi
fi
echo $$ > $LOCK
trap "rm -f $LOCK" EXIT

# 24h master switch (panel Loops card): no RUN file = paused, tick exits quietly
if [[ ! -f $SLOOP/RUN ]]; then
  echo "$(date -u +%FT%TZ) PAUSED (no RUN file, enable from panel Loops card)"
  exit 0
fi

# disk guard: backtests write zips + logs
USEPCT=$(df -P "$UD" | awk 'NR==2 {gsub(/%/,"",$5); print $5}')
if [[ ${USEPCT:-0} -ge 80 ]]; then
  echo "$(date -u +%FT%TZ) STOP disk ${USEPCT}% >= 80%"
  exit 0
fi

DEAD=$($PY -c "import json; print(json.load(open('$SLOOP/queue.json')).get('status','open'))")
if [[ $DEAD == dead ]]; then
  # 24/7: dead rounds roll over instead of halting (archived, champion kept)
  $PY $SLOOP/rollover.py $SLOOP/queue.json "dead-revive" >> $SLOOP/cron.log 2>&1
  exec "$0"
fi

ITEM=$($PY -c "
import json
q = json.load(open('$SLOOP/queue.json'))
pend = [i for i in q['items'] if i['status'] == 'pending']
print(pend[0]['id'] if pend else 'NONE')
")
if [[ $ITEM == NONE ]]; then
  REFILLS=$($PY -c "import json; print(json.load(open('$SLOOP/queue.json')).get('refills', 0))")
  CFAIL=$($PY -c "import json; print(json.load(open('$SLOOP/queue.json')).get('consecutive_fail', 0))")
  if [[ $CFAIL -ge $MAX_CONSEC_FAIL ]]; then
    progress rollover
    $PY $SLOOP/rollover.py $SLOOP/queue.json "6-consec-fail" >> $SLOOP/cron.log 2>&1
    exec "$0"
  fi
  if [[ $REFILLS -lt $MAX_REFILLS ]]; then
    if [[ ! -f $SLOOP/HYP ]]; then
      echo "$(date -u +%FT%TZ) hypothesis participation off; existing queue drained, waiting"
      exit 0
    fi
    echo "$(date -u +%FT%TZ) queue drained, researcher invents direction (refill $((REFILLS+1))/$MAX_REFILLS)"
    checkpoint || exit 0
    progress hypothesizing
    if ! $PY $SLOOP/hypothesize.py $SLOOP/queue.json >> $SLOOP/cron.log 2>&1; then
      echo "hypothesis failed: no strategy generation this tick"
      exit 1
    fi
    checkpoint || exit 0
    echo "$(date -u +%FT%TZ) queue drained, asking strategist (refill $((REFILLS+1))/$MAX_REFILLS)"
    progress proposing
    $PY $SLOOP/propose.py $SLOOP/queue.json >> $SLOOP/cron.log 2>&1 || exit 1
    $PY -c "
import json
p = '$SLOOP/queue.json'
q = json.load(open(p)); q['refills'] = q.get('refills', 0) + 1
json.dump(q, open(p, 'w'), indent=1)"
    exec "$0"
  fi
  echo "$(date -u +%FT%TZ) queue drained, refills exhausted -> rollover to round $(( $($PY -c "import json; print(json.load(open('$SLOOP/queue.json')).get('round',1))") + 1 ))"
  progress rollover
  $PY $SLOOP/rollover.py $SLOOP/queue.json exhausted >> $SLOOP/cron.log 2>&1
  exec "$0"
fi
echo "$(date -u +%FT%TZ) item $ITEM"
progress preparing --item "$ITEM"

STRAT=$($PY -c "import json; q=json.load(open('$SLOOP/queue.json')); print(next(i for i in q['items'] if i['id']=='$ITEM')['strategy'])")
SFILE="$UD/strategies/$STRAT.py"

# static re-validate (file may have been hand-edited since proposal)
progress validating --item "$ITEM" --strategy "$STRAT"
if ! $PY $SLOOP/validate.py "$SFILE" >> $SLOOP/cron.log 2>&1; then
  $PY - "$ITEM" << 'EOF'
import json, sys
p = '/opt/ccxt/user_data/bench/strat_loop/queue.json'
q = json.load(open(p))
for i in q["items"]:
    if i["id"] == sys.argv[1]:
        i["status"] = "done-fail-validate"
        i["error"] = "re-validate failed at run time, see cron.log"
q["consecutive_fail"] = q.get("consecutive_fail", 0) + 1
if q["consecutive_fail"] >= 6:
    q["status"] = "dead"
json.dump(q, open(p, "w"), indent=1)
EOF
  echo "$(date -u +%FT%TZ) $ITEM FAIL-validate"
  exit 0
fi

PAIRS="BTC/USDT SOL/USDT AVAX/USDT"
mkdir -p $SLOOP/results
# base config (same economics as aq_loop, no aq overrides: E1 files are self-contained)
$PY - > $SLOOP/cfg-$ITEM.json << 'EOF'
import json
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
}
print(json.dumps(cfg))
EOF
OK=1
JOBS=2  # parallel backtest windows per item (each docker capped 1500m); raise only with RAM headroom
run_win() {
  local TR=$1
  checkpoint || { echo "$ITEM $TR SKIP-gate"; echo "$TR" >> $SLOOP/.fail-$ITEM; return 0; }
  progress backtesting --item "$ITEM" --strategy "$STRAT" --window "$TR"
  timeout 1500 docker run --rm -m 1500m \
    -v /opt/ccxt/user_data:/freqtrade/user_data -w /freqtrade \
    ccxt-freqtrade:local backtesting \
    --userdir /freqtrade/user_data \
    -c /freqtrade/user_data/bench/strat_loop/cfg-$ITEM.json \
    --strategy $STRAT --timeframe 1h --timerange $TR -p $PAIRS \
    --datadir /freqtrade/user_data/data_aq/binance \
    --backtest-directory /freqtrade/user_data/bench/strat_loop/results/$ITEM-$TR \
    > $SLOOP/log-$ITEM-$TR.log 2>&1 || echo "$TR" >> $SLOOP/.fail-$ITEM
}
rm -f $SLOOP/.fail-$ITEM
for TR in 20210101-20211231 20220101-20221231 20230101-20251231 20210101-20251231; do
  while [[ $(jobs -r | wc -l) -ge $JOBS ]]; do sleep 15; done
  run_win $TR &
done
wait || true
if [[ -f $SLOOP/.fail-$ITEM ]]; then echo "$ITEM FAILED windows: $(tr '\n' ' ' < $SLOOP/.fail-$ITEM)"; OK=0; fi
rm -f $SLOOP/.fail-$ITEM
# collect in window order (touch keeps mtime order for evaluate's windowing)
for TR in 20210101-20211231 20220101-20221231 20230101-20251231 20210101-20251231; do
  for f in $SLOOP/results/$ITEM-$TR-*.zip $SLOOP/results/$ITEM-$TR-*.meta.json; do
    [[ -e $f ]] || continue
    mv "$f" $SLOOP/results/$ITEM/
    touch $SLOOP/results/$ITEM/$(basename "$f")
  done
done

TS=$(date -u +%FT%TZ)
if [[ $OK -eq 1 ]]; then
  mkdir -p $SLOOP/results/$ITEM
  mv $SLOOP/results/$ITEM-*.zip $SLOOP/results/$ITEM/ 2>/dev/null
  mv $SLOOP/results/$ITEM-*.meta.json $SLOOP/results/$ITEM/ 2>/dev/null
  set +e
  progress evaluating --item "$ITEM" --strategy "$STRAT"
  EVAL=$($PY $LOOP/evaluate.py "$SLOOP/results/$ITEM" $STRAT "$SLOOP/queue.json" $ITEM)
  EVRC=$?
  set -e
  echo "EVAL: $EVAL"
  if [[ -z $EVAL ]]; then
    # evaluate itself crashed (e.g. no zips): record infra failure, never parse empty
    $PY - "$ITEM" << 'EOF'
import json, sys
p = '/opt/ccxt/user_data/bench/strat_loop/queue.json'
q = json.load(open(p))
for i in q["items"]:
    if i["id"] == sys.argv[1]:
        i["status"] = "done-fail-error"
        i["error"] = "evaluate crashed (see cron.log), no metrics"
q["consecutive_fail"] = q.get("consecutive_fail", 0) + 1
if q["consecutive_fail"] >= 6:
    q["status"] = "dead"
json.dump(q, open(p, "w"), indent=1)
EOF
    echo "$TS $ITEM ERROR-eval"
    rm -f $SLOOP/cfg-$ITEM.json
    exit 0
  fi
  if [[ $EVRC -eq 0 ]]; then
    VERDICT=PASS
  else
    VERDICT=FAIL
  fi
  SHARPE=$(echo $EVAL | $PY -c "import json,sys; ms=json.load(sys.stdin)['metrics']; print(round(min(m['sharpe'] for m in ms.values()),3))")
  DD=$(echo $EVAL | $PY -c "import json,sys; ms=json.load(sys.stdin)['metrics']; print(round(max(m['dd'] for m in ms.values()),2))")
  REASONS=$(echo $EVAL | $PY -c "import json,sys; print(json.dumps(json.load(sys.stdin).get('reasons',[])))")
  TOTALS=$(echo $EVAL | $PY -c "import json,sys; ms=json.load(sys.stdin)['metrics']; print(json.dumps({w: round(m['total'],1) for w,m in ms.items()}))")
  $PY - "$ITEM" "$VERDICT" "$SHARPE" "$DD" "$REASONS" "$TOTALS" << 'EOF'
import json, sys
p = '/opt/ccxt/user_data/bench/strat_loop/queue.json'
q = json.load(open(p))
verdict = sys.argv[2]
for i in q["items"]:
    if i["id"] == sys.argv[1]:
        i["status"] = "done-" + verdict.lower()
        i["robust_sharpe"] = float(sys.argv[3])
        i["worst_dd"] = float(sys.argv[4])
        i["reasons"] = json.loads(sys.argv[5])
        i["totals"] = json.loads(sys.argv[6])
if verdict == "PASS":
    q["champion"] = sys.argv[1]
    q["consecutive_fail"] = 0
else:
    q["consecutive_fail"] = q.get("consecutive_fail", 0) + 1
    if q["consecutive_fail"] >= 6:
        q["status"] = "dead"
json.dump(q, open(p, "w"), indent=1)
with open('/opt/ccxt/user_data/bench/strat_loop/results-strat.tsv', 'a') as f:
    import os as _os
    if _os.path.getsize('/opt/ccxt/user_data/bench/strat_loop/results-strat.tsv') == 0:
        f.write("round\tevent\tstrategy_file\tsharpe\tmax_dd\tnote\n")
    f.write(f"{sys.argv[1]}\tstrat\t{sys.argv[1]}\t{sys.argv[3]}\t-{sys.argv[4]}\t{sys.argv[1]} {verdict}\n")
EOF
  echo "$TS $ITEM $VERDICT"
else
  $PY - "$ITEM" << 'EOF'
import json, sys
p = '/opt/ccxt/user_data/bench/strat_loop/queue.json'
q = json.load(open(p))
for i in q["items"]:
    if i["id"] == sys.argv[1]:
        i["status"] = "done-fail-error"
        i["error"] = "backtest crash/timeout, see log-*.log"
q["consecutive_fail"] = q.get("consecutive_fail", 0) + 1
if q["consecutive_fail"] >= 6:
    q["status"] = "dead"
json.dump(q, open(p, "w"), indent=1)
EOF
  echo "$TS $ITEM ERROR"
fi
rm -f $SLOOP/cfg-$ITEM.json
