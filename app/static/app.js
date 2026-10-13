"use strict";
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    let msg = body.detail || res.statusText;
    if (Array.isArray(msg)) msg = msg.map((m) => `${(m.loc || []).slice(1).join(".")}: ${m.msg}`).join("; ");
    throw new Error(msg);
  }
  return body;
}

const num = (v, d = 2) => (v == null || Number.isNaN(v) ? "-" : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
const pct = (v, d = 2) => (v == null ? "-" : (v * 100).toFixed(d) + "%");
const signCls = (v) => (v == null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "");
const shortT = (t) => (t || "").replace("T", " ").replace("Z", "").slice(5, 16);
const localTime = (t) => (t ? new Date(t).toLocaleString([], { hour12: false }) : "-");
const stat = (label, value, cls = "") => `<div class="stat"><span>${esc(label)}</span><b class="${cls}">${value}</b></div>`;
const showError = (id, msg) => { $(id).innerHTML = msg ? `<div class="notice err" role="alert">${esc(msg)}</div>` : ""; };

let CONFIG = null;
const charts = {};

function drawChart(key, canvasId, config) {
  if (charts[key]) charts[key].destroy();
  charts[key] = new Chart($(canvasId), config);
}
const baseOpts = (labels) => ({
  responsive: true, maintainAspectRatio: false, animation: false,
  interaction: { mode: "index", intersect: false },
  elements: { point: { radius: 0 }, line: { borderWidth: 1.6 } },
  plugins: { legend: { labels: { boxWidth: 12, usePointStyle: true } } },
  scales: {
    x: { ticks: { maxTicksLimit: 7, callback(v) { return shortT(labels[v]); } }, grid: { display: false } },
    y: { ticks: { callback: (v) => Number(v).toLocaleString() } },
  },
});

/* ---------- navigation ---------- */
function showView(name) {
  document.querySelectorAll(".view").forEach((v) => (v.hidden = v.id !== `view-${name}`));
  document.querySelectorAll(".nav-btn").forEach((b) => b.toggleAttribute("aria-current", b.dataset.view === name));
  document.querySelectorAll(".nav-btn").forEach((b) => { if (b.dataset.view === name) b.setAttribute("aria-current", "page"); });
  if (name === "live") { loadSessions(); }
}
document.querySelectorAll(".nav-btn").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));

/* ---------- strategy parameter inputs ---------- */
function buildParams(containerId, strategyName, prefix) {
  const strat = CONFIG.strategies.find((s) => s.name === strategyName);
  $(containerId).innerHTML = strat.params.map((p) => {
    const id = `${prefix}-p-${p.name}`;
    const label = p.name.replace(/_/g, " ");
    if (p.type === "bool") return `<div class="field"><label for="${id}">${esc(label)}</label><input id="${id}" data-param="${p.name}" data-type="bool" type="checkbox" ${p.default ? "checked" : ""}></div>`;
    return `<div class="field"><label for="${id}">${esc(label)}</label><input id="${id}" data-param="${p.name}" data-type="${p.type}" type="number" step="${p.type === "int" ? 1 : 0.1}" value="${p.default}"></div>`;
  }).join("");
  return strat.description;
}
function collectParams(containerId) {
  const out = {};
  document.querySelectorAll(`${containerId} [data-param]`).forEach((el) => {
    out[el.dataset.param] = el.dataset.type === "bool" ? el.checked : Number(el.value);
  });
  return out;
}
function fillSelect(sel, items, selected) {
  $(sel).innerHTML = items.map((i) => `<option value="${esc(i.value)}"${i.value === selected ? " selected" : ""}>${esc(i.label)}</option>`).join("");
}

