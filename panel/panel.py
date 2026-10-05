"""CCXT-Fr control panel: start / stop / watch the freqtrade bots from one web page.

Bots are the per-bot configs in user_data/bots/ (any *.json with a "strategy" key). The panel launches
each bot as a detached process (it keeps running if the panel restarts), remembers which bots should be
running, and restarts a bot that dies unexpectedly. It listens on 127.0.0.1 only.

Run:  .venv/bin/python user_data/panel/panel.py      (on macOS a LaunchAgent starts it at login)
"""

import json
from datetime import datetime, timezone
import os
import resource
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from panel.market_data import MarketDataGateway, install_routes
from panel.waiting import listings_note, weekly_note
from panel.resources import ResourceMonitor, ProcessResourceMonitor

import psutil
import requests
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[2]          # repo root (CCXT-Fr)
BOTS = ROOT / "user_data" / "bots"
LOGS = ROOT / "user_data" / "logs"
STATE = Path(__file__).parent / "state.json"
FREQTRADE = ROOT / ".venv" / "bin" / "freqtrade"
HOST, PORT = os.environ.get("PANEL_HOST", "127.0.0.1"), int(os.environ.get("PANEL_PORT", "8090"))
SUPERVISE_EVERY = 20      # seconds
STOP_GRACE = 40           # seconds before SIGKILL

lock = threading.RLock()
restarts: dict[str, list[float]] = {}
market_data = MarketDataGateway()
process_resources = ProcessResourceMonitor()

# PANEL_RUNNER=docker: bots run as containers (see panel/docker_runner.py); default: local processes
RUNNER = os.environ.get("PANEL_RUNNER", "process")
if RUNNER == "docker":
    from panel.docker_runner import DockerBackend, container_command
    docker = DockerBackend(local_user_data=str(ROOT / "user_data"))

# Local development mode (deploy/dev-panel.sh): every bot's API port is shifted by PANEL_BOT_PORT_OFFSET, and each bot
# gets its own database (user_data/dev/), log (logs/dev/) and panel state, so a local panel can run next to the SSH
# tunnel to the server without touching the server's ports or the dry-run records.
PORT_OFFSET = int(os.environ.get("PANEL_BOT_PORT_OFFSET", "0"))
ENV_LABEL = os.environ.get("PANEL_ENV_LABEL", "")
if os.environ.get("PANEL_STATE_FILE"):
    STATE = Path(os.environ["PANEL_STATE_FILE"])
BOT_LOGS = LOGS / "dev" if PORT_OFFSET else LOGS
DEV_DB_DIR = ROOT / "user_data" / "dev"


def raise_fd_limit(target: int = 2048) -> None:
    """Give the panel and its child bots room for concurrent exchange connections."""
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    desired = target if hard == resource.RLIM_INFINITY else min(target, hard)
    if desired > soft:
        resource.setrlimit(resource.RLIMIT_NOFILE, (desired, hard))


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2))


def _common() -> dict:
    try:
        return json.loads((BOTS / "common.json").read_text())
    except Exception:
        return {}


def strategy_created(cfg: dict) -> str:
    """Strategy file birth date (YYYY-MM-DD) for the bot card, '' if unknown."""
    try:
        base = ROOT / cfg.get("strategy_path", "user_data/strategies")
        f = base / f"{cfg['strategy']}.py"
        import datetime
        return datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")
    except Exception:
        return ""


def bots() -> dict[str, dict]:
    meta = {}
    try:
        meta = json.loads((BOTS / "panel.json").read_text())
    except Exception:
        pass
    out = {}
    for f in sorted(BOTS.glob("*.json")):
        try:
            cfg = json.loads(f.read_text())
        except Exception:
            continue
        if "strategy" not in cfg:
            continue
        name = f.stem
        out[name] = {
            "name": name,
            "config": f,
            "strategy": cfg["strategy"],
            "bot_name": cfg.get("bot_name", name),
            "port": (cfg.get("api_server", {}).get("listen_port") or 0) + PORT_OFFSET,
            "db_url": f"sqlite:///user_data/dev/tradesv3.{name}.sqlite" if PORT_OFFSET else cfg.get("db_url"),
            "max_trades": cfg.get("max_open_trades", _common().get("max_open_trades")),
            "description": meta.get(name, {}).get("description", ""),
            "strategy_created": strategy_created(cfg),
            "wait": meta.get(name, {}).get("wait"),
            "runtime": cfg.get("runtime", "freqtrade"),
            "exchange": cfg.get("exchange",{}).get("name",_common().get("exchange",{}).get("name")),
            "dry_run": cfg.get("dry_run",_common().get("dry_run",False)),
        }
    order = list(meta)
    return dict(sorted(out.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99))


