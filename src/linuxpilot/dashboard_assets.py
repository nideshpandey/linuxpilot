"""Static files for `linuxpilot serve`, kept as strings so the package stays one
pure-Python wheel with no data files. Everything is local: no fonts, CDNs,
libraries or external requests, so the page works on an offline Pi.

All values from the system are inserted with textContent, never innerHTML.
Trend history lives only in the open browser tab and is gone when it closes.
"""

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>LinuxPilot live</title>
<link rel="stylesheet" href="/app.css">
<script src="/app.js" defer></script>
</head>
<body>
<header class="bar">
  <p class="brand"><span class="mark">LinuxPilot</span> <span id="host" class="host"></span></p>
  <div class="live">
    <span id="updated" class="updated" aria-live="polite">Connecting</span>
    <label class="interval">Every
      <select id="interval" aria-label="Refresh interval"></select>
    </label>
    <button id="refresh" type="button" class="btn">Refresh now</button>
    <button id="pause" type="button" class="btn" aria-pressed="false">Pause</button>
  </div>
</header>

<main>
  <section id="verdict" class="verdict" data-status="unknown" aria-live="polite">
    <h1 id="verdict-text">Checking this machine</h1>
    <p id="verdict-sub" class="verdict-sub"></p>
  </section>

  <p id="error" class="error" role="alert" hidden></p>

  <section id="trends-section" class="block" hidden>
    <h2>Trends <span class="hint">since you opened this page</span></h2>
    <div id="trends" class="trends"></div>
  </section>

  <section id="attention-section" class="block" hidden>
    <h2>Needs attention</h2>
    <ol id="attention" class="attention"></ol>
  </section>

  <section class="block">
    <h2>All checks</h2>
    <div class="toolbar">
      <div class="filters" role="group" aria-label="Show checks">
        <button type="button" class="filter" data-filter="all">All <span class="count" data-count="all"></span></button>
        <button type="button" class="filter" data-filter="problems">Problems <span class="count" data-count="problems"></span></button>
        <button type="button" class="filter" data-filter="ok">OK <span class="count" data-count="ok"></span></button>
      </div>
      <input id="search" class="search" type="search" placeholder="Filter by name" aria-label="Filter checks by name" autocomplete="off">
    </div>
    <ul id="checks" class="checks"></ul>
    <details id="na" class="na" hidden>
      <summary id="na-summary"></summary>
      <ul id="na-list" class="checks quiet"></ul>
    </details>
  </section>
</main>

<footer class="foot">
  Read-only. This page updates only while <code>linuxpilot serve</code> is running.
  Click a check to see its details.
</footer>
</body>
</html>
"""

APP_CSS = r""":root {
  --bg: #e9eef1;
  --panel: #f7f9fa;
  --hover: #dfe6eb;
  --ink: #14202b;
  --muted: #566672;
  --rule: #cfd8de;
  --ok: #2c7a57;
  --warn: #a5650c;
  --crit: #b8322a;
  --unknown: #5f4fa0;
  --na: #7b8a95;
  --code-bg: #dde5ea;
  --flash: #fff1c7;
  --display: "Bahnschrift", "DIN Alternate", "Avenir Next Condensed", "Roboto Condensed",
             "Arial Narrow", system-ui, sans-serif;
  --text: system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", Ubuntu, Cantarell, sans-serif;
  --mono: ui-monospace, "SFMono-Regular", "DejaVu Sans Mono", Menlo, Consolas, monospace;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f171d;
    --panel: #16212a;
    --hover: #1c2a35;
    --ink: #e2e9ee;
    --muted: #93a3af;
    --rule: #26343f;
    --ok: #5cc49a;
    --warn: #e5ad4a;
    --crit: #f07567;
    --unknown: #ad9df0;
    --na: #6f7f8a;
    --code-bg: #213039;
    --flash: #3a3216;
  }
}