/* ---------- backtest ---------- */
function renderBacktest(res) {
  const m = res.metrics, s = res.series;
  $("#bt-result").hidden = false;
  $("#bt-warnings").innerHTML = (m.warnings || []).map((w) => `<div class="notice">${esc(w)}</div>`).join("");
  $("#bt-stats").innerHTML = [
    stat("Strategy return", pct(m.total_return), signCls(m.total_return)),
    stat("Buy and hold", pct(m.bh_return), signCls(m.bh_return)),
    stat("Sharpe", num(m.sharpe)),
    stat("Max drawdown", pct(m.max_drawdown), "neg"),
    stat("Closed trades", m.n_trades),
    stat("Win rate", pct(m.win_rate, 1)),
    stat("Time in market", pct(m.exposure, 0)),
    stat("Fees paid", num(m.fees_paid, 0)),
    stat("CAGR", m.cagr == null ? "n/a" : pct(m.cagr), signCls(m.cagr)),
    stat("Bars / years", `${m.bars} / ${num(m.years)}`),
  ].join("");

  drawChart("equity", "#ch-equity", {
    type: "line",
    data: { labels: s.t, datasets: [
      { label: "Strategy", data: s.equity, borderColor: "#1f6f8b" },
      { label: "Buy and hold", data: s.bh, borderColor: "#8a9aa1", borderDash: [5, 4] } ] },
    options: baseOpts(s.t),
  });

  const idx = new Map(s.t.map((t, i) => [t, i]));
  const buys = s.t.map(() => null), sells = s.t.map(() => null);
  res.fills.forEach((f) => { const i = idx.get(f.t); if (i != null) (f.side === "BUY" ? buys : sells)[i] = f.price; });
  drawChart("price", "#ch-price", {
    type: "line",
    data: { labels: s.t, datasets: [
      { label: "Close", data: s.price, borderColor: "#5b6d74", order: 3 },
      { label: "Buy", data: buys, showLine: false, pointStyle: "triangle", pointRadius: 6, backgroundColor: "#1c7a55", borderColor: "#1c7a55", order: 1 },
      { label: "Sell", data: sells, showLine: false, pointStyle: "triangle", rotation: 180, pointRadius: 6, backgroundColor: "#b3423b", borderColor: "#b3423b", order: 1 } ] },
    options: baseOpts(s.t),
  });

  $("#bt-trades").innerHTML = res.trades.length ? `<table><thead><tr><th>Side</th><th>Entry</th><th>Exit</th><th class="num">Entry price</th><th class="num">Exit price</th><th class="num">Return</th></tr></thead><tbody>${
    res.trades.slice().reverse().map((t) => `<tr><td class="txt">${t.side}</td><td>${shortT(t.entry_t)}</td><td>${shortT(t.exit_t)}</td><td class="num">${num(t.entry_price, 4)}</td><td class="num">${num(t.exit_price, 4)}</td><td class="num ${signCls(t.return_pct)}">${pct(t.return_pct)}</td></tr>`).join("")
  }</tbody></table>` : `<div class="empty">No closed trades in this window. Try a longer history or faster parameters.</div>`;
}

async function runBacktest() {
  showError("#bt-error", "");
  const btn = $("#bt-run");
  btn.disabled = true; btn.textContent = "Running...";
  try {
    const res = await api("/api/backtests", { method: "POST", body: JSON.stringify({
      symbol: $("#bt-symbol").value.trim(), interval: $("#bt-interval").value, period: $("#bt-period").value,
      strategy: $("#bt-strategy").value, params: collectParams("#bt-params"),
      cash: Number($("#bt-cash").value), fee_bps: Number($("#bt-fee").value), slippage_bps: Number($("#bt-slip").value),
      data_source: $("#bt-source").value || null }) });
    renderBacktest(res);
    loadHistory();
  } catch (e) { showError("#bt-error", e.message); }
  finally { btn.disabled = false; btn.textContent = "Run backtest"; }
}

