"""CCXT-Fr control panel: start / stop / watch the freqtrade bots from one web page.

Bots are the per-bot configs in user_data/bots/ (any *.json with a "strategy" key). The panel launches
each bot as a detached process (it keeps running if the panel restarts), remembers which bots should be
running, and restarts a bot that dies unexpectedly. It listens on 127.0.0.1 only.

Run:  .venv/bin/python user_data/panel/panel.py      (on macOS a LaunchAgent starts it at login)
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

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
            "port": cfg.get("api_server", {}).get("listen_port"),
            "db_url": cfg.get("db_url"),
            "max_trades": cfg.get("max_open_trades", _common().get("max_open_trades")),
            "description": meta.get(name, {}).get("description", ""),
        }
    order = list(meta)
    return dict(sorted(out.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99))


def credentials() -> tuple[str, str]:
    s = json.loads((BOTS / "secrets.json").read_text())["api_server"]
    return s["username"], s["password"]


def find_process(bot: dict) -> psutil.Process | None:
    """The running freqtrade process for this bot, started by the panel or by hand with the same config."""
    cfg = str(bot["config"].relative_to(ROOT))
    for p in psutil.process_iter(["cmdline"]):
        cmd = p.info.get("cmdline") or []
        if "trade" in cmd and any(c.endswith("freqtrade") for c in cmd) and (cfg in cmd or str(bot["config"]) in cmd):
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
        owner = port_owner(bot["port"])
        if owner:
            raise HTTPException(409, f"端口 {bot['port']} 已被进程 {owner.pid}（{' '.join(owner.cmdline()[1:4])}）占用")
        LOGS.mkdir(exist_ok=True)
        console = open(LOGS / f"{name}.console.log", "ab")
        subprocess.Popen(
            [str(FREQTRADE), "trade", "-c", str(bot["config"].relative_to(ROOT)), "--logfile", f"user_data/logs/{name}.log"],
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
        p = find_process(bot)
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
            r = requests.get("https://data-api.binance.vision/api/v3/ticker/price", params={"symbols": syms}, timeout=6)
            r.raise_for_status()
            _tickers.update(t=time.time(), px={f"{x['symbol'][:-4]}/USDT": float(x["price"]) for x in r.json()})
        except Exception:
            pass
    return _tickers["px"]


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
    out = {k: bot[k] for k in ("name", "strategy", "bot_name", "port", "description")}
    liq = liquidations.get(bot["name"])
    if liq and (not liq.get("done") or time.time() - liq["started"] < 600):
        out["liq"] = liq
    out.update(want=st.get("want", "stopped"), pid=p.pid if p else None, restarts_24h=len(
        [t for t in restarts.get(bot["name"], []) if t > time.time() - 86400]))
    if p:
        out["uptime_s"] = int(time.time() - p.create_time())
        out["mem_mb"] = int(p.memory_info().rss / 2**20)
    try:
        cfg = bot_api(bot, "show_config")
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
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

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


# ---------- C: BTC/ETH weekly holding signal (spec bench/specs/2026-09-27-c-btceth-risk-managed-hold.md) ----------
_csig = {"t": 0.0, "data": None}
C_LOOKBACK, C_VOL_TARGET, C_CAP, C_BAND = 30, 0.60, 0.5, 0.05


def _daily(symbol: str) -> list[tuple[int, float]]:
    r = requests.get("https://data-api.binance.vision/api/v3/klines",
                     params={"symbol": symbol, "interval": "1d", "limit": 200}, timeout=15)
    r.raise_for_status()
    now_ms = time.time() * 1000
    return [(k[0], float(k[4])) for k in r.json() if k[6] < now_ms]      # closed daily candles only


def _weights(closes: list[float]) -> dict:
    import math
    mom = closes[-1] / closes[-1 - C_LOOKBACK] - 1
    logr = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - 30, len(closes))]
    mean = sum(logr) / len(logr)
    vol = math.sqrt(sum((x - mean) ** 2 for x in logr) / (len(logr) - 1)) * math.sqrt(365)
    w = C_CAP * min(1.0, C_VOL_TARGET / vol) if mom > 0 else 0.0
    return {"mom30": mom, "vol30": vol, "on": mom > 0, "weight": w}


@app.get("/api/c-signal")
def c_signal():
    """This week's target weights: decided on the last completed Sunday close (UTC), traded at Monday's open."""
    if _csig["data"] is None or time.time() - _csig["t"] > 600:
        series = {c: _daily(f"{c}USDT") for c in ("BTC", "ETH")}
        days = [t for t, _ in series["BTC"]]
        # Sunday candles: open time on a Sunday (UTC); the decision uses that candle's close
        sundays = [i for i, t in enumerate(days) if time.gmtime(t / 1000).tm_wday == 6]
        hist = []
        for i in sundays[-13:]:
            row = {"decided_on": time.strftime("%Y-%m-%d", time.gmtime(days[i] / 1000))}
            for c in ("BTC", "ETH"):
                row[c] = _weights([x for _, x in series[c][: i + 1]])
            row["USDT"] = 1 - row["BTC"]["weight"] - row["ETH"]["weight"]
            hist.append(row)
        cur = hist[-1]
        prev = hist[-2] if len(hist) > 1 else None
        latest = {c: _weights([x for _, x in series[c]]) for c in ("BTC", "ETH")}
        last_sun = days[sundays[-1]] / 1000
        next_monday = last_sun + 8 * 86400                    # the Monday after the *next* Sunday close
        _csig.update(t=time.time(), data={
            "week": cur, "previous": prev, "history": hist,
            "today_preview": {"as_of": time.strftime("%Y-%m-%d", time.gmtime(days[-1] / 1000)), **latest},
            "trade_at": time.strftime("%Y-%m-%d 00:00 UTC", time.gmtime(last_sun + 86400)),
            "next_decision_utc": next_monday, "band": C_BAND,
            "backtest": {"oos": "2017-09→2020-12：年化 +79% vs 死拿 +64%，最大回撤 −49% vs −88%",
                         "is": "2021-01→2026-09：年化 +45% vs +22%，最大回撤 −33% vs −76%",
                         "verdict": "未通过预注册标准 1（回撤需 ≤ 死拿的一半，实际 56%），仅作参考"},
        })
    return _csig["data"]


@app.get("/api/bots")
def list_bots():
    return [status(b) for b in bots().values()]


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
    f = LOGS / f"{name}.log"
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
    out = {}
    for i, b in enumerate(bots().values()):
        url = f"http://127.0.0.1:{b['port']}"
        try:
            r = requests.post(f"{url}/api/v1/token/login", auth=(user, pw), timeout=4)
            r.raise_for_status()
            tok = r.json()
        except Exception:
            continue                                   # bot is not running: leave it out
        out[f"ccxt.{b['name']}"] = {"botName": b["bot_name"], "apiUrl": url, "username": user,
                                    "accessToken": tok["access_token"], "refreshToken": tok["refresh_token"],
                                    "autoRefresh": True, "sortId": i}
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
    threading.Thread(target=supervisor, daemon=True).start()
    threading.Thread(target=lambda: uvicorn.run(ui, host=HOST, port=FREQUI_PORT, log_level="warning"), daemon=True).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