def credentials() -> tuple[str, str]:
    s = json.loads((BOTS / "secrets.json").read_text())["api_server"]
    return s["username"], s["password"]


def shared_market_config() -> str:
    if PORT==8090:
        return 'user_data/bots/shared-market-data.json'
    # Runtime-only, credential-free overlay for the existing custom PANEL_PORT option.
    from tempfile import NamedTemporaryFile
    cfg=json.loads((Path(__file__).parents[1]/'bots/shared-market-data.json').read_text())
    for mode in ['ccxt_config','ccxt_async_config']:
        urls=cfg['exchange'][mode]['urls']['api']
        for name,url in urls.items():urls[name]=url.replace(':8090/',f':{PORT}/')
    path=ROOT/'user_data/logs'/f'shared-market-data-{PORT}.json'
    with lock:
        path.parent.mkdir(parents=True,exist_ok=True)
        with NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as tmp:
            json.dump(cfg,tmp);temp_path=tmp.name
        os.replace(temp_path,path)
    return str(path.relative_to(ROOT))


def dev_config(bot: dict) -> str:
    """Development-mode config for a bot (written under logs/dev/, no credentials inside), returned repo-relative.

    freqtrade bots: an overlay passed as an extra -c (port, database, CORS for the dev FreqUI).
    cross_venue_paper bots take a single --config: a copy of the bot's file with the overrides and absolute
    add_config_files paths.
    """
    BOT_LOGS.mkdir(parents=True, exist_ok=True)
    DEV_DB_DIR.mkdir(parents=True, exist_ok=True)
    frequi = int(os.environ.get("FREQUI_PORT", "8089"))
    cors = [f"http://127.0.0.1:{frequi}", f"http://localhost:{frequi}", f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"]
    if bot.get("runtime") == "cross_venue_paper":
        cfg = json.loads(bot["config"].read_text())
        cfg["add_config_files"] = [str((bot["config"].parent / f).resolve()) for f in cfg.get("add_config_files", [])]
        cfg["db_url"] = bot["db_url"]
        cfg.setdefault("api_server", {})["listen_port"] = bot["port"]
    else:
        cfg = {"db_url": bot["db_url"], "api_server": {"listen_port": bot["port"], "CORS_origins": cors}}
    path = _dev_config_path(bot)
    path.write_text(json.dumps(cfg, indent=2))
    return str(path.relative_to(ROOT))


def _dev_config_path(bot: dict) -> Path:
    # cross_venue/service.py derives the repo root as the config's parents[2], so its file must sit at
    # user_data/<dir>/<file>; freqtrade overlays have no such constraint
    if bot.get("runtime") == "cross_venue_paper":
        return DEV_DB_DIR / f"{bot['name']}.config.json"
    return BOT_LOGS / f"{bot['name']}.config.json"


def command_for_bot(bot: dict) -> list[str]:
    runtime = bot.get("runtime", "freqtrade")
    if runtime not in ("freqtrade", "cross_venue_paper"):
        raise ValueError(f"Unknown bot runtime: {runtime}")
    cfg = str(bot["config"].relative_to(ROOT))
    if runtime == "cross_venue_paper":
        return [sys.executable, str(ROOT / "user_data/cross_venue/service.py"), "--config",
                dev_config(bot) if PORT_OFFSET else cfg]
    shared=["-c",shared_market_config()] if bot.get('exchange','binance')=='binance' and bot.get('dry_run',True) else []
    dev = ["-c", dev_config(bot)] if PORT_OFFSET else []
    logfile = f"user_data/logs/{'dev/' if PORT_OFFSET else ''}{bot['name']}.log"
    return [str(FREQTRADE), "trade", "-c", cfg, *shared, *dev, "--logfile", logfile]


def find_process(bot: dict) -> psutil.Process | None:
    """The running freqtrade process for this bot, started by the panel or by hand with the same config."""
    if RUNNER == "docker":
        return docker.find(bot)
    cfg = str(bot["config"].relative_to(ROOT))
    for p in psutil.process_iter(["cmdline"]):
        cmd = p.info.get("cmdline") or []
        native = "trade" in cmd and any(c.endswith("freqtrade") for c in cmd)
        paper = any(c.endswith("cross_venue/service.py") for c in cmd)
        matched = paper if bot.get("runtime") == "cross_venue_paper" else native
        dev_cfg = str(_dev_config_path(bot).relative_to(ROOT))
        if matched and (cfg in cmd or str(bot["config"]) in cmd or (PORT_OFFSET and dev_cfg in cmd)):
            return p
    return None


def port_owner(port: int) -> psutil.Process | None:
    try:
        for c in psutil.net_connections("tcp"):
            if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN and c.pid:
                return psutil.Process(c.pid)
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        # macOS only lets root list all sockets; fall back to lsof
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True)
        pids = out.stdout.split()
        if pids:
            return psutil.Process(int(pids[0]))
    return None


