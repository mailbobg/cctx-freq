#!/bin/bash
# 30-min status snapshot for strat_loop (+aq_loop frozen summary).
# Appends one line to status-30min.log and refreshes LATEST_STATUS.md.
set -u
SLOOP=/opt/ccxt/user_data/bench/strat_loop
ALOOP=/opt/ccxt/user_data/bench/aq_loop
PY=python3
TS=$(date -u +%FT%TZ)
SUM=$($PY - << 'EOF'
import json
try:
    a = json.load(open('/opt/ccxt/user_data/bench/aq_loop/queue.json'))
    aq = f"aq done total={len(a['items'])} champ={a.get('champion')}"
except Exception as e:
    aq = f"aq ERR {e}"
try:
    s = json.load(open('/opt/ccxt/user_data/bench/strat_loop/queue.json'))
    items = s['items']
    pend = sum(1 for i in items if i['status'] == 'pending')
    passed = sum(1 for i in items if i['status'] == 'done-pass')
    failed = len(items) - passed - pend
    st = (f"strat {s.get('status')} total={len(items)} pass={passed} "
          f"fail={failed} pend={pend} champ={s.get('champion')} "
          f"refills={s.get('refills')} cfail={s.get('consecutive_fail')}")
except Exception as e:
    st = f"strat ERR {e}"
print(f"{aq} | {st}")
EOF
)
LAST=$(tail -n 3 $SLOOP/cron.log 2>/dev/null | tr '\n' ';' | cut -c1-400)
echo "$TS $SUM || $LAST" >> $SLOOP/status-30min.log
cat > $SLOOP/LATEST_STATUS.md << EOF
# strat_loop 最新状态（$TS）
- $SUM
- cron 尾巴：$LAST
EOF
echo "$TS snapshot ok"