* { box-sizing: border-box; }
[hidden] { display: none !important; }
.offscreen { position: fixed; left: -9999px; top: 0; opacity: 0; }

body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 16px/1.5 var(--text);
  -webkit-text-size-adjust: 100%;
}

code {
  font-family: var(--mono);
  font-size: 0.875em;
  background: var(--code-bg);
  padding: 0.1em 0.35em;
  border-radius: 3px;
  overflow-wrap: break-word;
}

button, select, input { font: inherit; color: inherit; }
:focus-visible { outline: 3px solid var(--unknown); outline-offset: 2px; }

.btn, .interval select, .filter, .search, .copy {
  background: var(--panel);
  border: 1px solid var(--rule);
  border-radius: 4px;
}
.btn { padding: 0.3rem 0.75rem; cursor: pointer; }
.btn:hover, .filter:hover, .copy:hover { border-color: var(--muted); }

/* Header */
.bar {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 1rem;
  align-items: center;
  justify-content: space-between;
  max-width: 56rem;
  margin: 0 auto;
  padding: 1rem 1rem 0;
}
.brand { margin: 0; font-size: 0.95rem; color: var(--muted); }
.mark { font-family: var(--display); font-weight: 700; color: var(--ink); letter-spacing: 0.01em; }
.host { margin-left: 0.4rem; }
.live { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 0.6rem; font-size: 0.9rem; color: var(--muted); }
.updated { margin-right: 0.25rem; }
.updated::before {
  content: "";
  display: inline-block;
  width: 0.5rem; height: 0.5rem;
  margin-right: 0.45rem;
  border-radius: 50%;
  background: var(--ok);
  vertical-align: 0.05em;
}
.live[data-state="paused"] .updated::before { background: var(--na); }
.live[data-state="lost"] .updated::before { background: var(--crit); }
.interval { display: inline-flex; align-items: center; gap: 0.35rem; }
.interval select { padding: 0.25rem 0.3rem; color: var(--ink); }
.btn { color: var(--ink); }
.btn[aria-pressed="true"] { background: var(--ink); color: var(--bg); border-color: var(--ink); }

main { max-width: 56rem; margin: 0 auto; padding: 0 1rem; }

/* Verdict: the one bold element on the page */
.verdict {
  margin: 1.75rem 0 2rem;
  padding: 0.25rem 0 0.25rem 1.25rem;
  border-left: 0.5rem solid var(--status-color, var(--na));
}
.verdict h1 {
  margin: 0;
  font-family: var(--display);
  font-weight: 700;
  font-size: clamp(2rem, 6vw, 3.4rem);
  line-height: 1.05;
  letter-spacing: -0.01em;
}
.verdict-sub { margin: 0.6rem 0 0; color: var(--muted); max-width: 40rem; }

.error {
  margin: 0 0 1.5rem;
  padding: 0.75rem 1rem;
  border: 1px solid var(--crit);
  border-radius: 4px;
  background: var(--panel);
}

.block { margin: 0 0 2.25rem; }
.block h2 {
  font-family: var(--display);
  font-size: 1.25rem;
  font-weight: 700;
  margin: 0 0 0.75rem;
}
.hint { font-family: var(--text); font-size: 0.85rem; font-weight: 400; color: var(--muted); margin-left: 0.4rem; }

/* Status colours: shape + word + colour, never colour alone */
[data-status="ok"] { --status-color: var(--ok); }
[data-status="warning"] { --status-color: var(--warn); }
[data-status="critical"] { --status-color: var(--crit); }
[data-status="unknown"] { --status-color: var(--unknown); }
[data-status="unsupported"], [data-status="skipped"] { --status-color: var(--na); }

.badge {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.85rem;
  font-weight: 650;
  color: var(--status-color);
  white-space: nowrap;
}
.badge::before {
  content: attr(data-icon);
  display: inline-grid;
  place-items: center;
  width: 1.15rem; height: 1.15rem;
  border-radius: 50%;
  background: var(--status-color);
  color: var(--panel);
  font-size: 0.75rem;
  font-weight: 800;
  line-height: 1;
}

