// CCXT-FR panel: each bot module is built once; refreshes only touch values that changed.

const $ = (sel, root = document) => root.querySelector(sel);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch {} } };
const num = (v, d = 2) => v == null ? "–" : (v > 0 ? "+" : "") + Number(v).toFixed(d);
const tone = v => v > 0 ? "pos" : v < 0 ? "neg" : "";
function dur(s) {
  if (s == null) return "";
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
  return d ? `${d}天${h}时` : h ? `${h}时${m}分` : `${m}分`;
}
const since = iso => iso ? dur((Date.now() - new Date(iso.replace(" ", "T") + "Z")) / 1000) : "";
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ---------- odometer ----------
   setTick(el, text): only the characters that changed are touched. Changed digits roll (up when the value rose,
   down when it fell), right-most first; the whole number glows green/red for a moment. */
function setTick(el, text) {
  text = String(text);
  const prev = el.dataset.v;
  if (prev === text) return;
  el.dataset.v = text;
  if (prev === undefined || reduced) { el.innerHTML = [...text].map(ch => `<span class="c">${esc(ch)}</span>`).join(""); return; }
  const a = parseFloat(prev.replace(/[^\d.+-]/g, "")), b = parseFloat(text.replace(/[^\d.+-]/g, ""));
  const dir = isNaN(a) || isNaN(b) || a === b ? "up" : (b > a ? "up" : "dn");
  const old = [...prev], neu = [...text];
  const pad = Math.max(old.length, neu.length);
  while (old.length < pad) old.unshift("");
  const lead = pad - neu.length;
  let rank = 0;
  const html = [];
  for (let i = pad - 1; i >= lead; i--) {
    const o = old[i], n = neu[i - lead];
    if (o !== n && /\d/.test(n) && /\d/.test(o)) {
      const pair = dir === "up" ? `<i>${o}</i><i>${n}</i>` : `<i>${n}</i><i>${o}</i>`;
      html.unshift(`<span class="d ${dir}" style="--w:1ch"><span class="roll" style="animation-delay:${rank * 45}ms">${pair}</span></span>`);
      rank++;
    } else {
      html.unshift(`<span class="c">${esc(n)}</span>`);
    }
  }
  el.innerHTML = html.join("");
  if (!el.dataset.noglow) {
    el.classList.remove("glow-up", "glow-dn");
    void el.offsetWidth;
    el.classList.add(dir === "up" ? "glow-up" : "glow-dn");
  }
  clearTimeout(el._t);
  el._t = setTimeout(() => {
    el.classList.remove("glow-up", "glow-dn");
    el.innerHTML = neu.map(ch => `<span class="c">${esc(ch)}</span>`).join("");
  }, 650 + rank * 45);
}
function setText(el, text) { if (el && el.textContent !== text) el.textContent = text; }
function setClass(el, cls, on) { if (el) el.classList.toggle(cls, !!on); }
function setTone(el, v) { setClass(el, "pos", v > 0); setClass(el, "neg", v < 0); }

/* ---------- module skeleton (built once per bot) ---------- */
const openLogs = new Set(JSON.parse(store.get("panel-open") || "[]"));
const busy = new Set();
const units = new Map();

