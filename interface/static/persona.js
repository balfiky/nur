import { createSurfaceState, renderMarkdown } from "/assets/lib/shared.js";

const state = createSurfaceState({
    payload: null,
    selectedSessionKey: "",
    refreshTimer: null,
    historyBySession: {},
}, {});

  const modulatorNames = ["arousal", "valence", "certainty", "bonding", "energy", "resolution"];
  const emotionSeries = [
    ["arousal", "Arousal", "#2563eb"],
    ["valence", "Valence", "#027a48"],
    ["certainty", "Certainty", "#7c3aed"],
    ["energy", "Energy", "#b54708"],
  ];
  const relationshipSeries = [
    ["trust", "Trust", "#0f766e"],
    ["bonding", "Bonding", "#2563eb"],
    ["resolution", "Resolution", "#b42318"],
  ];

  const els = {
    status: document.getElementById("dashboardStatus"),
    refreshBtn: document.getElementById("refreshBtn"),
    autoRefresh: document.getElementById("autoRefresh"),
    tokenBtn: document.getElementById("tokenBtn"),
    tokenDialog: document.getElementById("tokenDialog"),
    apiTokenInput: document.getElementById("apiTokenInput"),
    saveTokenBtn: document.getElementById("saveTokenBtn"),
    toast: document.getElementById("toast"),
    summaryGrid: document.getElementById("summaryGrid"),
    sessionList: document.getElementById("sessionList"),
    selectedChannel: document.getElementById("selectedChannel"),
    personaHeading: document.getElementById("personaHeading"),
    emptyState: document.getElementById("emptyState"),
    personaDetail: document.getElementById("personaDetail"),
  };

  function getApiToken() {
    return sessionStorage.getItem("nur_api_token") || "";
  }

  function setApiToken(token) {
    if (token) sessionStorage.setItem("nur_api_token", token);
    else sessionStorage.removeItem("nur_api_token");
  }

  async function authedFetch(url, opts) {
    const next = Object.assign({}, opts || {});
    next.headers = Object.assign({}, next.headers || {});
    const token = getApiToken();
    if (token) next.headers.Authorization = "Bearer " + token;
    const res = await fetch(url, next);
    if (res.status === 401) {
      openTokenDialog();
      throw new Error("Unauthorized. Set the API token for this browser session.");
    }
    return res;
  }

  function setStatus(text, tone) {
    els.status.textContent = text;
    els.status.className = "status-pill" + (tone ? " " + tone : "");
  }

  function showToast(message, tone) {
    els.toast.textContent = message;
    els.toast.hidden = false;
    els.toast.className = "toast" + (tone ? " " + tone : "");
    clearTimeout(showToast._timer);
    showToast._timer = setTimeout(() => { els.toast.hidden = true; }, 3200);
  }

  function openTokenDialog() {
    els.apiTokenInput.value = getApiToken();
    els.tokenDialog.showModal();
    els.apiTokenInput.focus();
  }

  async function loadDashboard() {
    setStatus("Loading");
    const res = await authedFetch("/admin/persona/state");
    const data = await res.json();
    if (!res.ok) throw new Error(responseErrorMessage(data, "Persona dashboard HTTP " + res.status));
    state.payload = data;
    if (!state.selectedSessionKey || !(data.sessions || []).some((item) => item.session_key === state.selectedSessionKey)) {
      state.selectedSessionKey = (data.sessions || [])[0]?.session_key || "";
    }
    renderDashboard();
    setStatus("Live", "ok");
  }

  function renderDashboard() {
    const payload = state.payload || {};
    const sessions = payload.sessions || [];
    captureHistory(payload.generated_at, sessions);
    renderSummary(payload);
    renderSessions(sessions);
    const selected = sessions.find((item) => item.session_key === state.selectedSessionKey) || null;
    renderPersona(selected);
  }

  function captureHistory(timestamp, sessions) {
    const now = Number(timestamp || Date.now() / 1000);
    for (const session of sessions || []) {
      const sample = sampleFromSession(session, now);
      if (!sample) continue;
      const key = session.session_key;
      const history = state.historyBySession[key] || [];
      const previous = history[history.length - 1];
      if (!previous || previous.last_activity !== sample.last_activity || now - previous.timestamp >= 3.5) {
        history.push(sample);
        state.historyBySession[key] = history.slice(-48);
      }
    }
  }

  function sampleFromSession(session, timestamp) {
    const view = session.persona_view || {};
    const emotions = view.emotions || {};
    const relationship = view.relationship || {};
    const mods = emotions.modulators || {};
    const sample = {
      timestamp,
      last_activity: Number(session.last_activity || 0),
      emotion: emotions.simple_label || emotions.primary || "neutral",
      trust: safeNumber(relationship.trust && relationship.trust.value, 0),
      strategy: relationship.strategy || "none",
    };
    for (const name of modulatorNames) {
      sample[name] = safeNumber(mods[name] && mods[name].value, 0);
    }
    return sample;
  }

  function renderSummary(payload) {
    const channels = payload.channel_counts || {};
    const channelText = Object.entries(channels)
      .map(([name, count]) => `${name} ${count}`)
      .join(" · ") || "none";
    const selected = (payload.sessions || []).find((item) => item.session_key === state.selectedSessionKey);
    const cards = [
      ["Active Sessions", payload.count || 0, channelText],
      ["Selected Channel", selected ? selected.platform : "none", selected ? selected.session_key : "No active session"],
      ["Last Refresh", payload.generated_at ? formatTime(payload.generated_at) : "", "runtime read only"],
      ["Dashboard Boundary", "observe only", "no pipeline call, no session creation"],
    ];
    els.summaryGrid.innerHTML = cards.map(([label, value, foot]) => `
      <article class="summary-card">
        <div class="metric-label">${escapeHtml(label)}</div>
        <div class="metric-value">${escapeHtml(value)}</div>
        <div class="metric-foot">${escapeHtml(foot || "")}</div>
      </article>
    `).join("");
  }

  function renderSessions(sessions) {
    if (!sessions.length) {
      els.sessionList.innerHTML = `<div class="empty-state">No active Web, Telegram, or runtime sessions.</div>`;
      return;
    }
    els.sessionList.innerHTML = sessions.map((session) => {
      const view = session.persona_view || {};
      const emotions = view.emotions || {};
      const active = session.session_key === state.selectedSessionKey;
      return `
        <button class="session-btn ${active ? "active" : ""}" data-session-key="${escapeAttr(session.session_key)}">
          <div class="session-row">
            <strong>${escapeHtml(session.user_id || "user")}</strong>
            <span class="channel-chip">${escapeHtml(session.platform || "channel")}</span>
          </div>
          <div class="session-key">${escapeHtml(session.session_key)}</div>
          <div class="session-row">
            <span>${escapeHtml(emotions.simple_label || emotions.primary || "neutral")}</span>
            <span class="muted">${escapeHtml(formatIdle(session.idle_seconds))}</span>
          </div>
        </button>
      `;
    }).join("");
  }

  function renderPersona(session) {
    if (!session) {
      els.selectedChannel.textContent = "No channel selected";
      els.personaHeading.textContent = "Persona State";
      els.emptyState.hidden = false;
      els.personaDetail.hidden = true;
      return;
    }
    const view = session.persona_view || {};
    const emotions = view.emotions || {};
    els.selectedChannel.textContent = `${session.platform || "channel"} · ${session.session_key}`;
    els.personaHeading.textContent = session.user_id || "Persona State";
    els.emptyState.hidden = true;
    els.personaDetail.hidden = false;

    document.getElementById("emotionLabel").textContent = emotions.simple_label || emotions.primary || "neutral";
    document.getElementById("emotionSub").textContent = [
      `best-fit state: ${emotions.primary || "neutral"}`,
      `intensity ${formatNumber(emotions.intensity)}`,
      `confidence ${formatNumber(emotions.confidence)}`,
    ].join(" · ");
    renderChips("emotionDrivers", emotions.drivers || ["balanced state"]);
    renderModulators(emotions.modulators || {});
    renderObservability(session, view);
    renderPerception(view.perception || {});
    renderRelationship(view.relationship || {});
    renderLife(view.life || {});
    renderMemory(view.memory || {});
    renderSkillsTools(view.skills_tools || {});
    renderExplanation(view.explanation || {});
  }

  function renderObservability(session, view) {
    const history = state.historyBySession[session.session_key] || [];
    const emotions = view.emotions || {};
    const relationship = view.relationship || {};
    renderStateRadar(emotions.modulators || {});
    renderLineChart("emotionTrend", history, emotionSeries);
    renderLineChart("relationshipArc", history, relationshipSeries);
    renderLegend("emotionLegend", emotionSeries);
    renderLegend("relationshipLegend", relationshipSeries);
    document.getElementById("historyCount").textContent = `${history.length} sample${history.length === 1 ? "" : "s"}`;
    renderTurnFlow(view, relationship);
  }

  function renderStateRadar(modulators) {
    const size = 280;
    const center = size / 2;
    const radius = 94;
    const axes = modulatorNames.map((name, index) => {
      const angle = -Math.PI / 2 + (index * Math.PI * 2) / modulatorNames.length;
      const value = clamp(safeNumber(modulators[name] && modulators[name].value, 0), 0, 1);
      return {
        name,
        value,
        x: center + Math.cos(angle) * radius * value,
        y: center + Math.sin(angle) * radius * value,
        ax: center + Math.cos(angle) * radius,
        ay: center + Math.sin(angle) * radius,
        lx: center + Math.cos(angle) * (radius + 28),
        ly: center + Math.sin(angle) * (radius + 28),
      };
    });
    const rings = [0.33, 0.66, 1].map((scale) => polygonPoints(
      axes.map((axis) => ({
        x: center + (axis.ax - center) * scale,
        y: center + (axis.ay - center) * scale,
      }))
    ));
    const shape = polygonPoints(axes);
    document.getElementById("stateRadar").innerHTML = `
      <svg class="radar-svg" viewBox="0 0 ${size} ${size}" role="img" aria-label="Current six-modulator state map">
        ${rings.map((points) => `<polygon class="radar-ring" points="${points}"></polygon>`).join("")}
        ${axes.map((axis) => `<line class="radar-axis" x1="${center}" y1="${center}" x2="${axis.ax.toFixed(1)}" y2="${axis.ay.toFixed(1)}"></line>`).join("")}
        <polygon class="radar-shape" points="${shape}"></polygon>
        ${axes.map((axis) => `<circle class="radar-point" cx="${axis.x.toFixed(1)}" cy="${axis.y.toFixed(1)}" r="4"></circle>`).join("")}
        ${axes.map((axis) => `<text class="radar-label" x="${axis.lx.toFixed(1)}" y="${axis.ly.toFixed(1)}" text-anchor="middle">${escapeHtml(shortLabel(axis.name))}</text>`).join("")}
      </svg>
    `;
  }

  function renderLineChart(id, history, series) {
    const width = 520;
    const height = 170;
    const pad = { left: 34, right: 14, top: 14, bottom: 26 };
    const chartWidth = width - pad.left - pad.right;
    const chartHeight = height - pad.top - pad.bottom;
    const samples = history.length ? history : [{}];
    const xFor = (index) => pad.left + (samples.length <= 1 ? chartWidth : (index / (samples.length - 1)) * chartWidth);
    const yFor = (value) => pad.top + (1 - clamp(safeNumber(value, 0), 0, 1)) * chartHeight;
    const grid = [0, 0.25, 0.5, 0.75, 1].map((value) => {
      const y = yFor(value);
      return `<line class="chart-grid-line" x1="${pad.left}" y1="${y.toFixed(1)}" x2="${(width - pad.right).toFixed(1)}" y2="${y.toFixed(1)}"></line>`;
    }).join("");
    const paths = series.map(([key]) => {
      const points = samples.map((sample, index) => `${xFor(index).toFixed(1)},${yFor(sample[key]).toFixed(1)}`);
      return `<polyline class="chart-line chart-${escapeAttr(key)}" points="${points.join(" ")}"></polyline>`;
    }).join("");
    const latest = series.map(([key, label]) => {
      const last = history[history.length - 1] || {};
      const x = xFor(Math.max(samples.length - 1, 0));
      const y = yFor(last[key]);
      return `<circle class="chart-dot chart-${escapeAttr(key)}" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3.5"><title>${escapeHtml(label)} ${formatNumber(last[key])}</title></circle>`;
    }).join("");
    const empty = history.length < 2
      ? `<text class="chart-empty" x="${width / 2}" y="${height / 2}" text-anchor="middle">Collecting samples</text>`
      : "";
    document.getElementById(id).innerHTML = `
      <svg class="line-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeAttr(id)} progress chart">
        ${grid}
        <line class="chart-axis" x1="${pad.left}" y1="${(height - pad.bottom).toFixed(1)}" x2="${(width - pad.right).toFixed(1)}" y2="${(height - pad.bottom).toFixed(1)}"></line>
        <text class="chart-tick" x="8" y="${yFor(1).toFixed(1)}">1.0</text>
        <text class="chart-tick" x="8" y="${yFor(0.5).toFixed(1)}">0.5</text>
        <text class="chart-tick" x="8" y="${yFor(0).toFixed(1)}">0.0</text>
        ${paths}
        ${latest}
        ${empty}
      </svg>
    `;
  }

  function renderLegend(id, series) {
    document.getElementById(id).innerHTML = series.map(([key, label]) => `
      <span class="legend-item"><i class="chart-${escapeAttr(key)}"></i>${escapeHtml(label)}</span>
    `).join("");
  }

  function renderTurnFlow(view, relationship) {
    const perception = view.perception || {};
    const life = view.life || {};
    const memory = view.memory || {};
    const tools = view.skills_tools || {};
    const steps = [
      ["Perception", perception.target || "none", perception.summary || "No social read."],
      ["Strategy", relationship.strategy || "none", relationship.strategy_reason || "No strategy trace."],
      ["Memory", memory.relationship_context_used ? `${memory.open_loop_count || 0} loop(s)` : "not used", `${memory.long_term_count || 0} long-term · ${memory.semantic_count || 0} semantic`],
      ["Life", life.context_available ? `${Object.keys(life.active_pressures || {}).length} pressure(s)` : "neutral", `${Object.keys(life.effects || {}).length} bounded effect(s)`],
      ["Tools", tools.tools_used ? `${tools.tools_used} used` : "none used", tools.summary || "No tools used."],
      ["Response", (view.explanation || {}).strategy ? "explained" : "recorded", (view.explanation || {}).limits || "No limits note."],
    ];
    document.getElementById("turnFlow").innerHTML = steps.map(([title, value, detail], index) => `
      <article class="flow-node">
        <span>${index + 1}</span>
        <strong>${escapeHtml(title)}</strong>
        <em>${escapeHtml(value)}</em>
        <small class="markdown-copy">${renderMarkdown(detail)}</small>
      </article>
    `).join("");
  }

  function renderModulators(modulators) {
    const names = ["arousal", "valence", "certainty", "bonding", "energy", "resolution"];
    document.getElementById("modulatorGrid").innerHTML = names.map((name) => {
      const item = modulators[name] || {};
      const value = clamp(Number(item.value || 0), 0, 1);
      return `
        <article class="mod-card">
          <div class="mod-head">
            <strong>${escapeHtml(name)}</strong>
            <span class="${deltaClass(item.delta)}">${formatDelta(item.delta)}</span>
          </div>
          <div class="bar"><progress value="${Math.round(value * 100)}" max="100" aria-hidden="true"></progress></div>
          <div class="metric-foot">${escapeHtml(item.level || "")} · ${escapeHtml(item.meaning || "")} · ${value.toFixed(2)}</div>
        </article>
      `;
    }).join("");
  }

  function renderPerception(perception) {
    renderKv("perceptionList", [
      ["Summary", perception.summary || "No social perception recorded."],
      ["Target", perception.target || "none"],
      ["Social move", perception.social_move || "none"],
      ["Intent", perception.intent || "none"],
      ["Vulnerability", formatNumber(perception.vulnerability)],
      ["Action need", formatNumber(perception.action_need)],
    ]);
  }

  function renderRelationship(relationship) {
    renderKv("relationshipList", [
      ["Strategy", relationship.strategy || "none"],
      ["Reason", relationship.strategy_reason || "none"],
      ["Trust", relationship.trust ? formatNumber(relationship.trust.value) : "0.00"],
      ["Bonding", relationship.bonding ? formatNumber(relationship.bonding.value) : "0.00"],
      ["Resolution", relationship.resolution ? formatNumber(relationship.resolution.value) : "0.00"],
      ["Open loops", relationship.open_loop_count || 0],
    ]);
    const events = (relationship.recent_events || []).slice(0, 6);
    const loops = (relationship.open_loops || []).slice(0, 4);
    const items = [
      ...loops.map((loop) => ({
        kind: "open loop",
        topic: loop.topic || loop.loop_kind || "relationship",
        detail: loop.description || loop.status || "",
      })),
      ...events.map((event) => ({
        kind: event.event_kind || "event",
        topic: event.topic || "relationship",
        detail: event.description || event.status || "",
      })),
    ];
    document.getElementById("relationshipTimeline").innerHTML = items.length
      ? items.map((item) => `
        <article class="timeline-item">
          <strong>${escapeHtml(labelize(item.kind))}</strong>
          <div>${escapeHtml(item.topic)}</div>
          <div class="muted markdown-copy">${renderMarkdown(item.detail || "")}</div>
        </article>
      `).join("")
      : `<p class="muted">No open loops or recent relationship events in the selected session.</p>`;
  }

  function renderLife(life) {
    renderKv("lifeList", [
      ["Context available", life.context_available ? "yes" : "no"],
      ["Beliefs", life.belief_count || 0],
      ["Drives", life.drive_count || 0],
      ["Recent evolution", life.recent_evolution_count || 0],
      ["Effects", Object.keys(life.effects || {}).length || 0],
    ]);
    const pressures = Object.entries(life.active_pressures || {});
    document.getElementById("lifePressures").innerHTML = pressures.length
      ? pressures.map(([name, value]) => pressureRow(name, value)).join("")
      : `<p class="muted">No active LifeInfluence pressure on the selected turn.</p>`;
  }

  function renderMemory(memory) {
    renderKv("memoryList", [
      ["Relationship used", memory.relationship_context_used ? "yes" : "no"],
      ["Open loops", memory.open_loop_count || 0],
      ["Recent relationship events", memory.recent_event_count || 0],
      ["Long-term memories", memory.long_term_count || 0],
      ["Semantic memories", memory.semantic_count || 0],
    ]);
    const summaries = [
      ...(memory.long_term_summaries || []).map((item) => ["long-term", item]),
      ...(memory.semantic_summaries || []).map((item) => ["semantic", item]),
    ].filter(([, text]) => text);
    document.getElementById("memorySummaries").innerHTML = summaries.length
      ? summaries.slice(0, 6).map(([kind, text]) => `
        <article class="memory-item">
          <strong>${escapeHtml(kind)}</strong>
          <div class="markdown-copy">${renderMarkdown(text)}</div>
        </article>
      `).join("")
      : `<p class="muted">No retrieved memory summaries on this turn.</p>`;
  }

  function renderSkillsTools(skillsTools) {
    const skills = skillsTools.enabled_skills || [];
    renderKv("skillsList", [
      ["Enabled skills", skillsTools.enabled_skill_count || 0],
      ["Tools considered", skillsTools.tools_considered || 0],
      ["Tools used", skillsTools.tools_used || 0],
      ["Summary", skillsTools.summary || "No tools were considered."],
      ["Skill names", skills.map((skill) => skill.name || skill.id).filter(Boolean).join(", ") || "none"],
      ["Tool names", (skillsTools.tool_names || []).join(", ") || "none"],
    ]);
  }

  function renderExplanation(explanation) {
    const order = ["interpretation", "strategy", "state", "memory", "life_history", "tools", "limits"];
    document.getElementById("explanationList").innerHTML = order.map((key) => `
      <article class="explain-item">
        <strong>${escapeHtml(labelize(key))}</strong>
        <div class="markdown-copy">${renderMarkdown(explanation[key] || "")}</div>
      </article>
    `).join("");
  }

  function renderKv(id, rows) {
    document.getElementById(id).innerHTML = rows.map(([label, value]) => `
      <div class="kv-row">
        <span>${escapeHtml(label)}</span>
        <div class="kv-value markdown-copy">${renderMarkdown(value)}</div>
      </div>
    `).join("");
  }

  function renderChips(id, values) {
    document.getElementById(id).innerHTML = values.slice(0, 8).map((value) => (
      `<span class="chip">${escapeHtml(value)}</span>`
    )).join("");
  }

  function pressureRow(name, value) {
    const number = Number(value || 0);
    const width = Math.min(100, Math.max(3, Math.abs(number) * 2000));
    return `
      <div class="pressure-row">
        <div class="kv-row">
          <span>${escapeHtml(labelize(name.replace(/_pressure$/, "")))}</span>
          <strong>${escapeHtml(formatDelta(number))}</strong>
        </div>
        <div class="bar"><progress value="${width}" max="100" aria-hidden="true"></progress></div>
      </div>
    `;
  }

  function polygonPoints(points) {
    return points.map((point) => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
  }

  function shortLabel(name) {
    return {
      arousal: "Arousal",
      valence: "Valence",
      certainty: "Certainty",
      bonding: "Bonding",
      energy: "Energy",
      resolution: "Resolve",
    }[name] || name;
  }

  function bindEvents() {
    els.refreshBtn.addEventListener("click", () => loadDashboard().catch(handleError));
    els.tokenBtn.addEventListener("click", openTokenDialog);
    els.saveTokenBtn.addEventListener("click", () => {
      setApiToken(els.apiTokenInput.value.trim());
      showToast("API token saved for this browser session.");
      loadDashboard().catch(handleError);
    });
    els.autoRefresh.addEventListener("change", configureRefreshTimer);
    els.sessionList.addEventListener("click", (event) => {
      const button = event.target.closest("[data-session-key]");
      if (!button) return;
      state.selectedSessionKey = button.getAttribute("data-session-key") || "";
      renderDashboard();
    });
  }

  function configureRefreshTimer() {
    if (state.refreshTimer) {
      clearInterval(state.refreshTimer);
      state.refreshTimer = null;
    }
    if (els.autoRefresh.checked) {
      state.refreshTimer = setInterval(() => loadDashboard().catch(handleError), 4000);
    }
  }

  function handleError(err) {
    setStatus("Error", "error");
    showToast(err.message || String(err), "error");
  }

  function responseErrorMessage(data, fallback) {
    const detail = data && data.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return detail.map((item) => item.msg || String(item)).join("; ");
    return fallback;
  }

  function formatTime(seconds) {
    const date = new Date(Number(seconds) * 1000);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString();
  }

  function formatIdle(seconds) {
    const value = Number(seconds || 0);
    if (value < 60) return `${value.toFixed(0)}s idle`;
    return `${Math.floor(value / 60)}m idle`;
  }

  function formatNumber(value) {
    const number = Number(value || 0);
    return number.toFixed(2);
  }

  function safeNumber(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function formatDelta(value) {
    if (value === null || value === undefined || value === "") return "—";
    const number = Number(value || 0);
    return `${number >= 0 ? "+" : ""}${number.toFixed(2)}`;
  }

  function deltaClass(value) {
    if (value === null || value === undefined || value === "") return "delta";
    const number = Number(value || 0);
    return "delta " + (number > 0 ? "positive" : number < 0 ? "negative" : "");
  }

  function labelize(value) {
    return String(value || "").replaceAll("_", " ");
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function escapeAttr(value) {
    return escapeHtml(value).replaceAll("`", "&#096;");
  }

  bindEvents();
  configureRefreshTimer();
  loadDashboard().catch(handleError);