/* Trends: a row of instrument readouts */
.trends {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 9.5rem), 1fr));
  gap: 1rem 1.5rem;
}
.trend { border-top: 3px solid var(--status-color, var(--rule)); padding-top: 0.5rem; }
.t-label { margin: 0; color: var(--muted); font-size: 0.9rem; }
.t-value { margin: 0; font-family: var(--display); font-size: 1.9rem; font-weight: 700; line-height: 1.15; }
.t-value small { font-family: var(--text); font-size: 0.85rem; font-weight: 400; color: var(--muted); margin-left: 0.3rem; }
.spark { display: block; width: 100%; height: 2.5rem; margin: 0.35rem 0 0.2rem; overflow: visible; }
.spark polyline { fill: none; stroke: var(--status-color, var(--muted)); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; vector-effect: non-scaling-stroke; }
.spark .base { stroke: var(--rule); stroke-width: 1; vector-effect: non-scaling-stroke; }
.t-range { margin: 0; font-size: 0.8rem; color: var(--muted); }

/* Needs attention */
.attention { list-style: none; margin: 0; padding: 0; }
.problem {
  background: var(--panel);
  border-left: 4px solid var(--status-color);
  border-radius: 0 4px 4px 0;
  padding: 0.9rem 1rem 1rem;
  margin: 0 0 0.75rem;
}
.problem h3 {
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.25rem 0.6rem;
  font-size: 1.05rem;
  line-height: 1.35;
}
.problem .summary { margin: 0.15rem 0 0.6rem; }
.problem dl, .detail dl { margin: 0; display: grid; grid-template-columns: 3.25rem 1fr; gap: 0.35rem 0.5rem; }
.problem dt, .detail dt { color: var(--muted); font-weight: 600; }
.problem dd, .detail dd { margin: 0; max-width: 46rem; overflow-wrap: anywhere; }

.copy {
  margin-left: 0.3rem;
  padding: 0 0.4rem;
  font-size: 0.75rem;
  line-height: 1.5;
  color: var(--muted);
  cursor: pointer;
  vertical-align: 0.05em;
}

/* Toolbar */
.toolbar { display: flex; flex-wrap: wrap; gap: 0.6rem 1rem; align-items: center; justify-content: space-between; margin: 0 0 0.75rem; }
.filters { display: inline-flex; }
.filter { padding: 0.3rem 0.75rem; cursor: pointer; border-radius: 0; margin-left: -1px; }
.filter:first-child { border-radius: 4px 0 0 4px; margin-left: 0; }
.filter:last-child { border-radius: 0 4px 4px 0; }
.filter[aria-pressed="true"] { background: var(--ink); color: var(--bg); border-color: var(--ink); position: relative; }
.count { opacity: 0.75; margin-left: 0.15rem; }
.search { padding: 0.3rem 0.6rem; min-width: 12rem; flex: 0 1 16rem; }

/* All checks: a checklist; each row opens to show its details */
.checks { list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--rule); }
.check { border-bottom: 1px solid var(--rule); }
.row {
  display: grid;
  grid-template-columns: 7rem 12rem 1fr 1rem;
  gap: 0.25rem 1rem;
  align-items: baseline;
  width: 100%;
  padding: 0.6rem 0.25rem;
  background: none;
  border: 0;
  text-align: left;
  cursor: pointer;
}
.row:hover { background: var(--hover); }
.row .title { font-weight: 600; }
.row .summary { overflow-wrap: anywhere; }
.quiet .row .title, .quiet .row .summary { color: var(--muted); font-weight: 400; }
.chev {
  justify-self: end;
  width: 0.5rem; height: 0.5rem;
  border-right: 2px solid var(--muted);
  border-bottom: 2px solid var(--muted);
  transform: translateY(-0.15rem) rotate(45deg);
  transition: transform 0.15s;
}
.row[aria-expanded="true"] .chev { transform: translateY(0.1rem) rotate(-135deg); }
.detail { padding: 0 0.25rem 0.9rem calc(7rem + 1.25rem); font-size: 0.9rem; }
.detail dl { grid-template-columns: minmax(7rem, max-content) 1fr; }
.check.changed { animation: flash 1.6s ease-out; }
@keyframes flash { from { background: var(--flash); } to { background: transparent; } }
.empty { padding: 1rem 0.25rem; color: var(--muted); border-bottom: 1px solid var(--rule); }
.empty button { margin-left: 0.5rem; }