function build(b, i) {
  const el = document.createElement("article");
  el.className = "unit";
  el.style.animationDelay = `${i * 90}ms`;
  el.innerHTML = `
    <div class="row">
      <div class="id">
        <div class="name"><span class="led"></span>${esc(b.bot_name)}</div>
        <div class="strat" title="${esc(b.strategy)}">:${b.port} · ${esc(b.strategy)}</div>
        <div class="desc">${esc(b.description)}</div>
        <div class="desc strategy-note" hidden></div>
        <div class="liqmsg" hidden></div>
        <div class="tags"><span class="tag state"></span><span class="tag mode" hidden></span><span class="tag up-t" hidden></span><span class="tag live rs" hidden></span></div>
      </div>
      <div class="metrics">
        <div class="metric"><div class="lbl">持仓</div><div class="val"><span class="tk" data-f="open"></span><small data-f="max"></small></div><div class="sub" data-f="closed"></div></div>
        <div class="metric"><div class="lbl" data-f="pnl-lbl">总盈亏 USDT</div><div class="val big"><span class="tk" data-f="pnl"></span></div><div class="sub" data-f="win"></div><div class="sub" data-f="unreal" hidden><span class="tk" data-f="unreal-v"></span> 浮动·按现价</div></div>
        <div class="metric"><div class="lbl">近 7 天</div><div class="val"><span class="tk" data-f="week"></span></div><div class="sub">仅已平仓</div></div>
      </div>
      <div class="chart"><div class="lbl">近 14 天每日盈亏</div><div class="bars">${Array.from({ length: 14 }, () => `<div class="bar"><i></i></div>`).join("")}</div></div>
      <div class="controls">
        <button class="switch"><span class="track"><span class="knob"></span></span><span data-f="sw">OFF</span></button>
        <div class="btns">
          <button class="btn restart" hidden>重启</button>
          <button class="btn warn liq" hidden>清仓并停止</button>
          <a class="btn ui" target="_blank" hidden>FreqUI ↗</a>
          <a class="btn portfolio" target="_blank" hidden>组合明细 ↗</a>
          <button class="btn logbtn">明细</button>
        </div>
      </div>
    </div>
    <div class="drawer" hidden><div class="positions"></div></div>
    <pre class="log" hidden></pre>`;
  $(".switch", el).onclick = () => {
    const running = el.dataset.running === "1";
    const n = Number(el.dataset.open || 0);
    if (running) ask(b.name, "stop", `停止 ${b.bot_name}`, n
      ? `还有 ${n} 笔持仓。只停止进程不会卖出，停止期间没人管理这些持仓（不会止盈止损）。要全部卖出请用“清仓并停止”。`
      : "进程会停止。");
    else act(b.name, "start");
  };
  $(".restart", el).onclick = () => act(b.name, "restart");
  $(".liq", el).onclick = () => {
    const list = [...el.querySelectorAll(".pos-chip")].map(c => `${c.dataset.pair}（${$(".pct", c).dataset.v || ""}）`).join("、");
    ask(b.name, "liquidate", `清仓并停止 ${b.bot_name}`,
      `会先暂停开仓，再以市价卖出全部持仓：${list || "（无）"}。全部成交后停止机器人；如果 10 分钟内没卖完，机器人保持运行并提示你。${el.dataset.dry === "1" ? "这是模拟盘，按模拟价成交。" : "这是实盘，会真实下单。"}`);
  };
  $(".ui", el).href = `http://127.0.0.1:8089/?bot=${encodeURIComponent(b.name)}`;
  if (b.runtime === "cross_venue_paper") {
    $(".ui", el).href = b.detail_url;
    $(".portfolio", el).href = b.portfolio_url;
  }
  $(".logbtn", el).onclick = () => {
    openLogs.has(b.name) ? openLogs.delete(b.name) : openLogs.add(b.name);
    store.set("panel-open", JSON.stringify([...openLogs]));
    renderLog(b.name, el);
  };
  renderLog(b.name, el);
  return el;
}

function stateOf(b) {
  if (b.liq && !b.liq.done) return { led: "boot", text: "清仓中", unit: "on" };
  if (busy.has(b.name) || busy.has("all")) return { led: "boot", text: "处理中", unit: "" };
  if (b.pid && b.api === "up") return { led: "on", text: b.trading === "running" ? "运行中" : "交易暂停", unit: "on" };
  if (b.pid) return { led: "boot", text: "启动中", unit: "on" };
  if (b.want === "running") return { led: "fault", text: "已退出 · 等待重启", unit: "fault" };
  return { led: "", text: "已停止", unit: "" };
}

