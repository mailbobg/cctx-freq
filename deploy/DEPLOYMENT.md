# CCXT-Fr 云服务器部署说明

最后更新：2026-09-28。本文记录当前实际在用的部署：环境怎么搭、平时怎么操作、出问题怎么处理。
英文版的简要步骤见同目录的 `README.md`。本文不包含任何密码或密钥。

---

## 1. 总览

| 项目 | 内容 |
|---|---|
| 云服务商 | Vultr，东京机房（Cloud Compute，Shared CPU） |
| 服务器 IP | `45.77.8.169` |
| 配置 | 2 vCPU、4 GB 内存、75 GB 硬盘，另有 7.7 GB swap |
| 系统 | Ubuntu 24.04 LTS x64，时区 UTC |
| 附加项 | 未开 Vultr 自动备份（Automatic Backups） |
| 运行方式 | Docker 29.8 + Docker Compose v5 |
| 代码目录 | `/opt/ccxt`（freqtrade 代码）、`/opt/ccxt/user_data`（策略、配置、数据库、日志） |
| 到币安的延迟 | 现货与合约 API 约 0.06–0.1 秒，无地区限制（无 451） |

选东京的原因：币安撮合服务器在 AWS 东京，延迟最低；本地 Mac 所在网络连不上币安交易接口，服务器不受影响。

### 架构

```
你的 Mac                                   东京服务器 45.77.8.169
─────────                                  ──────────────────────────────────────────
浏览器 127.0.0.1:8090 ─┐                   ccxt-panel（控制台容器，root，挂 docker.sock）
浏览器 127.0.0.1:8089 ─┤  SSH 隧道          ├─ :8090 控制台页面 + 共享行情网关 /market/...
                        ├─ (自动常驻) ────▶ ├─ :8089 FreqUI（自动登录）
机器人端口 8080–8086  ─┘                   └─ 按需创建机器人容器 ccxt-bot-<名字>
                                               ├─ ccxt-bot-e0v1en        :8083
                                               ├─ ccxt-bot-unlock-short  :8085
                                               ├─ ccxt-bot-funding-spread :8086
                                               └─ …（x7 :8080 / x8 :8081 / x12 :8082 / c-hold :8084）
```

- 所有容器使用**主机网络**（`network_mode: host`），全部只监听 `127.0.0.1`，公网访问不到。
- 所有容器共用一个镜像 `ccxt-freqtrade:local`，由本仓库里的 freqtrade 代码构建，和本地回测、模拟盘的代码版本一致。
- `user_data` 以目录挂载进容器，数据库、日志、配置都直接留在服务器磁盘上，容器删了也不丢。
- 控制台只有一个 compose 服务；机器人容器由控制台在你点"启动"时创建，点"停止"时停止。
- 机器人容器的重启策略是 `unless-stopped`：程序崩溃或服务器重启后自动拉起；在控制台上停掉的保持停止。

---

## 2. 从 Mac 访问

### 日常使用

直接在浏览器打开：

- 控制台：http://127.0.0.1:8090
- FreqUI：http://127.0.0.1:8089（或点控制台卡片上的"FreqUI ↗"）

Mac 上有一个常驻的 SSH 隧道把这些端口转发到服务器，开机自动连接、断线 10 秒内自动重连。
判断连的是服务器：控制台顶部"交易接口"显示几十毫秒（Mac 本机直连时显示"不通"）。

### 隧道的设置

- 文件：`~/Library/LaunchAgents/com.ccxtfr.tunnel.plist`（不在仓库里）
- 日志：`user_data/logs/tunnel.log`
- 使用密钥：`~/.ssh/ccxt_vps`（仅用于这台服务器）

它等价于下面这条命令（需要手动临时开一条时可以用）：

```bash
ssh -i ~/.ssh/ccxt_vps -o IdentitiesOnly=yes -N -L 8090:127.0.0.1:8090 -L 8089:127.0.0.1:8089 -L 8080:127.0.0.1:8080 -L 8081:127.0.0.1:8081 -L 8082:127.0.0.1:8082 -L 8083:127.0.0.1:8083 -L 8084:127.0.0.1:8084 -L 8085:127.0.0.1:8085 -L 8086:127.0.0.1:8086 root@45.77.8.169
```

隧道的开关：

```bash
launchctl bootout gui/$(id -u)/com.ccxtfr.tunnel
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ccxtfr.tunnel.plist
```

新增机器人用到新端口时，要在这个 plist 里加一行对应的 `-L 端口:127.0.0.1:端口`，然后按上面两条命令重载。

### 登录服务器

```bash
ssh -i ~/.ssh/ccxt_vps -o IdentitiesOnly=yes root@45.77.8.169
```

---

## 3. 本地 Mac 的状态

- 本地控制台服务 `com.ccxtfr.panel` 已停止，并已关闭开机自启（`launchctl disable`），本地 `panel/state.json` 里所有机器人都标为停止。
  这样 Mac 不会再拉起机器人，**避免同一个机器人在两边同时运行**（两边各写各的数据库，记录会分叉）。