.na { margin-top: 1rem; color: var(--muted); }
.na summary { cursor: pointer; padding: 0.25rem 0; }
.na .checks { margin-top: 0.5rem; }

.foot {
  max-width: 56rem;
  margin: 0 auto;
  padding: 1rem 1rem 2.5rem;
  color: var(--muted);
  font-size: 0.875rem;
}

@media (max-width: 40rem) {
  .row { grid-template-columns: auto 1fr 1rem; gap: 0.15rem 0.6rem; }
  .row .summary { grid-column: 1 / -1; grid-row: 2; }
  .chev { grid-column: 3; grid-row: 1; }
  .detail { padding-left: 0.25rem; }
  .problem dl, .detail dl { grid-template-columns: 1fr; gap: 0.1rem; }
  .problem dd, .detail dd { margin-bottom: 0.4rem; }
  .search { flex: 1 1 100%; }
}

@media (prefers-reduced-motion: reduce) {
  .check.changed { animation: none; outline: 2px solid var(--flash); }
  .chev { transition: none; }
}
"""

APP_JS = r""""use strict";

const ICONS = { ok: "✓", warning: "!", critical: "×", unknown: "?", unsupported: "–", skipped: "–" };
const WORDS = { ok: "OK", warning: "Warning", critical: "Critical", unknown: "Unknown", unsupported: "Not applicable", skipped: "Skipped" };
const SEVERITY = { critical: 4, warning: 3, unknown: 2, ok: 1, unsupported: 0, skipped: 0 };
const INTERVALS = [2, 5, 10, 30, 60];
const MAX_POINTS = 120;
const SVG_NS = "http://www.w3.org/2000/svg";

const token = new URLSearchParams(location.hash.slice(1)).get("token");
const $ = (id) => document.getElementById(id);

/* Per-browser preferences. Storage can be unavailable (private mode), so never rely on it. */
const prefs = {
  get(name, fallback) {
    try { const v = localStorage.getItem("linuxpilot." + name); return v === null ? fallback : JSON.parse(v); }
    catch (e) { return fallback; }
  },
  set(name, value) {
    try { localStorage.setItem("linuxpilot." + name, JSON.stringify(value)); } catch (e) { /* ignore */ }
  },
};

const state = {
  paused: false, timer: null, data: null, lastOk: 0, inFlight: false,
  previous: new Map(), expanded: new Set(), rowId: 0,
  filter: prefs.get("filter", "all"), query: "",
  interval: prefs.get("interval", null),
  history: { cpu: [], mem: [], load: [], temp: [] },
};

/* ---------------------------------------------------------------- helpers */

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function plural(n, one, many) { return n === 1 ? one : many; }
function isApplicable(f) { return f.status !== "unsupported" && f.status !== "skipped"; }
function isProblem(f) { return f.status === "warning" || f.status === "critical" || f.status === "unknown"; }
function key(f) { return `${f.check_id}|${f.title}`; }
function num(v) { return typeof v === "number" && isFinite(v) ? v : null; }