function update(el, b) {
  const st = stateOf(b), up = b.api === "up", running = !!b.pid;
  const has = up || !!b.offline;          // stopped bots: numbers read from their trade database
  el.dataset.running = running ? "1" : "0";
  setClass(el, "on", st.unit === "on"); setClass(el, "fault", st.unit === "fault");
  const led = $(".led", el); led.className = "led " + st.led;
  setText($(".tag.state", el), st.text);
  const mode = $(".tag.mode", el); mode.hidden = !has;
  if (up) { setText(mode, b.dry_run ? "模拟盘" : "实盘"); setClass(mode, "live", !b.dry_run); }
  else if (has) { setText(mode, "数据来自数据库"); setClass(mode, "live", false); }
  const upt = $(".tag.up-t", el); upt.hidden = !running; if (running) setText(upt, `${dur(b.uptime_s)} · ${b.mem_mb}MB`);
  const rs = $(".tag.rs", el); rs.hidden = !b.restarts_24h; if (b.restarts_24h) setText(rs, `24h 自动重启 ${b.restarts_24h}`);

  const f = k => $(`[data-f="${k}"]`, el);
  setTick(f("open"), has ? b.open_trades : "–");
  setText(f("max"), has && b.max_trades != null ? `/ ${b.max_trades}` : "");
  setText(f("closed"), has ? `已平仓 ${b.closed_trades}` : "");
  // running bot: one total (realised + unrealised). Stopped bot: realised (fixed) and unrealised (moves with price) apart.
  const split = !up && b.offline;
  const unreal = split ? (b.profit_all || 0) - (b.profit_closed || 0) : 0;
  setText(f("pnl-lbl"), split ? "已实现 USDT（固定）" : "总盈亏 USDT");
  const shown = split ? b.profit_closed : b.profit_all;
  setTick(f("pnl"), has ? num(shown) : "–"); setTone(f("pnl").parentElement, has ? shown : 0);
  setText(f("win"), has ? `${up ? "含浮动" : "已平仓"}${b.closed_trades ? ` · 胜率 ${(b.winrate * 100).toFixed(0)}%` : ""}` : "");
  const ur = f("unreal"); ur.hidden = !(split && b.open_trades);
  if (!ur.hidden) { setTick(f("unreal-v"), num(unreal)); setTone(f("unreal-v"), unreal); }
  const week = (b.daily || []).slice(-7).reduce((s, d) => s + d.abs, 0);
  setTick(f("week"), has ? num(week) : "–"); setTone(f("week").parentElement, has ? week : 0);

  const daily = has ? (b.daily || []) : [];
  const max = Math.max(1, ...daily.map(d => Math.abs(d.abs)));
  el.querySelectorAll(".bar").forEach((bar, i) => {
    const d = daily[daily.length - 14 + i];
    const i_ = bar.firstElementChild;
    if (!d) { i_.className = ""; i_.style.height = "0"; bar.title = ""; return; }
    i_.className = d.abs > 0 ? "up" : d.abs < 0 ? "dn" : "zero";
    i_.style.height = d.abs ? `${Math.abs(d.abs) / max * 50}%` : "";
    bar.title = `${d.date}  ${num(d.abs)} USDT  ${d.trades} 笔`;
  });

  const sw = $(".switch", el);
  setClass(sw, "on", running); sw.disabled = busy.has(b.name) || busy.has("all");
  setText(f("sw"), running ? "ON" : "OFF");
  $(".restart", el).hidden = !running;
  el.dataset.open = has ? (b.open_trades || 0) : 0;
  el.dataset.dry = b.dry_run === false ? "0" : "1";
  const liqActive = b.liq && !b.liq.done;
  $(".liq", el).hidden = !(has && b.open_trades > 0) || liqActive;
  const lm = $(".liqmsg", el);
  lm.hidden = !b.liq;
  if (b.liq) {
    setText(lm, `清仓：${b.liq.stage}${b.liq.msg ? " · " + b.liq.msg : ""}`);
    lm.className = "liqmsg " + (b.liq.stage === "完成" ? "pos" : ["失败", "超时"].includes(b.liq.stage) ? "neg" : "amb");
  }
  $(".ui", el).hidden = !up;
  $(".portfolio", el).hidden = !(up && b.runtime === "cross_venue_paper");
  const note = $(".strategy-note", el);
  note.hidden = !b.note;
  setText(note, b.note || "");

  // open positions: chips are keyed by pair; only their numbers change between refreshes
  const drawer = $(".drawer", el), box = $(".positions", el);
  const pos = has ? (b.positions || []) : [];
  drawer.hidden = !pos.length;
  const keep = new Set(pos.map(p => p.pair));
  box.querySelectorAll(".pos-chip").forEach(c => { if (!keep.has(c.dataset.pair)) c.remove(); });
  for (const p of pos) {
    let c = box.querySelector(`.pos-chip[data-pair="${CSS.escape(p.pair)}"]`);
    if (!c) {
      c = document.createElement("div"); c.className = "pos-chip"; c.dataset.pair = p.pair;
      c.innerHTML = `<b>${esc(p.pair)}</b><b class="tk pct"></b><span class="age"></span>`;
      box.appendChild(c);
    }
    setTick($(".pct", c), p.profit_pct == null ? "–" : num(p.profit_pct) + "%"); setTone($(".pct", c), p.profit_pct);
    setText($(".age", c), since(p.open_date));
  }
  setText($(".logbtn", el), openLogs.has(b.name) ? "收起" : "明细");
}