def start_bot(name: str) -> None:
    bot = bots()[name]
    with lock:
        if find_process(bot):
            return
        if RUNNER == "docker":
            docker.start(bot, container_command(command_for_bot(bot), str(ROOT), sys.executable, str(FREQTRADE)))
            state = load_state()
            state[name] = {"want": "running", "since": time.time()}
            save_state(state)
            return
        owner = port_owner(bot["port"])
        if owner:
            raise HTTPException(409, f"端口 {bot['port']} 已被进程 {owner.pid}（{' '.join(owner.cmdline()[1:4])}）占用")
        LOGS.mkdir(exist_ok=True)
        BOT_LOGS.mkdir(parents=True, exist_ok=True)
        with open(BOT_LOGS / f"{name}.console.log", "ab") as console:
            subprocess.Popen(
                command_for_bot(bot),
                cwd=ROOT, stdout=console, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
            )
        state = load_state()
        state[name] = {"want": "running", "since": time.time()}
        save_state(state)


def stop_bot(name: str) -> None:
    bot = bots()[name]
    with lock:
        state = load_state()
        state[name] = {"want": "stopped", "since": time.time()}
        save_state(state)
        p = None if RUNNER == "docker" else find_process(bot)
    if RUNNER == "docker":
        docker.stop(bot, STOP_GRACE)          # outside the lock: may take up to STOP_GRACE seconds
        return
    with lock:
        if not p:
            return
        p.send_signal(signal.SIGTERM)
    try:
        p.wait(STOP_GRACE)
    except psutil.TimeoutExpired:
        p.kill()


def bot_api(bot: dict, path: str):
    r = requests.get(f"http://127.0.0.1:{bot['port']}/api/v1/{path}", auth=credentials(), timeout=4)
    r.raise_for_status()
    return r.json()