function bytes(n) {
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0;
  while (Math.abs(n) >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return i === 0 ? `${n} B` : `${n.toFixed(1)} ${units[i]}`;
}

function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  return new Promise((resolve, reject) => {
    const area = el("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.className = "offscreen";
    document.body.appendChild(area);
    area.select();
    try { document.execCommand("copy") ? resolve() : reject(new Error("copy failed")); }
    catch (e) { reject(e); }
    finally { area.remove(); }
  });
}

function copyButton(text) {
  const btn = el("button", "copy", "Copy");
  btn.type = "button";
  btn.setAttribute("aria-label", `Copy command: ${text}`);
  btn.addEventListener("click", (ev) => {
    ev.stopPropagation();
    copyText(text).then(
      () => { btn.textContent = "Copied"; },
      () => { btn.textContent = "Select and copy"; },
    ).finally(() => setTimeout(() => { btn.textContent = "Copy"; }, 1500));
  });
  return btn;
}

/* Text with `inline code` becomes safe text nodes, <code> and a Copy button. */
function withCode(text) {
  const frag = document.createDocumentFragment();
  String(text).split("`").forEach((part, i) => {
    if (!part) return;
    if (i % 2) {
      frag.appendChild(el("code", null, part));
      frag.appendChild(copyButton(part));
    } else {
      frag.appendChild(document.createTextNode(part));
    }
  });
  return frag;
}

function badge(status) {
  const b = el("span", "badge", WORDS[status] || status);
  b.dataset.status = status;
  b.dataset.icon = ICONS[status] || "?";
  return b;
}

/* ---------------------------------------------------------------- verdict */

function verdict(findings) {
  const count = (s) => findings.filter((f) => f.status === s).length;
  const crit = count("critical"), warn = count("warning"), unknown = count("unknown");
  if (crit) {
    let text = `${crit} critical ${plural(crit, "problem", "problems")}`;
    if (warn) text += ` and ${warn} ${plural(warn, "warning", "warnings")}`;
    return { status: "critical", text: `${text} ${crit + warn === 1 ? "needs" : "need"} attention` };
  }
  if (warn) return { status: "warning", text: `${warn} ${plural(warn, "warning needs", "warnings need")} attention` };
  if (!findings.some(isApplicable)) return { status: "unknown", text: "None of the selected checks apply here" };
  if (unknown) return { status: "ok", text: `No problems found, ${unknown} ${plural(unknown, "check", "checks")} could not run` };
  return { status: "ok", text: "All clear" };
}

function subline(data) {
  const info = data.findings.find((f) => f.check_id === "system.info");
  const bits = [];
  if (info && info.details) {
    if (info.details.os) bits.push(info.details.os);
    if (info.details.uptime_human) bits.push(`up ${info.details.uptime_human}`);
  }
  const applicable = data.findings.filter(isApplicable);
  bits.push(`${applicable.filter((f) => f.status === "ok").length} of ${applicable.length} checks OK`);
  bits.push(`checked in ${data.duration_seconds.toFixed(1)} s`);
  return bits.join(", ");
}

/* ---------------------------------------------------------------- trends */

const METRICS = [
  { id: "cpu", label: "CPU busy", check: "cpu.usage", fixed: [0, 100],
    pick: (d) => num(d.busy_percent), show: (v) => `${Math.round(v)}%` },
  { id: "mem", label: "Memory used", check: "memory.usage", fixed: [0, 100],
    pick: (d) => (num(d.available_percent) === null ? null : 100 - d.available_percent), show: (v) => `${Math.round(v)}%` },
  { id: "load", label: "Load, 1 minute", check: "cpu.load",
    pick: (d) => num(d.load1), show: (v) => v.toFixed(2),
    note: (d) => (d.cores ? `on ${d.cores} ${plural(d.cores, "core", "cores")}` : ""), floor: (d) => d.cores || 1 },
  { id: "temp", label: "CPU temperature", check: "cpu.temperature",
    pick: (d) => num(d.celsius), show: (v) => `${Math.round(v)}°C` },
];

function recordHistory(data) {
  for (const m of METRICS) {
    const f = data.findings.find((x) => x.check_id === m.check && isApplicable(x));
    const v = f && f.details ? m.pick(f.details) : null;
    if (v === null) continue;
    const series = state.history[m.id];
    series.push(v);
    if (series.length > MAX_POINTS) series.shift();
  }
}

function sparkline(values, lo, hi) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "spark");
  svg.setAttribute("viewBox", "0 0 100 30");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");
  const base = document.createElementNS(SVG_NS, "line");
  base.setAttribute("class", "base");
  base.setAttribute("x1", "0"); base.setAttribute("x2", "100");
  base.setAttribute("y1", "29"); base.setAttribute("y2", "29");
  svg.appendChild(base);
  if (values.length < 2) return svg;
  const span = hi - lo || 1;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * 100;
    const y = 29 - ((Math.min(Math.max(v, lo), hi) - lo) / span) * 27;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  });
  const line = document.createElementNS(SVG_NS, "polyline");
  line.setAttribute("points", pts.join(" "));
  svg.appendChild(line);
  return svg;
}