async function renderLog(name, el) {
  const pre = $(".log", el);
  pre.hidden = !openLogs.has(name);
  setText($(".logbtn", el), openLogs.has(name) ? "收起" : "明细");
  if (pre.hidden) return;
  const text = await (await fetch(`/api/bots/${name}/log?lines=80`)).text();
  if (pre.dataset.raw === text) return;
  pre.dataset.raw = text;
  const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20 || !pre.dataset.init;
  pre.innerHTML = esc(text).split("\n").map(l => / - (ERROR|CRITICAL) - /.test(l) ? `<span class="e">${l}</span>`
    : / - WARNING - /.test(l) ? `<span class="w">${l}</span>` : l).join("\n") || "（还没有日志）";
  if (atBottom) { pre.scrollTop = pre.scrollHeight; pre.dataset.init = "1"; }
}

/* ---------- refresh loop ---------- */
async function refresh() {
  let bots;
  try { bots = await (await fetch("/api/bots")).json(); }
  catch (e) { setText($("#updated"), "读不到控制台后台：" + e); return; }
  const rack = $("#rack");
  bots.forEach((b, i) => {
    let el = units.get(b.name);
    if (!el) { el = build(b, i); units.set(b.name, el); rack.appendChild(el); }
    update(el, b);
    if (openLogs.has(b.name)) renderLog(b.name, el);
  });
  for (const [name, el] of units) if (!bots.some(b => b.name === name)) { el.remove(); units.delete(name); }

  const up = bots.filter(b => b.api === "up");
  const known = bots.filter(b => b.api === "up" || b.offline);
  const pnl = known.reduce((s, b) => s + (b.profit_all || 0), 0);
  setTick($("#s-run .tk"), bots.filter(b => b.pid).length); setText($("#s-run small"), `/ ${bots.length}`);
  setTick($("#s-open .tk"), known.reduce((s, b) => s + (b.open_trades || 0), 0));
  setTick($("#s-pnl .tk"), num(pnl)); setTone($("#s-pnl"), pnl);
  setText($("#updated"), "每 5 秒刷新 · 上次 " + new Date().toLocaleTimeString("zh-CN", { hour12: false }));
}

async function refreshNet() {
  try {
    const n = await (await fetch("/api/net")).json();
    for (const [id, key] of [["#s-net1", "行情线路"], ["#s-net2", "交易接口"]]) {
      const v = n[key], el = $(id);
      if (v == null) { setTick($(".tk", el), "不通"); setText($("small", el), ""); el.className = "val neg"; }
      else { setTick($(".tk", el), (v * 1000).toFixed(0)); setText($("small", el), "ms"); el.className = "val " + (v < 1 ? "pos" : "amb"); }
    }
  } catch {}
}

/* ---------- actions ---------- */
const dlg = $("#dlg");
function ask(name, action, title, text) {
  setText($("#dlg-t"), title); setText($("#dlg-p"), text);
  $("#dlg-ok").onclick = () => { dlg.close(); act(name, action); };
  dlg.showModal();
}
window.ask = ask;
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.style.display = "block"; setTimeout(() => t.style.display = "none", 5000); }
async function act(name, action) {
  busy.add(name); refresh();
  try {
    const r = await fetch(`/api/bots/${name}/${action}`, { method: "POST" });
    if (!r.ok) toast((await r.json()).detail || "操作失败");
  } catch (e) { toast("操作失败：" + e); }
  busy.delete(name); refresh();
}
window.act = act;