- 回测、研究仍然在 Mac 上做；服务器只负责跑机器人。
- 如果以后要把某个机器人搬回 Mac：先在服务器控制台停掉它，把它的 `tradesv3.*.sqlite` 拷回 Mac，再在 Mac 上启动。

---

## 4. 服务器上做过的初始化

```bash
# SSH：只允许密钥登录，root 只能用密钥
cat /etc/ssh/sshd_config.d/10-ccxt-hardening.conf
#   PasswordAuthentication no
#   KbdInteractiveAuthentication no
#   PermitRootLogin prohibit-password

# 防火墙：只放行 SSH（22）
ufw status

# 系统安全更新自动安装
apt-get install -y unattended-upgrades

# 时区 UTC
timedatectl set-timezone UTC

# Docker（官方脚本）
curl -fsSL https://get.docker.com -o /tmp/get-docker.sh && sh /tmp/get-docker.sh
```

本地 SSH 公钥 `~/.ssh/ccxt_vps.pub` 已加入服务器 `/root/.ssh/authorized_keys`。
服务器已关闭密码登录：Vultr 面板上显示的 root 密码现在不能用来 SSH 登录，只能用于 Vultr 网页控制台（View Console）的应急登录。

---

## 5. 部署与更新

### 首次部署（已完成，记录备查）

在 Mac 上同步代码（freqtrade 代码 + `user_data` 里 git 跟踪的部分 + 密码文件）：

```bash
cd ~/Documents/07-Hero/CCXT-Fr && rsync -a --delete -e "ssh -i ~/.ssh/ccxt_vps -o IdentitiesOnly=yes" --exclude .venv --exclude .git --exclude 'user_data/*' --exclude external --exclude '__pycache__' --exclude tests --exclude docs --exclude site ./ root@45.77.8.169:/opt/ccxt/
```

```bash
cd ~/Documents/07-Hero/CCXT-Fr/user_data && git ls-files | grep -v '^bots/local-network.json$' | rsync -a --files-from=- -e "ssh -i ~/.ssh/ccxt_vps -o IdentitiesOnly=yes" ./ root@45.77.8.169:/opt/ccxt/user_data/
```

```bash
rsync -a -e "ssh -i ~/.ssh/ccxt_vps -o IdentitiesOnly=yes" ~/Documents/07-Hero/CCXT-Fr/user_data/bots/secrets.json root@45.77.8.169:/opt/ccxt/user_data/bots/
```

在服务器上：

```bash
echo '{}' > /opt/ccxt/user_data/bots/local-network.json      # Mac 专用的行情镜像配置，服务器上必须为空
chown -R 1000:1000 /opt/ccxt/user_data                        # 机器人容器以 uid 1000（ftuser）运行
cd /opt/ccxt/user_data/deploy && export CCXT_USER_DATA=/opt/ccxt/user_data && docker compose up -d --build
```

迁移模拟盘记录时，先在原机器上停掉机器人，把数据库的 WAL 合并（`sqlite3 文件 "PRAGMA wal_checkpoint(TRUNCATE);"`），
再拷贝 `tradesv3.*.sqlite` 和 `panel/state.json`（记录哪些机器人应当运行）。2026-09-28 的迁移用校验值确认了两边一致。

### 更新代码（策略、控制台、配置）——用 deploy.sh

改好、测好、提交 git 之后，在 Mac 上运行：

```bash
~/Documents/07-Hero/CCXT-Fr/user_data/deploy/deploy.sh --dry-run
```

先看预演结果（会改哪些文件、要不要重建镜像、哪些运行中的机器人受影响），没问题再去掉 `--dry-run` 正式部署：

```bash
~/Documents/07-Hero/CCXT-Fr/user_data/deploy/deploy.sh
```

它会：拒绝部署未提交的改动；只同步内容有变化的文件（按校验值比较），跳过 Mac 专用配置；git 里删掉的文件在服务器上也删掉；
改了 freqtrade 代码或 Dockerfile 时重建镜像，改了控制台时重启控制台；新增机器人时把端口加进 Mac 的隧道；
最后列出**需要在控制台上重启**的运行中机器人（按策略文件、它引用的辅助模块、配置文件判断）。机器人不会被自动重启。

下面是 deploy.sh 做的手动等价步骤，备查：

1. 在 Mac 上改好、测好、提交 git。
2. 用上面第 2 条 rsync 命令同步 `user_data`（它会跳过 `local-network.json`，不会把 Mac 专用配置覆盖到服务器）。
3. 只改了策略或配置：在控制台上**重启**对应机器人即可。
4. 改了控制台代码（`panel/`）：

```bash
ssh -i ~/.ssh/ccxt_vps -o IdentitiesOnly=yes root@45.77.8.169 'cd /opt/ccxt/user_data/deploy && CCXT_USER_DATA=/opt/ccxt/user_data docker compose restart panel'
```

5. 改了 freqtrade 本身或依赖：先用第 1 条 rsync 同步代码，再重新构建：

