"use strict";
const $ = id => document.getElementById(id);
const LS = {
  get(k, d) { try { const v = localStorage.getItem("hzc_" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("hzc_" + k, JSON.stringify(v)); } catch { } },
};
const TFS = [["1m", "1m"], ["5m", "5m"], ["15m", "15m"], ["1h", "1h"], ["4h", "4h"], ["1d", "1D"]];
const TF_S = { "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400 };
const STRATS = ["IA forte", "Rompimento falso (suporte)", "Rompimento falso (resistência)", "Exaustão verde"];
const S = {
  symbols: [], ana: {}, pre: {}, px: {}, sigs: [], stats: [], news: [],
  sel: LS.get("sel", "BTCUSDT"), tf: LS.get("tf", "5m"), som: LS.get("som", false), magnet: LS.get("magnet", false),
  inds: LS.get("inds", { main: ["EMA"], sub: ["VOL"] }), nNew: 0, tool: "", tab: null,
  filt: LS.get("filt", { fortes: false, minForca: 0, strats: STRATS, syms: null }),
  alerts: LS.get("alerts", {}),
};
const short = s => s.replace("USDT", "");
const prec = v => v >= 100 ? 2 : v >= 1 ? 4 : 5;
const fmt = v => v == null || isNaN(v) ? "—" : (+v).toFixed(prec(+v));
const pct = (v, d = 1) => v == null || isNaN(v) ? "—" : (v * 100).toFixed(d) + "%";
const hhmm = t => new Date(t * 1000).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
const ddhh = t => new Date(t * 1000).toLocaleString("pt-BR", { weekday: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const abbrev = e => e.startsWith("IA") ? "IA" : e.startsWith("Romp") ? "RF" : e.startsWith("Exa") ? "EXV" : e;

if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => { });

// ============================================================= gráfico (KLineChart)
const chart = klinecharts.init("chart", { locale: "en-US" });
chart.setTimezone("America/Sao_Paulo");
chart.setStyles("dark");
chart.setStyles({
  grid: { horizontal: { color: "rgba(255,255,255,.04)" }, vertical: { color: "rgba(255,255,255,.04)" } },
  candle: {
    bar: { upColor: "#16c784", downColor: "#ea3943", noChangeColor: "#8a93a8", upBorderColor: "#16c784", downBorderColor: "#ea3943",
      upWickColor: "#16c784", downWickColor: "#ea3943" },
    priceMark: { last: { upColor: "#16c784", downColor: "#ea3943" }, high: { color: "#8a93a8" }, low: { color: "#8a93a8" } },
    tooltip: { text: { color: "#c9cfdb" } },
  },
  xAxis: { axisLine: { color: "rgba(255,255,255,.1)" }, tickText: { color: "#8a93a8" } },
  yAxis: { axisLine: { color: "rgba(255,255,255,.1)" }, tickText: { color: "#8a93a8" } },
  separator: { color: "rgba(255,255,255,.1)" },
  crosshair: { horizontal: { text: { backgroundColor: "#7c5cff" } }, vertical: { text: { backgroundColor: "#7c5cff" } } },
  overlay: { line: { color: "#22d3ee" }, point: { color: "#22d3ee", borderColor: "rgba(34,211,238,.35)", activeColor: "#22d3ee" },
    text: { color: "#e6e9f0" }, rectText: { backgroundColor: "#7c5cff" } },
});
window.addEventListener("resize", () => chart.resize());

let kws = null, loadSeq = 0, oldest = null;
const kurl = (sym, tf, end) => `https://api.binance.com/api/v3/klines?symbol=${sym}&interval=${tf}&limit=1000${end ? "&endTime=" + end : ""}`;
const toK = r => ({ timestamp: r[0], open: +r[1], high: +r[2], low: +r[3], close: +r[4], volume: +r[5] });

async function loadChart(scrollTo) {
  const seq = ++loadSeq, sym = S.sel, tf = S.tf;
  const rows = await (await fetch(kurl(sym, tf))).json();
  if (seq !== loadSeq) return;
  const data = rows.map(toK);
  oldest = data[0]?.timestamp;
  const p = prec(data.at(-1).close);
  chart.setPriceVolumePrecision(p, 2);
  chart.applyNewData(data, true);
  chart.setOffsetRightDistance(rightPad());
  // websocket direto da Binance para o par/timeframe do gráfico
  if (kws) { kws.onclose = null; kws.close(); }
  kws = new WebSocket(`wss://stream.binance.com:443/ws/${sym.toLowerCase()}@kline_${tf}`);
  kws.onmessage = ev => { const k = JSON.parse(ev.data).k; chart.updateData({ timestamp: k.t, open: +k.o, high: +k.h, low: +k.l, close: +k.c, volume: +k.v }); };
  kws.onclose = () => setTimeout(() => seq === loadSeq && loadChart(), 3000);
  drawLevels(); drawSignals(); restoreDrawings(); drawAlerts();
  if (scrollTo) setTimeout(() => centerOn(scrollTo * 1000), 250);
}

// centraliza uma vela (timestamp ms) no meio da área visível
// área livre do gráfico: desconta a gaveta aberta à direita
const rightPad = () => $("drawer").classList.contains("open") && innerWidth > 760 ? $("drawer").offsetWidth + 70 : 80;
const barX = ts => chart.convertToPixel({ timestamp: ts }, { paneId: "candle_pane" }).x;

// centraliza uma vela (timestamp ms) no meio da área visível livre
function centerOn(ts) {
  const list = chart.getDataList();
  let i = list.findIndex(k => k.timestamp >= ts);
  if (i < 0) i = list.length - 1;
  ts = list[i].timestamp;
  const w = $("chart").clientWidth, bs = chart.getBarSpace() || 8, pad = rightPad();
  const target = (w - pad) / 2;
  const after = (list.length - 1 - i) * bs;           // pixels de velas depois do sinal
  const need = w - target - after;                    // espaço à direita que falta
  if (need > pad) {                                   // sinal recente: abre espaço vazio à direita
    chart.setOffsetRightDistance(need);
    chart.scrollToRealTime(0);
    return;
  }
  chart.setOffsetRightDistance(pad);
  chart.scrollToDataIndex(i, 0);
  let d = target - barX(ts);
  chart.scrollByDistance(d, 0);
  if (Math.abs(target - barX(ts)) > Math.abs(d)) chart.scrollByDistance(-2 * d, 0);  // sentido invertido nesta versão
}
$("btnNow").onclick = () => { chart.setOffsetRightDistance(rightPad()); chart.scrollToRealTime(300); };
$("btnFit").onclick = () => { chart.setBarSpace(8); chart.setOffsetRightDistance(rightPad()); chart.scrollToRealTime(200); };
chart.setLoadDataCallback(async ({ type, data, callback }) => {
  if (type !== "forward" || !data) return callback([], false);
  const rows = await (await fetch(kurl(S.sel, S.tf, data.timestamp - 1))).json();
  callback(rows.map(toK), rows.length === 1000);
});

// níveis S/R 24h e sinais da IA (travados, grupos próprios)
function drawLevels() {
  chart.removeOverlay({ groupId: "sr" });
  const a = S.ana[S.sel]; if (!a) return;
  for (const [v, c, txt] of [[a.res24, "#ea3943", "Res 24h"], [a.sup24, "#16c784", "Sup 24h"]])
    chart.createOverlay({ name: "horizontalStraightLine", groupId: "sr", lock: true, points: [{ value: v }],
      styles: { line: { color: c + "aa", style: "dashed", dashedValue: [6, 4] } }, extendData: txt });
}
function drawSignals() {
  chart.removeOverlay({ groupId: "sig" });
  for (const s of S.sigs.filter(x => x.sym === S.sel && passFilter(x))) {
    const c = s.dir > 0 ? "#16c784" : "#ea3943";
    chart.createOverlay({ name: "simpleAnnotation", groupId: "sig", lock: true,
      points: [{ timestamp: s.t * 1000, value: s.entrada }],
      extendData: `${s.dir > 0 ? "▲" : "▼"} ${abbrev(s.estrategia)}${s.resultado ? " · " + s.resultado : ""}`,
      styles: { line: { color: c }, polygon: { color: c }, text: { backgroundColor: c, color: "#fff" } } });
    if (S.tf === "1m" || S.tf === "5m")
      chart.createOverlay({ name: "segment", groupId: "sig", lock: true,
        points: [{ timestamp: s.t * 1000, value: s.entrada }, { timestamp: (s.expira - 300) * 1000, value: s.saida ?? s.entrada }],
        styles: { line: { color: s.resultado === "LOSS" ? "#ea3943" : s.resultado === "WIN" ? "#16c784" : "#f5b301", style: "dashed", dashedValue: [3, 3] } } });
  }
}

// ---------------------------------------------- desenhos do usuário (salvos por ativo)
const drawKey = () => "draw_" + S.sel;
let userIds = new Set();
function saveDrawings() {
  const list = [...userIds].map(id => chart.getOverlayById(id)).filter(Boolean)
    .map(o => ({ name: o.name, points: o.points.map(p => ({ timestamp: p.timestamp, value: p.value })), extendData: o.extendData }));
  LS.set(drawKey(), list);
}
const userHandlers = {
  onDrawEnd: e => { userIds.add(e.overlay.id); saveDrawings(); setTool(""); return false; },
  onPressedMoveEnd: () => { saveDrawings(); return false; },
  onRemoved: e => { userIds.delete(e.overlay.id); saveDrawings(); return false; },
};
function restoreDrawings() {
  chart.removeOverlay({ groupId: "user" }); userIds = new Set();
  for (const d of LS.get(drawKey(), [])) {
    const id = chart.createOverlay({ ...d, groupId: "user", mode: S.magnet ? "weak_magnet" : "normal", ...userHandlers });
    if (id) userIds.add(id);
  }
}
function setTool(name) {
  S.tool = name;
  document.querySelectorAll("[data-tool]").forEach(b => b.classList.toggle("on", b.dataset.tool === name));
  if (!name) return;
  if (name === "alert") {
    chart.createOverlay({ name: "horizontalStraightLine", groupId: "alert", styles: { line: { color: "#f5b301", style: "dashed", dashedValue: [2, 3] } },
      onDrawEnd: e => { addAlert(e.overlay.points[0].value); chart.removeOverlay(e.overlay.id); setTool(""); return false; } });
    return;
  }
  const opts = { name, groupId: "user", mode: S.magnet ? "weak_magnet" : "normal", ...userHandlers };
  if (name === "simpleAnnotation") {
    const txt = prompt("Texto da anotação:"); if (!txt) return setTool("");
    opts.extendData = txt;
  }
  chart.createOverlay(opts);
}
document.querySelectorAll("[data-tool]").forEach(b => b.onclick = () => setTool(b.dataset.tool));
$("magnet").onclick = () => { S.magnet = !S.magnet; LS.set("magnet", S.magnet); $("magnet").classList.toggle("on", S.magnet); };
$("magnet").classList.toggle("on", S.magnet);
$("erase").onclick = () => { if (confirm(`Apagar todos os desenhos de ${short(S.sel)}?`)) { chart.removeOverlay({ groupId: "user" }); userIds.clear(); LS.set(drawKey(), []); } };
$("toolsToggle").onclick = () => $("tools").classList.toggle("closed");
if (innerWidth <= 760) $("tools").classList.add("closed");
document.addEventListener("keydown", e => { if (e.key === "Escape") setTool(""); });

// ---------------------------------------------- alertas de preço (no navegador)
function addAlert(v) {
  (S.alerts[S.sel] ||= []).push({ v, dir: (S.px[S.sel] ?? v) < v ? 1 : -1 });
  LS.set("alerts", S.alerts); drawAlerts();
  toast({ cls: "", big: "⏰", t1: `Alerta em ${fmt(v)}`, t2: `${short(S.sel)} — aviso quando o preço tocar` });
}
function drawAlerts() {
  chart.removeOverlay({ groupId: "alert" });
  (S.alerts[S.sel] || []).forEach((a, i) => chart.createOverlay({ name: "horizontalStraightLine", groupId: "alert", lock: false,
    points: [{ value: a.v }], styles: { line: { color: "#f5b301", style: "dashed", dashedValue: [2, 3] } },
    onRemoved: () => { S.alerts[S.sel].splice(i, 1); LS.set("alerts", S.alerts); return false; } }));
}
function checkAlerts(sym, px) {
  const list = S.alerts[sym]; if (!list?.length) return;
  const hit = list.filter(a => (a.dir > 0 && px >= a.v) || (a.dir < 0 && px <= a.v));
  if (!hit.length) return;
  S.alerts[sym] = list.filter(a => !hit.includes(a)); LS.set("alerts", S.alerts);
  for (const a of hit) {
    beep(a.dir > 0, true); notify(`⏰ ${short(sym)} tocou ${fmt(a.v)}`, `Preço atual ${fmt(px)}`);
    toast({ cls: "", big: "⏰", t1: `${short(sym)} tocou ${fmt(a.v)}`, t2: `preço ${fmt(px)}`, go: () => select(sym) });
  }
  if (sym === S.sel) drawAlerts();
}

// ---------------------------------------------- indicadores
const MAIN_INDS = [["EMA", "Médias EMA"], ["MA", "Médias simples"], ["BOLL", "Bandas de Bollinger"], ["SAR", "Parabolic SAR"]];
const SUB_INDS = [["VOL", "Volume"], ["RSI", "RSI"], ["MACD", "MACD"], ["KDJ", "Estocástico (KDJ)"], ["ATR", "ATR"]];
const subPanes = {};
function applyInds() {
  for (const [n] of MAIN_INDS) chart.removeIndicator("candle_pane", n);
  for (const n of S.inds.main) chart.createIndicator(n, true, { id: "candle_pane" });
  for (const [n] of SUB_INDS) {
    const on = S.inds.sub.includes(n);
    if (on && !subPanes[n]) subPanes[n] = chart.createIndicator(n, false, { height: 90 });
    if (!on && subPanes[n]) { chart.removeIndicator(subPanes[n], n); delete subPanes[n]; }
  }
}
$("btnInd").onclick = e => {
  openMenu(e.currentTarget, `<div class="mh">No gráfico</div>` + MAIN_INDS.map(([n, l]) =>
    `<button data-im="${n}" class="${S.inds.main.includes(n) ? "on" : ""}">${l}</button>`).join("") +
    `<div class="mh">Painéis</div>` + SUB_INDS.map(([n, l]) =>
    `<button data-is="${n}" class="${S.inds.sub.includes(n) ? "on" : ""}">${l}</button>`).join(""), m => {
    m.onclick = ev => {
      const b = ev.target.closest("button"); if (!b) return;
      const [k, n] = b.dataset.im ? ["main", b.dataset.im] : ["sub", b.dataset.is];
      S.inds[k] = S.inds[k].includes(n) ? S.inds[k].filter(x => x !== n) : [...S.inds[k], n];
      b.classList.toggle("on"); LS.set("inds", S.inds); applyInds();
    };
  });
};

// ---------------------------------------------- menus
function openMenu(anchor, html, bind) {
  const m = $("menu"); m.innerHTML = html; m.hidden = false;
  const r = anchor.getBoundingClientRect();
  m.style.top = r.bottom + 6 + "px"; m.style.left = Math.min(r.left, innerWidth - 230) + "px";
  bind?.(m);
  setTimeout(() => document.addEventListener("click", function off(ev) {
    if (!m.contains(ev.target)) { m.hidden = true; document.removeEventListener("click", off); }
  }), 0);
}
$("symBtn").onclick = e => openMenu(e.currentTarget, S.symbols.map(s => {
  const a = S.ana[s] || {};
  return `<button data-s="${s}"><b>${short(s)}</b><span class="num mut">${fmt(S.px[s] ?? a.preco)}</span>
    <span class="${a.dir > 0 ? "up" : "down"}" style="margin-left:auto">${a.dir > 0 ? "▲" : "▼"} ${pct(a.forca, 0)}</span></button>`;
}).join(""), m => m.onclick = ev => { const b = ev.target.closest("button"); if (b) { m.hidden = true; select(b.dataset.s); } });

// ---------------------------------------------- timeframes
$("tfs").innerHTML = TFS.map(([v, l]) => `<button class="pill" data-tf="${v}">${l}</button>`).join("");
$("tfSel").innerHTML = TFS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");
function paintTf() {
  document.querySelectorAll("[data-tf]").forEach(b => b.classList.toggle("on", b.dataset.tf === S.tf));
  $("tfSel").value = S.tf;
}
function setTf(tf, scrollTo) { S.tf = tf; LS.set("tf", tf); paintTf(); loadChart(scrollTo); }
$("tfs").onclick = e => { const b = e.target.closest("[data-tf]"); if (b) setTf(b.dataset.tf); };
$("tfSel").onchange = e => setTf(e.target.value);

// ============================================================= seleção / cartão IA / fita
function select(sym, scrollTo) {
  S.sel = sym; LS.set("sel", sym);
  renderTicker(); renderAI();
  $("symTitle").textContent = short(sym) + "/USDT";
  loadChart(scrollTo);
}
function renderTicker() {
  $("ticker").innerHTML = S.symbols.map(sym => {
    const a = S.ana[sym] || {}, f = a.forca ?? 0, up = a.dir > 0, w = Math.min(f / .3, 1) * 50;
    const open = S.sigs.find(s => s.sym === sym && !s.resultado && passFilter(s));
    return `<div class="chip ${sym === S.sel ? "sel" : ""}" data-sym="${sym}">
      ${S.pre[sym] ? `<i class="pre" title="${esc(S.pre[sym].tipo)}"></i>` : ""}<b>${short(sym)}</b>
      <span class="num">${fmt(S.px[sym] ?? a.preco)}</span>
      <span class="mini"><i style="left:${up ? 50 : 50 - w}%;width:${w}%;background:${up ? "var(--up)" : "var(--down)"}"></i></span>
      <span class="num ${f >= .2 ? (up ? "up" : "down") : "mut"}" style="font-size:12px">${pct(f, 0)}</span>
      ${open ? `<span class="sig ${open.dir > 0 ? "up" : "down"}">${open.dir > 0 ? "CALL" : "PUT"}</span>` : ""}</div>`;
  }).join("");
}
$("ticker").onclick = e => { const c = e.target.closest(".chip"); if (c) select(c.dataset.sym); };

function renderAI() {
  const a = S.ana[S.sel] || {}, up = a.dir > 0, f = a.forca ?? 0;
  const col = f < .15 ? "var(--mut)" : up ? "var(--up)" : "var(--down)";
  $("ring").style.setProperty("--p", Math.min(f / .3, 1) * 100);
  $("ring").style.setProperty("--c", col);
  $("ringTxt").textContent = pct(f, 0);
  $("aiDir").innerHTML = a.dir ? `<span style="color:${col}">${up ? "▲ CALL" : "▼ PUT"}</span> <span class="mut" style="font-size:13px;font-weight:600">${short(S.sel)}</span>` : "—";
  $("aiSub").textContent = a.t ? `IA · 15 min · vela ${hhmm(a.t)} · ${f >= .2 ? "FORTE" : f >= .15 ? "moderada" : "fraca — sem sinal"}` : "Leitura IA · próximos 15 min";
  $("aiWr").innerHTML = a.wr_hist == null ? "—" : `<span class="${a.wr_hist >= .58 ? "up" : a.wr_hist >= .549 ? "warn" : "down"}">${pct(a.wr_hist)}</span>`;
  $("aiP").textContent = pct(a.p_alta);
  const px = S.px[S.sel] ?? a.preco;
  $("aiSup").innerHTML = a.sup24 ? `${fmt(a.sup24)} <span class="mut" style="font-size:11px">${((px - a.sup24) / a.atr).toFixed(1)} ATR</span>` : "—";
  $("aiRes").innerHTML = a.res24 ? `${fmt(a.res24)} <span class="mut" style="font-size:11px">${((a.res24 - px) / a.atr).toFixed(1)} ATR</span>` : "—";
  const mx = Math.max(...(a.motivos || []).map(m => m.peso), .001);
  $("aiWhy").innerHTML = (a.motivos || []).map(m => `<li><span class="w" style="width:${8 + 40 * m.peso / mx}px"></span>${esc(m.txt)}</li>`).join("")
    || `<li class="mut">—</li>`;
  $("aiVeto").hidden = !a.vetos;
  $("symPx").textContent = fmt(px);
}
// cartão IA: arrastar pelo cabeçalho, redimensionar pelo canto; posição/tamanho salvos
(() => {
  const el = $("ai"), head = el.querySelector(".ai-head");
  const clamp = () => {
    const r = el.getBoundingClientRect();
    const x = Math.min(Math.max(r.left, 0), innerWidth - Math.min(r.width, innerWidth));
    const y = Math.min(Math.max(r.top, 0), innerHeight - Math.min(r.height, 60));
    Object.assign(el.style, { left: x + "px", top: y + "px", bottom: "auto", right: "auto" });
  };
  const pos = LS.get("aipos", null);
  if (pos && innerWidth > 760) { Object.assign(el.style, { left: pos.x + "px", top: pos.y + "px", bottom: "auto", width: pos.w + "px", height: pos.h ? pos.h + "px" : "" }); requestAnimationFrame(clamp); }
  const save = () => { const r = el.getBoundingClientRect(); LS.set("aipos", { x: r.left, y: r.top, w: r.width, h: el.classList.contains("min") ? null : r.height }); };
  head.addEventListener("pointerdown", e => {
    if (e.target.closest("button") || innerWidth <= 760) return;
    const r = el.getBoundingClientRect(), dx = e.clientX - r.left, dy = e.clientY - r.top;
    head.setPointerCapture(e.pointerId);
    const move = ev => Object.assign(el.style, { left: ev.clientX - dx + "px", top: ev.clientY - dy + "px", bottom: "auto", right: "auto" });
    const up = () => { head.removeEventListener("pointermove", move); head.removeEventListener("pointerup", up); clamp(); save(); };
    head.addEventListener("pointermove", move); head.addEventListener("pointerup", up);
  });
  const grip = $("aiGrip");
  grip.addEventListener("pointerdown", e => {
    e.preventDefault(); e.stopPropagation();
    const r = el.getBoundingClientRect(), x0 = e.clientX, y0 = e.clientY;
    Object.assign(el.style, { left: r.left + "px", top: r.top + "px", bottom: "auto" });
    grip.setPointerCapture(e.pointerId);
    const move = ev => Object.assign(el.style, { width: Math.max(260, r.width + ev.clientX - x0) + "px", height: Math.max(90, r.height + ev.clientY - y0) + "px" });
    const up = () => { grip.removeEventListener("pointermove", move); grip.removeEventListener("pointerup", up); save(); };
    grip.addEventListener("pointermove", move); grip.addEventListener("pointerup", up);
  });
  let t; new ResizeObserver(() => { clearTimeout(t); t = setTimeout(save, 300); }).observe(el);
  window.addEventListener("resize", () => innerWidth > 760 && clamp());
})();
$("aiTog").onclick = () => { $("ai").classList.toggle("min"); LS.set("aimin", $("ai").classList.contains("min")); };
if (LS.get("aimin", innerWidth <= 760)) $("ai").classList.add("min");

// ============================================================= filtros (modo só fortes)
function passFilter(s) {
  const F = S.filt;
  if (F.fortes && (s.forca ?? 0) < .2 && !String(s.estrategia).startsWith("Exa")) return false;
  if ((s.forca ?? 0) < F.minForca && !String(s.estrategia).startsWith("Exa")) return false;
  if (!F.strats.includes(s.estrategia)) return false;
  if (F.syms && !F.syms.includes(s.sym)) return false;
  return true;
}
function renderFiltros() {
  const F = S.filt, syms = F.syms || S.symbols;
  $("pane-filtros").innerHTML = `<div class="sec-h">Modo</div><div class="frm">
    <label class="chk"><input type="checkbox" id="fFortes" ${F.fortes ? "checked" : ""}> <span><b>Só sinais fortes</b><br><span class="mut" style="font-size:12px">esconde/silencia sinais com força &lt; 20% (exaustão verde sempre passa)</span></span></label>
    <div class="range"><span class="mut">Força mín.</span><input type="range" id="fMin" min="0" max="0.3" step="0.01" value="${F.minForca}"><b class="num" id="fMinV">${pct(F.minForca, 0)}</b></div></div>
    <div class="sec-h">Estratégias</div><div class="frm">${STRATS.map(s =>
    `<label class="chk"><input type="checkbox" data-fs="${esc(s)}" ${F.strats.includes(s) ? "checked" : ""}> ${esc(s)}</label>`).join("")}</div>
    <div class="sec-h">Ativos</div><div class="frm" style="grid-template-columns:repeat(4,1fr)">${S.symbols.map(s =>
    `<label class="chk"><input type="checkbox" data-fy="${s}" ${syms.includes(s) ? "checked" : ""}> ${short(s)}</label>`).join("")}</div>
    <div class="empty">Filtros valem para lista, avisos, som e marcações no gráfico. O servidor continua registrando tudo (o desempenho mostra todos).</div>`;
  const p = $("pane-filtros");
  p.onchange = p.oninput = e => {
    const t = e.target;
    if (t.id === "fFortes") F.fortes = t.checked;
    if (t.id === "fMin") { F.minForca = +t.value; $("fMinV").textContent = pct(F.minForca, 0); }
    if (t.dataset.fs) F.strats = [...p.querySelectorAll("[data-fs]:checked")].map(x => x.dataset.fs);
    if (t.dataset.fy) F.syms = [...p.querySelectorAll("[data-fy]:checked")].map(x => x.dataset.fy);
    LS.set("filt", F); renderSigs(); drawSignals(); renderTicker();
  };
}

// ============================================================= sinais
function renderSigs() {
  const now = Date.now() / 1000, list = S.sigs.filter(passFilter);
  const tot = S.stats.reduce((a, s) => [a[0] + s.win, a[1] + s.loss], [0, 0]);
  $("pane-sinais").innerHTML = `<div class="sec-h"><span>Sinais (${list.length})</span><span>${tot[0] + tot[1] ? `${tot[0]}W ${tot[1]}L · ${pct(tot[0] / (tot[0] + tot[1]))}` : ""}</span></div>` +
    (list.length ? list.slice(0, 100).map(s => {
      const c = s.dir > 0, left = s.expira - now;
      const res = s.resultado ? `<span class="${s.resultado === "WIN" ? "up" : s.resultado === "LOSS" ? "down" : ""}">${s.resultado}</span>`
        : left > 0 ? `<span class="warn num">${Math.floor(left / 60)}:${String(Math.floor(left % 60)).padStart(2, "0")}</span>` : `<span class="warn">…</span>`;
      const why = (s.motivos || []).slice(0, 3).map(m => m.txt).join(" · ");
      return `<div class="srow ${s._new ? "new" : ""}" data-id="${s.id}"><div class="d ${c ? "c" : "p"}">${c ? "▲" : "▼"}</div>
        <div class="t1">${short(s.sym)} · ${c ? "CALL" : "PUT"} <span class="badge">${s.min} min</span></div><div class="res">${res}</div>
        <div class="t2">${esc(s.estrategia)} · entrada ${hhmm(s.t + 300)} · ${s.forca == null ? "regra" : "força " + pct(s.forca, 0)} · hist ${pct(s.wr_hist, 0)}</div>
        ${why ? `<div class="why2">${esc(why)}</div>` : ""}</div>`;
    }).join("") : `<div class="empty">Aguardando sinais…</div>`) +
    `<div class="sec-h">Placar por estratégia</div><table class="t"><tr><th>Estratégia</th><th>W/L</th><th>Acerto</th></tr>${S.stats.map(s =>
      `<tr><td>${esc(s.estrategia)}</td><td class="num">${s.win}/${s.loss}</td><td class="num">${s.win + s.loss ? pct(s.win / (s.win + s.loss)) : "—"}</td></tr>`).join("")}</table>`;
  S.sigs.forEach(s => delete s._new);
}
$("pane-sinais").onclick = e => {
  const r = e.target.closest(".srow"); if (!r) return;
  const s = S.sigs.find(x => x.id == r.dataset.id); if (!s) return;
  goSignal(s);
};
function goSignal(s) {
  if (innerWidth <= 760) closeDrawer();
  S.sel = s.sym; LS.set("sel", s.sym); $("symTitle").textContent = short(s.sym) + "/USDT"; renderTicker(); renderAI();
  setTf("5m", s.t);
}
function bumpStats(estrategia, res) {
  let s = S.stats.find(x => x.estrategia === estrategia);
  if (!s) S.stats.push(s = { estrategia, win: 0, loss: 0, aberto: 0 });
  if (res === "WIN") s.win++; else if (res === "LOSS") s.loss++;
}

// ============================================================= desempenho
async function renderPerf() {
  const rows = await (await fetch("/api/perf")).json();
  const P = $("pane-perf");
  if (!rows.length) { P.innerHTML = `<div class="empty">Ainda sem sinais encerrados. O desempenho aparece aqui conforme os sinais fecham (WIN/LOSS).</div>`; return; }
  const PAY = .82; let bank = 1000, peak = 1000, dd = 0; const curve = [bank];
  for (const r of rows) { const st = bank * .01; bank += r.resultado === "WIN" ? st * PAY : -st; peak = Math.max(peak, bank); dd = Math.max(dd, 1 - bank / peak); curve.push(bank); }
  const w = rows.filter(r => r.resultado === "WIN").length;
  const grp = key => Object.entries(rows.reduce((m, r) => { const k = key(r); (m[k] ||= [0, 0])[r.resultado === "WIN" ? 0 : 1]++; return m; }, {}))
    .map(([k, [a, b]]) => `<tr><td>${esc(k)}</td><td class="num">${a + b}</td><td class="num ${a / (a + b) >= .549 ? "up" : "down"}">${pct(a / (a + b))}</td></tr>`).join("");
  const mn = Math.min(...curve), mx = Math.max(...curve), H = 140, W = 372;
  const pts = curve.map((v, i) => `${(i / (curve.length - 1 || 1)) * W},${H - 10 - (v - mn) / (mx - mn || 1) * (H - 20)}`).join(" ");
  P.innerHTML = `<div class="kpis">
      <div class="kv"><div class="k">Sinais</div><div class="v num">${rows.length}</div></div>
      <div class="kv"><div class="k">Acerto</div><div class="v num ${w / rows.length >= .549 ? "up" : "down"}">${pct(w / rows.length)}</div></div>
      <div class="kv"><div class="k">Banca sim.</div><div class="v num ${bank >= 1000 ? "up" : "down"}">${bank.toFixed(0)}</div></div></div>
    <div class="sec-h"><span>Curva (R$1000, entrada 1%, payout 82%)</span><span>queda máx ${pct(dd)}</span></div>
    <div class="curve"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><defs><linearGradient id="cg" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#7c5cff" stop-opacity=".35"/><stop offset="1" stop-color="#7c5cff" stop-opacity="0"/></linearGradient></defs>
      <polygon points="0,${H} ${pts} ${W},${H}" fill="url(#cg)"/><polyline points="${pts}" fill="none" stroke="#a78bfa" stroke-width="2"/></svg></div>
    <div class="sec-h">Por estratégia</div><table class="t"><tr><th></th><th>n</th><th>Acerto</th></tr>${grp(r => r.estrategia)}</table>
    <div class="sec-h">Por ativo</div><table class="t"><tr><th></th><th>n</th><th>Acerto</th></tr>${grp(r => short(r.sym))}</table>
    <div class="sec-h">Por hora (entrada)</div><table class="t"><tr><th></th><th>n</th><th>Acerto</th></tr>${grp(r => String(new Date((r.t + 300) * 1000).getHours()).padStart(2, "0") + "h")}</table>
    <div class="empty">Breakeven com payout 82% = 54,9%. Referência do backtest (6 meses): IA forte ~61%, rompimento falso + IA ~62%.</div>`;
}

// ============================================================= mapa de calor
function renderHeat() {
  const syms = S.symbols, cols = (S.ana[syms[0]]?.heat || []).map(h => h[0]);
  if (!cols.length) { $("pane-heat").innerHTML = `<div class="empty">Carregando…</div>`; return; }
  const cell = v => {
    const a = Math.min(Math.abs(v) / .3, 1), c = v > 0 ? `rgba(22,199,132,${.12 + a * .85})` : `rgba(234,57,67,${.12 + a * .85})`;
    return `background:${c}`;
  };
  $("pane-heat").innerHTML = `<div class="sec-h"><span>Força da IA · últimas 12 velas M5</span><span>agora →</span></div>
    <div class="heat"><span></span>${cols.map((t, i) => `<span class="hd">${i % 3 === 0 || i === cols.length - 1 ? hhmm(t) : ""}</span>`).join("")}
    ${syms.map(s => `<span class="lb" data-s="${s}">${short(s)}</span>` + (S.ana[s]?.heat || []).map(([t, v]) =>
      `<span class="c" style="${cell(v)}" title="${hhmm(t)} · ${v > 0 ? "alta" : "baixa"} ${pct(Math.abs(v), 0)}" data-s="${s}" data-t="${t}">${Math.abs(v) >= .2 ? (v > 0 ? "▲" : "▼") : ""}</span>`).join("")).join("")}</div>
    <div class="legend"><span class="down">▼ baixa forte</span><span>fraco</span><span class="up">alta forte ▲</span></div>
    <div class="empty">▲/▼ = força ≥ 20% (zona de sinal). Toque numa célula para abrir o gráfico naquela vela.</div>`;
}
$("pane-heat").onclick = e => {
  const c = e.target.closest("[data-s]"); if (!c) return;
  if (innerWidth <= 760) closeDrawer();
  S.sel = c.dataset.s; LS.set("sel", S.sel); $("symTitle").textContent = short(S.sel) + "/USDT"; renderTicker(); renderAI();
  setTf("5m", c.dataset.t ? +c.dataset.t : undefined);
};

// ============================================================= notícias
async function loadNews() {
  try { S.news = await (await fetch("/api/news")).json(); } catch { }
  renderNewsBadge(); if (S.tab === "news") renderNews();
}
function renderNews() {
  const now = Date.now() / 1000;
  $("pane-news").innerHTML = `<div class="sec-h"><span>Calendário econômico</span><span>USD + alto impacto</span></div>` +
    (S.news.length ? S.news.map(n => `<div class="nrow ${n.t < now - 1800 ? "past" : ""}">
      <div><div class="h num">${hhmm(n.t)}</div><div class="d">${new Date(n.t * 1000).toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit" })}</div></div>
      <div><span class="imp ${n.impacto}"></span><b>${esc(n.moeda)}</b> ${esc(n.titulo)}<div class="d">${n.previsto ? "prev. " + esc(n.previsto) : ""} ${n.anterior ? "· ant. " + esc(n.anterior) : ""}</div></div>
      <div class="d">${n.t > now ? "em " + humanIn(n.t - now) : ""}</div></div>`).join("")
      : `<div class="empty">Sem eventos nos próximos dias.</div>`) +
    `<div class="empty">Regra: evite entradas de 15 min antes até 30 min depois de notícia de alto impacto (🔴). Cripto reage forte a CPI, FOMC e Payroll.</div>`;
}
const humanIn = s => s < 3600 ? `${Math.round(s / 60)} min` : s < 86400 ? `${Math.floor(s / 3600)}h${String(Math.round(s % 3600 / 60)).padStart(2, "0")}` : `${Math.floor(s / 86400)}d`;
function renderNewsBadge() {
  const now = Date.now() / 1000;
  const hot = S.news.find(n => n.impacto === "high" && n.t - now < 15 * 60 && now - n.t < 30 * 60);
  const next = S.news.find(n => n.impacto === "high" && n.t > now);
  const b = $("newsBadge");
  if (hot) { b.hidden = false; b.className = "newsbadge only-d hot"; b.textContent = `⚠ ${hot.titulo} ${hot.t > now ? "em " + humanIn(hot.t - now) : "agora"} — evite`; }
  else if (next && next.t - now < 6 * 3600) { b.hidden = false; b.className = "newsbadge only-d"; b.textContent = `📅 ${next.titulo} em ${humanIn(next.t - now)}`; }
  else b.hidden = true;
}

// ============================================================= gaveta
function openTab(tab) {
  if (S.tab === tab && $("drawer").classList.contains("open")) return closeDrawer();
  S.tab = tab;
  $("drawer").classList.add("open");
  chart.setOffsetRightDistance(rightPad());
  document.querySelectorAll("[data-dt]").forEach(b => b.classList.toggle("on", b.dataset.dt === tab));
  document.querySelectorAll("[data-tab]").forEach(b => b.classList.toggle("on", b.dataset.tab === tab));
  ["sinais", "perf", "heat", "news", "filtros"].forEach(t => $("pane-" + t).hidden = t !== tab);
  ({ sinais: renderSigs, perf: renderPerf, heat: renderHeat, news: renderNews, filtros: renderFiltros })[tab]();
  if (tab === "sinais") { S.nNew = 0; paintNew(); }
}
function closeDrawer() {
  $("drawer").classList.remove("open"); S.tab = null;
  chart.setOffsetRightDistance(rightPad());
  document.querySelectorAll("[data-tab]").forEach(b => b.classList.remove("on"));
}
document.querySelectorAll("[data-tab]").forEach(b => b.onclick = () => openTab(b.dataset.tab));
document.querySelectorAll("[data-dt]").forEach(b => b.onclick = () => openTab(b.dataset.dt));
$("dClose").onclick = closeDrawer;
const paintNew = () => ["nNew", "nNewM"].forEach(id => { $(id).hidden = !S.nNew; $(id).textContent = S.nNew; });

// ============================================================= avisos
function toast({ cls, big, t1, t2, go }) {
  const el = document.createElement("div");
  el.className = `toast glass ${cls}`;
  el.innerHTML = `<div class="big">${big}</div><div class="t1">${esc(t1)}</div><div class="t2">${esc(t2)}</div>`;
  el.onclick = () => { el.remove(); go?.(); };
  $("toasts").prepend(el);
  setTimeout(() => el.remove(), 15000);
  while ($("toasts").children.length > 4) $("toasts").lastChild.remove();
}
let actx;
function beep(up, soft) {
  if (!S.som) return;
  actx = actx || new AudioContext();
  [0, .18, ...(soft ? [] : [.36])].forEach((d, i) => {
    const o = actx.createOscillator(), g = actx.createGain();
    o.frequency.value = up ? 660 + i * 220 : 880 - i * 200; o.type = "sine";
    g.gain.setValueAtTime(.0001, actx.currentTime + d); g.gain.exponentialRampToValueAtTime(.25, actx.currentTime + d + .02);
    g.gain.exponentialRampToValueAtTime(.0001, actx.currentTime + d + .16);
    o.connect(g).connect(actx.destination); o.start(actx.currentTime + d); o.stop(actx.currentTime + d + .18);
  });
}
async function notify(title, body) {
  if (!S.som || !("Notification" in window) || Notification.permission !== "granted") return;
  const reg = await navigator.serviceWorker?.getRegistration();
  reg ? reg.showNotification(title, { body, icon: "/icon.svg", tag: title }) : new Notification(title, { body });
}
const paintSom = () => { $("btnSom").classList.toggle("on", S.som); $("btnSom").style.color = S.som ? "var(--acc2)" : ""; };
$("btnSom").onclick = async () => {
  S.som = !S.som; LS.set("som", S.som);
  if (S.som && "Notification" in window && Notification.permission === "default") await Notification.requestPermission();
  if (S.som) beep(true, true);
  paintSom();
};

// ============================================================= tempo real (servidor)
function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => $("dot").classList.add("on");
  ws.onclose = e => { $("dot").classList.remove("on"); if (e.code === 4401) return location.href = "/login"; setTimeout(connect, 2000); };
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.type === "tick") {
      S.px[m.sym] = m.bar[4]; checkAlerts(m.sym, m.bar[4]);
      if (m.sym === S.sel) $("symPx").textContent = fmt(m.bar[4]);
      throttle();
    } else if (m.type === "analise") {
      delete m.type; S.ana[m.sym] = m;
      if (m.sym === S.sel) drawLevels();
      throttle(); if (S.tab === "heat") throttleHeat();
    } else if (m.type === "pre") {
      delete m.type; S.pre[m.sym] = m; throttle();
    } else if (m.type === "pre_off") {
      delete S.pre[m.sym]; throttle();
    } else if (m.type === "sinal") {
      delete m.type; m._new = true; S.sigs.unshift(m);
      if (passFilter(m)) {
        const c = m.dir > 0;
        beep(c); notify(`${short(m.sym)} ${c ? "CALL ▲" : "PUT ▼"} · ${m.min} min`, `${m.estrategia} · ${m.forca == null ? "regra" : "força " + pct(m.forca, 0)} · hist ${pct(m.wr_hist, 0)}`);
        toast({ cls: c ? "c" : "p", big: c ? "▲" : "▼", t1: `${short(m.sym)} ${c ? "CALL" : "PUT"} · ${m.min} min`,
          t2: `${m.estrategia} · força ${pct(m.forca, 0)} · ${(m.motivos || []).slice(0, 2).map(x => x.txt).join(" · ")}`, go: () => goSignal(m) });
        if (S.tab !== "sinais") { S.nNew++; paintNew(); }
      }
      if (S.tab === "sinais") renderSigs();
      if (m.sym === S.sel) drawSignals();
      renderTicker();
    } else if (m.type === "resultado") {
      const s = S.sigs.find(x => x.id === m.id);
      if (s) { s.resultado = m.resultado; s.saida = m.saida; bumpStats(s.estrategia, m.resultado);
        if (passFilter(s)) toast({ cls: m.resultado === "WIN" ? "c" : "p", big: m.resultado === "WIN" ? "✓" : "✗", t1: `${short(s.sym)} ${m.resultado}`, t2: s.estrategia, go: () => goSignal(s) }); }
      if (S.tab === "sinais") renderSigs();
      if (m.sym === S.sel) drawSignals();
    }
  };
}
let tT, hT;
const throttle = () => { if (!tT) tT = setTimeout(() => { tT = null; renderTicker(); renderAI(); }, 600); };
const throttleHeat = () => { if (!hT) hT = setTimeout(() => { hT = null; renderHeat(); }, 1500); };

setInterval(() => {
  const now = new Date();
  $("clock").textContent = now.toLocaleTimeString("pt-BR");
  const left = 300 - (Math.floor(now / 1000) % 300);
  $("nextBar").textContent = `· M5 ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  if (S.tab === "sinais" && S.sigs.some(s => !s.resultado)) renderSigs();
}, 1000);
setInterval(renderNewsBadge, 60000);
setInterval(loadNews, 30 * 60000);

(async () => {
  paintSom(); paintTf(); applyInds();
  const r = await fetch("/api/state");
  if (r.status === 401) return location.href = "/login";
  const st = await r.json();
  Object.assign(S, { symbols: st.symbols, ana: st.analise, pre: st.pre, stats: st.stats, sigs: st.historico });
  if (!S.symbols.includes(S.sel)) S.sel = S.symbols[0];
  select(S.sel); connect(); loadNews();
})();
