#!/bin/zsh
# Local development panel, next to the SSH tunnel to the server (which owns 8080–8090 on this Mac).
#   dev-panel.sh start    panel on http://127.0.0.1:9090, FreqUI on :9089, bot API ports = config port + 1000,
#                         databases in user_data/dev/, logs in user_data/logs/dev/, own state file
#   dev-panel.sh stop     stops every dev bot (processes are kept apart from the server's), then the panel
#   dev-panel.sh status
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEV="$ROOT/user_data/logs/dev"; PIDF="$DEV/panel.pid"; mkdir -p "$DEV"
export PANEL_PORT=9090 FREQUI_PORT=9089 PANEL_BOT_PORT_OFFSET=1000 PANEL_ENV_LABEL="本地开发" \
       PANEL_STATE_FILE="$ROOT/user_data/panel/state.dev.json"
running() { [[ -f "$PIDF" ]] && kill -0 "$(cat "$PIDF")" 2>/dev/null; }
case "${1:-status}" in
  start)
    if running; then echo "本地开发控制台已在运行：http://127.0.0.1:$PANEL_PORT"; exit 0; fi
    if lsof -nP -iTCP:$PANEL_PORT -sTCP:LISTEN -t >/dev/null; then echo "端口 $PANEL_PORT 被占用"; exit 1; fi
    cd "$ROOT" && nohup .venv/bin/python user_data/panel/panel.py >> "$DEV/panel.log" 2>&1 &
    echo $! > "$PIDF"
    for i in {1..30}; do curl -s -m 2 -o /dev/null "http://127.0.0.1:$PANEL_PORT/" && break; sleep 1; done
    if curl -s -m 2 -o /dev/null "http://127.0.0.1:$PANEL_PORT/"; then
      echo "本地开发控制台：http://127.0.0.1:$PANEL_PORT    FreqUI：http://127.0.0.1:$FREQUI_PORT"
      echo "（服务器的控制台仍是 http://127.0.0.1:8090）"
    else echo "启动失败，看日志：$DEV/panel.log"; exit 1; fi ;;
  stop)
    if running; then
      curl -s -m 300 -X POST "http://127.0.0.1:$PANEL_PORT/api/bots/all/stop" >/dev/null && echo "本地开发机器人已全部停止"
      kill "$(cat "$PIDF")" && rm -f "$PIDF" && echo "本地开发控制台已停止"
    else echo "本地开发控制台没有在运行"; fi ;;
  status)
    if running; then
      echo "运行中：http://127.0.0.1:$PANEL_PORT"
      curl -s -m 20 "http://127.0.0.1:$PANEL_PORT/api/bots" | "$ROOT/.venv/bin/python" -c "import json,sys; [print(' ', b['name'], ':'+str(b['port']), '运行' if b['pid'] else '-') for b in json.load(sys.stdin)]"
    else echo "本地开发控制台没有在运行"; fi ;;
  *) echo "用法：$0 start|stop|status"; exit 1 ;;
esac