```bash
ssh -i ~/.ssh/ccxt_vps -o IdentitiesOnly=yes root@45.77.8.169 'cd /opt/ccxt/user_data/deploy && CCXT_USER_DATA=/opt/ccxt/user_data docker compose up -d --build'
```

重建镜像后，每个机器人在下次从控制台启动或重启时，会被自动用新镜像重建（控制台比较镜像 id）。

### 新增一个机器人

1. 在 Mac 的 `user_data/bots/` 里加配置文件（参考已有的，端口不要重复），`bots/panel.json` 里加说明。
2. 同步到服务器，控制台会自动显示新卡片。
3. 在 Mac 隧道的 plist 里加上新端口（见第 2 节），FreqUI 才能直接访问它。

---

## 6. 日常运维

### 常用命令（在服务器上）

```bash
docker ps                                            # 看有哪些容器在跑
docker logs --tail 50 ccxt-panel                     # 控制台日志
tail -f /opt/ccxt/user_data/logs/e0v1en.log          # 某个机器人的日志（也可以在控制台卡片上点"明细"）
docker stats --no-stream                             # 各容器 CPU / 内存
free -m && df -h /                                   # 服务器内存、磁盘
```

### 启停

- 平时一律在控制台页面上操作：开关、重启、清仓并停止。
- 控制台本身：`docker compose restart panel`（在 `/opt/ccxt/user_data/deploy` 下，需要 `CCXT_USER_DATA=/opt/ccxt/user_data`）。
- 服务器重启后：Docker 开机自启，控制台和原来在跑的机器人会自动恢复。

### 资源

2026-09-28 实测：控制台 + 3 个机器人约用 1.4 GB 内存。NFI X7/X8 每个约 1 GB，7 个全开约 3.5 GB，接近 4 GB，依赖 swap 兜底。
长期全开的话，考虑升级到 8 GB。

---

## 7. 备份（待做）

Vultr 自动备份没有开。代码和配置都在 Mac 的 git 仓库里，服务器上唯一不可再生的是模拟盘数据库 `user_data/tradesv3.*.sqlite`（几 MB）。
计划：加一个每日任务，把这些数据库拉回 Mac 保存。未完成前，重要节点可手动拉取：

```bash
mkdir -p ~/Documents/07-Hero/CCXT-Fr/user_data/server-backup && rsync -a -e "ssh -i ~/.ssh/ccxt_vps -o IdentitiesOnly=yes" 'root@45.77.8.169:/opt/ccxt/user_data/tradesv3.*.sqlite' ~/Documents/07-Hero/CCXT-Fr/user_data/server-backup/
```

---

## 8. 常见问题

| 现象 | 原因与处理 |
|---|---|
| 浏览器打不开 127.0.0.1:8090 | 隧道没连上。看 `user_data/logs/tunnel.log`；Mac 刚开机或断网时等十几秒再刷新 |
| SSH 提示 `REMOTE HOST IDENTIFICATION HAS CHANGED` | 服务器重装后主机密钥变了。确认是自己刚重装的，再执行 `ssh-keygen -R 45.77.8.169` |
| SSH 提示 `Permission denied (publickey)` | 服务器上没有这把公钥（例如重装时没选 SSH Key）。用 Vultr 网页控制台登录后把 `~/.ssh/ccxt_vps.pub` 的内容加进 `/root/.ssh/authorized_keys` |
| 控制台容器起不来，日志里 `No module named ...` | 镜像依赖装在 ftuser 名下，控制台以 root 运行，compose 里的 `PYTHONUSERBASE=/home/ftuser/.local` 不能删 |
| 机器人容器启动后立刻退出 | 看 `user_data/logs/<名字>.log` 和 `docker logs ccxt-bot-<名字>`；常见是配置写错或端口冲突 |
| 机器人写不了数据库/日志 | 目录属主不对，执行 `chown -R 1000:1000 /opt/ccxt/user_data` |
| 某个币安接口返回 451 | 该地区 IP 被币安限制。东京目前正常；如出现需换机房 |

---

## 9. 相关文件

| 文件 | 作用 |
|---|---|
| `user_data/deploy/Dockerfile` | 构建镜像（Python 3.13，本仓库的 freqtrade 代码，本地 FreqUI） |
| `user_data/deploy/Dockerfile.dockerignore` | 构建时排除 `user_data`、`.venv`、`.git` 等 |
| `user_data/deploy/docker-compose.yml` | 控制台服务定义 |
| `user_data/deploy/README.md` | 英文简要部署步骤 |
| `user_data/panel/docker_runner.py` | 控制台的 Docker 模式（通过 Docker 接口创建/启停机器人容器） |
| `user_data/panel/test_docker_runner.py` | Docker 模式的测试（不需要真实 Docker） |
| `~/Library/LaunchAgents/com.ccxtfr.tunnel.plist` | Mac 上的常驻 SSH 隧道（不在仓库里） |
| `~/.ssh/ccxt_vps` | 登录服务器用的私钥（不在仓库里，不要外传） |