function renderTrends(data) {
  const blocks = [];
  for (const m of METRICS) {
    const series = state.history[m.id];
    const f = data.findings.find((x) => x.check_id === m.check && isApplicable(x));
    if (!series.length || !f) continue;
    const current = series[series.length - 1];
    let lo, hi;
    if (m.fixed) { [lo, hi] = m.fixed; }
    else {
      lo = Math.min(...series); hi = Math.max(...series);
      if (m.floor) { lo = 0; hi = Math.max(hi, m.floor(f.details)); }
      else if (hi - lo < 4) { const mid = (hi + lo) / 2; lo = mid - 2; hi = mid + 2; }
    }
    const box = el("div", "trend");
    box.dataset.status = f.status;
    box.appendChild(el("p", "t-label", m.label));
    const value = el("p", "t-value", m.show(current));
    const note = m.note ? m.note(f.details) : "";
    if (note) value.appendChild(el("small", null, note));
    box.appendChild(value);
    box.appendChild(sparkline(series, lo, hi));
    const low = Math.min(...series), high = Math.max(...series);
    box.appendChild(el("p", "t-range",
      series.length < 2 ? "Collecting data" : `Low ${m.show(low)}, high ${m.show(high)}, ${series.length} readings`));
    box.setAttribute("aria-label", `${m.label} ${m.show(current)}, low ${m.show(low)}, high ${m.show(high)}`);
    blocks.push(box);
  }
  $("trends-section").hidden = blocks.length === 0;
  $("trends").replaceChildren(...blocks);
}

/* ---------------------------------------------------------------- checks */

function formatValue(name, v) {
  if (v === null || v === undefined || v === "") return "not available";
  if (typeof v === "number" && /_bytes$/.test(name)) return bytes(v);
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (Array.isArray(v)) {
    if (!v.length) return "none";
    if (v.every((x) => typeof x !== "object" || x === null)) return v.join(", ");
  }
  if (typeof v === "object") {
    const text = JSON.stringify(v);
    return text.length > 240 ? text.slice(0, 237) + "..." : text;
  }
  return String(v);
}

function detailPanel(f) {
  const panel = el("div", "detail");
  const dl = el("dl");
  const add = (label, value, rich) => {
    dl.appendChild(el("dt", null, label));
    const dd = el("dd");
    if (rich) dd.appendChild(withCode(value)); else dd.textContent = value;
    dl.appendChild(dd);
  };
  for (const [name, value] of Object.entries(f.details || {})) {
    add(name.replace(/_/g, " "), formatValue(name, value), false);
  }
  if (f.reason) add("Why", f.reason, true);
  if (f.recommendation) add("Next", f.recommendation, true);
  if (f.limitations && f.limitations.length) add("Notes", f.limitations.join("; "), false);
  if (f.source) add("Source", f.source, false);
  add("Check ID", f.check_id, false);
  panel.appendChild(dl);
  return panel;
}

