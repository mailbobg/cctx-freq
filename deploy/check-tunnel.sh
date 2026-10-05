#!/bin/bash
# 隧道改后必跑的校验：plist 结构 + 通道实测，不过不许说修好了。
# 用法：user_data/deploy/check-tunnel.sh
set -u
PLIST="$HOME/Library/LaunchAgents/com.ccxtfr.tunnel.plist"
FAIL=0

# 1. plist 结构：无孤儿 -L，目的地必须是最后一个参数
python3 - "$PLIST" << 'EOF' || exit 1
import plistlib, re, sys
d = plistlib.load(open(sys.argv[1], "rb"))
a = d["ProgramArguments"]
spec = re.compile(r"^\d+:\S+:\d+$")
bad = [j for j, x in enumerate(a) if x == "-L" and (j + 1 >= len(a) or not spec.match(a[j + 1]))]
at = [j for j, x in enumerate(a) if "@" in x]
assert not bad, f"orphan -L at {bad}"
assert len(at) == 1 and at[0] == len(a) - 1, f"destination must be last: {at}"
print(f"plist OK: {sum(1 for x in a if x == '-L')} forwards, dest last")
EOF
[ $? -ne 0 ] && FAIL=1

# 2. 通道实测：面板 + FreqUI + 抽查机器人 ping（000 仅允许停机的机器人）
code=$(curl -s --max-time 8 -o /dev/null -w "%{http_code}" http://127.0.0.1:8090/api/bots)
[ "$code" = "200" ] || { echo "FAIL panel api: $code"; FAIL=1; }
code=$(curl -s --max-time 8 -o /dev/null -w "%{http_code}" http://127.0.0.1:8089/__ccxt/logins)
[ "$code" = "200" ] || { echo "FAIL frequi logins: $code"; FAIL=1; }
for p in 8100 8113; do
  code=$(curl -s --max-time 6 -o /dev/null -w "%{http_code}" http://127.0.0.1:$p/api/v1/ping)
  [ "$code" = "200" ] || { echo "FAIL bot ping $p: $code"; FAIL=1; }
done

[ $FAIL -eq 0 ] && echo "TUNNEL ALL GREEN" || { echo "TUNNEL BROKEN"; exit 1; }