function tick() {
  const n = new Date();
  setTick($("#clk-bj"), n.toLocaleTimeString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" }));
  setTick($("#clk-utc"), n.toLocaleTimeString("zh-CN", { hour12: false, timeZone: "UTC" }));
}

tick(); setInterval(tick, 1000);
refresh(); setInterval(refresh, 5000);
refreshNet(); setInterval(refreshNet, 30000);

/* ---------- C: weekly holding signal ---------- */
let cNext = null;
const pct = x => (x * 100).toFixed(1);
async function refreshSignal() {
  let d;
  try { d = await (await fetch("/api/c-signal")).json(); } catch { return; }
  const w = d.week;
  const b = w.BTC.weight, e = w.ETH.weight, u = Math.max(0, w.USDT);
  const bar = (id, v, label) => { const el = $(id); el.style.flexGrow = v; el.textContent = v >= 0.08 ? `${label} ${pct(v)}%` : ""; };
  bar("#c-bar-btc", b, "BTC"); bar("#c-bar-eth", e, "ETH"); bar("#c-bar-usdt", u, "USDT");
  setText($("#c-decided"), `本周目标仓位 · ${w.decided_on}（周日）收盘判断`);
  for (const [c, k] of [["BTC", "btc"], ["ETH", "eth"]]) {
    const x = w[c];
    setTick($(`#c-${k}-w`), pct(x.weight));
    const on = $(`#c-${k}-on`); on.className = "onoff " + (x.on ? "on" : "off"); on.textContent = x.on ? "ON" : "OFF";
    setText($(`#c-${k}-sub`), `30天 ${x.mom30 >= 0 ? "+" : ""}${pct(x.mom30)}% · 波动 ${(x.vol30 * 100).toFixed(0)}%`);
  }
  setTick($("#c-usdt-w"), pct(u));
  if (d.previous) {
    const moves = [["BTC", w.BTC.weight - d.previous.BTC.weight], ["ETH", w.ETH.weight - d.previous.ETH.weight]]
      .filter(([, dv]) => Math.abs(dv) > d.band).map(([c, dv]) => `${c} ${dv > 0 ? "+" : ""}${pct(dv)}`);
    setText($("#c-change"), moves.length ? `需调仓：${moves.join("，")} 个百分点` : "和上周相比无需调仓");
  }
  setText($("#c-trade"), `本周调仓时间 ${d.trade_at}（北京时间周一 08:00）`);
  $("#c-hist").innerHTML = d.history.map(h =>
    `<div title="${h.decided_on}  BTC ${pct(h.BTC.weight)}%  ETH ${pct(h.ETH.weight)}%"><i class="b" style="height:${h.BTC.weight * 100}%"></i><i class="e" style="height:${h.ETH.weight * 100}%"></i></div>`).join("");
  $("#c-bt").innerHTML = `样本外 ${esc(d.backtest.oos)}<br>样本内 ${esc(d.backtest.is)}`;
  cNext = d.next_decision_utc * 1000;
}
function tickCountdown() {
  if (!cNext) return;
  let s = Math.max(0, Math.floor((cNext - Date.now()) / 1000));
  const dd = Math.floor(s / 86400); s %= 86400;
  const hh = String(Math.floor(s / 3600)).padStart(2, "0"), mm = String(Math.floor(s % 3600 / 60)).padStart(2, "0"), ss = String(s % 60).padStart(2, "0");
  $("#c-countdown").dataset.noglow = "1";
  setTick($("#c-countdown"), `${dd}天 ${hh}:${mm}:${ss}`);
}
refreshSignal(); setInterval(refreshSignal, 600000);
setInterval(tickCountdown, 1000);

/* ---------- reload the page when the panel's UI code changes ---------- */
const uiVersion = document.querySelector('meta[name="ui-version"]')?.content;
setInterval(async () => {
  try {
    const { v } = await (await fetch("/api/ui-version", { cache: "no-store" })).json();
    if (uiVersion && v && v !== uiVersion) location.reload();
  } catch {}
}, 30000);