function checkRow(f) {
  const k = key(f);
  const li = el("li", "check");
  const open = state.expanded.has(k);
  const btn = el("button", "row");
  btn.type = "button";
  btn.setAttribute("aria-expanded", String(open));
  btn.append(badge(f.status), el("span", "title", f.title), el("span", "summary", f.summary), el("span", "chev"));
  const panel = detailPanel(f);
  panel.id = `detail-${++state.rowId}`;
  panel.hidden = !open;
  btn.setAttribute("aria-controls", panel.id);
  btn.addEventListener("click", () => {
    const now = !state.expanded.has(k);
    if (now) state.expanded.add(k); else state.expanded.delete(k);
    btn.setAttribute("aria-expanded", String(now));
    panel.hidden = !now;
  });
  li.append(btn, panel);
  const before = state.previous.get(k);
  if (before !== undefined && before !== f.status) li.classList.add("changed");
  return li;
}

function matches(f) {
  if (state.filter === "problems" && !isProblem(f)) return false;
  if (state.filter === "ok" && f.status !== "ok") return false;
  if (state.query && !`${f.title} ${f.summary}`.toLowerCase().includes(state.query)) return false;
  return true;
}

function renderChecks() {
  const data = state.data;
  if (!data) return;
  const applicable = data.findings.filter(isApplicable);
  const counts = { all: applicable.length, problems: applicable.filter(isProblem).length, ok: applicable.filter((f) => f.status === "ok").length };
  document.querySelectorAll(".count").forEach((c) => { c.textContent = counts[c.dataset.count]; });
  document.querySelectorAll(".filter").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.filter === state.filter)));

  const shown = applicable.filter(matches);
  if (shown.length) {
    $("checks").replaceChildren(...shown.map(checkRow));
  } else {
    const li = el("li", "empty", state.filter === "problems" && !state.query ? "No problems right now." : "No checks match this filter.");
    const reset = el("button", "btn", "Show all checks");
    reset.type = "button";
    reset.addEventListener("click", () => { setFilter("all"); $("search").value = ""; state.query = ""; renderChecks(); });
    li.appendChild(reset);
    $("checks").replaceChildren(li);
  }

  const other = data.findings.filter((f) => !isApplicable(f));
  $("na").hidden = other.length === 0 || state.filter !== "all";
  $("na-summary").textContent = `${other.length} ${plural(other.length, "check does", "checks do")} not apply here or ${plural(other.length, "was", "were")} skipped`;
  $("na-list").replaceChildren(...other.map(checkRow));
}

function problemItem(f) {
  const li = el("li", "problem");
  li.dataset.status = f.status;
  const h = el("h3");
  h.append(badge(f.status), el("span", null, f.title));
  li.appendChild(h);
  li.appendChild(el("p", "summary", f.summary));
  const dl = el("dl");
  const add = (label, value) => {
    if (!value) return;
    dl.appendChild(el("dt", null, label));
    const dd = el("dd");
    dd.appendChild(withCode(value));
    dl.appendChild(dd);
  };
  add("Why", f.reason);
  add("Next", f.recommendation);
  if (f.status === "unknown" && f.limitations && f.limitations.length) add("Note", f.limitations.join("; "));
  if (dl.childNodes.length) li.appendChild(dl);
  return li;
}

/* ---------------------------------------------------------------- render */

