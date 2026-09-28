"""Deploy the Mac's committed code to the server (run through deploy.sh).

What it does, in order:
  1. refuses to deploy uncommitted work in user_data (use --allow-dirty to override)
  2. works out what changed on the server side with rsync dry runs (checksums, not timestamps):
       - freqtrade code (repo root, without .venv/.git/user_data/external/tests/docs)
       - user_data: the git-tracked files, minus the Mac-only bots/local-network.json
     plus files deleted from git since the last deploy (tracked in /opt/ccxt/.deployed_files)
  3. syncs them, fixes ownership for the bot containers (uid 1000)
  4. rebuilds the image when freqtrade code / Dockerfile / requirements changed,
     otherwise restarts the panel container when panel code or the compose file changed
  5. lists running bots whose strategy (incl. imported helper modules), config files or runtime changed —
     they are NOT restarted automatically; restart them from the panel
  6. adds ports of new bots to the Mac's SSH tunnel (LaunchAgent com.ccxtfr.tunnel) and reloads it

Usage: user_data/deploy/deploy.sh [--dry-run] [--allow-dirty]
"""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

SERVER = os.environ.get("CCXT_SERVER", "root@45.77.8.169")
KEY = Path(os.environ.get("CCXT_KEY", "~/.ssh/ccxt_vps")).expanduser()
REPO = Path(__file__).resolve().parents[2]
UD = REPO / "user_data"
REMOTE = "/opt/ccxt"
REMOTE_UD = f"{REMOTE}/user_data"
COMPOSE = f"cd {REMOTE_UD}/deploy && CCXT_USER_DATA={REMOTE_UD} docker compose"
TUNNEL_PLIST = Path("~/Library/LaunchAgents/com.ccxtfr.tunnel.plist").expanduser()
TUNNEL_LABEL = "com.ccxtfr.tunnel"
MAC_ONLY = {"bots/local-network.json"}
CODE_EXCLUDES = [".venv", ".git", "user_data/", "external", "__pycache__", ".pytest_cache", "tests", "docs", "site",
                 "*.pyc", ".DS_Store",
                 "/build.log", "/.deployed_files"]            # server-only files: never compared or deleted
