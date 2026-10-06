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
let fleet = [], fleetFilter = "all", expandedBot = null;
const isAttention = b => (b.want === "running" && !b.pid) || (b.pid && b.api !== "up") || (b.liq && !b.liq.done) || (b.liq && ["失败", "超时"].includes(b.liq.stage));
const matchesFilter = (b, filter) => filter === "all" || (filter === "running" && !!b.pid) || (filter === "positions" && Number(b.open_trades) > 0) || (filter === "attention" && isAttention(b)) || (filter === "stopped" && !b.pid);
function layoutFleet() {
  const query = $("#bot-search").value.trim().toLowerCase();
  const sort = $("#bot-sort").value;
  const filtered = fleet.filter(b => matchesFilter(b, fleetFilter) && [b.name,b.bot_name,b.strategy,b.port,b.description].join(" ").toLowerCase().includes(query));
  const priority = b => isAttention(b) ? 0 : b.pid ? 1 : Number(b.open_trades) > 0 ? 2 : 3;
  filtered.sort((a,b) => sort === "name" ? String(a.bot_name || a.name).localeCompare(String(b.bot_name || b.name)) : sort === "pnl" ? (b.profit_all ?? -Infinity) - (a.profit_all ?? -Infinity) || a.name.localeCompare(b.name) : priority(a)-priority(b) || a.name.localeCompare(b.name));
  const visible = new Set(filtered.map(b=>b.name));
  const rack = $("#rack");
  for (const [name,el] of units) el.hidden = !visible.has(name);
  // Move only when ordering changes, preserving focused controls across polling.
  const ordered = [...filtered.map(b=>units.get(b.name)), ...fleet.filter(b=>!filtered.some(x=>x.name===b.name)).map(b=>units.get(b.name))];
  ordered.forEach((el,i)=>{ if (rack.children[i] !== el) rack.insertBefore(el,rack.children[i] || null); });
  $("#list-empty").hidden = filtered.length > 0;
  rack.hidden = !filtered.length;
  setText($("#list-count"), `${filtered.length} / ${fleet.length} 个机器人`);
  setText($("#nav-count"), String(fleet.length));
  document.querySelectorAll("[data-filter]").forEach(btn=>setText($("span",btn),String(fleet.filter(b=>matchesFilter(b,btn.dataset.filter)).length)));
}
function expandBot(name) {
  expandedBot = expandedBot === name ? null : name;
  for (const [key,el] of units) {
    const expanded = key === expandedBot;
    setClass(el,"expanded",expanded);
    $(".inspect",el).setAttribute("aria-expanded",String(expanded));
    setText($(".inspect",el),expanded ? "收起 ↑" : "详情 ↓");
    if (!expanded) { openLogs.delete(key); $(".log",el).hidden = true; }
  }
  store.set("panel-open", JSON.stringify([...openLogs]));
}
function selectView(view) {
  const views = { home: ["strip", "resource", "loops"], bots: ["bot-workspace"], research: ["verdicts"] };
  const show = views[view] || views.home;
  for (const [key, ids] of Object.entries(views)) ids.forEach(id => { const el = document.getElementById(id); if (el) el.hidden = !show.includes(id); });
  document.querySelectorAll("[data-view]").forEach(btn=>{ const active=btn.dataset.view===view; setClass(btn,"active",active); btn.setAttribute("aria-pressed",String(active)); });
}