async function loadHistory() {
  const rows = await api("/api/backtests");
  $("#bt-history").innerHTML = rows.length ? `<table><thead><tr><th>Run</th><th>When</th><th>Symbol</th><th>Bars</th><th>Strategy</th><th class="num">Return</th><th class="num">Sharpe</th><th class="num">Max DD</th><th class="num">Trades</th></tr></thead><tbody>${
    rows.map((r) => `<tr class="click" data-id="${r.id}"><td>#${r.id}</td><td>${localTime(r.created_at)}</td><td>${esc(r.symbol)}</td><td>${esc(r.interval)}</td><td class="txt">${esc(r.strategy)}</td><td class="num ${signCls(r.metrics.total_return)}">${pct(r.metrics.total_return)}</td><td class="num">${num(r.metrics.sharpe)}</td><td class="num neg">${pct(r.metrics.max_drawdown)}</td><td class="num">${r.metrics.n_trades}</td></tr>`).join("")
  }</tbody></table>` : `<div class="empty">No runs yet. Pick a strategy above and run your first backtest.</div>`;
  document.querySelectorAll("#bt-history tr.click").forEach((tr) => tr.addEventListener("click", async () => {
    try { renderBacktest(await api(`/api/backtests/${tr.dataset.id}`)); window.scrollTo(0, 0); $(".stage").scrollTo({ top: 0 }); }
    catch (e) { showError("#bt-error", e.message); }
  }));
}

/* ---------- live ---------- */
let selectedSession = null, reconCache = { id: null, html: "" };

async function startLive() {
  showError("#lv-error", "");
  const btn = $("#lv-start"); btn.disabled = true;
  try {
    const res = await api("/api/live", { method: "POST", body: JSON.stringify({
      symbol: $("#lv-symbol").value.trim(), interval: $("#lv-interval").value, strategy: $("#lv-strategy").value,
      params: collectParams("#lv-params"), cash: Number($("#lv-cash").value), fee_bps: Number($("#lv-fee").value),
      slippage_bps: Number($("#lv-slip").value), poll_seconds: Number($("#lv-poll").value),
      data_source: $("#lv-source").value || null }) });
    selectedSession = res.id;
    await loadSessions();
    refreshLive();
  } catch (e) { showError("#lv-error", e.message); }
  finally { btn.disabled = false; }
}

async function loadSessions() {
  const rows = await api("/api/live");
  $("#lv-sessions").innerHTML = rows.length ? `<table><thead><tr><th>Session</th><th>Symbol</th><th>Status</th></tr></thead><tbody>${
    rows.map((r) => `<tr class="click ${r.id === selectedSession ? "sel" : ""}" data-id="${r.id}"><td>#${r.id}</td><td>${esc(r.symbol)} ${esc(r.interval)}</td><td class="txt"><span class="dot ${r.running ? "running" : ""}"></span>${r.running ? "running" : esc(r.status)}</td></tr>`).join("")
  }</tbody></table>` : `<div class="empty">No sessions yet.</div>`;
  document.querySelectorAll("#lv-sessions tr.click").forEach((tr) => tr.addEventListener("click", () => {
    selectedSession = Number(tr.dataset.id); loadSessions(); refreshLive();
  }));
}

async function refreshLive() {
  if (selectedSession == null) return;
  let d;
  try { d = await api(`/api/live/${selectedSession}`); } catch (e) { return; }
  const s = d.session, st = d.state;
  const side = st ? (st.side > 0 ? "Long" : st.side < 0 ? "Short" : "Flat") : "-";
  $("#lv-detail").innerHTML = `
    <div class="panel">
      <div class="row-actions" style="justify-content:space-between">
        <h2 style="margin:0"><span class="dot ${s.running ? "running" : ""}"></span>Session #${s.id}: ${esc(s.symbol)} ${esc(s.interval)}, ${esc(s.strategy)} <span class="dim">${esc(JSON.stringify(s.params))}</span></h2>
        <div class="row-actions">
          <button class="btn quiet small" id="lv-recon">Compare with backtest</button>
          ${s.running ? `<button class="btn quiet small" id="lv-stop">Stop</button>` : ""}
        </div>
      </div>
      <p class="hint">Started ${localTime(s.started_at)}. Last poll ${localTime(s.last_tick_at)}.${s.last_error ? ` Last error: ${esc(s.last_error)}` : ""}</p>
    </div>
    <div class="strip">
      ${stat("Position", side)}
      ${stat("Last price", st ? num(st.price, 4) : "-")}
      ${stat("Equity", st ? num(st.equity) : "-")}
      ${stat("P&L", st ? pct(st.pnl_pct) : "-", st ? signCls(st.pnl_pct) : "")}
      ${stat("Fills", d.fills.length)}
    </div>
    <div id="lv-recon-out"></div>
    <div class="panel"><h2>Account equity</h2><div class="chart-box short"><canvas id="ch-live"></canvas></div></div>
    <div class="two">
      <div class="panel"><h2>Signals on closed bars</h2><div class="table-wrap">${d.signals.length ? `<table><thead><tr><th>Bar</th><th class="num">Close</th><th class="num">Target</th><th class="num">Was</th></tr></thead><tbody>${d.signals.map((x) => `<tr><td>${shortT(x.bar_t)}</td><td class="num">${num(x.bar_close, 4)}</td><td class="num">${x.target > 0 ? "+1" : x.target}</td><td class="num dim">${x.prev > 0 ? "+1" : x.prev}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">Waiting for the first closed bar.</div>`}</div></div>
      <div class="panel"><h2>Simulated fills</h2><div class="table-wrap">${d.fills.length ? `<table><thead><tr><th>Time</th><th>Side</th><th class="num">Qty</th><th class="num">Price</th><th class="num">Fee</th></tr></thead><tbody>${d.fills.map((f) => `<tr><td>${shortT(f.t)}</td><td class="txt">${f.side}</td><td class="num">${num(f.qty, 6)}</td><td class="num">${num(f.price, 4)}</td><td class="num">${num(f.fee)}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">No fills yet. The strategy is flat until its first entry signal.</div>`}</div></div>
    </div>`;
  if (d.equity.t.length) {
    drawChart("live", "#ch-live", { type: "line",
      data: { labels: d.equity.t, datasets: [{ label: "Equity", data: d.equity.v, borderColor: "#b9780f" }] },
      options: { ...baseOpts(d.equity.t), plugins: { legend: { display: false } } } });
  }
  if (reconCache.id === selectedSession) $("#lv-recon-out").innerHTML = reconCache.html;
  const stop = $("#lv-stop");
  if (stop) stop.addEventListener("click", async () => { try { await api(`/api/live/${s.id}/stop`, { method: "POST" }); } catch (e) {} loadSessions(); refreshLive(); });
  $("#lv-recon").addEventListener("click", reconcile);
}

async function reconcile() {
  const out = $("#lv-recon-out");
  out.innerHTML = `<div class="panel empty">Replaying stored bars through the backtester...</div>`;
  try {
    const r = await api(`/api/live/${selectedSession}/reconcile`);
    if (!r.ready) { out.innerHTML = reconCache.html = `<div class="notice">${esc(r.message)}</div>`; reconCache.id = selectedSession; return; }
    const bad = r.rows.filter((x) => !x.match);
    out.innerHTML = `
      <div class="strip">
        ${stat("Signals compared", `${r.n_match} / ${r.n_compared}`)}
        ${stat("Signal match rate", pct(r.match_rate, 1), r.match_rate === 1 ? "pos" : "neg")}
        ${stat("Avg fill vs bar close", r.avg_adverse_bps == null ? "-" : num(r.avg_adverse_bps, 1) + " bps")}
        ${stat("Modelled slippage", num(r.modelled_cost_bps_per_side, 1) + " bps")}
        ${stat("Live return", pct(r.live_return), signCls(r.live_return))}
        ${stat("Backtest return, same window", pct(r.backtest_return), signCls(r.backtest_return))}
      </div>
      <div class="notice" style="margin-top:8px">${esc(r.note)}</div>
      ${bad.length ? `<div class="panel" style="margin-top:8px"><h2>Signal mismatches</h2><div class="table-wrap"><table><thead><tr><th>Bar</th><th class="num">Live</th><th class="num">Backtest</th></tr></thead><tbody>${bad.map((x) => `<tr><td>${shortT(x.bar_t)}</td><td class="num">${x.live_target}</td><td class="num">${x.backtest_target}</td></tr>`).join("")}</tbody></table></div></div>` : ""}`;
    reconCache = { id: selectedSession, html: out.innerHTML };
  } catch (e) { out.innerHTML = `<div class="notice err">${esc(e.message)}</div>`; }
}

/* ---------- log tape ---------- */
let tapeAfter = 0, tapePaused = false;
async function pollTape() {
  if (tapePaused) return;
  try {
    const rows = await api(`/api/logs?after_id=${tapeAfter}&limit=300`);
    $("#foot-api").textContent = "online";
    if (!rows.length) return;
    const body = $("#tape-body");
    const stick = body.scrollTop + body.clientHeight >= body.scrollHeight - 30;
    body.insertAdjacentHTML("beforeend", rows.map((r) => `<div class="ln lvl-${esc(r.level)}"><span class="ts">${new Date(r.t).toLocaleTimeString([], { hour12: false })}</span><span class="src">${esc(r.source)}</span><span class="msg">${esc(r.message)}</span></div>`).join(""));
    tapeAfter = rows[rows.length - 1].id;
    while (body.childElementCount > 1000) body.firstElementChild.remove();
    if (stick) body.scrollTop = body.scrollHeight;
  } catch (e) { $("#foot-api").textContent = "offline"; }
}
$("#tape-pause").addEventListener("click", (e) => { tapePaused = !tapePaused; e.target.textContent = tapePaused ? "Resume" : "Pause"; });
$("#tape-clear").addEventListener("click", () => { $("#tape-body").innerHTML = ""; });
$("#tape-collapse").addEventListener("click", (e) => { const c = $("#tape").classList.toggle("collapsed"); e.target.textContent = c ? "Expand" : "Collapse"; });
$("#tape-level").addEventListener("change", (e) => { $("#tape-body").dataset.min = e.target.value; });

/* ---------- boot ---------- */
async function boot() {
  CONFIG = await api("/api/config");
  $("#foot-source").textContent = CONFIG.data_source;
  const intervals = CONFIG.intervals.map((i) => ({ value: i, label: i }));
  const strategies = CONFIG.strategies.map((s) => ({ value: s.name, label: s.label }));
  fillSelect("#bt-interval", intervals, "1h"); fillSelect("#lv-interval", intervals, "5m");
  fillSelect("#bt-strategy", strategies, "sma_cross"); fillSelect("#lv-strategy", strategies, "sma_cross");
  $("#bt-symbol").value = $("#lv-symbol").value = CONFIG.default_symbol;
  $("#lv-poll").value = CONFIG.poll_seconds;
  $("#bt-desc").textContent = buildParams("#bt-params", "sma_cross", "bt");
  buildParams("#lv-params", "sma_cross", "lv");
  $("#bt-strategy").addEventListener("change", (e) => { $("#bt-desc").textContent = buildParams("#bt-params", e.target.value, "bt"); });
  $("#lv-strategy").addEventListener("change", (e) => buildParams("#lv-params", e.target.value, "lv"));
  $("#bt-run").addEventListener("click", runBacktest);
  $("#lv-start").addEventListener("click", startLive);
  loadHistory();
  pollTape(); setInterval(pollTape, 2000);
  setInterval(() => { if (!$("#view-live").hidden) { loadSessions(); refreshLive(); } }, 5000);
}
boot().catch((e) => { $("#foot-api").textContent = "offline"; showError("#bt-error", "Cannot reach the API: " + e.message); });
