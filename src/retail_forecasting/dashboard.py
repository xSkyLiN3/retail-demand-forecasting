from __future__ import annotations


def dashboard_html() -> str:
    """Return the public, dependency-free portfolio dashboard shell."""
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta
    name="description"
    content="A read-only historical replay of a governed 14-day retail demand forecasting system."
  >
  <meta property="og:type" content="website">
  <meta property="og:title" content="Retail Forecast Lab · Cristóbal Vergara">
  <meta
    property="og:description"
    content="A governed ML forecasting case study that publishes its frozen no-go result."
  >
  <meta property="og:url" content="https://retail.nightstrike.cloud/">
  <meta name="twitter:card" content="summary">
  <title>Retail Forecast Lab · Cristóbal Vergara</title>
  <link rel="canonical" href="https://retail.nightstrike.cloud/">
  <link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="/assets/dashboard.css">
  <script defer src="/assets/dashboard.js"></script>
</head>
<body>
  <main>
    <nav class="portfolio-nav" aria-label="Project links">
      <a href="https://nightstrike.cloud/proyectos/retail-demand-forecasting/">Case study (ES)</a>
      <a href="https://github.com/xSkyLiN3/retail-demand-forecasting">GitHub</a>
      <a
        href="https://github.com/xSkyLiN3/retail-demand-forecasting/blob/v1.0.1/MODEL_CARD.md"
      >Model Card</a>
    </nav>

    <header class="hero">
      <p class="eyebrow">ML engineering · historical replay</p>
      <h1>Retail Forecast Lab</h1>
      <p class="lede">
        Explore a frozen 14-day demand forecast, development-calibrated uncertainty and
        the outcomes that were evaluated only after the evaluation policy was fixed.
      </p>

      <section class="verdict" aria-labelledby="verdict-title">
        <div>
          <span>Final holdout verdict</span>
          <strong id="verdict-title">NO-GO for operational use</strong>
        </div>
        <p>
          Empirical interval coverage reached <b>77.02%</b>, below the frozen
          <b>85% minimum</b> and <b>90% nominal target</b>. The result was published
          without tuning on the holdout.
        </p>
      </section>

      <section class="evidence-grid" aria-label="Frozen final evidence">
        <article class="evidence-card evidence-failed">
          <span>Interval coverage</span>
          <strong>77.02%</strong>
          <small>Failed · minimum 85%</small>
        </article>
        <article class="evidence-card">
          <span>WAPE</span>
          <strong>115.65%</strong>
          <small>Below alert threshold, still weak</small>
        </article>
        <article class="evidence-card">
          <span>Normalized bias</span>
          <strong>+5.93%</strong>
          <small>Passed · guardrail ±10%</small>
        </article>
        <article class="evidence-card">
          <span>Observed forecasts</span>
          <strong>1,680</strong>
          <small>20 SKUs · 14 horizons · 6 origins</small>
        </article>
      </section>

      <p class="model-decision">
        A learned Poisson challenger reduced confirmation WAPE by 11.94%, but failed
        the frozen bias and SKU-breadth gates. The seasonal baseline therefore remained champion.
      </p>
      <p class="notice">
        <strong>Educational historical demo.</strong> Sales are only a demand proxy.
        This service is not connected to a live retailer and is not validated for purchasing
        or inventory decisions.
      </p>
    </header>

    <section class="explorer" aria-labelledby="explorer-title">
      <div class="section-heading">
        <div>
          <p class="eyebrow">Reviewed evidence explorer</p>
          <h2 id="explorer-title">Inspect one coherent forecast slice</h2>
        </div>
        <p>
          Choose one issue date and one product. Each view contains exactly 14 forecast
          horizons and their observed outcomes.
        </p>
      </div>

      <div class="toolbar">
        <label for="run">Forecast issue date
          <select id="run" name="run" disabled>
            <option>Loading runs…</option>
          </select>
        </label>
        <label for="sku">Product SKU
          <select id="sku" name="sku" disabled>
            <option>Loading products…</option>
          </select>
        </label>
        <button id="reset" type="button">Reset view</button>
      </div>

      <section class="kpis" aria-label="Selected slice metrics">
        <article class="metric-card">
          <span>Selected coverage</span>
          <strong id="coverage">—</strong>
          <small id="coverage-note">Exploratory slice · 85% reference</small>
        </article>
        <article class="metric-card">
          <span>Selected WAPE</span>
          <strong id="wape">—</strong>
          <small>Absolute error / observed units</small>
        </article>
        <article class="metric-card">
          <span>Selected bias</span>
          <strong id="bias">—</strong>
          <small>(forecast − actual) / observed units</small>
        </article>
        <article class="metric-card">
          <span>Observed points</span>
          <strong id="observed">—</strong>
          <small>Expected: 14 horizons</small>
        </article>
      </section>

      <section class="split">
        <article class="panel chart-panel">
          <div class="panel-heading">
            <div>
              <h3>Forecast and calibrated interval</h3>
              <p id="chart-context">Loading reviewed evidence…</p>
            </div>
            <div class="legend" aria-label="Chart legend">
              <span><i class="legend-forecast"></i>Forecast</span>
              <span><i class="legend-interval"></i>Interval</span>
              <span><i class="legend-observed"></i>Observed</span>
            </div>
          </div>
          <div id="visual" class="loading" aria-live="polite">Loading forecast evidence…</div>
        </article>

        <aside class="panel reading-guide">
          <h3>What this view shows</h3>
          <dl>
            <div>
              <dt>Point forecast</dt>
              <dd>The frozen seven-day seasonal-naive champion.</dd>
            </div>
            <div>
              <dt>Interval</dt>
              <dd>Calibrated using development errors only, before the final outcomes.</dd>
            </div>
            <div>
              <dt>Observed point</dt>
              <dd>The historical outcome used for monitoring after the forecast was issued.</dd>
            </div>
          </dl>
          <p id="summary">Waiting for data.</p>
        </aside>
      </section>

      <section class="panel ledger-panel">
        <div class="panel-heading">
          <div>
            <h3>Forecast ledger</h3>
            <p>Every forecast, interval and outcome in the selected 14-day horizon.</p>
          </div>
        </div>
        <div id="ledger" class="loading" aria-live="polite">Loading records…</div>
      </section>
    </section>

    <footer>
      <p>Read-only service · immutable reviewed snapshot · no external analytics or assets</p>
      <nav aria-label="Evidence links">
        <a href="https://doi.org/10.24432/C5CG6D">UCI source · CC BY 4.0</a>
        <a href="https://github.com/xSkyLiN3/retail-demand-forecasting">Source code</a>
        <a href="https://nightstrike.cloud">Cristóbal Vergara</a>
      </nav>
    </footer>
  </main>