function build(b, i) {
  const el = document.createElement("article");
  el.className = "unit";
  el.style.animationDelay = `${i * 90}ms`;
  el.innerHTML = `
    <div class="row">
      <div class="id">
        <div class="name"><span class="led"></span>${esc(b.bot_name)}</div>
        <div class="strat" title="${esc(b.strategy)}">:${b.port} · ${esc(b.strategy)}</div>
        ${b.strategy_created ? `<div class="stratdate" title="策略文件产生日期">📅${esc(b.strategy_created)}</div>` : ""}
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
        <button class="inspect" type="button" aria-expanded="false" aria-label="${esc(b.bot_name)} 详情">详情 ↓</button>
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
    <div class="bot-resources" title="机器人整体资源占用；CPU 100% 表示占满一核，内存单位为 MiB。CPU 为两次采样之间的平均值。"><span>CPU <b data-f="bot-cpu">—</b></span><span>内存 <b data-f="bot-memory">—</b></span></div>
    <div class="drawer" hidden><div class="positions"></div></div>
    <pre class="log" hidden></pre>`;
  $(".inspect", el).onclick = () => expandBot(b.name);
  $(".switch", el).setAttribute("aria-label", `${b.bot_name} 启停`);
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
  openLogs.delete(b.name);
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
  setClass(el, "is-running", running);
  setClass(el, "on", st.unit === "on"); setClass(el, "fault", st.unit === "fault");
  const led = $(".led", el); led.className = "led " + st.led;
  setText($(".tag.state", el), st.text);
  const mode = $(".tag.mode", el); mode.hidden = !has;
  if (up) { setText(mode, b.dry_run ? "模拟盘" : "实盘"); setClass(mode, "live", !b.dry_run); }
  else if (has) { setText(mode, "数据来自数据库"); setClass(mode, "live", false); }
  const upt = $(".tag.up-t", el); upt.hidden = !running; if (running) setText(upt, `${dur(b.uptime_s)}`);
  const rs = $(".tag.rs", el); rs.hidden = !b.restarts_24h; if (b.restarts_24h) setText(rs, `24h 自动重启 ${b.restarts_24h}`);

  const f = k => $(`[data-f="${k}"]`, el);
  setText(f("bot-cpu"), running ? (b.cpu_percent == null ? (b.mem_mb == null ? "不可用" : "采样中") : Number(b.cpu_percent).toFixed(1)+"%") : "已停止");
  setText(f("bot-memory"), running ? (b.mem_mb == null ? "不可用" : Number(b.mem_mb).toFixed(1)+" MiB") : "—");
  setTick(f("open"), has ? b.open_trades : "–");
  setText(f("max"), has && b.max_trades != null ? `/ ${b.max_trades}` : "");
  setText(f("closed"), has ? `已平仓 ${b.closed_trades}` : "");
  // Stopped totals include realised history only; residual positions are not revalued.
  const split = b.valuation_mode === "realized_only";
  setText(f("pnl-lbl"), b.historical_stop_reconstruction && split ? "总盈亏 USDT（历史补算）" : split ? "已实现 USDT（固定）" : "总盈亏 USDT");
  const shown = split ? b.profit_closed : b.profit_all;
  setTick(f("pnl"), has ? num(shown) : "–"); setTone(f("pnl").parentElement, has ? shown : 0);
  setText(f("win"), has ? `${up ? "含浮动" : "已平仓"}${b.closed_trades ? ` · 胜率 ${(b.winrate * 100).toFixed(0)}%` : ""}` : "");
  const ur = f("unreal"); ur.hidden = true;
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
  let text;
  try {
    const response = await fetch(`/api/bots/${name}/log?lines=80`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    text = await response.text();
  } catch { setText(pre, "日志暂时不可用，请稍后重试。"); return; }
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
  try { const response = await fetch("/api/bots"); if (!response.ok) throw new Error(`HTTP ${response.status}`); bots = await response.json(); }
  catch (e) { setText($("#updated"), "读不到控制台后台：" + e); return; }
  const rack = $("#rack");
  bots.forEach((b, i) => {
    let el = units.get(b.name);
    if (!el) { el = build(b, i); units.set(b.name, el); rack.appendChild(el); }
    update(el, b);
    if (openLogs.has(b.name)) renderLog(b.name, el);
  });
  for (const [name, el] of units) if (!bots.some(b => b.name === name)) { el.remove(); units.delete(name); }

  fleet = bots; layoutFleet();
  try { if (typeof refreshLoops === "function") await refreshLoops(); } catch (e) { console.warn("loops", e); }
  const up = bots.filter(b => b.api === "up");
  const known = bots.filter(b => b.api === "up" || b.offline);
  const pnl = known.reduce((s, b) => s + (b.profit_all || 0), 0);
  setTick($("#s-run .tk"), bots.filter(b => b.pid).length); setText($("#s-run small"), `/ ${bots.length}`);
  setTick($("#s-open .tk"), known.reduce((s, b) => s + (b.open_trades || 0), 0));
  setTick($("#s-pnl .tk"), num(pnl)); setTone($("#s-pnl"), pnl);
  setText($("#updated"), "每 5 秒刷新 · 上次 " + new Date().toLocaleTimeString("zh-CN", { hour12: false }));
}


/* ---------- 24h loops card ---------- */
async function loopAct(name, action) {
  if (action === "stop" && !confirm(`暂停 ${name}？进行中的回测跑完当前窗口后停，队列保留。`)) return;
  try {
    const r = await fetch(`/api/loops/${encodeURIComponent(name)}/${action}`, { method: "POST" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    await refreshLoops();
  } catch (e) { alert(`操作未完成：${e.message}。请刷新后确认调度状态。`); }
}
const loopsList = $("#loops-list");
if (loopsList) loopsList.addEventListener("click", async e => {
  const button = e.target.closest("button[data-loop-action]");
  if (!button || button.disabled) return;
  button.disabled = true;
  try { await loopAct(button.dataset.loopName, button.dataset.loopAction); }
  finally { button.disabled = false; }
});
function researchSwitch(l, label, enabled, action, note) {
  return `<div class="research-control"><div><strong>${esc(label)}</strong><small>${esc(note)}</small></div><button type="button" class="hypothesis-switch" role="switch" aria-checked="${enabled ? "true" : "false"}" aria-label="${esc(l.title)}：${esc(label)}" data-loop-name="${esc(l.name)}" data-loop-action="${action}"><span class="switch-track" aria-hidden="true"><i></i></span><span>${enabled ? "开启" : "关闭"}</span></button></div>`;
}
function renderResearchTask(l) {
  if (!l.active) return "";
  const task = l.current_task || {};
  const phases = {starting:"准备执行",preparing:"准备候选",advising:"生成参数建议",hypothesizing:"生成研究假设",proposing:"生成策略代码",validating:"校验策略代码",backtesting:"执行回测",evaluating:"评估回测结果",rollover:"研究轮次切换"};
  const windows = ["20210101-20211231","20220101-20221231","20230101-20251231","20210101-20251231"];
  const windowIndex = windows.indexOf(task.window);
  const start = l.task_started_at ? new Date(l.task_started_at*1000).toLocaleTimeString("zh-CN",{hour12:false}) : "—";
  const elapsed = task.phase_started_at ? dur(Math.max(0,Date.now()/1000-task.phase_started_at)) : "—";
  const range = task.window ? task.window.split("-").map(d => `${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6,8)}`).join(" → ") : "";
  const params = task.parameters ? Object.entries(task.parameters).map(([k,v]) => `${k}=${JSON.stringify(v)}`).join(" · ") : "";
  return `<div class="research-task" aria-label="正在执行的任务"><div class="task-heading"><strong>${esc(phases[task.phase] || "运行中，等待阶段上报")}</strong><span>本阶段 ${esc(elapsed)}</span></div><p>${task.item ? `候选 <b>${esc(task.item)}</b>` : ""}${task.strategy ? ` · ${esc(task.strategy)}` : ""}</p>${range ? `<p>窗口 ${windowIndex >= 0 ? windowIndex+1 : "—"} / 4 · ${esc(range)}</p>` : ""}${params ? `<details><summary>当前候选参数</summary><p>${esc(params)}</p></details>` : ""}<small>任务开始 ${esc(start)}</small></div>`;
}
function renderResearchLoop(l) {
  const strategy = l.name === "strat_loop";
  const status = { open: "持续研究", dead: "研究已终止", paused: "已暂停", done: "已完成" }[l.status] || l.status || "—";
  const state = l.error ? "状态异常" : l.active ? "执行中" : !l.enabled ? "任务停用" : l.scheduler_enabled === false ? "门控暂停" : "等待调度";
  const direction = l.hypothesis || (strategy ? "等待下一轮研究假设" : "围绕现有策略调整参数，通过回测筛选候选配置。");
  const stats = l.error ? `<p class="loop-error">${esc(l.error)}</p>` : `<div class="research-counts"><span><b>${esc(l.passed ?? 0)}</b> / ${esc(l.total ?? 0)} 通过</span><span><b>${esc(l.pending ?? 0)}</b> 待回测</span><span>第 <b>${esc(l.round ?? 1)}</b> 轮</span></div>`;
  const schedule = researchSwitch(l, "参与研究", l.enabled, l.enabled ? "stop" : "start", "启用后由统一门控分配并发");
  const hypothesis = l.hypothesis_supported ? researchSwitch(l, "参与假设", l.hyp_enabled, l.hyp_enabled ? "hyp_off" : "hyp_on", "关闭后继续验证已有候选，不再生成新策略") : "";
  return `<article class="loop-card ${strategy ? "strategy-research" : "parameter-research"}" data-loop-card="${esc(l.name)}"><div class="loop-top"><div class="loop-title"><span class="research-kind">${strategy ? "策略探索" : "参数优化"}</span><h3>${esc(l.title)}</h3></div><span class="loop-state ${l.error ? "fault" : l.enabled ? "enabled" : ""}">${state}</span></div>${stats}${renderResearchTask(l)}<div class="research-direction"><span>${strategy ? "当前研究假设" : "研究方式"}</span><p>${esc(direction)}</p></div><div class="loop-candidate"><span>当前选中候选</span><strong>${esc(l.champion || "尚未选出")}</strong></div><div class="research-controls">${schedule}${hypothesis}</div><details class="loop-details"><summary>运行记录 <span>${esc(l.name)}</span></summary><p>队列状态：${esc(status)} · 补充候选 ${esc(l.refills ?? 0)} 次</p>${l.hypothesis ? `<p>${esc(l.hypothesis)}</p>` : ""}<p>${esc(l.last || "暂无运行记录")}</p></details></article>`;
}
async function refreshResearchGate() {
  const root = $("#research-gate");
  if (!root) return;
  try {
    const r = await fetch("/api/research-scheduler");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const g = await r.json();
    const button = root.querySelector("[role=switch]");
    button.setAttribute("aria-checked", String(g.enabled));
    button.dataset.enabled = String(g.enabled);
    button.disabled = false;
    setText(root.querySelector(".gate-state"), g.enabled ? "接收新任务" : "暂停接收新任务");
    const select = root.querySelector("select");
    if (document.activeElement !== select) select.value = String(g.max_concurrent);
    setText(root.querySelector(".gate-capacity"), `${g.active.length} / ${g.max_concurrent} 执行中`);
    setText(root.querySelector(".gate-active"), g.active.length ? "当前任务：" + g.active.map(a => a.name).join("、") : "当前没有任务执行");
  } catch (e) { setText(root.querySelector(".gate-state"), "门控状态读取失败"); }
}
async function updateResearchGate(payload) {
  const r = await fetch("/api/research-scheduler", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload) });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  await refreshLoops();
}
const gateRoot = $("#research-gate");
if (gateRoot) {
  gateRoot.addEventListener("click", async e => {
    const button = e.target.closest("button[role=switch]");
    if (!button || button.disabled) return;
    button.disabled = true;
    try { await updateResearchGate({enabled:button.dataset.enabled !== "true"}); }
    catch (e) { alert(`门控操作未完成：${e.message}`); }
    finally { button.disabled = false; }
  });
  gateRoot.querySelector("select").addEventListener("change", async e => {
    e.target.disabled = true;
    try { await updateResearchGate({max_concurrent:Number(e.target.value)}); }
    catch (error) { alert(`并发设置未完成：${error.message}`); await refreshResearchGate(); }
    finally { e.target.disabled = false; }
  });
}
async function refreshLoops() {
  const list = $("#loops-list");
  if (!list) return;
  await refreshResearchGate();
  let loops;
  try { const r = await fetch("/api/loops"); if (!r.ok) throw 0; loops = await r.json(); }
  catch (e) { setText($("#loops-updated"), "连接失败 · 状态可能已过期"); return; }
  const expanded = new Set([...list.querySelectorAll(".loop-card")].filter(c => c.querySelector("details")?.open).map(c => c.dataset.loopCard));
  list.innerHTML = loops.map(renderResearchLoop).join("") || '<div class="loops-notice">暂无研究循环</div>';
  list.querySelectorAll(".loop-card").forEach(c => { if (expanded.has(c.dataset.loopCard)) c.querySelector("details").open = true; });
  setText($("#loops-updated"), "更新于 " + new Date().toLocaleTimeString("zh-CN", { hour12: false }));
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

document.querySelectorAll("[data-view]").forEach(btn=>btn.onclick=()=>selectView(btn.dataset.view));
document.querySelectorAll("[data-filter]").forEach(btn=>btn.onclick=()=>{
  fleetFilter=btn.dataset.filter;
  document.querySelectorAll("[data-filter]").forEach(other=>{ const active=other===btn;setClass(other,"active",active);other.setAttribute("aria-pressed",String(active)); });
  layoutFleet();
});
$("#bot-search").oninput=layoutFleet;
$("#bot-sort").onchange=layoutFleet;
$("#reset-filters").onclick=()=>{$("#bot-search").value="";$("[data-filter=all]").click();};
tick(); setInterval(tick, 1000);
refresh(); setInterval(refresh, 5000);
refreshNet(); setInterval(refreshNet, 30000);

/* ---------- judgement page: criteria are static text, only the numbers move ---------- */
const vCards = new Map();
function vBuild(e) {
  const el = document.createElement("article");
  el.className = "vcard";
  const cps = (e.checkpoints || []).map((c, i) => `<div data-f="cp${i}"></div>`).join("");
  el.innerHTML = `
    <div class="vt">${esc(e.title)}</div>
    <div class="tags"><span class="tag state" data-f="status"></span></div>
    ${e.protected ? `<div class="prot">${esc(e.protected)}</div>` : ""}
    <div class="written">${esc(e.written || "")}</div>
    ${(e.bots || []).length ? `<div class="vbots" data-f="bots"></div>` : ""}
    <div class="vcp">${cps}</div>
    ${e.target ? `<div><div class="vcp"><div data-f="prog"></div></div><div class="vbar"><i data-f="bar"></i></div></div>` : ""}
    <ul class="vlist">${(e.criteria || []).map(c => `<li>${esc(c)}</li>`).join("")}</ul>
    <div class="vref">${esc(e.reference || "")}</div>`;
  return el;
}
function vUpdate(el, e) {
  const f = k => $(`[data-f="${k}"]`, el);
  const st = f("status"); setText(st, e.status);
  st.className = "tag " + (e.tone === "paused" || e.tone === "research" ? "paused" : e.tone === "reference" ? "state" : "state ok");
  const bots = f("bots");
  if (bots) {
    const html = (e.live || []).map(b => {
      const pnl = b.profit_all;
      return `<div class="vbot"><span><span class="n">${esc(b.name)}</span> · ${b.running ? (b.dry_run === false ? "实盘运行中" : "模拟盘运行中") : "已停止"}</span>` +
        `<span>持仓 ${b.open ?? "–"} · 已平仓 ${b.closed ?? "–"} · <span class="${tone(pnl)}">${num(pnl)} USDT</span></span></div>`;
    }).join("");
    if (bots.dataset.h !== html) { bots.innerHTML = html; bots.dataset.h = html; }
  }
  (e.checkpoints || []).forEach((c, i) => {
    const left = c.days_left;
    setText(f(`cp${i}`), c.date ? `${c.date} · ${c.what}  ${left > 0 ? `还有 ${left} 天` : left === 0 ? "今天" : `已过 ${-left} 天`}` : `${c.what}`);
  });
  if (e.target) {
    const have = e.target.have ?? 0;
    setText(f("prog"), `${e.target.label} ${e.target.have ?? "–"} / ${e.target.closed}`);
    f("bar").style.width = `${Math.min(100, have / e.target.closed * 100)}%`;
  }
}
async function refreshVerdicts() {
  let d;
  try { d = await (await fetch("/api/verdicts")).json(); } catch { return; }
  setText($("#v-intro"), d.intro || "");
  const grid = $("#v-grid");
  for (const e of d.entries || []) {
    let el = vCards.get(e.id);
    if (!el) { el = vBuild(e); vCards.set(e.id, el); grid.appendChild(el); }
    vUpdate(el, e);
  }
  const list = $("#v-closed-list");
  const html = (d.closed || []).map(c => `<li><b>${esc(c.name)}</b> · ${esc(c.why)}</li>`).join("");
  if (list.dataset.h !== html) { list.innerHTML = html; list.dataset.h = html; }
}
refreshVerdicts(); setInterval(refreshVerdicts, 60000);

/* ---------- reload the page when the panel's UI code changes ---------- */
const uiVersion = document.querySelector('meta[name="ui-version"]')?.content;
setInterval(async () => {
  try {
    const { v } = await (await fetch("/api/ui-version", { cache: "no-store" })).json();
    if (uiVersion && v && v !== uiVersion) location.reload();
  } catch {}
}, 30000);

/* Server metrics: keep the last sample visible and mark failed refreshes. */
const gib = bytes => (bytes / 1024 ** 3).toFixed(1) + " GiB";
let resourcePending = false;
async function refreshResources() {
  if (resourcePending) return;
  resourcePending = true;
  try {
    const response = await fetch("/api/resources", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    for (const [key, value] of [["cpu",data.cpu], ["memory",data.memory], ["disk",data.disk]]) {
      setText($("#r-"+key), value ? value.percent.toFixed(1)+"%" : "不可用");
      const bar = $("#r-"+key+"-bar");
      bar.style.width = value ? `${Math.max(0,Math.min(100,value.percent))}%` : "0%";
      setClass(bar,"warn",value && value.percent >= 75 && value.percent < 90);
      setClass(bar,"hot",value && value.percent >= 90);
    }
    setText($("#r-cores"), `${data.cpu.cores ?? "—"} 核`);
    setText($("#r-load"), "负载 1/5/15m · "+data.cpu.load.map(n=>n.toFixed(2)).join(" / "));
    setText($("#r-memory-size"), `${gib(data.memory.used)} / ${gib(data.memory.total)}`);
    setText($("#r-disk-size"), data.disk ? `${gib(data.disk.used)} / ${gib(data.disk.total)} · 可用 ${gib(data.disk.free)}` : "数据盘读取失败");
    setText($("#r-uptime"), dur(data.uptime_s));
    setText($("#r-swap"), `Swap ${gib(data.swap.used)} / ${gib(data.swap.total)}`);
    setText($("#resource-updated"), "采样 "+new Date(data.sampled_at).toLocaleTimeString("zh-CN",{hour12:false}));
    setClass($("#resource-updated"),"neg",false);
  } catch {
    setText($("#resource-updated"), "更新失败 · 数据可能已过期");
    setClass($("#resource-updated"),"neg",true);
  } finally { resourcePending = false; }
}
refreshResources(); setInterval(refreshResources, 10000);
