(function () {
  const state = {
    config: {},
    metadata: [],
    status: null,
    soul: null,
    tools: [],
    toolsLoaded: false,
    skills: [],
    skillsRoot: "",
    skillsLoaded: false,
    life: null,
    lifeLoaded: false,
    activePage: "overview",
  };

  const secretFields = new Set(["telegram_token", "llm_api_key", "minimax_api_key", "api_key"]);
  const listFields = new Set(["telegram_allowlist", "cors_origins"]);
  const advancedConfigFields = new Set([
    "data_dir",
    "max_queue_per_user",
    "max_active_sessions",
    "session_timeout_seconds",
    "debug_host",
    "debug_port",
    "proactive_idle_threshold",
    "proactive_max_per_session",
    "proactive_cooldown",
    "proactive_check_interval",
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
      ["proactive_max_per_session", "Proactive Max Per Session", "number"],
      ["proactive_cooldown", "Proactive Cooldown", "number"],
      ["proactive_check_interval", "Proactive Check Interval", "number"],
    ],
    models: [
      ["llm_backend", "LLM Backend", "select"],
      ["llm_base_url", "LLM Base URL", "text", true],
      ["llm_model", "LLM Model", "text"],
      ["llm_api_key", "LLM API Key", "secret"],
      ["minimax_api_key", "MiniMax API Key", "secret"],
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
    "proactive_max_per_session",
    "proactive_cooldown",
    "proactive_check_interval",
    "telegram_token",
    "llm_api_key",
    "minimax_api_key",
    "api_key",
    "cors_origins",
    "tools_enabled",
    "autonomy_level",
    "tools_workspace",
    "shell_tool_enabled",
  ];

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
    els.toast.style.background = tone === "error" ? "#b42318" : (tone === "warn" ? "#b54708" : "#172033");
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
    if (name === "llm_backend") return ["auto", "provider", "openai_compatible", "minimax", "mock"];
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

  function renderOverview() {
    const status = state.status || {};
    const tools = status.tools || {};
    const sessions = status.sessions || {};
    const setup = status.setup || {};
    const cards = [
      ["LLM", status.llm_configured ? "Configured" : "Not configured", status.llm_backend || "auto"],
      ["Auth", status.auth_enabled ? "Enabled" : "Disabled", status.auth_enabled ? "Bearer token required" : "Open local surface"],
      ["Tools", tools.enabled ? (tools.shell_enabled ? "Shell enabled" : "Enabled") : "Disabled", tools.workspace || "No workspace"],
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
      els.tokenBtn.style.display = status.auth_enabled ? "" : "none";
    }
    renderWarnings(status.warnings || []);
    const hasErrors = (status.warnings || []).some((item) => item.severity === "error");
    const hasWarnings = (status.warnings || []).length > 0;
    setStatus(hasErrors ? "Needs attention" : (hasWarnings ? "Warnings" : "OK"), hasErrors ? "error" : (hasWarnings ? "warn" : "ok"));
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

    if (!state.config.tools_enabled) {
      list.innerHTML = `<div class="tool-row"><p class="empty-copy">Tools are disabled in runtime config. Enable Agentic Tools and save to inspect registered capabilities.</p></div>`;
      return;
    }
    if (!filtered.length) {
      list.innerHTML = `<div class="tool-row"><p class="empty-copy">${tools.length ? "No tools match the current filter." : "No tools are registered for the current runtime."}</p></div>`;
      return;
    }
    list.innerHTML = filtered.map(renderToolRow).join("");
  }

  function renderToolRow(tool) {
    const argKeys = Object.keys(tool.arg_schema || {});
    const chips = [
      tool.category,
      tool.requires_network ? "network" : "",
      tool.mcp_backed ? "mcp" : "",
      tool.supports_streaming ? "streaming" : "",
    ].filter(Boolean);
    return `
      <article class="tool-row">
        <div>
          <div class="tool-name">${escapeHtml(tool.name)}</div>
          <div class="chip-row">
            ${chips.map((chip) => `<span class="chip ${escapeAttr(chip)}">${escapeHtml(labelize(chip))}</span>`).join("")}
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
            <div class="skill-description">${escapeHtml(skill.description || "No description")}</div>
          </div>
          <div class="skill-actions">
            <span class="status-chip ${escapeAttr(skill.status || "disabled")}">${escapeHtml(labelize(skill.status || "disabled"))}</span>
            <button class="secondary" data-skill-action="audit" data-skill-id="${escapeAttr(skill.id)}">Audit</button>
            ${enabled
              ? `<button class="secondary" data-skill-action="disable" data-skill-id="${escapeAttr(skill.id)}">Disable</button>`
              : `<button class="primary" data-skill-action="enable" data-skill-id="${escapeAttr(skill.id)}" ${canEnable ? "" : "disabled"}>Enable</button>`}
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
    const res = await authedFetch(`/admin/skills/${encodeURIComponent(skillId)}/${action}`, {
      method: "POST",
    });
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

  async function rollbackLifeBatch() {
    const batchId = document.getElementById("lifeRollbackBatchId").value.trim();
    if (!batchId) throw new Error("Enter a Life History batch ID first.");
    const res = await authedFetch("/admin/life/rollback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ batch_id: batchId }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(responseErrorMessage(data, "Rollback failed"));
    document.getElementById("lifeRollbackBatchId").value = "";
    showToast("Evolution batch rolled back.");
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
              <div class="domain-bar"><span style="width:${Math.max(3, Math.round((count / maxDomain) * 100))}%"></span></div>
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
              <div class="drift-meter"><span class="${delta < 0 ? "negative" : ""}" style="width:${width}%"></span></div>
              <div class="life-reason">${escapeHtml(item.description || "")}</div>
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
        <div class="life-change">${escapeHtml(event.after_state || "")}</div>
        <div class="life-reason">${escapeHtml(event.reason || "")}</div>
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
        <div class="life-change">${escapeHtml(experience.content_summary || "")}</div>
        <div class="life-reason">${escapeHtml(experience.emotional_impact || "")}</div>
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
        <div class="life-change">${escapeHtml(belief.statement || "")}</div>
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
          <div class="drive-meter"><span style="width:${Math.max(0, Math.min(100, value * 100))}%"></span></div>
          <div class="life-reason">${escapeHtml(drive.description || "")}</div>
        </article>
      `;
    }).join("");
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
    renderOverview();
    renderAllForms();
    if (state.activePage === "tools") {
      state.toolsLoaded = false;
      await loadTools();
    }
    if (state.activePage === "life") {
      state.lifeLoaded = false;
      await loadLife();
    }
    await loadSoul(false);
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
    els.saveBtn.disabled = true;
    try {
      const res = await authedFetch("/admin/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(collectConfigPayload()),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "HTTP " + res.status);
      state.config = data.config || {};
      state.metadata = data.field_metadata || [];
      await refreshStatusOnly();
      renderAllForms();
      if (state.activePage === "tools") {
        state.toolsLoaded = false;
        await loadTools();
      }
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
          minimax_api_key: payload.minimax_api_key,
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
    state.activePage = page;
    for (const el of document.querySelectorAll(".page")) {
      el.classList.toggle("active", el.id === "page-" + page);
    }
    for (const item of document.querySelectorAll(".nav-item")) {
      item.classList.toggle("active", item.dataset.page === page);
    }
    els.pageTitle.textContent = document.querySelector(`.nav-item[data-page="${CSS.escape(page)}"]`)?.textContent || "Admin";
    history.replaceState(null, "", "#" + page);
    if (page === "tools" && !state.toolsLoaded && Object.keys(state.config).length) {
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
      item.addEventListener("click", () => openPage(item.dataset.page));
    }
    els.saveBtn.addEventListener("click", () => saveConfig().catch((err) => showToast(err.message, "error")));
    els.refreshAllBtn.addEventListener("click", () => loadAll().then(() => showToast("Refreshed.")).catch((err) => showToast(err.message, "error")));
    els.tokenBtn.addEventListener("click", openTokenDialog);
    els.saveTokenBtn.addEventListener("click", () => setApiToken(els.apiTokenInput.value.trim()));
    document.getElementById("testLlmBtn").addEventListener("click", testLlm);
    document.getElementById("testTelegramBtn").addEventListener("click", testTelegram);
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
    document.getElementById("ingestLifeTextBtn").addEventListener("click", () => {
      ingestLifeText().catch((err) => showToast(err.message || String(err), "error"));
    });
    document.getElementById("ingestLifeFileBtn").addEventListener("click", () => {
      ingestLifeFile().catch((err) => showToast(err.message || String(err), "error"));
    });
    document.getElementById("rollbackLifeBatchBtn").addEventListener("click", () => {
      rollbackLifeBatch().catch((err) => showToast(err.message || String(err), "error"));
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
  }

  wireEvents();
  openPage((location.hash || "#overview").slice(1) || "overview");
  loadAll().catch((err) => {
    setStatus("Error", "error");
    showToast(err.message || String(err), "error");
  });
})();
