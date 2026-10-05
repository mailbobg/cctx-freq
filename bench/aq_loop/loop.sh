#!/bin/bash
# aq_loop tick: run the next pending queue item (4 backtests in docker),
# evaluate gates, log to results-aq.tsv, advance queue.json.
# One tick ~= 10-20 min on the Vultr box. Cron every 30 min with a lock.
# Manual: ssh server -> /opt/ccxt/user_data/bench/aq_loop/loop.sh
set -u
UD=/opt/ccxt/user_data
LOOP=$UD/bench/aq_loop
LOCK=/tmp/aq_loop.lock
PY=python3

if [[ -e $LOCK ]]; then
  echo "$(date -u +%FT%TZ) SKIP locked"
  exit 0
fi
touch $LOCK
trap "rm -f $LOCK" EXIT

ITEM=$($PY -c "
import json
q = json.load(open('$LOOP/queue.json'))
pend = [i for i in q['items'] if i['status'] == 'pending']
print(pend[0]['id'] if pend else 'NONE')
")
if [[ $ITEM == NONE ]]; then
  echo "$(date -u +%FT%TZ) queue drained"
  exit 0
fi
echo "$(date -u +%FT%TZ) item $ITEM"

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
for TR in 20210101-20211231 20220101-20221231 20230101-20251231 20210101-20251231; do
  timeout 1500 docker run --rm \
    -v /opt/ccxt/user_data:/freqtrade/user_data -w /freqtrade \
    ccxt-freqtrade:local backtesting \
    --userdir /freqtrade/user_data \
    -c /freqtrade/user_data/bench/aq_loop/cfg-$ITEM.json \
    --datadir /freqtrade/user_data/data_aq/binance \
    --strategy $STRAT --timeframe 1h --timerange $TR -p $PAIRS \
    --backtest-directory /freqtrade/user_data/bench/aq_loop/results/$ITEM \
    > $LOOP/log-$ITEM-$TR.log 2>&1
  if [[ $? -ne 0 ]]; then echo "$ITEM $TR FAILED"; OK=0; break; fi
done

TS=$(date -u +%FT%TZ)
if [[ $OK -eq 1 ]]; then
  EVAL=$($PY $LOOP/evaluate.py "$LOOP/results/$ITEM" $STRAT "$LOOP/queue.json" $ITEM)
  echo "EVAL: $EVAL"
  VERDICT=$(echo $EVAL | $PY -c "import json,sys; print(json.load(sys.stdin)['verdict'])")
  SHARPE=$(echo $EVAL | $PY -c "import json,sys; ms=json.load(sys.stdin)['metrics']; print(round(min(m['sharpe'] for m in ms.values()),3))")
  DD=$(echo $EVAL | $PY -c "import json,sys; ms=json.load(sys.stdin)['metrics']; print(round(max(m['dd'] for m in ms.values()),2))")
  $PY - "$ITEM" "$VERDICT" "$SHARPE" "$DD" << 'EOF'
import json, sys
p = '/opt/ccxt/user_data/bench/aq_loop/queue.json'
q = json.load(open(p))
for i in q["items"]:
    if i["id"] == sys.argv[1]:
        i["status"] = "done-" + sys.argv[2].lower()
        i["robust_sharpe"] = float(sys.argv[3])
        i["worst_dd"] = float(sys.argv[4])
if sys.argv[2] == "PASS":
    cur = q.get("champion")
    q["champion"] = sys.argv[1]  # latest passer (totals compared at review)
json.dump(q, open(p, "w"), indent=1)
with open('/opt/ccxt/user_data/bench/aq_loop/results-aq.tsv', 'a') as f:
    import os as _os
    if _os.path.getsize('/opt/ccxt/user_data/bench/aq_loop/results-aq.tsv') == 0:
        f.write("round\tevent\tstrategy_name\tsharpe\tmax_dd\tnote\n")
    f.write(f"{sys.argv[1]}\tevolve\tE0V1ENMTF1H-var\t{sys.argv[3]}\t-{sys.argv[4]}\t{sys.argv[1]} aq sweep {sys.argv[2]}\n")
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