function render(data) {
  state.data = data;
  state.rowId = 0;
  $("host").textContent = data.hostname;
  document.title = `LinuxPilot live: ${data.hostname}`;

  const v = verdict(data.findings);
  $("verdict").dataset.status = v.status;
  $("verdict-text").textContent = v.text;
  $("verdict-sub").textContent = subline(data);

  recordHistory(data);
  renderTrends(data);

  const problems = data.findings.filter(isProblem).sort((a, b) => SEVERITY[b.status] - SEVERITY[a.status]);
  $("attention-section").hidden = problems.length === 0;
  $("attention").replaceChildren(...problems.map(problemItem));

  renderChecks();
  state.previous = new Map(data.findings.map((f) => [key(f), f.status]));
}

/* ---------------------------------------------------------------- live updates */

function showError(message) {
  $("error").textContent = message;
  $("error").hidden = !message;
}

function setLive(name) { document.querySelector(".live").dataset.state = name; }

function tick() {
  if (state.paused) { $("updated").textContent = "Paused"; return; }
  if (!state.lastOk) return;
  const ago = Math.max(0, Math.round((Date.now() - state.lastOk) / 1000));
  $("updated").textContent = ago < 2 ? "Updated just now" : `Updated ${ago} s ago`;
}

function currentInterval() {
  return state.interval || (state.data && state.data.interval_seconds) || 5;
}

async function refresh() {
  if (state.inFlight) return;
  state.inFlight = true;
  clearTimeout(state.timer);
  $("refresh").disabled = true;
  try {
    const headers = token ? { "X-LinuxPilot-Token": token } : {};
    const res = await fetch("/api/status", { headers, cache: "no-store" });
    if (res.status === 401) {
      setLive("lost");
      $("updated").textContent = "No access";
      showError("This dashboard needs its full access link. Copy the link printed in the terminal, including the part after #token=.");
      return; // a wrong token will not fix itself, so stop retrying
    }
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.error || `server returned ${res.status}`);
    }
    render(await res.json());
    if (state.interval === null && !state.pickerSynced) { buildIntervalPicker(); state.pickerSynced = true; }
    state.lastOk = Date.now();
    showError("");
    setLive(state.paused ? "paused" : "live");
    tick();
  } catch (err) {
    setLive("lost");
    $("updated").textContent = "Disconnected";
    showError("Lost connection to LinuxPilot. The serve command may have stopped. Start it again with linuxpilot serve; this page reconnects on its own.");
  } finally {
    state.inFlight = false;
    $("refresh").disabled = false;
    schedule();
  }
}

function schedule() {
  clearTimeout(state.timer);
  if (state.paused) return;
  state.timer = setTimeout(refresh, currentInterval() * 1000);
}

function togglePause() {
  state.paused = !state.paused;
  $("pause").textContent = state.paused ? "Resume" : "Pause";
  $("pause").setAttribute("aria-pressed", String(state.paused));
  setLive(state.paused ? "paused" : "live");
  if (state.paused) { clearTimeout(state.timer); tick(); } else refresh();
}

function buildIntervalPicker() {
  const select = $("interval");
  const current = currentInterval();
  const options = INTERVALS.includes(current) ? INTERVALS : [...INTERVALS, current].sort((a, b) => a - b);
  select.replaceChildren(...options.map((s) => {
    const o = el("option", null, s < 60 ? `${s} s` : `${s / 60} min`);
    o.value = String(s);
    o.selected = s === current;
    return o;
  }));
}

function setFilter(name) {
  state.filter = name;
  prefs.set("filter", name);
}

document.addEventListener("DOMContentLoaded", () => {
  buildIntervalPicker();
  $("interval").addEventListener("change", (ev) => {
    state.interval = Number(ev.target.value);
    prefs.set("interval", state.interval);
    schedule();
  });
  $("refresh").addEventListener("click", refresh);
  $("pause").addEventListener("click", togglePause);
  document.querySelectorAll(".filter").forEach((b) => b.addEventListener("click", () => { setFilter(b.dataset.filter); renderChecks(); }));
  $("search").addEventListener("input", (ev) => { state.query = ev.target.value.trim().toLowerCase(); renderChecks(); });
  setInterval(tick, 1000);
  refresh();
});
"""