def wait_api(bot: dict, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            requests.get(f"http://127.0.0.1:{bot['port']}/api/v1/ping", timeout=2).raise_for_status()
            return True
        except Exception:
            if not find_process(bot):
                return False
            time.sleep(2)
    return False


_tickers = {"t": 0.0, "px": {}}


def _prices(pairs: set[str]) -> dict[str, float]:
    if time.time() - _tickers["t"] > 60 or not pairs <= set(_tickers["px"]):
        try:
            syms = json.dumps(sorted(p.replace("/", "") for p in pairs), separators=(",", ":"))
            r = market_data.get('spot','ticker/price',[('symbols',syms)])
            if r.status!=200:raise ValueError('public price unavailable')
            _tickers.update(t=time.time(), px={f"{x['symbol'][:-4]}/USDT": float(x["price"]) for x in json.loads(r.body)})
        except Exception:
            pass
    return _tickers["px"]


_listings_cache: dict = {"t": 0.0, "rows": []}


def _listings() -> list[tuple[float, str, str]]:
    """(onboard epoch, base asset, underlyingType) of every Binance USDT-M perpetual (incl. pre-market), cached 10 min."""
    if time.time() - _listings_cache["t"] > 600:
        try:
            r = market_data.get("usd-m", "exchangeInfo", [])
            if r.status == 200:
                rows = [(int(x["onboardDate"]) / 1000, x["baseAsset"], x["underlyingType"])
                        for x in json.loads(r.body)["symbols"]
                        if x.get("underlyingType") in ("COIN", "PREMARKET")
                        and x.get("contractType") in ("PERPETUAL", "TRADIFI_PERPETUAL") and x.get("onboardDate")]
                _listings_cache.update(t=time.time(), rows=rows)
        except Exception:  # noqa: BLE001
            pass
    return _listings_cache["rows"]


def waiting_note(bot: dict) -> str:
    """Event-driven bots are silent most of the time: say when their next chance is (panel.json "wait")."""
    w = bot.get("wait") or {}
    now = datetime.now(timezone.utc)
    if w.get("kind") == "weekly":
        return weekly_note(now, w["weekday"], w["hour"], w.get("text", ""))
    if w.get("kind") == "listings":
        return listings_note(now, _listings(), set(w.get("types", ["COIN"])))
    return ""


def offline_stats(bot: dict) -> dict:
    """Numbers for a bot that is not running, read from its trade database."""
    import sqlite3
    url = bot.get("db_url") or ""
    if not url.startswith("sqlite:///"):
        return {}
    path = ROOT / url.removeprefix("sqlite:///")
    if not path.exists():
        return {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    if bot.get("runtime") == "cross_venue_paper":
        try:
            row = con.execute("SELECT payload FROM state WHERE key='summary'").fetchone()
        finally:
            con.close()
        if not row:
            return {}
        summary = json.loads(row[0])
        return {"offline": True, "open_trades": summary["open_trades"], "max_trades": 1,
                "closed_trades": summary["closed_trade_count"], "profit_closed": summary["profit_closed_coin"],
                "profit_all": summary["profit_all_coin"], "winrate": summary["winrate"], "stake": "USDT",
                "positions": [{"pair": p["pair"], "profit_pct": p["profit_ratio"]*100,
                               "profit_abs": p["profit_abs"], "open_date": p["open_date"],
                               "stake": p["stake_amount"], "tag": p["enter_tag"]} for p in summary["positions"]],
                "note": "模拟服务已停止；权益与浮盈为最后一次采集估值", "daily": []}
    try:
        closed = con.execute("select close_profit_abs, close_date from trades where is_open = 0").fetchall()
        opened = con.execute("select pair, open_date, open_rate, amount, stake_amount from trades where is_open = 1").fetchall()
    finally:
        con.close()
    px = _prices({p for p, *_ in opened}) if opened else {}
    positions, unreal = [], 0.0
    for pair, od, rate, amount, stake in opened:
        cur = px.get(pair)
        pct = (cur / rate - 1) * 100 if cur else None
        pnl = (cur - rate) * amount if cur else 0.0
        unreal += pnl
        positions.append({"pair": pair, "profit_pct": round(pct, 2) if pct is not None else None,
                          "profit_abs": pnl, "open_date": str(od)[:19], "stake": stake, "tag": ""})
    wins = sum(1 for a, _ in closed if (a or 0) > 0)
    closed_sum = sum(a or 0 for a, _ in closed)
    today = time.strftime("%Y-%m-%d", time.gmtime())
    days = [time.strftime("%Y-%m-%d", time.gmtime(time.time() - i * 86400)) for i in range(13, -1, -1)]
    per_day = {d: [0.0, 0] for d in days}
    for a, cd in closed:
        d = str(cd)[:10]
        if d in per_day:
            per_day[d][0] += a or 0
            per_day[d][1] += 1
    return {
        "offline": True, "open_trades": len(opened), "max_trades": bot.get("max_trades"),
        "closed_trades": len(closed), "profit_closed": closed_sum, "profit_all": closed_sum + unreal,
        "winrate": wins / len(closed) if closed else 0, "stake": "USDT", "positions": positions,
        "daily": [{"date": d, "abs": v[0], "trades": v[1]} for d, v in per_day.items()],
        "prices_as_of": today,
    }


def status(bot: dict) -> dict:
    st = load_state().get(bot["name"], {})
    p = find_process(bot)
    out = {k: bot[k] for k in ("name", "strategy", "bot_name", "port", "description", "strategy_created")}
    out["runtime"] = bot.get("runtime", "freqtrade")
    try:
        if bot.get("wait"):
            out["note"] = waiting_note(bot)
    except Exception as e:  # noqa: BLE001
        print("waiting note", bot["name"], e, file=sys.stderr, flush=True)
    if out["runtime"] == "cross_venue_paper":
        out["detail_url"] = f"http://127.0.0.1:{bot['port']}/"
        out["portfolio_url"] = f"http://127.0.0.1:{bot['port']}/portfolio"
    liq = liquidations.get(bot["name"])
    if liq and (not liq.get("done") or time.time() - liq["started"] < 600):
        out["liq"] = liq
    out.update(want=st.get("want", "stopped"), pid=p.pid if p else None, restarts_24h=len(
        [t for t in restarts.get(bot["name"], []) if t > time.time() - 86400]))
    if p:
        out["uptime_s"] = int(time.time() - p.create_time())
        try:
            usage = docker.resource_usage(p.id) if RUNNER == "docker" else process_resources.snapshot(p)
            out["cpu_percent"] = usage["cpu_percent"]
            out["mem_mb"] = round(usage["memory_bytes"] / 2**20, 1) if usage["memory_bytes"] is not None else None
        except Exception:
            out.update(cpu_percent=None, mem_mb=None)
    try:
        cfg = bot_api(bot, "show_config")
        if out["runtime"] == "cross_venue_paper":
            summary = bot_api(bot, "portfolio")
            daily = bot_api(bot, "daily?timescale=14&scope=portfolio")
            out.update(api="up",trading=cfg.get("state"),dry_run=True,open_trades=summary["open_trades"],
                       max_trades=1,closed_trades=summary["closed_trade_count"],
                       profit_all=summary["profit_all_coin"],profit_closed=summary["profit_closed_coin"],
                       winrate=summary["winrate"],stake="USDT",timeframe=f"{cfg['poll_seconds']}s",
                       note=summary["health"].get("note", ""))
            out["positions"]=[{"pair":p["pair"],"profit_pct":round(p["profit_ratio"]*100,2),
                               "profit_abs":p["profit_abs"],"open_date":p["open_date"],
                               "stake":p["stake_amount"],"tag":p["enter_tag"]} for p in summary["positions"]]
            out["daily"]=[{"date":d["date"],"abs":d["abs_profit"],"trades":d["trade_count"]}
                          for d in reversed(daily["data"])]
            return out
        prof = bot_api(bot, "profit")
        cnt = bot_api(bot, "count")
        out.update(
            api="up", trading=cfg.get("state"), dry_run=cfg.get("dry_run"),
            open_trades=cnt.get("current"), max_trades=cnt.get("max"),
            closed_trades=prof.get("closed_trade_count"), profit_all=prof.get("profit_all_coin"),
            profit_closed=prof.get("profit_closed_coin"), winrate=prof.get("winrate"),
            stake=cfg.get("stake_currency"), timeframe=cfg.get("timeframe"),
        )
        out["positions"] = [
            {"pair": t["pair"], "profit_pct": round((t.get("profit_ratio") or 0) * 100, 2),
             "profit_abs": t.get("profit_abs"), "open_date": t.get("open_date"),
             "stake": t.get("stake_amount"), "tag": t.get("enter_tag")}
            for t in bot_api(bot, "status")
        ]
        daily = bot_api(bot, "daily?timescale=14")
        out["daily"] = [{"date": d["date"], "abs": d["abs_profit"], "trades": d["trade_count"]}
                        for d in reversed(daily.get("data", []))]
    except Exception:
        out["api"] = "starting" if p else "down"
        try:
            out.update(offline_stats(bot))
        except Exception as e:  # noqa: BLE001
            print("offline stats", bot["name"], e, file=sys.stderr, flush=True)
    return out


def supervisor() -> None:
    """Bring back bots that should be running (after a crash, or after the panel/computer restarted)."""
    while True:
        try:
            state = load_state()
            for name, bot in bots().items():
                if state.get(name, {}).get("want") == "running" and not find_process(bot):
                    recent = [t for t in restarts.get(name, []) if t > time.time() - 600]
                    if len(recent) >= 3:          # crash loop: stop retrying for a while
                        continue
                    restarts.setdefault(name, []).append(time.time())
                    try:
                        start_bot(name)
                        wait_api(bot, 120)       # one bot at a time
                    except HTTPException:
                        pass
        except Exception as e:  # noqa: BLE001
            print("supervisor:", e, file=sys.stderr, flush=True)
        time.sleep(SUPERVISE_EVERY)


app = FastAPI(title="CCXT-Fr panel")
install_routes(app,market_data)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

resource_monitor = ResourceMonitor(ROOT / "user_data")


@app.get("/api/resources")
def server_resources():
    return resource_monitor.snapshot()


_net = {"t": 0.0, "data": {}}


@app.get("/api/net")
def net():
    """Latency to the market-data mirror the bots use, and to the trading API (cached 30 s)."""
    if time.time() - _net["t"] > 30:
        data = {}
        for key, url in (("行情线路", "https://data-api.binance.vision/api/v3/ping"),
                         ("交易接口", "https://api.binance.com/api/v3/ping")):
            t0 = time.time()
            try:
                requests.get(url, timeout=6).raise_for_status()
                data[key] = round(time.time() - t0, 2)
            except Exception:
                data[key] = None
        _net.update(t=time.time(), data=data)
    return _net["data"]


def _ui_version() -> str:
    """Changes whenever the page or its script changes; the open page polls it and reloads itself."""
    import hashlib
    h = hashlib.sha1()
    for f in ("index.html", "static/panel.js"):
        h.update((Path(__file__).parent / f).read_bytes())
    return h.hexdigest()[:12]


@app.get("/", response_class=HTMLResponse)
def index():
    v = _ui_version()
    html = (Path(__file__).parent / "index.html").read_text()
    html = html.replace('src="/static/panel.js"', f'src="/static/panel.js?v={v}"')
    html = html.replace("<head>", f'<head><meta name="ui-version" content="{v}">', 1)
    if ENV_LABEL:   # e.g. local development: make it impossible to mistake this page for the server's panel
        html = html.replace("<title>", f"<title>【{ENV_LABEL}】", 1)
        html = html.replace("<body>", '<body><div style="position:sticky;top:0;z-index:10;background:#1f6feb;color:#fff;'
                            'font:600 13px/1.4 var(--mono,monospace);padding:8px 16px;text-align:center;letter-spacing:.08em">'
                            f'{ENV_LABEL} · 本机 127.0.0.1:{PORT} · 机器人端口 +{PORT_OFFSET} · 独立数据库 user_data/dev/</div>', 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/api/ui-version")
def ui_version():
    return {"v": _ui_version()}


@app.middleware("http")
async def no_cache_static(request, call_next):
    resp = await call_next(request)
    if request.url.path.startswith("/static/") and request.url.path.endswith(".js"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# Retain an explicit disabled response for old clients and bookmarks.
@app.get("/api/c-signal")
def c_signal():
    raise HTTPException(status_code=410, detail="每周参考信号已停用")


@app.get("/api/bots")
def list_bots():
    return [status(b) for b in bots().values()]


# ---------- 24h research loops (one click, server-side, no Mac needed) ----------
LOOPS = {
    "aq_loop": {"title": "AQ 调参", "dir": "bench/aq_loop"},
    "strat_loop": {"title": "策略进化", "dir": "bench/strat_loop"},
}


def loop_status(key: str) -> dict:
    base = ROOT / "user_data" / LOOPS[key]["dir"]
    running = (base / "RUN").exists()
    out: dict = {"name": key, "title": LOOPS[key]["title"], "enabled": running}
    try:
        q = json.loads((base / "queue.json").read_text())
        items = q.get("items", [])
        out.update({
            "round": q.get("round", 1),
            "status": q.get("status", "open"),
            "champion": q.get("champion"),
            "refills": q.get("refills", 0),
            "total": len(items),
            "passed": sum(1 for i in items if str(i.get("status", "")).startswith("done-pass")),
            "pending": sum(1 for i in items if i.get("status") == "pending"),
        })
    except Exception:
        out["error"] = "读不到 queue.json"
    try:
        ex = json.loads((base / "explore.json").read_text())
        out["hypothesis"] = f"[{ex.get('mode')}] {ex.get('brief', '')[:180]}"
    except Exception:
        out["hypothesis"] = ""
    if key == "strat_loop":
        out["hyp_enabled"] = (base / "HYP").exists()
    try:
        lines = (base / "cron.log").read_text().strip().splitlines()
        out["last"] = lines[-1][:160] if lines else ""
    except Exception:
        out["last"] = ""
    return out


@app.get("/api/loops")
def list_loops():
    return [loop_status(k) for k in LOOPS]


@app.post("/api/loops/{name}/{action}")
def act_loop(name: str, action: str):
    if name not in LOOPS:
        raise HTTPException(404, "没有这个 loop")
    flag = ROOT / "user_data" / LOOPS[name]["dir"] / "RUN"
    hyp = ROOT / "user_data" / LOOPS[name]["dir"] / "HYP"
    if action == "start":
        flag.touch()
    elif action == "stop":
        try:
            flag.unlink()
        except FileNotFoundError:
            pass
    elif action == "hyp_on":
        hyp.touch()
    elif action == "hyp_off":
        try:
            hyp.unlink()
        except FileNotFoundError:
            pass
    else:
        raise HTTPException(400, "未知操作")
    return {"ok": True, "enabled": flag.exists(),
            "hyp_enabled": hyp.exists() if name == "strat_loop" else None}


def _closed_events(bot: dict, exclude: str | None) -> int | None:
    """Closed trades of a bot, optionally without one pair (the BTC hedge leg of the new-listing bots)."""
    try:
        trades = bot_api(bot, "trades?limit=500").get("trades", [])
    except Exception:  # noqa: BLE001
        return None
    return sum(1 for t in trades if not t.get("is_open") and t.get("pair") != exclude)


@app.get("/api/verdicts")
def verdicts():
    """Judgement page: per strategy the written criteria and review dates (bots/verdicts.json) plus live numbers."""
    try:
        data = json.loads((BOTS / "verdicts.json").read_text())
    except Exception:  # noqa: BLE001
        return {"entries": [], "closed": [], "intro": ""}
    live = {s["name"]: s for s in list_bots()}
    registry = bots()
    today = datetime.now(timezone.utc).date()
    for e in data.get("entries", []):
        rows = []
        for name in e.get("bots", []):
            s = live.get(name)
            if s:
                rows.append({"name": name, "running": s.get("want") == "running" and s.get("api") == "up",
                             "dry_run": s.get("dry_run"), "open": s.get("open_trades"), "closed": s.get("closed_trades"),
                             "profit_all": s.get("profit_all"), "profit_closed": s.get("profit_closed")})
        e["live"] = rows
        for c in e.get("checkpoints", []):
            c["days_left"] = (datetime.strptime(c["date"], "%Y-%m-%d").date() - today).days if c.get("date") else None
        t = e.get("target")
        if t:
            bot = registry.get(t["bot"])
            have = _closed_events(bot, t.get("exclude")) if bot and live.get(t["bot"], {}).get("api") == "up" else None
            if have is None:
                have = (live.get(t["bot"]) or {}).get("closed_trades")
            t["have"] = have
    return data


@app.post("/api/bots/{name}/{action}")
def act(name: str, action: str):
    if name != "all" and name not in bots():
        raise HTTPException(404, "没有这个机器人")
    if action == "liquidate":
        return liquidate(name)
    names = list(bots()) if name == "all" else [name]
    for n in names:
        if action == "start":
            start_bot(n)
            if name == "all":
                wait_api(bots()[n], 120)   # one at a time: startup downloads compete on a slow line
        elif action == "stop":
            stop_bot(n)
        elif action == "restart":
            stop_bot(n)
            start_bot(n)
        else:
            raise HTTPException(400, "未知操作")
        if action in ("start", "restart") and bots()[n].get("runtime") == "cross_venue_paper":
            bot = bots()[n]
            if not wait_api(bot, 30):
                raise HTTPException(503, "模拟组合服务尚未就绪，请查看日志")
            _bot_post(bot, "start")
    return {"ok": True}


# ---------- close all positions and stop ----------
liquidations: dict[str, dict] = {}
LIQ_TIMEOUT = 600          # seconds to wait for all exits to fill before giving up (process is then left running)


def _bot_post(bot: dict, path: str, body: dict | None = None):
    r = requests.post(f"http://127.0.0.1:{bot['port']}/api/v1/{path}", json=body or {}, auth=credentials(), timeout=15)
    r.raise_for_status()
    return r.json()


def _liquidate(name: str) -> None:
    bot = bots()[name]
    st = liquidations[name]
    was_running = bool(find_process(bot))

    def give_up(stage: str, msg: str) -> None:
        # a bot that was stopped before goes back to stopped; one that was running stays up (paused) so it keeps
        # managing whatever could not be sold
        if not was_running:
            stop_bot(name)
            msg += "。机器人已恢复为停止状态"
        else:
            msg += "。机器人保持运行（暂停开仓），请到 FreqUI 处理"
        st.update(stage=stage, msg=msg, done=True)

    try:
        if not was_running:
            st.update(stage="启动中", msg="机器人未运行，先启动它以便卖出持仓")
            start_bot(name)
        if not wait_api(bot, 240):
            give_up("失败", "机器人没能启动，持仓没有卖出")
            return
        deadline = time.time() + LIQ_TIMEOUT
        # the API answers before the bot has finished initialising (pairlist refresh); retry until it accepts
        st.update(stage="等待就绪", msg="等机器人完成初始化")
        while True:
            try:
                _bot_post(bot, "pause")
                break
            except Exception:
                if time.time() > deadline:
                    give_up("失败", "机器人一直没有就绪，持仓没有卖出")
                    return
                time.sleep(5)
        st.update(stage="暂停开仓", msg="已停止新的买入")
        last_fire, last_err = 0.0, ""
        while True:
            try:
                open_trades = bot_api(bot, "status")
            except Exception as e:  # noqa: BLE001
                last_err = str(e)
                open_trades = None
            if open_trades is not None:
                st["remaining"] = [t["pair"] for t in open_trades]
                if not open_trades:
                    break
            if time.time() > deadline:
                left = ", ".join(st["remaining"]) or "未知"
                give_up("超时", f"10 分钟内没卖完：{left}{'（' + last_err + '）' if last_err else ''}")
                return
            if open_trades and time.time() - last_fire > 60:   # (re)send market exits for whatever is still open
                try:
                    _bot_post(bot, "forceexit", {"tradeid": "all", "ordertype": "market"})
                    st.update(stage="卖出中", msg=f"市价卖出 {len(open_trades)} 笔持仓")
                    last_fire = time.time()
                except Exception as e:  # noqa: BLE001
                    last_err = str(e)
                    st.update(stage="卖出中", msg=f"卖出请求被拒绝，10 秒后重试（{e}）")
                    last_fire = time.time() - 50
            time.sleep(3)
        st.update(stage="停止进程", msg="持仓已全部卖出，正在停止机器人")
        stop_bot(name)
        st.update(stage="完成", msg="已清仓并停止", done=True, remaining=[])
    except Exception as e:  # noqa: BLE001
        give_up("失败", f"出错：{e}")


@app.post("/api/bots/{name}/liquidate")
def liquidate(name: str):
    if name not in bots():
        raise HTTPException(404, "没有这个机器人")
    cur = liquidations.get(name)
    if cur and not cur.get("done"):
        raise HTTPException(409, "正在清仓中")
    liquidations[name] = {"stage": "开始", "msg": "", "remaining": [], "done": False, "started": time.time()}
    threading.Thread(target=_liquidate, args=(name,), daemon=True).start()
    return {"ok": True}


@app.get("/api/bots/{name}/log", response_class=PlainTextResponse)
def log(name: str, lines: int = 80):
    f = BOT_LOGS / f"{name}.log"
    if name not in bots() or not f.exists():
        return ""
    with open(f, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - 200_000))
        return b"\n".join(fh.read().splitlines()[-lines:]).decode(errors="replace")


# ---------- FreqUI served by the panel on its own port, with automatic login ----------
# FreqUI keeps its bot logins in the browser's localStorage per origin. Served from a bot's own port, the logins vanish
# with that bot; served here (always up) and pre-filled with fresh tokens, it never asks for a password.
FREQUI_DIR = ROOT / "freqtrade" / "rpc" / "api_server" / "ui" / "installed"
FREQUI_PORT = int(os.environ.get("FREQUI_PORT", "8089"))
ui = FastAPI(title="FreqUI via panel")


def _frequi_logins() -> dict:
    user, pw = credentials()
    bots_list = [(i, b) for i, b in enumerate(bots().values())
                 if b.get("runtime", "freqtrade") in ("freqtrade", "cross_venue_paper")]

    def login_one(args):
        i, b = args
        url = f"http://127.0.0.1:{b['port']}"
        try:
            r = requests.post(f"{url}/api/v1/token/login", auth=(user, pw), timeout=3)
            r.raise_for_status()
            tok = r.json()
        except Exception:
            return None  # bot is not running: leave it out
        return (f"ccxt.{b['name']}", {"botName": b["bot_name"], "apiUrl": url, "username": user,
                "accessToken": tok["access_token"], "refreshToken": tok["refresh_token"],
                "autoRefresh": True, "sortId": i})

    # parallel: 14 sequential blocking logins could stall the event loop ~1 min
    import concurrent.futures
    out = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(login_one, bots_list):
            if res:
                out[res[0]] = res[1]
    return out


def _frequi_index(bot: str | None) -> HTMLResponse:
    html = (FREQUI_DIR / "index.html").read_text()
    logins = json.dumps(_frequi_logins())
    pick = json.dumps(f"ccxt.{bot}") if bot else "null"
    boot = f"""<script>
try {{
  var fresh = {logins};
  var cur = JSON.parse(localStorage.getItem("ftAuthLoginInfo") || "{{}}");
  Object.keys(cur).forEach(function (k) {{ if (k.indexOf("ccxt.") === 0 && !fresh[k]) delete cur[k]; }});
  Object.keys(fresh).forEach(function (k) {{ cur[k] = fresh[k]; }});
  localStorage.setItem("ftAuthLoginInfo", JSON.stringify(cur));
  var pick = {pick};
  if (pick && cur[pick]) localStorage.setItem("ftSelectedBot", pick);
  // keep the bot list in step with the panel: when a bot starts or stops, refresh logins and reload once
  var known = Object.keys(fresh).sort().join(",");
  setInterval(function () {{
    fetch("/__ccxt/logins", {{ cache: "no-store" }}).then(function (r) {{ return r.json(); }}).then(function (now) {{
      var ids = Object.keys(now).sort().join(",");
      if (ids === known) return;
      var c = JSON.parse(localStorage.getItem("ftAuthLoginInfo") || "{{}}");
      Object.keys(c).forEach(function (k) {{ if (k.indexOf("ccxt.") === 0 && !now[k]) delete c[k]; }});
      Object.keys(now).forEach(function (k) {{ c[k] = now[k]; }});
      localStorage.setItem("ftAuthLoginInfo", JSON.stringify(c));
      location.reload();
    }}).catch(function () {{}});
  }}, 20000);
}} catch (e) {{ console.warn("ccxt autologin", e); }}
</script>"""
    return HTMLResponse(html.replace("<head>", "<head>" + boot, 1), headers={"Cache-Control": "no-store"})


@ui.get("/__ccxt/logins")
def frequi_logins():
    return _frequi_logins()


@ui.get("/{path:path}")
def frequi(path: str, bot: str | None = None):
    f = (FREQUI_DIR / path).resolve()
    if path and f.is_file() and FREQUI_DIR in f.parents:
        from fastapi.responses import FileResponse
        return FileResponse(f)
    return _frequi_index(bot)


if __name__ == "__main__":
    raise_fd_limit()
    threading.Thread(target=supervisor, daemon=True).start()
    threading.Thread(target=lambda: uvicorn.run(ui, host=HOST, port=FREQUI_PORT, log_level="warning"), daemon=True).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
