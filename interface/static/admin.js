import { createSurfaceState, renderMarkdown } from "/assets/lib/shared.js";

const state = createSurfaceState({
    config: {},
    metadata: [],
    status: null,
    persona: null,
    previousPersona: null,
    soul: null,
    tools: [],
    toolsLoaded: false,
    skills: [],
    skillsRoot: "",
    skillsLoaded: false,
    codexModels: [],
    codexModelsLoaded: false,
    codexModelsLoading: false,
    codexModelsError: "",
    life: null,
    lifeLoaded: false,
    activePage: "overview",
}, {});

  const secretFields = new Set(["telegram_token", "llm_api_key", "api_key"]);
  const listFields = new Set(["telegram_allowlist", "cors_origins"]);
  const advancedConfigFields = new Set([
    "data_dir",
    "max_queue_per_user",
    "max_active_sessions",
    "session_timeout_seconds",
    "debug_host",
    "debug_port",
    "proactive_idle_threshold",
    "proactive_density_reference",
    "proactive_recovery_seconds",
    "proactive_check_interval",
    "coherence_min_score",
    "coherence_max_regenerations",
    "pending_intake_ttl_turns",
    "tools_workspace",
    "api_key",
    "cors_origins",
    "telegram_poll_timeout",
    "dedupe_ttl",
  ]);

  const fieldSections = {
    runtime: [
      ["data_dir", "Data Directory", "text", true],
      ["max_queue_per_user", "Max Queue Per User", "number"],
      ["max_active_sessions", "Max Active Sessions", "number"],
      ["session_timeout_seconds", "Session Timeout Seconds", "number"],
      ["console_enabled", "Console Enabled", "boolean"],
      ["debug_host", "Debug Host", "text"],
      ["debug_port", "Debug Port", "number"],
      ["proactive_enabled", "Proactive Enabled", "boolean"],
      ["proactive_idle_threshold", "Proactive Idle Threshold", "number"],
      ["proactive_density_reference", "Proactive Density Reference", "number"],
      ["proactive_recovery_seconds", "Proactive Recovery Seconds", "number"],
      ["proactive_check_interval", "Proactive Check Interval", "number"],
    ],
    character: [
      ["character_independence", "Character Independence", "boolean"],
      ["coherence_min_score", "Coherence Min Score", "number"],
      ["coherence_max_regenerations", "Coherence Regenerations", "number"],
      ["pending_intake_ttl_turns", "Pending Intake TTL", "number"],
    ],
    learning: [
      ["learning_budget_kind", "Budget Kind", "select"],
      ["learning_max_questions_per_day", "Max Questions Per Day", "number"],
      ["learning_max_seconds_per_day", "Max Learning Seconds Per Day", "number"],
      ["metabolism_min_elapsed_days", "Metabolism Min Elapsed Days", "number"],
    ],
    models: [
      ["llm_backend", "LLM Backend", "select"],
      ["llm_base_url", "LLM Base URL", "text", true],
      ["llm_model", "LLM Model", "text"],
      ["llm_api_key", "LLM API Key", "secret"],
    ],
    tools: [
      ["tools_enabled", "Agentic Tools", "boolean"],
      ["shell_tool_enabled", "Shell Tool", "boolean"],
      ["autonomy_level", "Autonomy Level", "select"],
      ["tools_workspace", "Tools Workspace", "text", true],
    ],
    access: [
      ["api_key", "API Key", "secret"],
      ["cors_origins", "CORS Origins", "list", true],
    ],
    channels: [
      ["telegram_token", "Telegram Token", "secret"],
      ["telegram_allowlist", "Telegram Allowlist", "list", true],
      ["telegram_poll_timeout", "Telegram Poll Timeout", "number"],
      ["dedupe_ttl", "Dedupe TTL", "number"],
    ],
  };

  const requiredConfigFields = [
    "data_dir",
    "max_queue_per_user",
    "max_active_sessions",
    "session_timeout_seconds",
    "console_enabled",
    "telegram_allowlist",
    "telegram_poll_timeout",
    "dedupe_ttl",
    "llm_backend",
    "llm_base_url",
    "llm_model",
    "debug_host",
    "debug_port",
    "proactive_enabled",
    "proactive_idle_threshold",
    "proactive_density_reference",
    "proactive_recovery_seconds",
    "proactive_check_interval",
    "character_independence",
    "coherence_min_score",
    "coherence_max_regenerations",
    "pending_intake_ttl_turns",
    "telegram_token",
    "llm_api_key",
    "api_key",
    "cors_origins",
    "tools_enabled",
    "autonomy_level",
    "tools_workspace",
    "shell_tool_enabled",
  ];

  const modulatorNames = ["arousal", "valence", "certainty", "bonding", "energy", "resolution"];

  const soulFields = [
    ["name", "Name", "text"],
    ["identity", "Identity", "textarea"],
    ["voice", "Voice", "textarea"],
    ["relational_stance", "Relational Stance", "textarea"],
    ["growth_policy", "Growth Policy", "textarea"],
    ["likes", "Likes", "list"],
    ["dislikes", "Dislikes", "list"],
    ["boundaries", "Boundaries", "list"],
    ["core_values", "Core Values", "map"],
    ["initial_traits", "Initial Traits", "map"],
  ];

  const els = {
    pageTitle: document.getElementById("pageTitle"),
    statusPill: document.getElementById("statusPill"),
    saveBtn: document.getElementById("saveBtn"),
    tokenBtn: document.getElementById("tokenBtn"),
    refreshAllBtn: document.getElementById("refreshAllBtn"),
    tokenDialog: document.getElementById("tokenDialog"),
    apiTokenInput: document.getElementById("apiTokenInput"),
    saveTokenBtn: document.getElementById("saveTokenBtn"),
    toast: document.getElementById("toast"),
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

  function showToast(message, tone) {
    els.toast.textContent = message;
    els.toast.hidden = false;
    els.toast.className = "toast" + (tone ? " " + tone : "");
    clearTimeout(showToast._timer);
    showToast._timer = setTimeout(() => { els.toast.hidden = true; }, 3600);
  }

  function pretty(data) {
    return JSON.stringify(data, null, 2);
  }

  function responseErrorMessage(data, fallback) {
    const detail = data && data.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail.map((item) => {
        if (typeof item === "string") return item;
        const loc = Array.isArray(item.loc) ? item.loc.join(".") : "";
        const msg = item.msg || JSON.stringify(item);
        return loc ? `${loc}: ${msg}` : msg;
      }).join("; ");
    }
    if (detail && typeof detail === "object") return JSON.stringify(detail);
    return fallback;
  }

  function setStatus(text, tone) {
    els.statusPill.textContent = text;
    els.statusPill.className = "status-pill" + (tone ? " " + tone : "");
  }

  function metaFor(name) {
    return state.metadata.find((item) => item.name === name) || {};
  }

  function renderAllForms() {
    renderConfigForm("runtimeForm", fieldSections.runtime);
    renderConfigForm("characterForm", fieldSections.character);
    renderConfigForm("learningForm", fieldSections.learning);
    renderConfigForm("modelsForm", fieldSections.models);
    renderConfigForm("toolsForm", fieldSections.tools);
    renderConfigForm("accessForm", fieldSections.access);
    renderConfigForm("channelsForm", fieldSections.channels);
    renderIdentityForm();
  }

  function renderConfigForm(formId, fields) {
    const form = document.getElementById(formId);
    if (!form) return;
    const renderField = ([name, label, type, wide]) => {
      const fieldMeta = metaFor(name);
      const value = state.config[name];
      const restart = fieldMeta.restart_required ? "Requires server restart. " : "";
      const reload = fieldMeta.session_manager_reload ? "Reloads active runtime manager on save. " : "";
      const live = fieldMeta.live_reload ? "Applies live after save. " : "";
      const secret = secretFields.has(name) ? secretStatusText(fieldMeta.secret_status) : "";
      const help = `${restart}${reload}${live}${secret}`.trim();
      return `
        <div class="field-card ${wide ? "wide" : ""}">
          <label for="cfg-${name}">${escapeHtml(label)}</label>
          ${renderConfigInput(name, type, value, fieldMeta)}
          ${help ? `<div class="field-help">${escapeHtml(help)}</div>` : ""}
        </div>
      `;
    };
    const basic = fields.filter(([name]) => !advancedConfigFields.has(name));
    const advanced = fields.filter(([name]) => advancedConfigFields.has(name));
    form.innerHTML = [
      basic.map(renderField).join(""),
      advanced.length ? `
        <details class="advanced-settings wide">
          <summary>Advanced settings</summary>
          <div class="form-grid nested">${advanced.map(renderField).join("")}</div>
        </details>
      ` : "",
    ].join("");
  }

  function renderConfigInput(name, type, value, fieldMeta) {
    if (type === "boolean") {
      return `
        <label class="checkbox-row">
          <input id="cfg-${name}" data-config-field="${name}" type="checkbox" ${value ? "checked" : ""}>
          <span>Enabled</span>
        </label>
      `;
    }
    if (name === "llm_model" && String(state.config.llm_backend || "") === "codex") {
      return renderCodexModelSelect(value);
    }
    if (type === "select") {
      const allowed = fieldMeta.allowed_values || selectDefaults(name);
      return `
        <select id="cfg-${name}" data-config-field="${name}">
          ${allowed.map((item) => `<option value="${escapeAttr(item)}" ${String(value) === item ? "selected" : ""}>${escapeHtml(labelize(item))}</option>`).join("")}
        </select>
      `;
    }
    if (type === "list") {
      const text = Array.isArray(value) ? value.join("\n") : "";
      return `<textarea id="cfg-${name}" data-config-field="${name}" spellcheck="false">${escapeHtml(text)}</textarea>`;
    }
    if (type === "secret") {
      const configured = fieldMeta.secret_status && fieldMeta.secret_status.configured;
      return `
        <input id="cfg-${name}" data-config-field="${name}" type="password" autocomplete="off" placeholder="${configured ? "Configured - leave blank to keep" : "Unset"}">
        <label class="checkbox-row">
          <input id="clear-${name}" data-clear-secret="${name}" type="checkbox">
          <span>Clear stored value</span>
        </label>
      `;
    }
    const inputType = type === "number" ? "number" : "text";
    return `<input id="cfg-${name}" data-config-field="${name}" type="${inputType}" value="${escapeAttr(value ?? "")}">`;
  }

  function renderCodexModelSelect(value) {
    const selected = String(value || "");
    const known = new Set(state.codexModels.map((model) => model.slug));
    const options = [
      `<option value="" ${selected ? "" : "selected"}>Codex default</option>`,
    ];
    if (selected && !known.has(selected)) {
      options.push(`<option value="${escapeAttr(selected)}" selected>${escapeHtml(selected)} (custom)</option>`);
    }
    if (state.codexModelsLoading && !state.codexModels.length) {
      options.push(`<option value="" disabled>Loading Codex models...</option>`);
    }
    for (const model of state.codexModels) {
      const slug = String(model.slug || "");
      if (!slug) continue;
      const label = codexModelLabel(model);
      const title = model.description ? ` title="${escapeAttr(model.description)}"` : "";
      options.push(`<option value="${escapeAttr(slug)}"${title} ${selected === slug ? "selected" : ""}>${escapeHtml(label)}</option>`);
    }
    const hint = state.codexModelsError
      ? `<div class="field-help">${escapeHtml(state.codexModelsError)}</div>`
      : "";
    return `
      <select id="cfg-llm_model" data-config-field="llm_model">
        ${options.join("")}
      </select>
      ${hint}
    `;
  }

  function codexModelLabel(model) {
    const slug = String(model.slug || "");
    const name = String(model.display_name || slug);
    return name && name !== slug ? `${name} (${slug})` : slug;
  }

  function renderIdentityForm() {
    const form = document.getElementById("identityForm");
    if (!form || !state.soul) return;
    form.innerHTML = soulFields.map(([name, label, type]) => {
      const value = state.soul[name];
      const wide = type !== "text";
      return `
        <div class="field-card ${wide ? "wide" : ""}">
          <label for="soul-${name}">${escapeHtml(label)}</label>
          ${renderSoulInput(name, type, value)}
        </div>
      `;
    }).join("");
  }

  function renderSoulInput(name, type, value) {
    if (type === "textarea") {
      return `<textarea id="soul-${name}" data-soul-field="${name}">${escapeHtml(value || "")}</textarea>`;
    }
    if (type === "list") {
      return `<textarea id="soul-${name}" data-soul-field="${name}">${escapeHtml(Array.isArray(value) ? value.join("\n") : "")}</textarea>`;
    }
    if (type === "map") {
      const text = Object.entries(value || {}).map(([key, val]) => `${key}=${val}`).join("\n");
      return `<textarea id="soul-${name}" data-soul-field="${name}" spellcheck="false">${escapeHtml(text)}</textarea>`;
    }
    return `<input id="soul-${name}" data-soul-field="${name}" type="text" value="${escapeAttr(value || "")}">`;
  }

  function secretStatusText(status) {
    if (!status) return "";
    if (!status.configured) return "Currently unset.";
    if (status.source === "environment") return `Configured from ${status.env_var}.`;
    return "Stored in runtime_config.yaml.";
  }

  function selectDefaults(name) {
    if (name === "llm_backend") return ["auto", "provider", "openai_compatible", "codex"];
    if (name === "autonomy_level") return ["off", "assisted", "autonomous", "high_risk"];
    return [];
  }

  function labelize(value) {
    return String(value).replaceAll("_", " ");
  }

  function titleize(value) {
    return labelize(value).replace(/\b\w/g, (char) => char.toUpperCase());
  }

  function formatDate(timestamp) {
    const date = new Date(Number(timestamp) * 1000);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function signed(value) {
    const number = Number(value || 0);
    return `${number >= 0 ? "+" : ""}${number.toFixed(2)}`;
  }

  function formatNumber(value, digits) {
    const number = safeNumber(value, 0);
    return number.toFixed(digits == null ? 2 : digits);
  }

  function safeNumber(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function renderOverview() {
    const status = state.status || {};
    const tools = status.tools || {};
    const sessions = status.sessions || {};
    const setup = status.setup || {};
    const cards = [
      ["LLM", status.llm_configured ? "Configured" : "Not configured", status.llm_backend || "auto"],
      ["Auth", status.auth_enabled ? "Enabled" : "Disabled", status.auth_enabled ? "Bearer token required" : "Open local surface"],
      ["Tools", tools.enabled ? (tools.shell_enabled ? "Shell enabled" : "Enabled") : "Skill registry only", tools.workspace || "No workspace"],
      ["Sessions", String(sessions.active || 0), `Max ${sessions.max_active || 0}`],
      ["Telegram", status.telegram_configured ? "Configured" : "Not configured", "Long polling in web host"],
      ["Setup", setup.completed ? "Complete" : "Incomplete", setup.state_path || ""],
      ["Identity", status.soul_name || "Nūr", "Seed soul"],
      ["Config", "runtime_config.yaml", status.config_path || ""],
    ];
    document.getElementById("overviewCards").innerHTML = cards.map(([label, value, foot]) => `
      <article class="card">
        <div class="metric-label">${escapeHtml(label)}</div>
        <div class="metric-value">${escapeHtml(value)}</div>
        <div class="metric-foot">${escapeHtml(foot || "")}</div>
      </article>
    `).join("");
    if (els.tokenBtn) {
      els.tokenBtn.hidden = !status.auth_enabled;
    }
    renderOverviewPersona();
    renderPersonaAdminPage();
    renderWarnings(status.warnings || []);
    const hasErrors = (status.warnings || []).some((item) => item.severity === "error");
    const hasWarnings = (status.warnings || []).length > 0;
    setStatus(hasErrors ? "Needs attention" : (hasWarnings ? "Warnings" : "OK"), hasErrors ? "error" : (hasWarnings ? "warn" : "ok"));
  }

  function renderOverviewPersona() {
    const el = document.getElementById("overviewPersona");
    if (!el) return;
    const payload = state.persona || {};
    if (payload.error) {
      el.innerHTML = `<p class="empty-copy">Persona state is unavailable: ${escapeHtml(payload.error)}</p>`;
      return;
    }
    const sessions = Array.isArray(payload.sessions) ? payload.sessions.slice() : [];
    if (!sessions.length) {
      el.innerHTML = `
        <div class="persona-empty">
          <strong>No active persona session.</strong>
          <p class="empty-copy">Send a Web or Telegram message, then refresh Overview to see Nūr's mental state, relationship context, memory, Life influence, and tool activity here.</p>
        </div>
      `;
      return;
    }
    sessions.sort((a, b) => safeNumber(b.last_activity, 0) - safeNumber(a.last_activity, 0));
    const session = sessions[0];
    const view = session.persona_view || {};
    const emotions = view.emotions || {};
    const relationship = view.relationship || {};
    const memory = view.memory || {};
    const life = view.life || {};
    const skillsTools = view.skills_tools || {};
    const explanation = view.explanation || {};
    const channels = Object.entries(payload.channel_counts || {})
      .map(([name, count]) => `${name} ${count}`)
      .join(" · ");
    const activePressures = activeEntries(life.active_pressures || {});
    const activeEffects = activeEntries(life.effects || {});
    const openLoops = (relationship.open_loops || []).slice(0, 3);
    const recentEvents = (relationship.recent_events || []).slice(0, 3);
    const modulators = emotions.modulators || {};
    const mentalHealth = emotions.mental_health || {};

    el.innerHTML = `
      <div class="persona-overview-grid">
        <section class="persona-overview-block persona-emotion-block">
          <div class="metric-label">Current Emotion</div>
          <div class="persona-emotion">${escapeHtml(emotions.simple_label || emotions.primary || "neutral")}</div>
          <div class="metric-foot">${escapeHtml([
            emotions.primary ? `best-fit ${emotions.primary}` : "",
            `intensity ${formatNumber(emotions.intensity)}`,
            `confidence ${formatNumber(emotions.confidence)}`,
          ].filter(Boolean).join(" · "))}</div>
          ${renderPersonaChips(emotions.drivers || ["balanced state"])}
        </section>
        <section class="persona-overview-block">
          <div class="metric-label">Mental Status</div>
          <div class="metric-foot">${escapeHtml(mentalHealth.label ? `health ${mentalHealth.label} (${mentalHealth.score}/100)` : "")}</div>
          ${renderModulatorBars(modulators, session)}
        </section>
        <section class="persona-overview-block">
          <div class="metric-label">Relationship</div>
          ${renderPersonaFacts([
            ["strategy", relationship.strategy || "none"],
            ["reason", relationship.strategy_reason || "none"],
            ["trust", relationship.trust ? formatNumber(relationship.trust.value) : "0.00"],
            ["bonding", relationship.bonding ? formatNumber(relationship.bonding.value) : "0.00"],
            ["resolution", relationship.resolution ? formatNumber(relationship.resolution.value) : "0.00"],
            ["open loops", relationship.open_loop_count || 0],
          ])}
        </section>
        <section class="persona-overview-block">
          <div class="metric-label">Memory, Life, Skills</div>
          ${renderPersonaFacts([
            ["relationship memory", memory.relationship_context_used ? "used" : "not used"],
            ["long-term", memory.long_term_count || 0],
            ["semantic", memory.semantic_count || 0],
            ["life context", life.context_available ? "active" : "none"],
            ["skills", skillsTools.enabled_skill_count || 0],
            ["tools used", skillsTools.tools_used || 0],
          ])}
        </section>
      </div>
      <div class="persona-overview-lower">
        <section>
          <div class="mini-heading">Why This Response</div>
          <div class="markdown-copy">${renderMarkdown(explanation.interpretation || "No turn explanation is available.")}</div>
          <div class="markdown-copy muted">${renderMarkdown(explanation.strategy || "")}</div>
        </section>
        <section>
          <div class="mini-heading">Active Loops</div>
          ${renderPersonaItems(openLoops, "No active relationship loops.")}
        </section>
        <section>
          <div class="mini-heading">Recent Relationship Events</div>
          ${renderPersonaItems(recentEvents, "No recent relationship events.")}
        </section>
        <section>
          <div class="mini-heading">Life Influence</div>
          ${activePressures.length || activeEffects.length
            ? `${renderPersonaChips(activePressures.map(([key, value]) => `${labelize(key)} ${signed(value)}`))}
               ${renderPersonaChips(activeEffects.map(([key, value]) => `${labelize(key)} ${signed(value)}`))}`
            : `<p class="empty-copy">No active LifeInfluence pressure or effect in the latest turn.</p>`}
        </section>
      </div>
      <div class="persona-session-foot">
        <span>${escapeHtml(payload.count || sessions.length)} active session(s)</span>
        <span>${escapeHtml(channels || "no channel breakdown")}</span>
        <span>${escapeHtml(session.session_key || "latest session")}</span>
      </div>
    `;
  }

  function renderPersonaAdminPage() {
    const el = document.getElementById("personaAdminPage");
    if (!el) return;
    const payload = state.persona || {};
    if (payload.error) {
      el.innerHTML = `<div class="panel"><p class="empty-copy">Persona state is unavailable: ${escapeHtml(payload.error)}</p></div>`;
      return;
    }
    const sessions = Array.isArray(payload.sessions) ? payload.sessions.slice() : [];
    sessions.sort((a, b) => safeNumber(b.last_activity, 0) - safeNumber(a.last_activity, 0));
    if (!sessions.length) {
      el.innerHTML = `
        <div class="panel persona-empty">
          <strong>No active persona state yet.</strong>
          <p class="empty-copy">Use Web, Telegram, or another channel first. Admin reads active core sessions without creating or mutating one.</p>
        </div>
      `;
      return;
    }

    const session = sessions[0];
    const view = session.persona_view || {};
    const emotions = view.emotions || {};
    const perception = view.perception || {};
    const relationship = view.relationship || {};
    const memory = view.memory || {};
    const life = view.life || {};
    const skillsTools = view.skills_tools || {};
    const explanation = view.explanation || {};
    const activePressures = activeEntries(life.active_pressures || {});
    const activeEffects = activeEntries(life.effects || {});
    const mentalHealth = emotions.mental_health || {};

    el.innerHTML = `
      <div class="panel persona-admin-hero">
        <div>
          <div class="metric-label">Latest Active Session</div>
          <h3>${escapeHtml(session.platform || "channel")} · ${escapeHtml(session.user_id || "user")}</h3>
          <p class="muted">${escapeHtml(session.session_key || "")}</p>
        </div>
        <div>
          <div class="metric-label">Emotion</div>
          <div class="persona-emotion">${escapeHtml(emotions.simple_label || emotions.primary || "neutral")}</div>
          <p class="muted">${escapeHtml((emotions.secondary || []).length ? `secondary: ${(emotions.secondary || []).join(", ")}` : "no strong secondary emotion")}</p>
        </div>
        <div>
          <div class="metric-label">Strategy</div>
          <div class="metric-value">${escapeHtml(relationship.strategy || "none")}</div>
          <p class="muted">${escapeHtml(relationship.strategy_reason || "no strategy trace")}</p>
        </div>
      </div>

      <div class="persona-admin-grid">
        <section class="panel persona-state-panel">
          <div class="panel-heading"><h3>Mental And Emotional State</h3>${mentalHealth.label ? `<span>${escapeHtml(mentalHealth.label)} ${escapeHtml(mentalHealth.score)}/100</span>` : ""}</div>
          ${renderModulatorBars(emotions.modulators || {}, session)}
          ${renderPersonaChips(emotions.drivers || ["balanced state"])}
        </section>

        <section class="panel">
          <div class="panel-heading"><h3>Perception</h3></div>
          ${renderPersonaFacts([
            ["target", perception.target || "unknown"],
            ["social move", perception.social_move || "none"],
            ["intent", perception.intent || "none"],
            ["vulnerability", formatNumber(perception.vulnerability)],
            ["action need", formatNumber(perception.action_need)],
          ])}
          <div class="muted persona-summary-copy markdown-copy">${renderMarkdown(perception.summary || "")}</div>
        </section>

        <section class="panel">
          <div class="panel-heading"><h3>Relationship</h3></div>
          ${renderPersonaFacts([
            ["trust", relationship.trust ? formatNumber(relationship.trust.value) : "0.00"],
            ["bonding", relationship.bonding ? formatNumber(relationship.bonding.value) : "0.00"],
            ["resolution", relationship.resolution ? formatNumber(relationship.resolution.value) : "0.00"],
            ["open loops", relationship.open_loop_count || 0],
          ])}
          <div class="mini-heading">Open Loops</div>
          ${renderPersonaItems((relationship.open_loops || []).slice(0, 5), "No active relationship loops.")}
          <div class="mini-heading spaced">Recent Events</div>
          ${renderPersonaItems((relationship.recent_events || []).slice(0, 5), "No recent relationship events.")}
        </section>

        <section class="panel">
          <div class="panel-heading"><h3>Memory</h3></div>
          ${renderPersonaFacts([
            ["relationship context", memory.relationship_context_used ? "used" : "not used"],
            ["open loop count", memory.open_loop_count || 0],
            ["long-term memories", memory.long_term_count || 0],
            ["semantic memories", memory.semantic_count || 0],
          ])}
          <div class="mini-heading">Current Turn Recall</div>
          ${renderMemorySnippets(memory.long_term_summaries, memory.semantic_summaries)}
        </section>

        <section class="panel">
          <div class="panel-heading"><h3>Life Influence</h3></div>
          ${renderPersonaFacts([
            ["context", life.context_available ? "active" : "none"],
            ["beliefs", life.belief_count || 0],
            ["drives", life.drive_count || 0],
            ["recent evolution", life.recent_evolution_count || 0],
          ])}
          <div class="mini-heading">Pressures</div>
          ${activePressures.length ? renderPersonaChips(activePressures.map(([key, value]) => `${labelize(key)} ${signed(value)}`)) : `<p class="empty-copy">No active pressure.</p>`}
          <div class="mini-heading spaced">Effects</div>
          ${activeEffects.length ? renderPersonaChips(activeEffects.map(([key, value]) => `${labelize(key)} ${signed(value)}`)) : `<p class="empty-copy">No policy effect recorded.</p>`}
        </section>

        <section class="panel">
          <div class="panel-heading"><h3>Skills And Tools</h3></div>
          ${renderPersonaFacts([
            ["enabled skills", skillsTools.enabled_skill_count || 0],
            ["tools considered", skillsTools.tools_considered || 0],
            ["tools used", skillsTools.tools_used || 0],
          ])}
          <div class="muted persona-summary-copy markdown-copy">${renderMarkdown(skillsTools.summary || "No tools were considered.")}</div>
          ${renderPersonaChips((skillsTools.enabled_skills || []).map((skill) => skill.name || skill.id))}
        </section>
      </div>

      <div class="panel">
        <div class="panel-heading"><h3>Why This Response?</h3></div>
        <div class="persona-explain-grid">
          ${renderExplanationItem("Interpretation", explanation.interpretation)}
          ${renderExplanationItem("Strategy", explanation.strategy)}
          ${renderExplanationItem("State", explanation.state)}
          ${renderExplanationItem("Memory", explanation.memory)}
          ${renderExplanationItem("Life History", explanation.life_history)}
          ${renderExplanationItem("Tools", explanation.tools)}
          ${renderExplanationItem("Limits", explanation.limits)}
        </div>
      </div>
    `;
  }

  function activeEntries(source) {
    return Object.entries(source || {})
      .filter(([, value]) => Number.isFinite(Number(value)) && Math.abs(Number(value)) >= 0.0005)
      .slice(0, 6);
  }

  function renderPersonaFacts(items) {
    return `<dl class="persona-facts">${items.map(([label, value]) => `
      <div>
        <dt>${escapeHtml(label)}</dt>
        <dd class="markdown-copy">${renderMarkdown(value)}</dd>
      </div>
    `).join("")}</dl>`;
  }

  function renderPersonaChips(items) {
    const values = (items || []).filter(Boolean).slice(0, 6);
    if (!values.length) return "";
    return `<div class="persona-chip-row">${values.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>`;
  }

  function renderPersonaItems(items, emptyText) {
    if (!items.length) return `<p class="empty-copy">${escapeHtml(emptyText)}</p>`;
    return `<div class="persona-mini-list">${items.map((item) => `
      <article>
        <strong>${escapeHtml(item.topic || item.loop_kind || item.event_type || item.kind || "relationship")}</strong>
        <div class="markdown-copy">${renderMarkdown(item.status || item.event_type || item.description || "active")}</div>
      </article>
    `).join("")}</div>`;
  }

  function renderModulatorBars(modulators, session) {
    return `<div class="persona-mod-bars">${modulatorNames.map((name) => {
      const mod = modulators[name] || {};
      const value = clamp(safeNumber(mod.value, name === "energy" ? 1 : name === "resolution" ? 0 : 0.5), 0, 1);
      const delta = mod.delta == null ? personaDelta(session, name, value) : mod.delta;
      return `
        <div class="persona-mod-row">
          <div>
            <strong>${escapeHtml(labelize(name))}</strong>
            <span>${escapeHtml(mod.meaning || "")}</span>
          </div>
          <progress class="persona-mod-meter" value="${Math.round(value * 100)}" max="100" aria-hidden="true"></progress>
          <div class="persona-mod-value">${formatNumber(value)}${delta == null ? "" : ` <span class="${delta < 0 ? "negative" : "positive"}">${signed(delta)}</span>`}</div>
        </div>
      `;
    }).join("")}</div>`;
  }

  function personaDelta(session, name, currentValue) {
    const previous = previousPersonaSession(session);
    if (!previous) return null;
    const previousMods = (((previous.persona_view || {}).emotions || {}).modulators || {});
    if (!previousMods[name]) return null;
    return safeNumber(currentValue, 0) - safeNumber(previousMods[name].value, currentValue);
  }

  function previousPersonaSession(session) {
    const sessions = ((state.previousPersona || {}).sessions || []);
    return sessions.find((item) => item.session_key === session.session_key) || null;
  }

  function renderMemorySnippets(ltItems, semanticItems) {
    const ltEntries = (ltItems || []).filter(Boolean).slice(0, 4);
    const semEntries = (semanticItems || []).filter(Boolean).slice(0, 4);
    if (!ltEntries.length && !semEntries.length) {
      return `<p class="empty-copy">No long-term or semantic memories were retrieved for this turn.</p>`;
    }
    const renderLt = (m) => {
      const tags = [];
      if (m.spike) tags.push(`<span class="memory-tag spike" title="Spike memory — bypassed gradual accumulation">spike</span>`);
      if (typeof m.activation === "number") tags.push(`<span class="memory-tag activation" title="ACT-R activation score (base + valence bias + spike bonus)">act ${m.activation.toFixed(2)}</span>`);
      const tagHtml = tags.length ? `<div class="memory-tags">${tags.join("")}</div>` : "";
      return `<article class="memory-item">${tagHtml}<div class="markdown-copy">${renderMarkdown(m.summary || String(m))}</div></article>`;
    };
    const renderSem = (m) => {
      const tags = [`<span class="memory-tag semantic" title="Semantic memory score">${escapeHtml(m.kind || "semantic")} ${typeof m.score === "number" ? m.score.toFixed(2) : ""}</span>`];
      const tagHtml = `<div class="memory-tags">${tags.join("")}</div>`;
      return `<article class="memory-item">${tagHtml}<div class="markdown-copy">${renderMarkdown(m.summary || "")}</div></article>`;
    };
    return `<div class="persona-mini-list">
      ${ltEntries.map(renderLt).join("")}
      ${semEntries.map(renderSem).join("")}
    </div>`;
  }

  function renderExplanationItem(label, value) {
    return `
      <article>
        <strong>${escapeHtml(label)}</strong>
        <div class="markdown-copy">${renderMarkdown(value || "No data recorded.")}</div>
      </article>
    `;
  }

  async function loadTools() {
    const summary = document.getElementById("toolsSummary");
    const list = document.getElementById("toolsList");
    if (summary) summary.innerHTML = `<p class="empty-copy">Loading tools...</p>`;
    if (list) list.innerHTML = "";
    const res = await authedFetch("/v1/tools?platform=web&user_id=admin&chat_id=admin");
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Tools HTTP " + res.status);
    state.tools = data.tools || [];
    state.toolsLoaded = true;
    renderToolInventory();
  }

  function renderToolInventory() {
    const summary = document.getElementById("toolsSummary");
    const list = document.getElementById("toolsList");
    const categoryFilter = document.getElementById("toolCategoryFilter");
    if (!summary || !list || !categoryFilter) return;

    const tools = state.tools || [];
    const categories = [...new Set(tools.map((tool) => tool.category || "unknown"))].sort();
    const currentCategory = categoryFilter.value;
    categoryFilter.innerHTML = `<option value="">All categories</option>` + categories.map((category) => (
      `<option value="${escapeAttr(category)}" ${currentCategory === category ? "selected" : ""}>${escapeHtml(labelize(category))}</option>`
    )).join("");

    const counts = countTools(tools);
    summary.innerHTML = [
      ["Total", tools.length],
      ["Read", counts.read_only || 0],
      ["Write", counts.write || 0],
      ["Destructive", counts.destructive || 0],
      ["External", counts.external_action || 0],
    ].map(([label, value]) => `
      <div class="tool-summary-card">
        <strong>${escapeHtml(value)}</strong>
        <span>${escapeHtml(label)}</span>
      </div>
    `).join("");

    const query = (document.getElementById("toolSearch").value || "").trim().toLowerCase();
    const filtered = tools.filter((tool) => {
      const categoryOk = !categoryFilter.value || tool.category === categoryFilter.value;
      const haystack = `${tool.name} ${tool.description} ${tool.category}`.toLowerCase();
      return categoryOk && (!query || haystack.includes(query));
    });

    if (!filtered.length) {
      list.innerHTML = `<div class="tool-row"><p class="empty-copy">${tools.length ? "No tools match the current filter." : "No tools are registered for the current runtime."}</p></div>`;
      return;
    }
    list.innerHTML = filtered.map(renderToolRow).join("");
  }

  const CATEGORY_CHIP_LABEL = {
    read_only: "Observes",
    cognitive: "Mutates registry",
    write: "Writes",
    destructive: "Destructive",
    external_action: "External action",
  };

  function renderToolRow(tool) {
    const argKeys = Object.keys(tool.arg_schema || {});
    const categoryLabel = CATEGORY_CHIP_LABEL[tool.category] || labelize(tool.category || "unknown");
    const chips = [
      { className: tool.category || "unknown", label: categoryLabel },
      tool.requires_network ? { className: "network", label: "network" } : null,
      tool.mcp_backed ? { className: "mcp", label: "mcp" } : null,
      tool.supports_streaming ? { className: "streaming", label: "streaming" } : null,
    ].filter(Boolean);
    return `
      <article class="tool-row">
        <div>
          <div class="tool-name">${escapeHtml(tool.name)}</div>
          <div class="chip-row">
            ${chips.map((chip) => `<span class="chip ${escapeAttr(chip.className)}">${escapeHtml(labelize(chip.label))}</span>`).join("")}
          </div>
        </div>
        <div class="tool-description">${escapeHtml(tool.description || "")}</div>
        <div class="tool-args">${escapeHtml(argKeys.length ? argKeys.join(", ") : "no arguments")}</div>
      </article>
    `;
  }

  function countTools(tools) {
    const counts = {};
    for (const tool of tools) {
      const category = tool.category || "unknown";
      counts[category] = (counts[category] || 0) + 1;
    }
    return counts;
  }

  function renderWarnings(warnings) {
    const list = document.getElementById("warningsList");
    if (!warnings.length) {
      list.innerHTML = `<p class="empty-copy">No active admin warnings.</p>`;
      return;
    }
    list.innerHTML = warnings.map((warning) => `
      <div class="warning-item ${escapeAttr(warning.severity || "info")}">
        <strong>${escapeHtml(warning.message || "Warning")}</strong>
        <div class="warning-code">${escapeHtml(warning.code || "")} · ${escapeHtml(warning.field || "")}</div>
      </div>
    `).join("");
  }

  async function loadSkills() {
    const list = document.getElementById("skillsList");
    if (list) list.innerHTML = `<p class="empty-copy">Loading skills...</p>`;
    const res = await authedFetch("/admin/skills");
    const data = await res.json();
    if (!res.ok) throw new Error(responseErrorMessage(data, "Skills HTTP " + res.status));
    state.skills = data.skills || [];
    state.skillsRoot = data.root || "";
    state.skillsLoaded = true;
    renderSkills();
  }

  async function importSkill() {
    const uploadInput = document.getElementById("skillUpload");
    const upload = uploadInput?.files?.[0] || null;
    const sourcePath = document.getElementById("skillSourcePath").value.trim();
    const skillMarkdown = document.getElementById("skillMarkdown").value.trim();
    const nameHint = document.getElementById("skillNameHint").value.trim();
    let res;
    if (upload) {
      const form = new FormData();
      form.append("file", upload);
      form.append("name_hint", nameHint);
      res = await authedFetch("/admin/skills/import/upload", {
        method: "POST",
        body: form,
      });
    } else {
      res = await authedFetch("/admin/skills/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_path: sourcePath || null,
          skill_markdown: skillMarkdown || null,
          name_hint: nameHint,
        }),
      });
    }
    const data = await res.json();
    if (!res.ok) throw new Error(responseErrorMessage(data, "Import failed"));
    if (uploadInput) uploadInput.value = "";
    document.getElementById("skillSourcePath").value = "";
    document.getElementById("skillMarkdown").value = "";
    document.getElementById("skillNameHint").value = "";
    showToast("Skill imported for review.");
    await loadSkills();
  }

  function renderSkills() {
    const list = document.getElementById("skillsList");
    const root = document.getElementById("skillsRoot");
    if (!list || !root) return;
    root.textContent = state.skillsRoot;
    if (!state.skills.length) {
      list.innerHTML = `<p class="empty-copy">No imported skills yet. Import a SKILL.md folder or paste a skill definition above.</p>`;
      return;
    }
    list.innerHTML = state.skills.map(renderSkillRow).join("");
  }

  function renderSkillRow(skill) {
    const audit = skill.compatibility || {};
    const errors = audit.errors || [];
    const warnings = audit.warnings || [];
    const requiredTools = audit.required_tools || [];
    const riskFlags = audit.risk_flags || [];
    const unsupported = audit.unsupported_features || [];
    const enabled = !!skill.enabled;
    const canEnable = !enabled && !errors.length;
    return `
      <article class="skill-row">
        <div class="skill-row-header">
          <div>
            <div class="skill-title">${escapeHtml(skill.name || skill.id)}</div>
            <div class="skill-description markdown-copy">${renderMarkdown(skill.description || "No description")}</div>
          </div>
          <div class="skill-actions">
            <span class="status-chip ${escapeAttr(skill.status || "disabled")}">${escapeHtml(labelize(skill.status || "disabled"))}</span>
            <button class="secondary" data-skill-action="audit" data-skill-id="${escapeAttr(skill.id)}">Audit</button>
            ${enabled
              ? `<button class="secondary" data-skill-action="disable" data-skill-id="${escapeAttr(skill.id)}">Disable</button>`
              : `<button class="primary" data-skill-action="enable" data-skill-id="${escapeAttr(skill.id)}" ${canEnable ? "" : "disabled"}>Enable</button>`}
            <button class="danger" data-skill-action="delete" data-skill-id="${escapeAttr(skill.id)}">Delete</button>
          </div>
        </div>
        <div class="skill-meta">${escapeHtml(skill.root || "")}</div>
        <div class="audit-grid">
          ${renderAuditBox("Required Tools", requiredTools)}
          ${renderAuditBox("Risk Flags", riskFlags)}
          ${renderAuditBox("Unsupported Hints", unsupported)}
          ${renderAuditBox("Errors", errors)}
          ${renderAuditBox("Warnings", warnings)}
          ${renderAuditBox("Files", skillFileSummary(audit.files || {}))}
        </div>
      </article>
    `;
  }

  function renderAuditBox(title, items) {
    const values = Array.isArray(items) ? items : [String(items || "none")];
    const content = values.length
      ? `<ul>${values.slice(0, 8).map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
      : `<p class="empty-copy">None</p>`;
    return `<div class="audit-box"><strong>${escapeHtml(title)}</strong>${content}</div>`;
  }

  function skillFileSummary(files) {
    if (!files.total_count) return ["No files scanned"];
    return [
      `${files.total_count} total files`,
      `${files.script_count || 0} scripts`,
      `${files.resource_count || 0} resources`,
    ];
  }

  async function mutateSkill(skillId, action) {
    let url = `/admin/skills/${encodeURIComponent(skillId)}/${action}`;
    let method = "POST";
    if (action === "delete") {
      if (!window.confirm(`Delete skill ${skillId}? This removes it from the registry and disk.`)) return;
      url = `/admin/skills/${encodeURIComponent(skillId)}`;
      method = "DELETE";
    }
    const res = await authedFetch(url, { method });
    const data = await res.json();
    if (!res.ok) throw new Error(responseErrorMessage(data, `${action} failed`));
    showToast(`Skill ${labelize(action)} complete.`);
    await loadSkills();
  }

  async function loadLife() {
    const timeline = document.getElementById("lifeTimeline");
    if (timeline) timeline.innerHTML = `<p class="empty-copy">Loading life history...</p>`;
    const res = await authedFetch("/admin/life");
    const data = await res.json();
    if (!res.ok) throw new Error(responseErrorMessage(data, "Life HTTP " + res.status));
    state.life = data;
    state.lifeLoaded = true;
    renderLife();
  }

  async function ingestLifeText() {
    const title = document.getElementById("lifeTextTitle").value.trim();
    const sourceType = document.getElementById("lifeSourceType").value.trim() || "pasted_text";
    const text = document.getElementById("lifeText").value.trim();
    const participants = splitList(document.getElementById("lifeParticipants").value);
    const res = await authedFetch("/admin/life/experiences/text", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, source_type: sourceType, text, participants }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(responseErrorMessage(data, "Text digestion failed"));
    document.getElementById("lifeText").value = "";
    showToast("Experience digested.");
    await loadLife();
  }

  async function ingestLifeFile() {
    const uploadInput = document.getElementById("lifeUploadFile");
    const upload = uploadInput?.files?.[0] || null;
    const filePath = document.getElementById("lifeFilePath").value.trim();
    const title = document.getElementById("lifeFileTitle").value.trim();
    const participantsText = document.getElementById("lifeUploadParticipants").value
      || document.getElementById("lifeParticipants").value;
    let res;
    if (upload) {
      const form = new FormData();
      form.append("file", upload);
      form.append("title", title);
      form.append("participants", participantsText);
      res = await authedFetch("/admin/life/experiences/upload", {
        method: "POST",
        body: form,
      });
    } else {
      const participants = splitList(participantsText);
      res = await authedFetch("/admin/life/experiences/file", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_path: filePath, title, participants }),
      });
    }
    const data = await res.json();
    if (!res.ok) throw new Error(responseErrorMessage(data, "File digestion failed"));
    if (uploadInput) uploadInput.value = "";
    document.getElementById("lifeFilePath").value = "";
    showToast("File experience digested.");
    await loadLife();
  }

  function renderLife() {
    const data = state.life || {};
    const dbPath = document.getElementById("lifeDbPath");
    if (dbPath) dbPath.textContent = data.db_path || "";
    renderLifeSummary(data.counts || {});
    renderLifeSnapshot(data.snapshot || {});
    renderLifeTimeline(data.recent_evolution || []);
    renderLifeExperiences(data.recent_experiences || []);
    renderLifeBeliefs(data.beliefs || []);
    renderLifeDrives(data.drives || []);
    renderLifeOpenQuestions(
      data.open_questions || [],
      (data.counts && data.counts.open_questions) || {},
    );
    loadConstitution();
  }

  async function loadConstitution() {
    const textarea = document.getElementById("constitutionText");
    const updatedEl = document.getElementById("constitutionUpdated");
    if (!textarea) return;
    const res = await authedFetch("/admin/identity/constitution");
    if (!res || !res.ok) return;
    const data = await res.json();
    textarea.value = data.constitution || "";
    if (updatedEl) {
      const ts = Number(data.updated_at || 0);
      if (ts > 0) {
        updatedEl.textContent = `last updated ${new Date(ts * 1000).toISOString()}`;
      } else {
        updatedEl.textContent = "not yet set";
      }
    }
  }

  function renderLifeSummary(counts) {
    const el = document.getElementById("lifeSummary");
    if (!el) return;
    const items = [
      ["Experiences", counts.experiences || 0],
      ["Evolution Events", counts.evolution_events || 0],
      ["Beliefs", counts.beliefs || 0],
      ["Drives", counts.drives || 0],
    ];
    el.innerHTML = items.map(([label, value]) => `
      <article class="card">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(value)}</strong>
      </article>
    `).join("");
  }

  function renderLifeSnapshot(snapshot) {
    const narrative = document.getElementById("lifeSnapshotNarrative");
    const cards = document.getElementById("lifeSnapshotCards");
    const domains = document.getElementById("lifeDomainMix");
    const drift = document.getElementById("lifeDriveDrift");
    if (!narrative || !cards || !domains || !drift) return;

    narrative.textContent = snapshot.readable_summary || "No character evolution has been recorded yet.";
    const latest = snapshot.latest_experience || {};
    const first = snapshot.first_experience || {};
    const dominantDrive = (snapshot.dominant_drives || [])[0] || {};
    const largestDrift = (snapshot.drive_drift || []).find((item) => Math.abs(Number(item.delta || 0)) >= 0.02) || {};
    const signalCards = [
      ["First Experience", first.source_title || "None", first.timestamp ? formatDate(first.timestamp) : ""],
      ["Latest Experience", latest.source_title || "None", latest.timestamp ? formatDate(latest.timestamp) : ""],
      ["Dominant Drive", dominantDrive.name ? titleize(dominantDrive.name) : "Unchanged", dominantDrive.value != null ? Number(dominantDrive.value).toFixed(2) : ""],
      ["Largest Drift", largestDrift.name ? titleize(largestDrift.name) : "None", largestDrift.delta != null ? signed(largestDrift.delta) : "near baseline"],
    ];
    cards.innerHTML = signalCards.map(([label, value, foot]) => `
      <article class="snapshot-card">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(value)}</strong>
        ${foot ? `<div class="metric-foot">${escapeHtml(foot)}</div>` : ""}
      </article>
    `).join("");

    const domainCounts = snapshot.domain_counts || [];
    const maxDomain = Math.max(1, ...domainCounts.map((item) => Number(item.count || 0)));
    domains.innerHTML = domainCounts.length
      ? domainCounts.map((item) => {
          const count = Number(item.count || 0);
          return `
            <article class="domain-row">
              <span>${escapeHtml(titleize(item.domain || "change"))}</span>
              <strong>${escapeHtml(count)}</strong>
              <progress class="domain-bar" value="${Math.max(3, Math.round((count / maxDomain) * 100))}" max="100" aria-hidden="true"></progress>
            </article>
          `;
        }).join("")
      : `<p class="empty-copy">No evolution events yet.</p>`;

    const changedDrives = (snapshot.drive_drift || [])
      .filter((item) => Math.abs(Number(item.delta || 0)) >= 0.005)
      .slice(0, 7);
    drift.innerHTML = changedDrives.length
      ? changedDrives.map((item) => {
          const delta = Number(item.delta || 0);
          const width = Math.max(3, Math.min(100, Math.round(Math.abs(delta) * 200)));
          return `
            <article class="drift-row">
              <span>${escapeHtml(titleize(item.name || "drive"))}</span>
              <strong>${escapeHtml(signed(delta))}</strong>
              <progress class="drift-meter ${delta < 0 ? "negative" : ""}" value="${width}" max="100" aria-hidden="true"></progress>
              <div class="life-reason markdown-copy">${renderMarkdown(item.description || "")}</div>
            </article>
          `;
        }).join("")
      : `<p class="empty-copy">Drives remain near their seed baselines.</p>`;
  }

  function renderLifeTimeline(events) {
    const list = document.getElementById("lifeTimeline");
    if (!list) return;
    if (!events.length) {
      list.innerHTML = `<p class="empty-copy">No evolution events yet.</p>`;
      return;
    }
    list.innerHTML = events.map((event) => `
      <article class="life-row">
        <div class="life-row-head">
          <strong>${escapeHtml(labelize(event.domain || "change"))}</strong>
          <span>${escapeHtml(event.subject || "")}</span>
        </div>
        <div class="life-change markdown-copy">${renderMarkdown(event.after_state || "")}</div>
        <div class="life-reason markdown-copy">${renderMarkdown(event.reason || "")}</div>
        <div class="skill-meta">confidence ${Number(event.confidence || 0).toFixed(2)} · experience ${escapeHtml(event.experience_id ?? "")} · batch ${escapeHtml(event.batch_id || "")}</div>
      </article>
    `).join("");
  }

  function renderLifeExperiences(experiences) {
    const list = document.getElementById("lifeExperiences");
    if (!list) return;
    if (!experiences.length) {
      list.innerHTML = `<p class="empty-copy">No experiences recorded yet.</p>`;
      return;
    }
    list.innerHTML = experiences.map((experience) => `
      <article class="life-row">
        <div class="life-row-head">
          <strong>${escapeHtml(experience.source_title || "Experience")}</strong>
          <span>${escapeHtml(labelize(experience.source_type || ""))}</span>
        </div>
        <div class="life-change markdown-copy">${renderMarkdown(experience.content_summary || "")}</div>
        <div class="life-reason markdown-copy">${renderMarkdown(experience.emotional_impact || "")}</div>
        <div class="skill-meta">salience ${Number(experience.salience || 0).toFixed(2)} · confidence ${Number(experience.confidence || 0).toFixed(2)} · batch ${escapeHtml(experience.batch_id || "")}</div>
      </article>
    `).join("");
  }

  function renderLifeBeliefs(beliefs) {
    const list = document.getElementById("lifeBeliefs");
    if (!list) return;
    if (!beliefs.length) {
      list.innerHTML = `<p class="empty-copy">No worldview records yet.</p>`;
      return;
    }
    list.innerHTML = beliefs.slice(0, 12).map((belief) => `
      <article class="life-row compact">
        <div class="life-row-head">
          <strong>${escapeHtml(belief.key || "belief")}</strong>
          <span>${escapeHtml((belief.confidence || 0).toFixed ? belief.confidence.toFixed(2) : belief.confidence)}</span>
        </div>
        <div class="life-change markdown-copy">${renderMarkdown(belief.statement || "")}</div>
      </article>
    `).join("");
  }

  function renderLifeDrives(drives) {
    const list = document.getElementById("lifeDrives");
    if (!list) return;
    if (!drives.length) {
      list.innerHTML = `<p class="empty-copy">No drive state found.</p>`;
      return;
    }
    list.innerHTML = drives.map((drive) => {
      const value = Number(drive.value || 0);
      return `
        <article class="life-row compact">
          <div class="life-row-head">
            <strong>${escapeHtml(labelize(drive.name || "drive"))}</strong>
            <span>${value.toFixed(2)}</span>
          </div>
          <progress class="drive-meter" value="${Math.max(0, Math.min(100, value * 100))}" max="100" aria-hidden="true"></progress>
          <div class="life-reason markdown-copy">${renderMarkdown(drive.description || "")}</div>
        </article>
      `;
    }).join("");
  }

  function renderLifeOpenQuestions(questions, counts) {
    const list = document.getElementById("lifeOpenQuestions");
    const countsEl = document.getElementById("lifeOpenQuestionsCounts");
    if (countsEl) {
      const open = Number(counts.open || 0);
      const pursuing = Number(counts.pursuing || 0);
      const resolved = Number(counts.resolved || 0);
      const abandoned = Number(counts.abandoned || 0);
      countsEl.textContent =
        `open ${open} · pursuing ${pursuing} · resolved ${resolved} · abandoned ${abandoned}`;
    }
    if (!list) return;
    if (!questions.length) {
      list.innerHTML = `<p class="empty-copy">No open questions yet. Reflection populates this on each session-start metabolism tick.</p>`;
      return;
    }
    list.innerHTML = questions.map((q) => {
      const id = Number(q.id || 0);
      const priority = Number(q.priority || 0);
      const sourceKind = String(q.source_kind || "open_question");
      const targetDrive = q.target_drive ? ` · drive=${escapeHtml(String(q.target_drive))}` : "";
      return `
        <article class="life-row compact">
          <div class="life-row-head">
            <strong>${escapeHtml(labelize(sourceKind))}</strong>
            <span>priority ${priority.toFixed(2)}${targetDrive}</span>
          </div>
          <div class="life-change markdown-copy">${renderMarkdown(q.prompt_text || "")}</div>
          <div class="button-row end">
            <button class="secondary" data-abandon-oq="${id}">Abandon</button>
          </div>
        </article>
      `;
    }).join("");
    list.querySelectorAll("[data-abandon-oq]").forEach((btn) => {
      btn.addEventListener("click", async (event) => {
        const target = event.currentTarget;
        const id = Number(target.getAttribute("data-abandon-oq") || 0);
        if (!id) return;
        const res = await authedFetch(
          `/admin/life/open-questions/${id}/abandon`,
          { method: "POST" },
        );
        if (res && res.ok) {
          await loadLife();
        }
      });
    });
  }

  async function ensureCodexModels() {
    if (state.codexModelsLoaded || state.codexModelsLoading) return;
    state.codexModelsLoading = true;
    state.codexModelsError = "";
    try {
      const res = await authedFetch("/admin/codex/models");
      const data = await res.json();
      if (!res.ok || !data.ok) {
        throw new Error(responseErrorMessage(data, data.error || "Could not load Codex models."));
      }
      state.codexModels = Array.isArray(data.models) ? data.models : [];
    } catch (err) {
      state.codexModels = [];
      state.codexModelsError = err.message || String(err);
    } finally {
      state.codexModelsLoaded = true;
      state.codexModelsLoading = false;
    }
  }

  async function loadAll() {
    setStatus("Loading");
    const [statusRes, configRes] = await Promise.all([
      authedFetch("/admin/status"),
      authedFetch("/admin/config"),
    ]);
    if (!statusRes.ok) throw new Error("Status HTTP " + statusRes.status);
    if (!configRes.ok) throw new Error("Config HTTP " + configRes.status);
    state.status = await statusRes.json();
    const configPayload = await configRes.json();
    state.config = configPayload.config || {};
    state.metadata = configPayload.field_metadata || [];
    if (state.config.llm_backend === "codex") await ensureCodexModels();
    await loadPersonaState();
    renderOverview();
    renderAllForms();
    await loadSoul(false);
    state.toolsLoaded = false;
    state.skillsLoaded = false;
    state.lifeLoaded = false;
    await Promise.all([
      loadTools().catch((err) => {
        document.getElementById("toolsList").innerHTML = `<div class="tool-row"><p class="empty-copy">${escapeHtml(err.message || String(err))}</p></div>`;
        showToast(err.message || String(err), "error");
      }),
      loadSkills().catch((err) => {
        document.getElementById("skillsList").innerHTML = `<p class="empty-copy">${escapeHtml(err.message || String(err))}</p>`;
        showToast(err.message || String(err), "error");
      }),
      loadLife().catch((err) => {
        document.getElementById("lifeTimeline").innerHTML = `<p class="empty-copy">${escapeHtml(err.message || String(err))}</p>`;
        showToast(err.message || String(err), "error");
      }),
    ]);
  }

  async function loadPersonaState() {
    try {
      const res = await authedFetch("/admin/persona/state");
      const data = await res.json();
      if (!res.ok) throw new Error(responseErrorMessage(data, "Persona HTTP " + res.status));
      state.previousPersona = state.persona;
      state.persona = data;
    } catch (err) {
      state.previousPersona = state.persona;
      state.persona = {
        error: err.message || String(err),
        count: 0,
        channel_counts: {},
        sessions: [],
      };
    }
  }

  async function loadSoul(showMessage) {
    try {
      const res = await authedFetch("/admin/soul");
      if (!res.ok) throw new Error("Soul HTTP " + res.status);
      const data = await res.json();
      state.soul = data.soul || {};
      renderIdentityForm();
      if (showMessage) showToast("Identity reloaded.");
    } catch (err) {
      const out = document.getElementById("soulOutput");
      out.hidden = false;
      out.textContent = String(err.message || err);
    }
  }

  function collectConfigPayload() {
    const payload = {};
    for (const name of requiredConfigFields) {
      const el = document.querySelector(`[data-config-field="${CSS.escape(name)}"]`);
      if (!el) {
        payload[name] = state.config[name];
        continue;
      }
      if (secretFields.has(name)) {
        payload[name] = el.value;
        const clear = document.querySelector(`[data-clear-secret="${CSS.escape(name)}"]`);
        payload["clear_" + name] = !!(clear && clear.checked);
      } else if (listFields.has(name)) {
        payload[name] = splitList(el.value);
      } else if (el.type === "checkbox") {
        payload[name] = el.checked;
      } else if (el.type === "number") {
        payload[name] = numberValue(el.value, state.config[name]);
      } else {
        payload[name] = el.value;
      }
    }
    return payload;
  }

  async function saveConfig() {
    const payload = collectConfigPayload();
    // Gate high-blast-radius toggles with explicit confirmation.
    const wasShellOff = !state.config.shell_tool_enabled;
    const wasNotHighRisk = state.config.autonomy_level !== "high_risk";
    if (wasShellOff && payload.shell_tool_enabled) {
      const reply = window.prompt("Shell tool can execute arbitrary commands on this machine.\nType ENABLE SHELL to confirm.");
      if (reply !== "ENABLE SHELL") { showToast("Shell tool not enabled — confirmation required.", "warn"); return; }
    }
    if (wasNotHighRisk && payload.autonomy_level === "high_risk") {
      const reply = window.prompt("High-risk autonomy allows destructive tool operations without confirmation.\nType I UNDERSTAND HIGH RISK to confirm.");
      if (reply !== "I UNDERSTAND HIGH RISK") { showToast("High-risk autonomy not enabled — confirmation required.", "warn"); return; }
    }
    els.saveBtn.disabled = true;
    try {
      const res = await authedFetch("/admin/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "HTTP " + res.status);
      state.config = data.config || {};
      state.metadata = data.field_metadata || [];
      if (state.config.llm_backend === "codex") await ensureCodexModels();
      await refreshStatusOnly();
      renderAllForms();
      state.toolsLoaded = false;
      await loadTools();
      const applyState = data.apply_state || {};
      const restartFields = applyState.restart_required_fields || [];
      if (restartFields.length) {
        showToast(`Configuration saved. Restart required for ${restartFields.join(", ")}.`, "warn");
      } else if (applyState.session_manager_reloaded) {
        showToast("Configuration saved and runtime reloaded.");
      } else {
        showToast(data.message || "Configuration saved.");
      }
    } finally {
      els.saveBtn.disabled = false;
    }
  }

  async function refreshStatusOnly() {
    const res = await authedFetch("/admin/status");
    if (!res.ok) throw new Error("Status HTTP " + res.status);
    state.status = await res.json();
    await loadPersonaState();
    renderOverview();
  }

  async function runJsonAction(outputId, fn) {
    const out = document.getElementById(outputId);
    out.hidden = false;
    out.textContent = "Running...";
    try {
      const data = await fn();
      out.textContent = pretty(data);
    } catch (err) {
      out.textContent = String(err.message || err);
      showToast(out.textContent, "error");
    }
  }

  async function testLlm() {
    await runJsonAction("llmTestOutput", async () => {
      const payload = collectConfigPayload();
      const res = await authedFetch("/admin/test/llm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          llm_backend: payload.llm_backend,
          llm_base_url: payload.llm_base_url,
          llm_model: payload.llm_model,
          llm_api_key: payload.llm_api_key,
          live: true,
        }),
      });
      return await res.json();
    });
  }

  async function testTelegram() {
    await runJsonAction("telegramTestOutput", async () => {
      const payload = collectConfigPayload();
      const res = await authedFetch("/admin/test/telegram", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          telegram_token: payload.telegram_token,
          telegram_allowlist: payload.telegram_allowlist,
        }),
      });
      return await res.json();
    });
  }

  async function reloadRuntime() {
    await runJsonAction("maintenanceOutput", async () => {
      const res = await authedFetch("/admin/runtime/reload", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(responseErrorMessage(data, "Runtime reload failed"));
      showToast("Saved config applied to runtime.");
      await refreshStatusOnly();
      return data;
    });
  }

  async function restartRuntime() {
    const confirmation = window.prompt("Type RESTART to restart the web server process.");
    if (confirmation === null) return;
    await runJsonAction("maintenanceOutput", async () => {
      const res = await authedFetch("/admin/runtime/restart", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(responseErrorMessage(data, "Restart failed"));
      showToast("Web server restart scheduled.");
      return data;
    });
  }

  async function deleteBackup() {
    const filename = window.prompt("Backup filename to delete, for example nur-backup-YYYYMMDDTHHMMSSZ.zip");
    if (!filename) return;
    const confirmation = window.prompt(`Type DELETE ${filename} to delete this backup.`);
    if (confirmation === null) return;
    await runJsonAction("maintenanceOutput", async () => {
      const res = await authedFetch("/admin/backups/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename, confirmation }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(responseErrorMessage(data, "Backup delete failed"));
      showToast("Backup deleted.");
      return data;
    });
  }

  async function saveSoul() {
    const payload = {};
    for (const [name, , type] of soulFields) {
      const el = document.querySelector(`[data-soul-field="${CSS.escape(name)}"]`);
      if (!el) continue;
      if (type === "list") payload[name] = splitList(el.value);
      else if (type === "map") payload[name] = parseMap(el.value);
      else payload[name] = el.value;
    }
    await runJsonAction("soulOutput", async () => {
      const res = await authedFetch("/admin/soul", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "HTTP " + res.status);
      state.soul = data.soul || {};
      renderIdentityForm();
      showToast("Identity saved.");
      return data;
    });
  }

  function splitList(value) {
    return String(value || "")
      .split(/[\n,]/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function parseMap(value) {
    const out = {};
    for (const line of String(value || "").split("\n")) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      const idx = trimmed.indexOf("=");
      if (idx < 0) continue;
      const key = trimmed.slice(0, idx).trim();
      const raw = trimmed.slice(idx + 1).trim();
      const parsed = Number(raw);
      if (key && !Number.isNaN(parsed)) out[key] = parsed;
    }
    return out;
  }

  function numberValue(value, fallback) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function openPage(page) {
    const settingsAliases = {
      runtime: "runtime",
      character: "character",
      models: "models",
      tools: "tools",
      identity: "identity",
      access: "access",
      channels: "channels",
      maintenance: "maintenance",
    };
    const settingsSection = settingsAliases[page] || "";
    if (settingsSection) page = "settings";
    if (page === "persona") page = "observability";
    if (!document.getElementById("page-" + page)) page = "overview";
    state.activePage = page;
    for (const el of document.querySelectorAll(".page")) {
      el.classList.toggle("active", el.id === "page-" + page);
    }
    for (const item of document.querySelectorAll(".nav-item")) {
      item.classList.toggle("active", item.dataset.page === page);
    }
    els.pageTitle.textContent = "Nūr Settings";
    history.replaceState(null, "", "#" + page);
    if (page === "settings" && !state.toolsLoaded && Object.keys(state.config).length) {
      loadTools().catch((err) => {
        document.getElementById("toolsList").innerHTML = `<div class="tool-row"><p class="empty-copy">${escapeHtml(err.message || String(err))}</p></div>`;
        showToast(err.message || String(err), "error");
      });
    }
    if (page === "skills" && !state.skillsLoaded) {
      loadSkills().catch((err) => {
        document.getElementById("skillsList").innerHTML = `<p class="empty-copy">${escapeHtml(err.message || String(err))}</p>`;
        showToast(err.message || String(err), "error");
      });
    }
    if (page === "life" && !state.lifeLoaded) {
      loadLife().catch((err) => {
        document.getElementById("lifeTimeline").innerHTML = `<p class="empty-copy">${escapeHtml(err.message || String(err))}</p>`;
        showToast(err.message || String(err), "error");
      });
    }
    window.requestAnimationFrame(() => {
      if (settingsSection) openSettingsSection(settingsSection);
      else document.getElementById("page-" + page)?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  function openSettingsSection(section) {
    const details = document.getElementById("settings-" + section);
    if (!details) return;
    details.open = true;
    details.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function openPageFromHash() {
    openPage((location.hash || "#overview").slice(1) || "overview");
  }

  function openTokenDialog() {
    els.apiTokenInput.value = getApiToken();
    if (typeof els.tokenDialog.showModal === "function") {
      els.tokenDialog.showModal();
    } else {
      const token = window.prompt("API token", getApiToken());
      if (token !== null) setApiToken(token.trim());
    }
  }

  function syncModelDraftValues() {
    for (const name of ["llm_backend", "llm_base_url", "llm_model"]) {
      const el = document.querySelector(`[data-config-field="${CSS.escape(name)}"]`);
      if (el) state.config[name] = el.value;
    }
  }

  async function handleModelsFormChange(event) {
    const target = event.target;
    if (!target || target.dataset.configField !== "llm_backend") return;
    syncModelDraftValues();
    if (state.config.llm_backend === "codex") await ensureCodexModels();
    renderConfigForm("modelsForm", fieldSections.models);
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
    return escapeHtml(value);
  }

  function wireEvents() {
    for (const item of document.querySelectorAll(".nav-item")) {
      item.addEventListener("click", () => openPage(item.dataset.page));
    }
    for (const item of document.querySelectorAll("[data-action='open-page']")) {
      item.addEventListener("click", () => {
        openPage(item.dataset.page);
        if (item.dataset.settingsSection) openSettingsSection(item.dataset.settingsSection);
      });
    }
    els.saveBtn.addEventListener("click", () => saveConfig().catch((err) => showToast(err.message, "error")));
    els.refreshAllBtn.addEventListener("click", () => loadAll().then(() => showToast("Refreshed.")).catch((err) => showToast(err.message, "error")));
    els.tokenBtn.addEventListener("click", openTokenDialog);
    els.saveTokenBtn.addEventListener("click", () => {
      setApiToken(els.apiTokenInput.value.trim());
      // Re-run loadAll so the admin page populates with the new token
      // without requiring a manual refresh.
      loadAll().catch((err) => {
        setStatus("Error", "error");
        showToast(err.message || String(err), "error");
      });
    });
    document.getElementById("modelsForm").addEventListener("change", (event) => {
      handleModelsFormChange(event).catch((err) => showToast(err.message || String(err), "error"));
    });
    document.getElementById("testLlmBtn").addEventListener("click", testLlm);
    document.getElementById("testTelegramBtn").addEventListener("click", testTelegram);
    document.getElementById("refreshPersonaBtn").addEventListener("click", async () => {
      await loadPersonaState();
      renderPersonaAdminPage();
      renderOverviewPersona();
      showToast("Persona refreshed.");
    });
    document.getElementById("refreshToolsBtn").addEventListener("click", () => {
      state.toolsLoaded = false;
      loadTools().catch((err) => showToast(err.message || String(err), "error"));
    });
    document.getElementById("toolSearch").addEventListener("input", renderToolInventory);
    document.getElementById("toolCategoryFilter").addEventListener("change", renderToolInventory);
    document.getElementById("refreshSkillsBtn").addEventListener("click", () => {
      state.skillsLoaded = false;
      loadSkills().catch((err) => showToast(err.message || String(err), "error"));
    });
    document.getElementById("importSkillBtn").addEventListener("click", () => {
      importSkill().catch((err) => showToast(err.message || String(err), "error"));
    });
    document.getElementById("skillsList").addEventListener("click", (event) => {
      const target = event.target.closest("[data-skill-action]");
      if (!target) return;
      mutateSkill(target.dataset.skillId, target.dataset.skillAction)
        .catch((err) => showToast(err.message || String(err), "error"));
    });
    document.getElementById("refreshLifeBtn").addEventListener("click", () => {
      state.lifeLoaded = false;
      loadLife().catch((err) => showToast(err.message || String(err), "error"));
    });
    document.getElementById("saveConstitutionBtn").addEventListener("click", async () => {
      const textarea = document.getElementById("constitutionText");
      if (!textarea) return;
      const res = await authedFetch("/admin/identity/constitution", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ constitution: textarea.value }),
      });
      if (res && res.ok) {
        showToast("Constitution saved", "ok");
        await loadConstitution();
      } else {
        showToast("Failed to save constitution", "error");
      }
    });
    document.getElementById("ingestLifeTextBtn").addEventListener("click", () => {
      ingestLifeText().catch((err) => showToast(err.message || String(err), "error"));
    });
    document.getElementById("ingestLifeFileBtn").addEventListener("click", () => {
      const uploadInput = document.getElementById("lifeUploadFile");
      const hasFile = uploadInput?.files?.length > 0;
      const hasPath = document.getElementById("lifeFilePath").value.trim().length > 0;
      if (!hasFile && !hasPath) {
        uploadInput?.click();
        return;
      }
      ingestLifeFile().catch((err) => showToast(err.message || String(err), "error"));
    });
    document.getElementById("reloadSoulBtn").addEventListener("click", () => loadSoul(true));
    document.getElementById("saveSoulBtn").addEventListener("click", saveSoul);
    document.getElementById("testStorageBtn").addEventListener("click", () => runJsonAction("maintenanceOutput", async () => {
      const payload = collectConfigPayload();
      const res = await authedFetch("/admin/test/storage", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ data_dir: payload.data_dir, tools_workspace: payload.tools_workspace, create_missing: true }),
      });
      return await res.json();
    }));
    document.getElementById("diagnosticsBtn").addEventListener("click", () => runJsonAction("maintenanceOutput", async () => {
      const res = await authedFetch("/admin/diagnostics");
      return await res.json();
    }));
    document.getElementById("reloadRuntimeBtn").addEventListener("click", () => {
      reloadRuntime().catch((err) => showToast(err.message || String(err), "error"));
    });
    document.getElementById("restartRuntimeBtn").addEventListener("click", () => {
      restartRuntime().catch((err) => showToast(err.message || String(err), "error"));
    });
    document.getElementById("exportConfigBtn").addEventListener("click", () => runJsonAction("maintenanceOutput", async () => {
      const res = await authedFetch("/admin/export/config");
      return await res.json();
    }));
    document.getElementById("backupBtn").addEventListener("click", () => runJsonAction("maintenanceOutput", async () => {
      const res = await authedFetch("/admin/backup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ include_data: true }),
      });
      return await res.json();
    }));
    document.getElementById("listBackupsBtn").addEventListener("click", () => runJsonAction("maintenanceOutput", async () => {
      const res = await authedFetch("/admin/backups");
      return await res.json();
    }));
    document.getElementById("deleteBackupBtn").addEventListener("click", () => {
      deleteBackup().catch((err) => showToast(err.message || String(err), "error"));
    });
  }

  wireEvents();
  window.addEventListener("hashchange", openPageFromHash);
  openPageFromHash();
  loadAll().catch((err) => {
    setStatus("Error", "error");
    showToast(err.message || String(err), "error");
  });