</body>
</html>
"""


def dashboard_css() -> str:
    """Return the dashboard stylesheet as a first-party asset."""
    return """:root {
  --bg: #06101c;
  --panel: #0c1a2b;
  --panel-soft: #102238;
  --text: #f4f7fb;
  --muted: #96a9c1;
  --line: #213a55;
  --cyan: #42ddc7;
  --blue: #7caeff;
  --amber: #ffc86b;
  --red: #ff8791;
  --green: #75e0a7;
}

* { box-sizing: border-box; }

html { color-scheme: dark; }

body {
  margin: 0;
  background:
    radial-gradient(circle at 82% 0, #12365d 0, transparent 34rem),
    radial-gradient(circle at 10% 28%, #0b3b3b55 0, transparent 25rem),
    var(--bg);
  color: var(--text);
  font:
    15px/1.55 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
    "Segoe UI", sans-serif;
}

a { color: var(--cyan); }

a:focus-visible,
button:focus-visible,
select:focus-visible {
  outline: 3px solid #ffffff;
  outline-offset: 3px;
}

main {
  width: min(1180px, calc(100% - 32px));
  margin: auto;
  padding: 28px 0 64px;
}

.portfolio-nav,
footer nav {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 18px;
}

.portfolio-nav {
  justify-content: flex-end;
  margin-bottom: 40px;
  font-size: 0.86rem;
}

.hero { max-width: 1040px; }

.eyebrow {
  margin: 0;
  color: var(--cyan);
  font-size: 0.74rem;
  font-weight: 800;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}

h1,
h2,
h3,
p { margin-top: 0; }

h1 {
  margin: 0.35rem 0 1rem;
  font-size: clamp(2.4rem, 7vw, 4.8rem);
  line-height: 0.95;
  letter-spacing: -0.05em;
}

h2 {
  margin: 0.25rem 0 0;
  font-size: clamp(1.65rem, 4vw, 2.5rem);
  letter-spacing: -0.035em;
}

h3 { margin-bottom: 8px; font-size: 1rem; }

.lede {
  max-width: 780px;
  color: var(--muted);
  font-size: 1.08rem;
}

.verdict {
  display: grid;
  grid-template-columns: minmax(220px, 0.72fr) 1.28fr;
  gap: 24px;
  align-items: center;
  margin: 28px 0 18px;
  padding: 20px;
  border: 1px solid #8e3842;
  border-radius: 16px;
  background: linear-gradient(135deg, #351821, #1a1824);
  box-shadow: 0 18px 70px #0005;
}

.verdict span,
.evidence-card span,
.metric-card span {
  display: block;
  color: var(--muted);
  font-size: 0.72rem;
  font-weight: 800;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.verdict strong {
  display: block;
  margin-top: 4px;
  color: var(--red);
  font-size: clamp(1.25rem, 3vw, 1.8rem);
}

.verdict p { margin: 0; color: #f6ced2; }

.evidence-grid,
.kpis,
.split { display: grid; gap: 14px; }

.evidence-grid,
.kpis { grid-template-columns: repeat(4, 1fr); }

.evidence-card,
.metric-card,
.panel {
  border: 1px solid var(--line);
  border-radius: 16px;
  background: linear-gradient(180deg, #0e2034, var(--panel));
  box-shadow: 0 14px 45px #0003;
}

.evidence-card,
.metric-card { padding: 17px; }

.evidence-card strong,
.metric-card strong {
  display: block;
  margin: 3px 0;
  font-size: 1.65rem;
  font-variant-numeric: tabular-nums;
}

.evidence-card small,
.metric-card small { color: var(--muted); }

.evidence-failed { border-color: #7f3a43; }
.evidence-failed strong { color: var(--red); }

.model-decision,
.notice {
  max-width: 930px;
  margin: 18px 0 0;
  padding-left: 16px;
  border-left: 3px solid var(--cyan);
  color: #c8d7e8;
}

.notice { border-color: var(--amber); color: #ffe0a4; }

.explorer {
  margin-top: 70px;
  padding-top: 34px;
  border-top: 1px solid var(--line);
}

.section-heading {
  display: grid;
  grid-template-columns: 1fr minmax(280px, 0.72fr);
  gap: 30px;
  align-items: end;
  margin-bottom: 22px;
}

.section-heading > p { margin: 0; color: var(--muted); }

.toolbar {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
  gap: 14px;
  margin-bottom: 16px;
  padding: 16px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: #0d1b2de8;
}

label {
  display: grid;
  gap: 5px;
  color: var(--muted);
  font-size: 0.75rem;
  font-weight: 800;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

select,
button {
  min-height: 44px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: #091728;
  color: var(--text);
  padding: 0 12px;
  font: inherit;
}

button {
  align-self: end;
  border: 0;
  background: var(--cyan);
  color: #04201c;
  cursor: pointer;
  font-weight: 800;
}

button:hover { filter: brightness(1.08); }
button:disabled, select:disabled { cursor: wait; opacity: 0.65; }

.metric-card.metric-failed { border-color: #7f3a43; }
.metric-card.metric-failed strong { color: var(--red); }
.metric-card.metric-passed strong { color: var(--green); }

.kpis { margin-bottom: 14px; }

.split { grid-template-columns: minmax(0, 1.55fr) minmax(280px, 0.75fr); }

.panel { padding: 18px; }

.panel-heading {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
  margin-bottom: 14px;
}

.panel-heading p,
.reading-guide dd,
.reading-guide > p { color: var(--muted); }

.panel-heading p { margin: 0; font-size: 0.84rem; }

.legend { display: flex; flex-wrap: wrap; gap: 10px; color: var(--muted); font-size: 0.72rem; }
.legend span { display: inline-flex; align-items: center; gap: 5px; }
.legend i { width: 18px; height: 3px; border-radius: 3px; background: var(--cyan); }
.legend .legend-interval { height: 8px; background: #73a7ff55; border: 1px solid var(--blue); }
.legend .legend-observed { width: 8px; height: 8px; border-radius: 50%; background: var(--amber); }

.chart { width: 100%; height: 330px; border-radius: 11px; background: #081625; }
.chart text { fill: var(--muted); font-size: 11px; }
.chart .grid { stroke: #203853; stroke-width: 1; }
.chart .interval-band { fill: #73a7ff22; }
.chart .interval-edge { fill: none; stroke: var(--blue); stroke-width: 1.2; }
.chart .forecast-line { fill: none; stroke: var(--cyan); stroke-width: 3; }
.chart .observed-line { fill: none; stroke: var(--amber); stroke-width: 2; stroke-dasharray: 5 5; }
.chart .observed-dot { fill: var(--amber); }

.reading-guide dl { display: grid; gap: 14px; margin: 18px 0; }
.reading-guide dl div { padding-bottom: 13px; border-bottom: 1px solid var(--line); }
.reading-guide dt { color: var(--text); font-weight: 800; }
.reading-guide dd { margin: 3px 0 0; }

.ledger-panel { margin-top: 14px; }
.table-wrap { overflow: auto; max-height: 470px; }
table { width: 100%; min-width: 760px; border-collapse: collapse; }
th, td {
  padding: 10px 9px;
  border-bottom: 1px solid var(--line);
  text-align: right;
  font-variant-numeric: tabular-nums;
}
th {
  position: sticky;
  top: 0;
  background: var(--panel);
  color: var(--muted);
  font-size: 0.7rem;
  text-transform: uppercase;
}
th:nth-child(-n + 2), td:nth-child(-n + 2) { text-align: left; }

.badge {
  display: inline-block;
  padding: 3px 8px;
  border-radius: 99px;
  font-size: 0.72rem;
  font-weight: 800;
}
.badge-covered { background: #123d33; color: var(--green); }
.badge-missed { background: #49232a; color: var(--red); }

.empty,
.error,
.loading {
  display: grid;
  min-height: 190px;
  place-items: center;
  color: var(--muted);
  text-align: center;
}
.error { color: var(--red); }

footer {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  margin-top: 28px;
  color: var(--muted);
  font-size: 0.8rem;
}
footer p { margin: 0; }

@media (max-width: 900px) {
  .evidence-grid,
  .kpis { grid-template-columns: 1fr 1fr; }
  .split,
  .section-heading { grid-template-columns: 1fr; }
}

@media (max-width: 700px) {
  main { width: min(100% - 20px, 1180px); padding-top: 20px; }
  .portfolio-nav { justify-content: flex-start; margin-bottom: 28px; }
  .verdict,
  .toolbar { grid-template-columns: 1fr; }
  .panel-heading,
  footer { flex-direction: column; }
}

@media (max-width: 470px) {
  .evidence-grid,
  .kpis { grid-template-columns: 1fr; }
  .chart { height: 280px; }
}
"""


def dashboard_favicon() -> str:
    """Return a small first-party SVG mark for browser tabs."""
    return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="14" fill="#06101c"/>
  <path d="M14 45V30l10 8 9-18 7 11 10-14" fill="none" stroke="#42ddc7"
        stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""


def dashboard_javascript() -> str:
    """Return the dashboard client as a first-party asset."""
    return """(() => {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const byId = (id) => document.getElementById(id);
  const state = { runs: [], skus: [] };

  function percent(value, { signed = false, digits = 1 } = {}) {
    if (!Number.isFinite(value)) return "Not evaluable";
    const rendered = `${(100 * value).toFixed(digits)}%`;
    return signed && value > 0 ? `+${rendered}` : rendered;
  }

  function message(container, text, className) {
    const node = document.createElement("div");
    node.className = className;
    node.textContent = text;
    container.replaceChildren(node);
  }

  async function read(path) {
    const response = await fetch(path, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`API ${response.status}`);
    const payload = await response.json();
    if (!payload || !Array.isArray(payload.items)) throw new Error("Invalid API response");
    return payload.items;
  }

  function setOptions(select, options) {
    select.replaceChildren();
    for (const item of options) select.append(new Option(item.label, item.value));
    select.disabled = options.length === 0;
  }

  function metrics(rows) {
    if (!rows.length) {
      for (const id of ["coverage", "wape", "bias", "observed"]) byId(id).textContent = "—";
      byId("coverage-note").textContent = "No observed outcomes";
      return;
    }

    const actual = rows.reduce((sum, row) => sum + Number(row.actual), 0);
    const error = rows.reduce((sum, row) => sum + Number(row.absolute_error), 0);
    const bias = rows.reduce(
      (sum, row) => sum + Number(row.prediction) - Number(row.actual),
      0,
    );
    const coverage = rows.filter((row) => row.covered === true).length / rows.length;

    byId("coverage").textContent = percent(coverage);
    byId("wape").textContent = actual ? percent(error / actual) : "Not evaluable";
    byId("bias").textContent = actual
      ? percent(bias / actual, { signed: true })
      : "Not evaluable";
    byId("observed").textContent = String(rows.length);

    byId("coverage-note").textContent = "Compared with 85% reference · exploratory only";
  }

  function svgNode(name, attributes = {}, text = "") {
    const node = document.createElementNS(SVG_NS, name);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
    if (text) node.textContent = text;
    return node;
  }

  function linePath(points) {
    return points.map(([x, y], index) => `${index ? "L" : "M"}${x},${y}`).join(" ");
  }

  function chart(forecasts, monitoring) {
    const visual = byId("visual");
    if (!forecasts.length) {
      message(visual, "No forecasts match this selection.", "empty");
      return;
    }

    const rows = [...forecasts].sort((a, b) => Number(a.horizon) - Number(b.horizon));
    const observed = new Map(monitoring.map((row) => [Number(row.horizon), row]));
    const width = 760;
    const height = 330;
    const left = 52;
    const right = 24;
    const top = 24;
    const bottom = 42;
    const values = rows.flatMap((row) => [
      Number(row.upper),
      Number(row.prediction),
      Number(observed.get(Number(row.horizon))?.actual ?? 0),
    ]);
    const maximum = Math.max(1, ...values.filter(Number.isFinite));
    const x = (index) => left + (index * (width - left - right)) / Math.max(1, rows.length - 1);
    const y = (value) => top + (height - top - bottom) * (1 - Number(value) / maximum);

    const svg = svgNode("svg", {
      class: "chart",
      role: "img",
      viewBox: `0 0 ${width} ${height}`,
      "aria-label":
        "Fourteen-day forecast with calibrated interval and observed positive invoiced units",
    });

    for (const fraction of [0, 0.5, 1]) {
      const value = maximum * fraction;
      const yPosition = y(value);
      svg.append(
        svgNode("line", {
          class: "grid",
          x1: left,
          x2: width - right,
          y1: yPosition,
          y2: yPosition,
        }),
        svgNode("text", { x: 7, y: yPosition + 4 }, value.toFixed(0)),
      );
    }

    const upper = rows.map((row, index) => [x(index), y(row.upper)]);
    const lower = rows.map((row, index) => [x(index), y(row.lower)]);
    const prediction = rows.map((row, index) => [x(index), y(row.prediction)]);
    const actual = rows.map((row, index) => [
      x(index),
      y(observed.get(Number(row.horizon))?.actual ?? 0),
    ]);

    const bandPath = `${linePath(upper)} ${linePath([...lower].reverse()).replace(/^M/, "L")} Z`;
    svg.append(
      svgNode("path", { class: "interval-band", d: bandPath }),
      svgNode("path", { class: "interval-edge", d: linePath(upper) }),
      svgNode("path", { class: "interval-edge", d: linePath(lower) }),
      svgNode("path", { class: "forecast-line", d: linePath(prediction) }),
      svgNode("path", { class: "observed-line", d: linePath(actual) }),
    );

    for (const [xPosition, yPosition] of actual) {
      svg.append(svgNode("circle", {
        class: "observed-dot",
        cx: xPosition,
        cy: yPosition,
        r: 3.4,
      }));
    }

    svg.append(
      svgNode("text", { x: left, y: height - 13 }, String(rows[0].forecast_date)),
      svgNode(
        "text",
        { x: width - right, y: height - 13, "text-anchor": "end" },
        String(rows.at(-1).forecast_date),
      ),
      svgNode(
        "text",
        { x: width / 2, y: height - 13, "text-anchor": "middle" },
        "Forecast horizon",
      ),
    );

    visual.className = "";
    visual.replaceChildren(svg);
  }

  function appendCell(row, value) {
    const cell = document.createElement("td");
    cell.textContent = String(value);
    row.append(cell);
  }

  function ledger(forecasts, monitoring) {
    const container = byId("ledger");
    if (!forecasts.length) {
      message(container, "No ledger rows match this selection.", "empty");
      return;
    }

    const observed = new Map(monitoring.map((row) => [Number(row.horizon), row]));
    const wrapper = document.createElement("div");
    wrapper.className = "table-wrap";
    const table = document.createElement("table");
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    for (const label of [
      "Date", "SKU", "H", "Forecast", "Lower", "Upper", "Actual", "Coverage",
    ]) {
      const header = document.createElement("th");
      header.scope = "col";
      header.textContent = label;
      headRow.append(header);
    }
    head.append(headRow);
    const body = document.createElement("tbody");

    for (const forecast of [...forecasts].sort(
      (a, b) => Number(a.horizon) - Number(b.horizon),
    )) {
      const result = observed.get(Number(forecast.horizon));
      const row = document.createElement("tr");
      appendCell(row, forecast.forecast_date);
      appendCell(row, forecast.sku);
      appendCell(row, forecast.horizon);
      appendCell(row, Number(forecast.prediction).toFixed(1));
      appendCell(row, Number(forecast.lower).toFixed(1));
      appendCell(row, Number(forecast.upper).toFixed(1));
      appendCell(row, result ? Number(result.actual).toFixed(1) : "—");
      const statusCell = document.createElement("td");
      const badge = document.createElement("span");
      const covered = result?.covered === true;
      badge.className = `badge ${covered ? "badge-covered" : "badge-missed"}`;
      badge.textContent = result ? (covered ? "covered" : "missed") : "pending";
      statusCell.append(badge);
      row.append(statusCell);
      body.append(row);
    }

    table.append(head, body);
    wrapper.append(table);
    container.className = "";
    container.replaceChildren(wrapper);
  }

  async function load() {
    const runId = byId("run").value;
    const sku = byId("sku").value;
    if (!runId || !sku) return;

    message(byId("visual"), "Loading forecast evidence…", "loading");
    message(byId("ledger"), "Loading records…", "loading");
    try {
      const query = new URLSearchParams({ run_id: runId, sku, limit: "2000" });
      const [forecasts, monitoring] = await Promise.all([
        read(`/api/forecasts?${query}`),
        read(`/api/monitoring?${query}`),
      ]);
      metrics(monitoring);
      chart(forecasts, monitoring);
      ledger(forecasts, monitoring);
      const run = state.runs.find((item) => item.value === runId);
      byId("chart-context").textContent = `${sku} · issued ${run?.cutoff ?? "unknown"}`;
      byId("summary").textContent =
        `${forecasts.length} forecasts are shown; ${monitoring.length} have known outcomes. ` +
        "This 14-point slice is exploratory; frozen gates apply to reviewed evaluation scopes.";
    } catch (error) {
      const text = `Unable to load demo data: ${error.message}`;
      message(byId("visual"), text, "error");
      message(byId("ledger"), text, "error");
      byId("summary").textContent = text;
    }
  }

  async function bootstrap() {
    try {
      const [forecasts, monitoring] = await Promise.all([
        read("/api/forecasts?limit=2000"),
        read("/api/monitoring?limit=2000"),
      ]);
      if (!forecasts.length || !monitoring.length) throw new Error("Reviewed snapshot is empty");

      const runMap = new Map();
      for (const row of forecasts) runMap.set(String(row.run_id), String(row.cutoff));
      state.runs = [...runMap.entries()]
        .map(([value, cutoff]) => ({ value, cutoff, label: `Issued ${cutoff}` }))
        .sort((a, b) => a.cutoff.localeCompare(b.cutoff));
      state.skus = [...new Set(forecasts.map((row) => String(row.sku)))].sort();

      setOptions(byId("run"), state.runs);
      setOptions(byId("sku"), state.skus.map((value) => ({ value, label: value })));
      byId("run").value = state.runs.at(-1).value;
      byId("sku").value = state.skus[0];
      await load();
    } catch (error) {
      const text = `Unable to initialize the reviewed demo: ${error.message}`;
      message(byId("visual"), text, "error");
      message(byId("ledger"), text, "error");
      byId("summary").textContent = text;
    }
  }

  byId("run").addEventListener("change", load);
  byId("sku").addEventListener("change", load);
  byId("reset").addEventListener("click", () => {
    byId("run").value = state.runs.at(-1)?.value ?? "";
    byId("sku").value = state.skus[0] ?? "";
    load();
  });

  bootstrap();
})();
"""
