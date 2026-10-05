# 隧道（Mac→Vultr SSH 转发）血泪规矩

事故（2026-10-05）：改 plist 加整段转发时把 `root@45.77.8.169` 留在了一堆 `-L`
中间，ssh 把后面 61 条转发当远端命令吞了。launchd 显示 running，实际新端口全断，
面板+FreqUI 全 offline。修完又误报面板容器没了（`docker ps | head` 截断）。

## 铁律

1. `root@IP` 永远是 `ProgramArguments` 最后一个参数，所有 `-L` 必须在它前面。
2. 改完先跑 `user_data/deploy/check-tunnel.sh`，全绿才算修好，不许口头说修好了。
3. 排障顺序：服务器直连（`ssh` 上去 curl 127.0.0.1）先行——直连通=隧道问题，
   直连不通=服务器问题。不要倒过来猜。
4. 看 running 的 ssh 命令行（`ps aux | grep ccxt_vps`），别只看 launchd 状态。
5. `docker ps` 永远别 `head`，要 `grep` 找容器。
6. 新机器人端口：8100–8160 整段已转发，不用逐个加；真超了 8160 再扩段。

## 快速恢复

```bash
launchctl bootout gui/$(id -u)/com.ccxtfr.tunnel
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ccxtfr.tunnel.plist
user_data/deploy/check-tunnel.sh
```