SSH = ["ssh", "-i", str(KEY), "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
RSYNC_E = " ".join(shlex.quote(a) for a in SSH)
REBUILD_TRIGGERS = ("deploy/Dockerfile", "deploy/Dockerfile.dockerignore")
PANEL_TRIGGERS = ("panel/", "deploy/docker-compose.yml")
SHARED_CONFIGS = {"bots/shared-market-data.json"}          # passed by the panel to Binance dry-run bots


def say(msg: str = "") -> None:
    print(msg, flush=True)


def run(cmd: list[str], check: bool = True, capture: bool = True, input_: str | None = None) -> str:
    r = subprocess.run(cmd, capture_output=capture, text=True, input=input_)
    if check and r.returncode != 0:
        raise SystemExit(f"command failed ({r.returncode}): {' '.join(cmd[:6])} …\n{r.stderr or r.stdout}")
    return r.stdout if capture else ""


def remote(cmd: str, check: bool = True) -> str:
    return run(SSH + [SERVER, cmd], check=check)


# ---------------------------------------------------------------- what changed

def tracked_files() -> list[str]:
    out = run(["git", "-C", str(UD), "ls-files"]).splitlines()
    return [f for f in out if f not in MAC_ONLY]


def rsync_changes(src: str, dst: str, extra: list[str]) -> list[str]:
    """Files whose content differs on the server (dry run, checksums)."""
    out = run(["rsync", "-rlptcni", "-e", RSYNC_E, *extra, src, dst], check=True)
    changed = []
    for line in out.splitlines():
        code, _, path = line.partition(" ")
        if code[:1] in "<>" and code[1:2] == "f":
            changed.append(path.strip())
        elif code.startswith("*deleting"):
            changed.append(path.strip())
    return changed


def code_changes() -> list[str]:
    ex = [a for e in CODE_EXCLUDES for a in ("--exclude", e)]
    return rsync_changes(f"{REPO}/", f"{SERVER}:{REMOTE}/", ex + ["--delete"])


def user_data_changes(files: list[str]) -> list[str]:
    listing = "\n".join(files + ["bots/secrets.json"]) + "\n"
    tmp = UD / "logs" / ".deploy-files.txt"
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_text(listing)
    return rsync_changes(f"{UD}/", f"{SERVER}:{REMOTE_UD}/", [f"--files-from={tmp}"])


def deleted_since_last(files: list[str]) -> list[str]:
    before = remote(f"cat {REMOTE}/.deployed_files 2>/dev/null || true").splitlines()
    return sorted(set(before) - set(files) - MAC_ONLY) if before else []


# ---------------------------------------------------------------- which bots are affected

def strategy_modules(strategy: str) -> set[str]:
    """strategies/<file>.py defining the class, plus local modules it imports (recursively)."""
    sdir = UD / "strategies"
    start = None
    for f in sdir.glob("*.py"):
        if re.search(rf"^class\s+{re.escape(strategy)}\b", f.read_text(errors="ignore"), re.M):
            start = f
            break
    if not start:
        return set()
    seen, todo = set(), [start]
    while todo:
        f = todo.pop()
        rel = f"strategies/{f.name}"
        if rel in seen:
            continue
        seen.add(rel)
        for m in re.findall(r"^\s*(?:from|import)\s+([A-Za-z_]\w*)", f.read_text(errors="ignore"), re.M):
            dep = sdir / f"{m}.py"
            if dep.exists():
                todo.append(dep)
    return seen


def bot_files(name: str) -> set[str]:
    cfg_path = UD / "bots" / f"{name}.json"
    cfg = json.loads(cfg_path.read_text())
    files = {f"bots/{name}.json"} | {f"bots/{f}" for f in cfg.get("add_config_files", [])}
    for f in list(files):                                   # included files may include more
        p = UD / f
        if p.exists():
            files |= {f"bots/{x}" for x in json.loads(p.read_text()).get("add_config_files", [])}
    if cfg.get("runtime") == "cross_venue_paper":
        files |= {f"cross_venue/{p.name}" for p in (UD / "cross_venue").glob("*.py") if not p.name.startswith("test_")}
    else:
        files |= strategy_modules(cfg["strategy"]) | SHARED_CONFIGS
    return files


def running_bots() -> list[str]:
    raw = remote("curl -s -m 20 http://127.0.0.1:8090/api/bots", check=False)
    try:
        return [b["name"] for b in json.loads(raw) if b.get("pid")]
    except ValueError:
        return []


# ---------------------------------------------------------------- tunnel ports

def bot_ports() -> dict[int, str]:
    ports = {8090: "panel", 8089: "FreqUI"}
    for f in (UD / "bots").glob("*.json"):
        try:
            cfg = json.loads(f.read_text())
        except ValueError:
            continue
        port = cfg.get("api_server", {}).get("listen_port") if "strategy" in cfg else None
        if port:
            ports[int(port)] = f.stem
    return ports


def tunnel_missing_ports() -> list[int]:
    if not TUNNEL_PLIST.exists():
        return []
    args = json.loads(run(["plutil", "-extract", "ProgramArguments", "json", "-o", "-", str(TUNNEL_PLIST)]))
    have = {int(args[i + 1].split(":")[0]) for i, a in enumerate(args) if a == "-L"}
    return sorted(set(bot_ports()) - have)


def add_tunnel_ports(ports: list[int]) -> None:
    args = json.loads(run(["plutil", "-extract", "ProgramArguments", "json", "-o", "-", str(TUNNEL_PLIST)]))
    host = args.pop()                                       # last argument is user@host
    for p in ports:
        args += ["-L", f"{p}:127.0.0.1:{p}"]
    args.append(host)
    run(["plutil", "-replace", "ProgramArguments", "-json", json.dumps(args), str(TUNNEL_PLIST)])
    uid = str(os.getuid())
    run(["launchctl", "bootout", f"gui/{uid}/{TUNNEL_LABEL}"], check=False)
    time.sleep(1)
    run(["launchctl", "bootstrap", f"gui/{uid}", str(TUNNEL_PLIST)])


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description="Deploy committed code to the CCXT-Fr server")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    ap.add_argument("--allow-dirty", action="store_true", help="deploy even with uncommitted changes in user_data")
    args = ap.parse_args()
    dry = args.dry_run

    say(f"== 部署到 {SERVER}{'（预演，不做任何修改）' if dry else ''}")
    dirty = run(["git", "-C", str(UD), "status", "--porcelain"]).strip()
    if dirty and not args.allow_dirty:
        say("user_data 里有未提交的改动，先提交再部署（或加 --allow-dirty）：")
        say(dirty)
        raise SystemExit(1)
    head = run(["git", "-C", str(UD), "rev-parse", "--short", "HEAD"]).strip()
    say(f"user_data 提交：{head}{'（含未提交改动）' if dirty else ''}")

    files = tracked_files()
    code = code_changes()
    ud = user_data_changes(files)
    gone = deleted_since_last(files)

    rebuild = bool(code) or any(f.startswith(REBUILD_TRIGGERS) for f in ud)
    panel_restart = any(f.startswith(PANEL_TRIGGERS) and "/test_" not in f for f in ud)
    changed = set(ud) | set(gone)

    say(f"\nfreqtrade 代码有变化的文件：{len(code)}" + ("".join(f"\n  {f}" for f in code[:15])) +
        (f"\n  …另 {len(code) - 15} 个" if len(code) > 15 else ""))
    say(f"user_data 有变化的文件：{len(ud)}" + "".join(f"\n  {f}" for f in ud))
    if gone:
        say(f"git 里已删除、服务器上要删除的文件：{len(gone)}" + "".join(f"\n  {f}" for f in gone))

    running = running_bots()
    affected = {}
    for name in running:
        if not (UD / "bots" / f"{name}.json").exists():
            continue
        hit = sorted(bot_files(name) & changed)
        if rebuild:
            hit = ["（镜像重建）"] + hit
        if hit:
            affected[name] = hit
    missing_ports = tunnel_missing_ports()

    if not (code or ud or gone or missing_ports):
        say("\n服务器已是最新，没有需要部署的内容。")
        return
    if dry:
        say("\n预演结果：")
        say(f"  重建镜像：{'是' if rebuild else '否'}；重启控制台：{'是' if (panel_restart or rebuild) else '否'}")
        say(f"  需要在控制台上重启的运行中机器人：{', '.join(affected) or '无'}")
        if missing_ports:
            say(f"  隧道要新增端口：{missing_ports}")
        return

    # 1) sync
    if code:
        ex = [a for e in CODE_EXCLUDES for a in ("--exclude", e)]
        run(["rsync", "-rlptc", "--delete", "-e", RSYNC_E, *ex, f"{REPO}/", f"{SERVER}:{REMOTE}/"], capture=False)
        say("✓ freqtrade 代码已同步")
    if ud:
        run(["rsync", "-rlptc", "-e", RSYNC_E, f"--files-from={UD / 'logs' / '.deploy-files.txt'}",
             f"{UD}/", f"{SERVER}:{REMOTE_UD}/"], capture=False)
        say("✓ user_data 已同步")
    if gone:
        remote("cd " + REMOTE_UD + " && rm -f -- " + " ".join(shlex.quote(f) for f in gone))
        say("✓ 已删除服务器上多余的文件")
    remote(f"chown -R 1000:1000 {REMOTE_UD} && printf '%s\\n' " +
           " ".join(shlex.quote(f) for f in files) + f" > {REMOTE}/.deployed_files")

    # 2) image / panel
    if rebuild:
        say("… 重建镜像（几分钟）")
        remote(f"{COMPOSE} up -d --build 2>&1 | tail -3")
        say("✓ 镜像已重建，控制台已用新镜像重启")
    elif panel_restart:
        compose_changed = "deploy/docker-compose.yml" in ud
        remote(f"{COMPOSE} {'up -d' if compose_changed else 'restart panel'} 2>&1 | tail -2")
        say("✓ 控制台已重启")
    if rebuild or panel_restart:
        ok = remote("for i in $(seq 1 30); do curl -s -m 2 -o /dev/null -w '%{http_code}' http://127.0.0.1:8090/ "
                    "| grep -q 200 && echo up && break; sleep 2; done", check=False).strip()
        say("✓ 控制台已恢复" if ok == "up" else "⚠ 控制台 60 秒内没有恢复，查看：docker logs --tail 50 ccxt-panel")

    # 3) tunnel
    if missing_ports:
        add_tunnel_ports(missing_ports)
        say(f"✓ Mac 隧道已加端口 {missing_ports} 并重新连接")

    # 4) what the user still has to do
    say("\n== 完成")
    if affected:
        say("下面这些**正在运行**的机器人用到了改动的文件，需要在控制台上点「重启」才会生效：")
        for name, hit in affected.items():
            say(f"  {name}: {', '.join(hit)}")
    else:
        say("没有正在运行的机器人受影响，不需要重启机器人。")
    new_bots = [n for n in (f.split('/')[1][:-5] for f in ud if f.startswith("bots/") and f.endswith(".json"))
                if (UD / "bots" / f"{n}.json").exists() and "strategy" in json.loads((UD / "bots" / f"{n}.json").read_text())
                and n not in running]
    if new_bots:
        say(f"新增或修改了配置、目前未运行的机器人：{', '.join(new_bots)}（在控制台上点开关启动）")


if __name__ == "__main__":
    main()
