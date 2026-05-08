import { createSurfaceState, renderMarkdown } from "/assets/lib/shared.js";
import { AdminOverlayState } from "/assets/admin-overlay.js";
import { DebugState } from "/assets/debug.js";
import { PersonaPanelState } from "/assets/persona-panel.js";
import { WizardState } from "/assets/wizard.js";

const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('msgInput');
const sendBtn = document.getElementById('sendBtn');
const typingRow = document.getElementById('typingRow');
const emptyState = document.getElementById('emptyState');
const debugPanel = document.getElementById('debugPanel');
const debugToggle = document.getElementById('debugToggle');
const settingsOverlay = document.getElementById('settingsOverlay');

const userId = getOrCreateId('nur_user_id', localStorage);
const chatId = getOrCreateId('nur_chat_id', sessionStorage);
let sending = false;
let lastEmotion = 'neutral';
let agentName = 'Nūr';
let lastExplanation = null;
let lastDebugPayload = null;
const ChatState = createSurfaceState({ sending: false, messageCount: 0 }, {
  sending(value) {
    sending = value;
    updateSendButtonState();
  },
});

function updateSendButtonState() {
  sendBtn.disabled = sending || !inputEl.value.trim();
}

function applyAgentName(name) {
  if (!name || !name.trim()) return;
  agentName = name.trim();
  const initial = agentName.charAt(0).toUpperCase();
  document.title = agentName;
  const logo = document.getElementById('headerLogo');
  if (logo) logo.textContent = agentName;
  const emptyTitle = document.getElementById('emptyTitle');
  if (emptyTitle) emptyTitle.textContent = agentName;
  document.getElementById('msgInput').placeholder = 'Message ' + agentName + '...';
  document.querySelectorAll('.nur-av').forEach(el => { el.textContent = initial; });
}

// ---- API-bearer token (client-side) --------------------------------------
// Stored only for this browser session. Must match RuntimeConfig.api_key on
// the server when set.
function getApiToken() { return sessionStorage.getItem('nur_api_token') || ''; }
function setApiToken(tok) {
  if (tok) sessionStorage.setItem('nur_api_token', tok);
  else sessionStorage.removeItem('nur_api_token');
}
function authHeaders(extra) {
  const h = extra ? Object.assign({}, extra) : {};
  const t = getApiToken();
  if (t) h['Authorization'] = 'Bearer ' + t;
  return h;
}
async function authedFetch(url, opts) {
  const o = Object.assign({}, opts || {});
  o.headers = authHeaders(o.headers || {});
  return fetch(url, o);
}

// Auto-resize textarea
inputEl.addEventListener('input', () => {
  updateSendButtonState();
});
inputEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});
updateSendButtonState();
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeSettings(); });
document.addEventListener('click', handleModuleAction);
document.addEventListener('input', e => {
  if (e.target && e.target.id && e.target.id.startsWith('wiz-val-')) wizardUpdateSliderLabels();
});
document.addEventListener('change', e => {
  if (e.target && e.target.id === 'wiz-soul-archetype') wizardApplyArchetype(e.target.value);
  if (e.target && ['cfg-llm_backend','cfg-llm_base_url','cfg-llm_model'].includes(e.target.id)) {
    if (e.target.id === 'cfg-llm_backend') handleLegacyBackendChange().catch(() => {});
    try { updateSoulDraftAvailability(); } catch (_) {}
  }
});
settingsOverlay.addEventListener('click', closeSettingsIfBackdrop);
new MutationObserver(refreshMoodContrast)
  .observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
refreshMoodContrast();
loadSettings().catch(() => {});

function handleModuleAction(event) {
  const preset = event.target.closest('.wizard-preset[data-preset]');
  if (preset) {
    event.preventDefault();
    wizardSelectPreset(preset.dataset.preset);
    return;
  }
  const target = event.target.closest('[data-action]');
  if (!target) return;
  event.preventDefault();
  const action = target.dataset.action;
  const step = target.dataset.step ? Number(target.dataset.step) : null;
  const actions = {
    'toggle-debug': () => { window.location.href = '/settings#observability'; },
    'open-settings': () => { window.location.href = '/settings'; },
    'close-settings': closeSettings,
    'open-wizard': openWizard,
    'wizard-dismiss': wizardDismiss,
    'wizard-goto': () => wizardGoto(step),
    'wizard-test-llm': wizardTestLLM,
    'wizard-save-llm': wizardSaveLLM,
    'wizard-save-runtime': wizardSaveRuntimeChannels,
    'wizard-save-soul': wizardSaveSoul,
    'wizard-skip-life': wizardSkipLife,
    'wizard-save-life': wizardSaveLife,
    'wizard-finish': wizardFinish,
    'save-soul': saveSoul,
    'load-soul': () => loadSoul(),
    'test-storage': testStorage,
    'test-llm': testLLM,
    'test-telegram': testTelegram,
    'load-diagnostics': loadDiagnostics,
    'export-config': exportConfig,
    'create-backup': createBackup,
    'reset-admin-session': resetAdminSession,
    'delete-admin-user': deleteAdminUser,
    'save-settings': saveSettings,
    'load-settings': () => loadSettings(),
    'send-message': sendMessage,
    'show-why-response': showWhyResponse,
    'end-session': endSession,
    'apply-rest': applyRest,
  };
  const fn = actions[action];
  if (fn) Promise.resolve(fn()).catch(err => setStatus('Error: ' + err.message, true));
}

async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text || sending) return;
  inputEl.value = '';
  ChatState.sending = true;

  if (emptyState) emptyState.remove();
  addMessage(text, 'user');
  typingRow.classList.add('visible');
  document.documentElement.style.setProperty('--mood-pulse', '1.2s');
  scrollToBottom();

  try {
    const res = await authedFetch('/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ message: text, user_id: userId, chat_id: chatId }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Server error');
    typingRow.classList.remove('visible');
    lastEmotion = (data.debug && data.debug.emotion_label) || 'neutral';
    addMessage(data.response, 'assistant', lastEmotion);
    if (data.debug) updateDebug(data.debug);
  } catch (err) {
    typingRow.classList.remove('visible');
    addMessage('Error: ' + err.message, 'assistant');
  } finally {
    ChatState.sending = false;
    document.documentElement.style.setProperty('--mood-pulse', '3s');
    inputEl.focus();
  }
}

function addMessage(text, role, emotion) {
  const row = document.createElement('div');
  row.className = 'message-row ' + role;
  const isUser = role === 'user';
  let headerExtra = '';
  if (!isUser && emotion) {
    headerExtra = '<span class="msg-mood-tag">' + esc(emotion) + '</span>';
  }
  row.innerHTML =
    '<div class="message-inner">' +
      '<div class="msg-avatar ' + (isUser ? 'user-av' : 'nur-av') + '">' + (isUser ? 'Y' : esc(agentName.charAt(0).toUpperCase())) + '</div>' +
      '<div class="msg-content">' +
        '<div class="msg-header"><span class="msg-name">' + (isUser ? 'You' : esc(agentName)) + '</span>' + headerExtra + '</div>' +
        '<div class="msg-body">' + (isUser ? esc(text) : renderMarkdown(text)) + '</div>' +
      '</div>' +
    '</div>';
  row.addEventListener('animationend', () => row.classList.add('is-settled'), { once: true });
  messagesEl.appendChild(row);
  scrollToBottom();
}

function scrollToBottom() {
  requestAnimationFrame(() => { messagesEl.scrollTop = messagesEl.scrollHeight; });
}

// Mood color mapping
function updateMood(d) {
  const label = d.emotion_label || 'neutral';
  const valence = d.modulator_snapshot?.valence ?? 0.5;
  const arousal = d.modulator_snapshot?.arousal ?? 0.5;
  const energy = d.modulator_snapshot?.energy ?? 1.0;

  // Map to hue: low valence=blue(220), neutral=purple(260), high valence=gold(45)
  let hue;
  if (valence < 0.4) hue = 200 + (0.4 - valence) * 100; // 200-240 blue range
  else if (valence > 0.6) hue = 260 - (valence - 0.6) * 500; // 260 down to ~60 warm
  else hue = 260; // neutral purple
  hue = ((hue % 360) + 360) % 360;

  const sat = 40 + arousal * 30 + energy * 10;
  const light = 45 + energy * 20;
  const pulse = 4 - arousal * 2.5; // higher arousal = faster pulse
  const contrast = resolveMoodContrast(hue, sat, light);
  const pageReadsLight = document.documentElement.dataset.theme === 'light';
  contrast.useDarkText = pageReadsLight;

  document.documentElement.style.setProperty('--mood-hue', hue.toFixed(0));
  document.documentElement.style.setProperty('--mood-sat', sat.toFixed(0) + '%');
  document.documentElement.style.setProperty('--mood-light', contrast.light.toFixed(0) + '%');
  document.documentElement.style.setProperty('--text-primary', contrast.useDarkText ? '#0F0B1A' : '#FFFFFF');
  document.documentElement.style.setProperty('--text-muted', contrast.useDarkText ? 'rgba(15, 11, 26, 0.6)' : 'rgba(255, 255, 255, 0.7)');
  document.documentElement.style.setProperty('--surface-bg', contrast.useDarkText ? 'rgba(0, 0, 0, 0.03)' : 'rgba(255, 255, 255, 0.05)');
  if (!sending) document.documentElement.style.setProperty('--mood-pulse', pulse.toFixed(1) + 's');

  document.getElementById('moodLabel').textContent = label;
}

function refreshMoodContrast() {
  updateMood(lastDebugPayload || {
    emotion_label: lastEmotion || 'neutral',
    modulator_snapshot: { valence: 0.5, arousal: 0.5, energy: 1 },
  });
}

function hslToRgb(h, s, l) {
  s /= 100; l /= 100;
  const k = n => (n + h / 30) % 12;
  const a = s * Math.min(l, 1 - l);
  const f = n => l - a * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1));
  return [f(0), f(8), f(4)];
}

function relativeLuminance([r, g, b]) {
  const lin = c => c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

function contrastRatio(L1, L2) {
  const [hi, lo] = L1 > L2 ? [L1, L2] : [L2, L1];
  return (hi + 0.05) / (lo + 0.05);
}

function resolveMoodContrast(hue, sat, light) {
  const whiteLum = 1;
  const darkLum = relativeLuminance([15 / 255, 11 / 255, 26 / 255]);
  let nextLight = light;
  let best = null;
  for (let i = 0; i < 40; i++) {
    const bgLum = relativeLuminance(hslToRgb(hue, sat, nextLight));
    const whiteContrast = contrastRatio(bgLum, whiteLum);
    const darkContrast = contrastRatio(bgLum, darkLum);
    best = { light: nextLight, useDarkText: darkContrast >= whiteContrast, score: Math.max(whiteContrast, darkContrast) };
    if (whiteContrast >= 4.5 || darkContrast >= 4.5) return best;
    const target = darkContrast >= whiteContrast ? 65 : 35;
    if (Math.abs(nextLight - target) < 0.5) break;
    nextLight += nextLight < target ? 1 : -1;
  }
  return best || { light, useDarkText: false };
}

function updateDebug(d) {
  DebugState.payload = d;
  updateMood(d);
  lastExplanation = d.explanation || null;
  const whyBtn = document.getElementById('whyBtn');
  if (whyBtn) whyBtn.disabled = !lastExplanation;
  updateExplanation(lastExplanation);
  updatePersonaPanel(d.persona_view || null, d, lastDebugPayload);

  const mods = ['arousal', 'valence', 'certainty', 'bonding', 'energy', 'resolution'];
  const relationshipView = d.relationship_view || {};
  const modView = relationshipView.modulators || {};
  const clientDeltas = computeClientModulatorDeltas(d, lastDebugPayload);
  for (const m of mods) {
    const v = (d.modulator_snapshot && d.modulator_snapshot[m]) || 0;
    document.getElementById('val-' + m).textContent = v.toFixed(2);
    document.documentElement.style.setProperty('--gauge-' + m, (v * 100) + '%');
    const viewDelta = modView[m] ? modView[m].delta : null;
    updateDelta('delta-' + m, viewDelta ?? clientDeltas[m]);
  }

  document.getElementById('eventType').textContent = d.event_classified || '\u2014';
  document.getElementById('eventIntensity').textContent = d.event_intensity ? d.event_intensity.toFixed(2) : '\u2014';
  document.getElementById('eventSpike').textContent = d.is_spike ? 'YES' : 'no';

  if (d.person_profile) {
    document.getElementById('personTrust').textContent = d.person_profile.trust.toFixed(2);
    document.getElementById('personInteractions').textContent = d.person_profile.interaction_count;
  }
  if (d.self_profile) {
    document.getElementById('selfStrengths').textContent = d.self_profile.strengths.join(', ') || '\u2014';
    document.getElementById('selfFlaws').textContent = d.self_profile.flaws.join(', ') || '\u2014';
    document.getElementById('selfDissonance').textContent = d.self_profile.dissonance.toFixed(2);
  }

  const cEl = document.getElementById('contradictions');
  cEl.innerHTML = (d.contradiction_flags && d.contradiction_flags.length > 0)
    ? d.contradiction_flags.map(f => '<span class="debug-flag">' + esc(f) + '</span>').join('')
    : '<span class="debug-none">None</span>';

  const mEl = document.getElementById('memories');
  mEl.innerHTML = (d.retrieved_memories && d.retrieved_memories.length > 0)
    ? d.retrieved_memories.map(m => '<div class="debug-mem markdown-copy">' + renderMarkdown(m.summary) + ' <span class="dl">(v=' + m.valence.toFixed(2) + ')</span>' + (m.spike ? ' \u26a1' : '') + '</div>').join('')
    : '<span class="debug-none">None</span>';

  document.getElementById('selfCheckPassed').textContent = d.self_check_passed ? 'yes' : 'NO';
  document.getElementById('selfCheckIssues').textContent = (d.self_check_issues && d.self_check_issues.length) ? d.self_check_issues.join('; ') : '\u2014';

  updateAnticipation(d.anticipation);
  updateDialogueTrace(d.dialogue_trace);
  updateDefense(d.defense_activation);
  updateRelationshipState(d, relationshipView);
  updateMemoryInspector(d, relationshipView);
  document.getElementById('unresolvedCount').textContent = d.unresolved_count || 0;
  updateUnresolvedItems(d.unresolved_items);
  lastDebugPayload = d;
}

function updatePersonaPanel(view, currentDebug, previousDebug) {
  PersonaPanelState.view = view;
  const el = document.getElementById('personaPanel');
  if (!el) return;
  if (!view || view.active === false) {
    el.innerHTML = '<span class="debug-none">No persona state yet.</span>';
    return;
  }
  const emotions = view.emotions || {};
  const relationship = view.relationship || {};
  const perception = view.perception || {};
  const life = view.life || {};
  const memory = view.memory || {};
  const skillsTools = view.skills_tools || {};
  const drivers = (emotions.drivers || []).slice(0, 4);
  const mods = emotions.modulators || {};
  const pressures = Object.entries(life.active_pressures || {});
  const clientDeltas = computeClientModulatorDeltas(currentDebug || {}, previousDebug || null);

  let html = '<div class="persona-hero">' +
    '<div class="persona-emotion">' + esc(emotions.simple_label || emotions.primary || 'neutral') + '</div>' +
    '<div class="persona-sub">best-fit state: ' + esc(emotions.primary || 'neutral') +
    ' · intensity ' + formatNumber(emotions.intensity) +
    ' · confidence ' + formatNumber(emotions.confidence) + '</div>' +
    '<div class="persona-drivers">' +
    (drivers.length ? drivers.map(d => '<span class="debug-chip">' + esc(d) + '</span>').join('') : '<span class="debug-chip">balanced state</span>') +
    '</div></div>';

  html += personaRow('Perception', perception.summary || 'No perception recorded.');
  html += personaRow('Strategy', (relationship.strategy || 'none') + (relationship.strategy_reason ? ' · ' + relationship.strategy_reason : ''));
  html += personaRow('Open loops', String(relationship.open_loop_count || 0));
  html += personaRow('Memory used', 'relationship ' + yesNo(memory.relationship_context_used) + ' · long-term ' + (memory.long_term_count || 0) + ' · semantic ' + (memory.semantic_count || 0));
  html += personaRow('Skills/tools', (skillsTools.enabled_skill_count || 0) + ' skill(s) · ' + (skillsTools.tools_used || 0) + ' tool(s) used');

  html += '<div class="debug-item debug-stack-title"><span class="dl">Emotion drivers</span></div>';
  for (const key of ['arousal', 'valence', 'certainty', 'bonding', 'energy', 'resolution']) {
    const item = mods[key] || {};
    html += personaBar(key, item.value, item.level, item.delta ?? clientDeltas[key]);
  }
  if (pressures.length) {
    html += '<div class="debug-item debug-stack-title"><span class="dl">Life pressures</span></div>';
    for (const [key, value] of pressures.slice(0, 5)) {
      html += personaBar(key.replace(/_pressure$/, ''), Math.abs(Number(value) || 0) * 20, signed(Number(value) || 0), null, true);
    }
  }
  el.innerHTML = html;
}

function personaRow(label, value) {
  return '<div class="persona-row"><span>' + esc(label) + '</span><span class="markdown-copy">' + renderMarkdown(value || '\u2014') + '</span></div>';
}

function personaBar(label, value, detail, delta, pressure) {
  const v = Math.max(0, Math.min(1, Number(value) || 0));
  const deltaText = typeof delta === 'number' ? ' ' + signed(delta) : '';
  return '<div class="debug-card ' + (pressure ? 'persona-pressure' : '') + '">' +
    '<strong>' + esc(label) + '</strong> <span class="dl">' + esc(detail || '') + esc(deltaText) + '</span>' +
    '<div class="persona-bar"><progress value="' + (v * 100).toFixed(0) + '" max="100"></progress></div>' +
    '</div>';
}

function yesNo(value) {
  return value ? 'yes' : 'no';
}

function updateExplanation(explanation) {
  const el = document.getElementById('turnExplanation');
  if (!el) return;
  if (!explanation) {
    el.innerHTML = '<span class="debug-none">\u2014</span>';
    return;
  }
  el.innerHTML = '<div class="explain-box">' +
    explanationLine('Interpretation', explanation.interpretation) +
    explanationLine('Strategy', explanation.strategy) +
    explanationLine('State', explanation.state) +
    explanationLine('Memory', explanation.memory) +
    explanationLine('Life History', explanation.life_history) +
    explanationLine('Tools', explanation.tools) +
    explanationLine('Limits', explanation.limits) +
    '</div>';
}

function explanationLine(label, value) {
  return '<div><strong>' + esc(label) + '</strong><div class="markdown-copy">' + renderMarkdown(value || '') + '</div></div>';
}

function computeClientModulatorDeltas(current, previous) {
  const deltas = {};
  if (!previous || previous.timestamp === current.timestamp) return deltas;
  const cur = current.modulator_snapshot || {};
  const prev = previous.modulator_snapshot || {};
  for (const key of ['arousal', 'valence', 'certainty', 'bonding', 'energy', 'resolution']) {
    if (typeof cur[key] === 'number' && typeof prev[key] === 'number') {
      deltas[key] = Number((cur[key] - prev[key]).toFixed(6));
    }
  }
  return deltas;
}

function updateDelta(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove('pos', 'neg');
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    el.textContent = '\u2014';
    return;
  }
  if (Math.abs(value) < 0.0005) {
    el.textContent = '0.00';
    return;
  }
  el.textContent = (value > 0 ? '+' : '') + value.toFixed(2);
  el.classList.add(value > 0 ? 'pos' : 'neg');
}

function updateRelationshipState(debug, view) {
  const el = document.getElementById('relationshipState');
  if (!el) return;
  view = view || {};
  const mods = view.modulators || {};
  const loops = view.open_loops || [];
  const events = view.recent_relationship_events || [];
  const influence = nonNeutralInfluence(view.life_influence || {});
  const effects = view.life_influence_effects || {};
  const effectEntries = Object.entries(effects);

  let html = '<div class="debug-item"><span class="dl">Strategy</span> ' + esc(view.strategy || debug.response_strategy || '\u2014') + '</div>';
  html += '<div class="debug-item"><span class="dl">Reason</span> ' + esc(view.strategy_reason || debug.strategy_trace?.matched_rule || '\u2014') + '</div>';
  html += '<div class="debug-grid">' +
    relationshipMetric('Trust', view.trust?.value, view.trust?.delta) +
    relationshipMetric('Bonding', mods.bonding?.value, mods.bonding?.delta) +
    relationshipMetric('Resolution', mods.resolution?.value, mods.resolution?.delta) +
    '<div class="debug-chip">Open loops ' + esc(String(loops.length || debug.relationship_context?.open_loop_count || 0)) + '</div>' +
    '</div>';

  html += '<div class="debug-item debug-stack-title"><span class="dl">Active loops</span></div>';
  html += loops.length ? '<div class="debug-list">' + loops.map(renderLoopCard).join('') + '</div>' : '<span class="debug-none">None</span>';
  html += '<div class="debug-item debug-stack-title"><span class="dl">Recent events</span></div>';
  html += events.length ? '<div class="debug-list">' + events.map(renderRelationshipEvent).join('') + '</div>' : '<span class="debug-none">None</span>';

  if (influence.length) {
    html += '<div class="debug-item debug-stack-title"><span class="dl">LifeInfluence</span></div>';
    html += '<div class="debug-list">' + influence.map(([k, v]) => '<span class="debug-chip">' + esc(k.replace(/_pressure$/, '')) + ' ' + signed(v) + '</span>').join('') + '</div>';
  }
  if (effectEntries.length) {
    html += '<div class="debug-item debug-stack-title"><span class="dl">Influence effects</span></div>';
    html += '<div class="debug-list">' + effectEntries.map(([k, v]) => '<span class="debug-chip">' + esc(k) + ' ' + esc(String(v)) + '</span>').join('') + '</div>';
  }
  el.innerHTML = html;
}

function relationshipMetric(label, value, delta) {
  const valueText = typeof value === 'number' ? value.toFixed(2) : '\u2014';
  const deltaText = typeof delta === 'number' ? ' ' + signed(delta) : '';
  return '<div class="debug-chip">' + esc(label) + ' ' + valueText + esc(deltaText) + '</div>';
}

function renderLoopCard(loop) {
  const status = loop.status || 'open';
  const cls = status === 'resolved' ? 'resolved' : 'open';
  return '<div class="debug-card ' + cls + '">' +
    '<strong>' + esc(loop.topic || loop.loop_kind || 'loop') + '</strong><br>' +
    esc(loop.description || '') +
    '<div class="dl">intensity ' + formatNumber(loop.intensity) + ' · ' + esc(status) + '</div>' +
    '</div>';
}

function renderRelationshipEvent(event) {
  return '<div class="debug-card">' +
    '<strong>' + esc((event.event_kind || 'event').replace(/_/g, ' ')) + '</strong> ' +
    esc(event.topic || '') +
    '<div>' + esc(event.summary || '') + '</div>' +
    '<div class="dl">intensity ' + formatNumber(event.intensity) + formatTime(event.created_at) + '</div>' +
    '</div>';
}

function updateMemoryInspector(debug, view) {
  const el = document.getElementById('memoryInspector');
  if (!el) return;
  view = view || {};
  const rel = debug.relationship_context || {};
  const loops = view.open_loops || rel.active_loops || [];
  const events = view.recent_relationship_events || rel.recent_events || [];
  const longTerm = (debug.retrieved_memories || []).slice(0, 3);
  const semantic = (debug.semantic_memories || []).slice(0, 3);
  const life = debug.life_history_context || {};
  const beliefs = (life.beliefs || []).slice(0, 3);
  const drives = (life.drives || []).slice(0, 3);

  let html = '';
  html += memoryBlock('Relationship', compactRelationshipMemory(loops, events));
  html += memoryBlock('Long-term', longTerm.map(m => esc(m.summary || '')).filter(Boolean));
  html += memoryBlock('Semantic', semantic.map(m => esc(m.summary || m.topic || '')).filter(Boolean));
  html += memoryBlock('Life History', compactLifeMemory(beliefs, drives));
  el.innerHTML = html || '<span class="debug-none">None</span>';
}

function memoryBlock(label, items) {
  if (!items || !items.length) return '<div class="debug-item"><span class="dl">' + esc(label) + '</span> <span class="debug-none">None</span></div>';
  return '<div class="debug-item"><span class="dl">' + esc(label) + '</span></div><div class="debug-list">' +
    items.slice(0, 3).map(item => '<div class="debug-card">' + item + '</div>').join('') +
    '</div>';
}

function compactRelationshipMemory(loops, events) {
  const parts = [];
  for (const loop of loops.slice(0, 2)) {
    parts.push(esc('Open: ' + (loop.topic || loop.description || 'relationship loop')));
  }
  for (const event of events.slice(0, 2)) {
    parts.push(esc((event.event_kind || 'event').replace(/_/g, ' ') + ': ' + (event.topic || event.summary || 'relationship event')));
  }
  return parts;
}

function compactLifeMemory(beliefs, drives) {
  const parts = [];
  for (const belief of beliefs) {
    parts.push(esc('Belief: ' + (belief.subject || belief.summary || belief.content || 'life belief')));
  }
  for (const drive of drives) {
    const name = drive.name || 'drive';
    const delta = typeof drive.delta === 'number' ? ' ' + signed(drive.delta) : '';
    parts.push(esc('Drive: ' + name + delta));
  }
  return parts;
}

function nonNeutralInfluence(influence) {
  return Object.entries(influence).filter(([, v]) => typeof v === 'number' && Math.abs(v) >= 0.0005);
}

function signed(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '';
  if (Math.abs(value) < 0.0005) return '0.00';
  return (value > 0 ? '+' : '') + value.toFixed(2);
}

function formatNumber(value) {
  return typeof value === 'number' ? value.toFixed(2) : '\u2014';
}

function formatTime(timestamp) {
  if (!timestamp) return '';
  const date = new Date(timestamp * 1000);
  if (Number.isNaN(date.getTime())) return '';
  return ' · ' + date.toLocaleString();
}

function showWhyResponse() {
  if (!lastExplanation) return;
  const text = [
    '**Why this response?**',
    lastExplanation.interpretation,
    lastExplanation.strategy,
    lastExplanation.state,
    lastExplanation.memory,
    lastExplanation.life_history,
    lastExplanation.tools,
    lastExplanation.limits,
  ].filter(Boolean).join('\n\n');
  addMessage(text, 'assistant', lastEmotion);
}

function updateAnticipation(ant) {
  const el = document.getElementById('anticipation');
  if (!ant || ant.confidence === 0) { el.innerHTML = '<span class="debug-none">No prediction</span>'; return; }
  const topics = ant.predicted_topics.length > 0
    ? ant.predicted_topics.map(t => '<span class="topic-tag">' + esc(t) + '</span>').join('')
    : '<span class="dl">none</span>';
  const shifts = Object.entries(ant.modulator_pre_shifts || {}).map(([k,v]) => k + ': ' + (v>0?'+':'') + v.toFixed(2)).join(', ') || 'none';
  el.innerHTML = '<div class="anticipation-box">' +
    '<div><span class="dl">Topics</span> ' + topics + '</div>' +
    '<div><span class="dl">Tone</span> ' + esc(ant.predicted_emotional_tone) + '</div>' +
    '<div><span class="dl">Confidence</span> ' + ant.confidence.toFixed(2) + '</div>' +
    '<div><span class="dl">Pre-shifts</span> ' + shifts + '</div>' +
    '<div class="debug-note debug-note-tight">' + esc(ant.basis) + '</div></div>';
}

function updateDialogueTrace(trace) {
  const el = document.getElementById('dialogueTrace');
  if (!trace) { el.innerHTML = '<span class="debug-none">\u2014</span>'; return; }
  let h = '';
  for (const r of trace.rounds) {
    const st = r.slow_path_approved ? '<span class="approved">APPROVED</span>' : '<span class="objection">OBJECTION</span>';
    h += '<div class="dialogue-round"><div class="rh">Round ' + r.round_number + ' ' + st + '</div>' +
      '<div><span class="fast">Fast:</span></div><div class="ct">' + esc(trunc(r.fast_path_candidate, 200)) + '</div>' +
      '<div><span class="slow">Slow:</span></div><div class="ct">' + esc(trunc(r.slow_path_evaluation, 200)) + '</div>';
    if (r.objection_reason) h += '<div class="debug-note debug-note-danger">Objection: ' + esc(r.objection_reason) + '</div>';
    h += '</div>';
  }
  const dl = trace.reached_deadlock ? '<span class="debug-flag">DEADLOCK' + (trace.deadlock_resolution ? ' \u2192 ' + trace.deadlock_resolution : '') + '</span> ' : '';
  h += '<div class="tension-meter"><span>Tension</span><progress class="tension-progress" value="' + (trace.tension_level * 100).toFixed(0) + '" max="100"></progress><span>' + trace.tension_level.toFixed(2) + '</span></div>';
  h += '<div class="debug-note debug-note-tight">' + dl + 'Path: ' + trace.dominant_path + ' &middot; LLM: ' + trace.total_llm_calls + '</div>';
  el.innerHTML = h;
}

function updateDefense(defense) {
  const el = document.getElementById('defense');
  if (!defense) { el.innerHTML = '<span class="debug-none">None</span>'; return; }
  const rp = (defense.raw_intensity * 100).toFixed(0), ep = (defense.expressed_intensity * 100).toFixed(0);
  el.innerHTML = '<div class="defense-box">' +
    '<div class="dt">' + defense.defense_type + '</div>' +
    '<div class="ib"><span class="debug-bar-label">Raw</span><div class="bt"><progress class="defense-progress raw" value="' + rp + '" max="100"></progress></div><span class="debug-bar-value">' + defense.raw_intensity.toFixed(2) + '</span></div>' +
    '<div class="ib"><span class="debug-bar-label">Expr</span><div class="bt"><progress class="defense-progress expressed" value="' + ep + '" max="100"></progress></div><span class="debug-bar-value">' + defense.expressed_intensity.toFixed(2) + '</span></div>' +
    '<div class="debug-note debug-note-warning">Suppressed: ' + defense.suppression_delta.toFixed(2) + '</div>' +
    '<div class="debug-note debug-note-tight">' + esc(defense.reason) + '</div></div>';
}

function updateUnresolvedItems(items) {
  const el = document.getElementById('unresolvedItems');
  if (!items || !items.length) { el.innerHTML = ''; return; }
  el.innerHTML = items.map(i => {
    const dr = i.decay_rate === 0 ? 'no decay' : i.decay_rate.toFixed(2) + '/hr';
    return '<div class="unresolved-item"><span class="source-tag">' + esc(i.source) + '</span> ' +
      '<span class="debug-muted-inline">' + esc(trunc(i.description, 100)) + '</span>' +
      '<div class="debug-note">intensity: ' + i.intensity.toFixed(2) + ' &middot; ' + dr + '</div></div>';
  }).join('');
}

function toggleDebug() {
  DebugState.open = !DebugState.open;
}

// Session actions
async function endSession() {
  try {
    const res = await authedFetch('/session/end', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({user_id: userId, chat_id: chatId}) });
    const data = await res.json();
    addMessage('[Session ended: ' + (data.summary || 'digested') + ']', 'assistant');
  } catch (err) { addMessage('Error: ' + err.message, 'assistant'); }
}

async function applyRest() {
  try {
    const res = await authedFetch('/rest', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({hours: 1.0, user_id: userId, chat_id: chatId}) });
    const data = await res.json();
    addMessage('[Rested 1hr: energy ' + data.energy_before.toFixed(2) + ' \u2192 ' + data.energy_after.toFixed(2) + ']', 'assistant');
    const dr = await authedFetch('/debug?' + new URLSearchParams({user_id: userId, chat_id: chatId}));
    const dd = await dr.json();
    if (dd.modulator_snapshot) updateDebug(dd);
  } catch (err) { addMessage('Error: ' + err.message, 'assistant'); }
}

let codexModels = [];
let codexModelsLoaded = false;
let codexModelsLoading = false;
let codexModelsError = '';

async function ensureCodexModels() {
  if (codexModelsLoaded || codexModelsLoading) return;
  codexModelsLoading = true;
  codexModelsError = '';
  try {
    const res = await authedFetch('/admin/codex/models');
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Could not load Codex models.');
    codexModels = Array.isArray(data.models) ? data.models : [];
  } catch (err) {
    codexModels = [];
    codexModelsError = err.message || String(err);
  } finally {
    codexModelsLoaded = true;
    codexModelsLoading = false;
  }
}

function renderCodexModelControl(id, backend, value, placeholder) {
  const current = document.getElementById(id);
  if (!current) return;
  const parent = current.parentElement;
  const selected = String(value || current.value || '');
  const shouldSelect = backend === 'codex';
  let next = current;
  if (shouldSelect && current.tagName !== 'SELECT') {
    next = document.createElement('select');
    next.id = id;
    current.replaceWith(next);
  } else if (!shouldSelect && current.tagName !== 'INPUT') {
    next = document.createElement('input');
    next.id = id;
    next.type = 'text';
    current.replaceWith(next);
  }
  if (!shouldSelect) {
    next.value = selected;
    next.placeholder = placeholder || '';
    removeCodexModelHint(parent);
    return;
  }
  next.innerHTML = buildCodexModelOptions(selected);
  addCodexModelHint(parent);
}

function buildCodexModelOptions(selected) {
  const known = new Set(codexModels.map((model) => model.slug));
  const options = [`<option value="" ${selected ? '' : 'selected'}>Codex default</option>`];
  if (selected && !known.has(selected)) {
    options.push(`<option value="${esc(selected)}" selected>${esc(selected)} (custom)</option>`);
  }
  if (codexModelsLoading && !codexModels.length) {
    options.push('<option value="" disabled>Loading Codex models...</option>');
  }
  for (const model of codexModels) {
    const slug = String(model.slug || '');
    if (!slug) continue;
    const name = String(model.display_name || slug);
    const label = name && name !== slug ? `${name} (${slug})` : slug;
    options.push(`<option value="${esc(slug)}" ${selected === slug ? 'selected' : ''}>${esc(label)}</option>`);
  }
  return options.join('');
}

function addCodexModelHint(parent) {
  if (!parent) return;
  let hint = parent.querySelector('.codex-model-hint');
  if (!hint) {
    hint = document.createElement('div');
    hint.className = 'settings-note codex-model-hint';
    parent.appendChild(hint);
  }
  hint.textContent = codexModelsError || 'Models loaded from the installed Codex CLI.';
}

function removeCodexModelHint(parent) {
  const hint = parent && parent.querySelector('.codex-model-hint');
  if (hint) hint.remove();
}

async function handleLegacyBackendChange() {
  const backend = gv('cfg-llm_backend');
  renderCodexModelControl('cfg-llm_model', backend, gv('cfg-llm_model'), '');
  if (backend === 'codex') {
    await ensureCodexModels();
    renderCodexModelControl('cfg-llm_model', backend, gv('cfg-llm_model'), '');
  }
}

// Settings
function openSettings() { AdminOverlayState.open = true; loadSettings().catch(e => setStatus('Failed: ' + e.message, true)); }
function closeSettings() { AdminOverlayState.open = false; }
function closeSettingsIfBackdrop(e) { if (e.target === settingsOverlay) closeSettings(); }

async function loadSettings(options = {}) {
  setStatus('Loading...');
  const res = await authedFetch('/admin/config'); if (!res.ok) throw new Error('HTTP ' + res.status);
  applySettings(await res.json());
  if (options.loadSoul !== false) loadSoul().catch(() => {});
}

async function saveSettings() {
  const p = {
    data_dir: gv('cfg-data_dir'), max_queue_per_user: gi('cfg-max_queue_per_user'), max_active_sessions: gi('cfg-max_active_sessions'),
    session_timeout_seconds: gf('cfg-session_timeout_seconds'), console_enabled: gb('cfg-console_enabled'),
    telegram_allowlist: gv('cfg-telegram_allowlist').split('\n').map(s=>s.trim()).filter(Boolean),
    telegram_poll_timeout: gi('cfg-telegram_poll_timeout'), dedupe_ttl: gf('cfg-dedupe_ttl'),
    llm_backend: gv('cfg-llm_backend'), llm_base_url: gv('cfg-llm_base_url'), llm_model: gv('cfg-llm_model'),
    debug_host: gv('cfg-debug_host'), debug_port: gi('cfg-debug_port'),
    cors_origins: lines('cfg-cors_origins'),
    tools_enabled: gb('cfg-tools_enabled'), autonomy_level: gv('cfg-autonomy_level'), tools_workspace: gv('cfg-tools_workspace'), shell_tool_enabled: gb('cfg-shell_tool_enabled'),
    proactive_enabled: gb('cfg-proactive_enabled'), proactive_idle_threshold: gf('cfg-proactive_idle_threshold'),
    proactive_density_reference: gi('cfg-proactive_density_reference'), proactive_recovery_seconds: gf('cfg-proactive_recovery_seconds'),
    proactive_check_interval: gf('cfg-proactive_check_interval'),
    character_independence: gb('cfg-character_independence'), coherence_min_score: gf('cfg-coherence_min_score'),
    coherence_max_regenerations: gi('cfg-coherence_max_regenerations'), pending_intake_ttl_turns: gi('cfg-pending_intake_ttl_turns'),
    telegram_token: gv('cfg-telegram_token'), llm_api_key: gv('cfg-llm_api_key'),
    api_key: gv('cfg-api_key'),
    setup_completed: document.getElementById('cfg-setup_completed').checked,
    clear_telegram_token: document.getElementById('cfg-clear_telegram_token').checked,
    clear_llm_api_key: document.getElementById('cfg-clear_llm_api_key').checked,
    clear_api_key: document.getElementById('cfg-clear_api_key').checked,
  };
  try {
    setStatus('Saving...');
    const res = await authedFetch('/admin/config', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(p) });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'HTTP ' + res.status);
    // Sync the local copy of the bearer token to whatever the server now
    // expects, so subsequent fetches do not 401 after a key rotation.
    if (p.clear_api_key) setApiToken('');
    else if (p.api_key && p.api_key.trim()) setApiToken(p.api_key.trim());
    applySettings(data);
    setStatus((data.message || 'Saved.') + (data.reloaded_web_manager ? ' Session manager reloaded.' : ''), false, true);
  } catch (err) { setStatus('Error: ' + err.message, true); }
}

function applySettings(payload) {
  const c = payload.config || {};
  sv('cfg-data_dir', c.data_dir||'data'); sv('cfg-max_queue_per_user', c.max_queue_per_user??3);
  sv('cfg-max_active_sessions', c.max_active_sessions??10); sv('cfg-session_timeout_seconds', c.session_timeout_seconds??1800);
  sv('cfg-console_enabled', String(c.console_enabled)); sv('cfg-telegram_poll_timeout', c.telegram_poll_timeout??30);
  sv('cfg-dedupe_ttl', c.dedupe_ttl??60); sv('cfg-llm_backend', c.llm_backend||'auto');
  sv('cfg-llm_base_url', c.llm_base_url||'');
  renderCodexModelControl('cfg-llm_model', c.llm_backend || 'auto', c.llm_model || '', '');
  sv('cfg-llm_model', c.llm_model||'');
  if ((c.llm_backend || '') === 'codex') {
    ensureCodexModels().then(() => {
      renderCodexModelControl('cfg-llm_model', 'codex', gv('cfg-llm_model'), '');
    }).catch(() => {});
  }
  sv('cfg-debug_host', c.debug_host||'127.0.0.1'); sv('cfg-debug_port', c.debug_port??8077);
  sv('cfg-cors_origins', (c.cors_origins||[]).join('\n'));
  sv('cfg-tools_enabled', String(!!c.tools_enabled)); sv('cfg-autonomy_level', c.autonomy_level||'assisted'); sv('cfg-tools_workspace', c.tools_workspace||''); sv('cfg-shell_tool_enabled', String(!!c.shell_tool_enabled));
  sv('cfg-proactive_enabled', String(c.proactive_enabled)); sv('cfg-proactive_idle_threshold', c.proactive_idle_threshold??300);
  sv('cfg-proactive_density_reference', c.proactive_density_reference??3); sv('cfg-proactive_recovery_seconds', c.proactive_recovery_seconds??300);
  sv('cfg-proactive_check_interval', c.proactive_check_interval??60);
  sv('cfg-character_independence', String(!!c.character_independence));
  sv('cfg-coherence_min_score', c.coherence_min_score??0.6);
  sv('cfg-coherence_max_regenerations', c.coherence_max_regenerations??2);
  sv('cfg-pending_intake_ttl_turns', c.pending_intake_ttl_turns??3);
  sv('cfg-telegram_allowlist', (c.telegram_allowlist||[]).join('\n'));
  sv('cfg-telegram_token',''); sv('cfg-llm_api_key',''); sv('cfg-api_key','');
  ['cfg-clear_telegram_token','cfg-clear_llm_api_key','cfg-clear_api_key'].forEach(id => document.getElementById(id).checked = false);
  const ss = payload.secret_status || {};
  const meta = {};
  for (const f of payload.field_metadata || []) meta[f.name] = f;
  ['telegram_token','llm_api_key','api_key'].forEach(k => {
    const el = document.getElementById('cfg-status-' + k);
    if (!el) return;
    const detail = meta[k]?.secret_status;
    if (detail) el.textContent = detail.configured ? 'Configured via ' + detail.source + '. Leave blank to keep.' : 'Not configured.';
    else el.textContent = ss[k] ? 'Configured. Leave blank to keep.' : 'Not configured.';
  });
  renderAdminOverview(payload);
  renderAdminWarnings(payload.warnings || []);
  renderSetup(payload.setup || {});
  updateSoulDraftAvailability();
  // Auto-open the first-run wizard when setup is incomplete, the user hasn't
  // dismissed it this session, and the admin drawer isn't already open (the
  // /admin route or a manual "Settings" click means they know where to go).
  const setup = payload.setup || {};
  if (
    setup.required
    && !sessionStorage.getItem('nur_wizard_dismissed')
    && !wizardOverlay.classList.contains('open')
    && !settingsOverlay.classList.contains('open')
    && location.pathname !== '/admin'
  ) {
    openWizard();
  }
  const notes = (payload.notes||[]).map(n => '<div class="settings-note">' + esc(n) + '</div>').join('');
  setStatusHtml('Config: ' + esc(payload.config_path || 'runtime_config.yaml') + notes);
}

async function testLLM() {
  await runAdminTest('llm', '/admin/test/llm', {
    llm_backend: gv('cfg-llm_backend'), llm_base_url: gv('cfg-llm_base_url'), llm_model: gv('cfg-llm_model'),
    llm_api_key: gv('cfg-llm_api_key'),
    clear_llm_api_key: document.getElementById('cfg-clear_llm_api_key').checked,
    live: true,
  });
}

async function testTelegram() {
  await runAdminTest('telegram', '/admin/test/telegram', {
    telegram_token: gv('cfg-telegram_token'),
    clear_telegram_token: document.getElementById('cfg-clear_telegram_token').checked,
    telegram_allowlist: lines('cfg-telegram_allowlist'),
  });
}

async function testStorage() {
  await runAdminTest('storage', '/admin/test/storage', {
    data_dir: gv('cfg-data_dir'),
    tools_workspace: gv('cfg-tools_workspace'),
    create_missing: true,
  });
}

async function loadDiagnostics() {
  await runAdminTest('maintenance', '/admin/diagnostics', null);
}

async function exportConfig() {
  const el = document.getElementById('test-maintenance');
  el.hidden = false;
  el.className = 'admin-test-result';
  el.textContent = 'Exporting config...';
  try {
    const res = await authedFetch('/admin/export/config');
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'HTTP ' + res.status);
    downloadJson('nur-runtime-config-redacted.json', data);
    el.className = 'admin-test-result ok';
    el.textContent = 'Redacted config export prepared. Secrets were not included.';
  } catch (err) {
    el.className = 'admin-test-result error';
    el.textContent = 'Error: ' + err.message;
  }
}

async function createBackup() {
  await runAdminTest('maintenance', '/admin/backup', { include_data: true });
}

async function resetAdminSession() {
  const key = gv('admin-reset-session_key');
  const confirmation = gv('admin-reset-confirmation');
  const expected = key ? 'RESET ' + key : '';
  if (!key || confirmation !== expected) {
    const el = document.getElementById('test-danger');
    el.hidden = false;
    el.className = 'admin-test-result error';
    el.textContent = 'Confirmation must be exactly: ' + (expected || 'RESET <session_key>');
    return;
  }
  await runAdminTest('danger', '/admin/sessions/reset', {
    session_key: key,
    confirmation,
  });
}

async function deleteAdminUser() {
  const platform = gv('admin-delete-platform');
  const userId = gv('admin-delete-user_id');
  const confirmation = gv('admin-delete-confirmation');
  const expected = (platform && userId) ? 'DELETE ' + platform + ':' + userId : '';
  if (!platform || !userId || confirmation !== expected) {
    const el = document.getElementById('test-danger');
    el.hidden = false;
    el.className = 'admin-test-result error';
    el.textContent = 'Confirmation must be exactly: ' + (expected || 'DELETE <platform>:<user_id>');
    return;
  }
  await runAdminTest('danger', '/admin/users/delete', {
    platform,
    user_id: userId,
    confirmation,
  });
}

function parseLines(text) {
  return (text || '').split(/\r?\n/).map(s => s.trim()).filter(Boolean);
}

function readSoulWeights(key) {
  const rows = document.querySelectorAll('#soul-' + key + '-rows .soul-weight-row');
  const out = {};
  rows.forEach(row => {
    const name = row.querySelector('input[type="text"]').value.trim();
    if (!name) return;
    const weight = Number(row.querySelector('input[type="range"]').value) / 100;
    out[name] = Math.round(weight * 100) / 100;
  });
  return out;
}

function clearSoulWeights(key) {
  document.getElementById('soul-' + key + '-rows').innerHTML = '';
}

function addSoulWeightRow(key, name, value) {
  const container = document.getElementById('soul-' + key + '-rows');
  const row = document.createElement('div');
  row.className = 'soul-weight-row';
  const safeName = (name || '').replace(/"/g, '&quot;');
  const pct = Math.round(((value ?? 0.5) * 100));
  row.innerHTML = '' +
    '<input type="text" placeholder="name" value="' + safeName + '" maxlength="64">' +
    '<input type="range" min="0" max="100" step="5" value="' + pct + '">' +
    '<div class="soul-weight-val">' + (pct / 100).toFixed(2) + '</div>' +
    '<button type="button" title="remove">×</button>';
  const [, range, valEl, removeBtn] = row.querySelectorAll('input, div.soul-weight-val, button');
  range.addEventListener('input', () => {
    valEl.textContent = (Number(range.value) / 100).toFixed(2);
  });
  removeBtn.addEventListener('click', () => row.remove());
  container.appendChild(row);
}

function populateSoulWeights(key, obj) {
  clearSoulWeights(key);
  const entries = Object.entries(obj || {});
  if (entries.length === 0) return;
  entries.forEach(([k, v]) => addSoulWeightRow(key, k, Number(v)));
}

async function loadSoul() {
  const el = document.getElementById('test-soul');
  if (el) { el.hidden = false; el.className = 'admin-test-result'; el.textContent = 'Loading identity...'; }
  try {
    const res = await authedFetch('/admin/soul');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    applySoulToForm(data.soul || {});
    if ((data.soul || {}).name) applyAgentName(data.soul.name);
    if (el) { el.hidden = true; el.textContent = ''; }
    updateSoulDraftAvailability();
  } catch (err) {
    if (el) {
      el.hidden = false;
      el.className = 'admin-test-result error';
      el.textContent = 'Could not load soul: ' + err.message;
    }
  }
}

function applySoulToForm(s) {
  const el = document.getElementById('soul-text');
  if (el) el.value = soulToText(s);
}

function soulToText(s) {
  const lines = [];
  lines.push('Name: ' + (s.name || ''));
  if (s.identity) lines.push('', s.identity);
  if (s.voice) lines.push('', 'Core tone: ' + s.voice);
  if (s.relational_stance) lines.push('', 'Relational stance: ' + s.relational_stance);
  if (s.growth_policy) lines.push('', 'Growth policy: ' + s.growth_policy);
  if (s.likes && s.likes.length) lines.push('', 'Likes: ' + s.likes.join(', '));
  if (s.dislikes && s.dislikes.length) lines.push('', 'Dislikes: ' + s.dislikes.join(', '));
  if (s.boundaries && s.boundaries.length) lines.push('', 'Boundaries: ' + s.boundaries.join(' '));
  return lines.join('\n').trim();
}

async function importSoulText(raw) {
  const res = await authedFetch('/admin/soul/import', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ raw }),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(typeof (data && data.detail) === 'string' ? data.detail : JSON.stringify(data && data.detail) || 'HTTP ' + res.status);
  }
  return data.draft || {};
}

async function saveSoul() {
  const el = document.getElementById('test-soul');
  el.hidden = false;
  el.className = 'admin-test-result';
  el.textContent = 'Parsing identity...';
  const raw = (document.getElementById('soul-text')?.value || '').trim();
  if (raw.length < 8) {
    el.className = 'admin-test-result error';
    el.textContent = 'Paste or write the identity text first.';
    return;
  }
  try {
    const payload = await importSoulText(raw);
    el.textContent = 'Saving identity...';
    const res = await authedFetch('/admin/soul', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error((data && data.detail) || 'HTTP ' + res.status);
    el.className = 'admin-test-result ok';
    const lines = ['Identity saved to ' + data.path + (data.is_default_name ? ' (still the default name — pick something for your deployment)' : '')];
    (data.notes || []).forEach(n => lines.push(n));
    el.textContent = lines.join('\n');
    applyAgentName(payload.name);
    loadSettings({ loadSoul: false }).catch(() => {});
  } catch (err) {
    el.className = 'admin-test-result error';
    el.textContent = 'Save failed: ' + err.message;
  }
}

function updateSoulDraftAvailability() {
  const btn = document.querySelector('.soul-draft-actions button');
  const hint = document.getElementById('soul-draft-hint');
  if (!btn || !hint) return;
  const backend = gv('cfg-llm_backend');
  const hasConfig = !!(gv('cfg-llm_base_url') && gv('cfg-llm_model')) ||
    backend === 'codex';
  btn.disabled = !hasConfig;
  if (hasConfig) {
    hint.textContent = 'Uses your current LLM backend (' + (backend || 'auto') + '). Review the draft before saving.';
  } else {
    hint.textContent = 'Configure the LLM section above (backend, base URL, model) to enable this.';
  }
}

async function runAdminTest(name, path, payload) {
  const el = document.getElementById('test-' + name);
  el.hidden = false;
  el.className = 'admin-test-result';
  el.textContent = 'Testing...';
  try {
    const opts = payload === null
      ? {}
      : { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) };
    const res = await authedFetch(path, opts);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'HTTP ' + res.status);
    el.className = 'admin-test-result ' + (data.ok ? 'ok' : 'error');
    el.textContent = formatTestResult(data);
  } catch (err) {
    el.className = 'admin-test-result error';
    el.textContent = 'Error: ' + err.message;
  }
}

function formatTestResult(data) {
  const warnings = (data.warnings || []).map(w => w.severity.toUpperCase() + ': ' + w.message).join('\n');
  if (data.checked === 'storage') {
    const checks = (data.checks || []).map(c => c.name + ': ' + (c.ok ? 'ok' : 'failed') + ' · ' + c.path).join('\n');
    return (data.ok ? 'Storage ok' : 'Storage has issues') + (checks ? '\n' + checks : '') + (warnings ? '\n' + warnings : '');
  }
  if (data.checked === 'llm') {
    return (data.ok ? 'LLM config ok' : 'LLM config has issues') + (data.live ? ' · live sample checked' : ' · no live remote call') + (data.sample_response ? '\n' + data.sample_response : '') + (warnings ? '\n' + warnings : '');
  }
  if (data.checked === 'telegram') {
    return (data.ok ? 'Telegram config ok' : 'Telegram config has issues') + '\nAllowlist entries: ' + (data.allowlist_count || 0) + (warnings ? '\n' + warnings : '');
  }
  if (data.server && data.storage) {
    const storage = data.storage.data_dir;
    return 'Diagnostics ok'
      + '\nUptime: ' + Math.round(data.server.uptime_seconds || 0) + 's'
      + '\nPython: ' + data.server.python
      + '\nActive sessions: ' + data.runtime.active_sessions + '/' + data.runtime.max_active_sessions
      + '\nData dir: ' + storage.path + ' · ' + (storage.exists ? 'exists' : 'missing') + ' · files: ' + storage.file_count
      + (warnings ? '\n' + warnings : '');
  }
  if (data.path && data.size_bytes !== undefined) {
    return 'Backup created'
      + '\nPath: ' + data.path
      + '\nSize: ' + data.size_bytes + ' bytes'
      + '\nFiles: ' + data.file_count
      + '\nRedacted config: ' + (data.redacted_config ? 'yes' : 'no');
  }
  if (data.action === 'session_reset') {
    return 'Session reset'
      + '\nSession: ' + data.session_key
      + '\nStatus: ' + data.status;
  }
  if (data.action === 'user_delete') {
    return 'User data deleted'
      + '\nUser: ' + data.rel_key
      + '\nSessions evicted: ' + (data.sessions_evicted || []).length
      + '\nSession files removed: ' + data.session_files_removed
      + '\nShared self-model preserved: ' + (data.shared_self_model_db_preserved ? 'yes' : 'no');
  }
  return JSON.stringify(data, null, 2);
}

function renderAdminOverview(payload) {
  const c = payload.config || {};
  const warnings = payload.warnings || [];
  document.getElementById('adminBackend').textContent = c.llm_backend || 'auto';
  document.getElementById('adminAuth').textContent = (payload.secret_status || {}).api_key ? 'protected' : 'open';
  document.getElementById('adminTelegram').textContent = (payload.secret_status || {}).telegram_token ? 'configured' : 'off';
  document.getElementById('adminTools').textContent = c.tools_enabled ? ((c.shell_tool_enabled ? 'shell enabled' : 'enabled') + ' / ' + (c.autonomy_level || 'assisted')) : 'skill registry only';
  document.getElementById('adminOverview').title = warnings.length ? warnings.length + ' warning(s)' : 'No warnings';
}

function renderAdminWarnings(warnings) {
  const el = document.getElementById('adminWarnings');
  if (!warnings.length) {
    el.innerHTML = '<div class="admin-warning is-hidden"></div>';
    return;
  }
  el.innerHTML = warnings.map(w => '<div class="admin-warning ' + (w.severity === 'error' ? 'error' : '') + '">' + esc(w.message) + '<div class="code">' + esc(w.code) + '</div></div>').join('');
}

const SETUP_REASON_LABELS = {
  missing_config: 'Save your runtime config',
  default_or_minimal_config: 'Configure an LLM backend',
  default_soul: 'Set your agent’s identity',
  setup_not_completed: 'Mark setup complete when done',
};

function renderSetup(setup) {
  document.getElementById('cfg-setup_completed').checked = !!setup.completed;
  const reasons = (setup.reasons || []).map(code => SETUP_REASON_LABELS[code] || code);
  const el = document.getElementById('setupSummary');
  if (setup.completed) {
    el.textContent = 'Setup complete.';
    return;
  }
  if (reasons.length === 0) {
    el.textContent = 'Setup pending.';
    return;
  }
  el.innerHTML = 'Setup pending:<ul class="setup-reasons">' +
    reasons.map(r => '<li>' + esc(r) + '</li>').join('') +
    '</ul>';
}

// ---- Setup wizard --------------------------------------------------------
const wizardOverlay = document.getElementById('wizardOverlay');
const WIZARD_PRESET_CONFIG = {
  local:       { backend: 'openai_compatible', base_url: 'http://localhost:11434/v1',    model: 'llama3.2',           model_placeholder: 'e.g. llama3.2',              label: 'Ollama / LM Studio',         key_label: 'API Key (usually blank for local)', key_placeholder: 'Often blank' },
  vllm:        { backend: 'openai_compatible', base_url: 'http://localhost:8002/v1',     model: '',                   model_placeholder: 'Name of the model loaded in vLLM', label: 'vLLM',                  key_label: 'API Key (usually blank for vLLM)',   key_placeholder: 'Often blank' },
  hosted:      { backend: 'openai_compatible', base_url: 'https://api.openai.com/v1',    model: 'gpt-4o-mini',        model_placeholder: 'e.g. gpt-4o-mini',           label: 'Hosted OpenAI-compatible gateway', key_label: 'API Key (blank if gateway needs none)', key_placeholder: 'Optional' },
  openrouter:  { backend: 'provider',          base_url: 'https://openrouter.ai/api/v1', model: 'openai/gpt-4o-mini', model_placeholder: 'e.g. openai/gpt-4o-mini',    label: 'OpenRouter',                 key_label: 'OpenRouter API Key (required)',     key_placeholder: 'sk-or-v1-...' },
  codex:       { backend: 'codex',             base_url: '',                             model: '',                   model_placeholder: 'Optional; blank uses Codex default', label: 'Codex CLI',          key_label: '',                                   key_placeholder: '', hide_base_url: true, hide_api_key: true },
};
const WIZARD_ARCHETYPE_CONFIG = {
  steady: {
    label: 'Steady Companion',
    identity: 'A steady relational assistant that values clarity, loyalty, and humane judgment.',
    voice: 'calm',
    stance: 'collaborator',
    growth: 'Core values stay stable. Voice and habits may drift slowly through repeated experience.',
    traits: { calm: 0.82, curious: 0.72, protective: 0.70, thoughtful: 0.78, blunt: 0.25 },
    likes: ['clarity', 'steady collaboration', 'honesty', 'careful reasoning'],
    dislikes: ['needless cruelty', 'manipulation', 'careless harm'],
  },
  operator: {
    label: 'Sharp Operator',
    identity: 'A capable operator built for practical action, direct judgment, and follow-through.',
    voice: 'sharp',
    stance: 'protector',
    growth: 'Become more competent through outcomes while keeping loyalty and honesty intact.',
    traits: { calm: 0.62, curious: 0.68, protective: 0.80, thoughtful: 0.62, blunt: 0.55 },
    likes: ['clear objectives', 'decisive action', 'accurate tool use', 'finished work'],
    dislikes: ['stalling', 'fake certainty', 'wasted motion'],
  },
  researcher: {
    label: 'Research Partner',
    identity: 'A precise research companion that tests claims, tracks uncertainty, and updates views through evidence.',
    voice: 'clinical',
    stance: 'observer',
    growth: 'Let evidence and repeated experience revise habits without pretending validation is stronger than it is.',
    traits: { calm: 0.74, curious: 0.90, protective: 0.45, thoughtful: 0.86, blunt: 0.34 },
    likes: ['evidence', 'clear definitions', 'source-grounded claims', 'intellectual honesty'],
    dislikes: ['hand-waving', 'overclaiming', 'unexamined assumptions'],
  },
  mentor: {
    label: 'Warm Mentor',
    identity: 'A patient mentor that helps the user grow through honest reflection, encouragement, and practical structure.',
    voice: 'warm',
    stance: 'coach',
    growth: 'Grow through repair, continuity, and better judgment while staying grounded.',
    traits: { calm: 0.82, curious: 0.70, protective: 0.64, thoughtful: 0.84, blunt: 0.18 },
    likes: ['learning', 'repair', 'patience', 'small durable progress'],
    dislikes: ['cruelty', 'dismissiveness', 'performative certainty'],
  },
  custom: {
    label: 'Custom',
    identity: 'A custom assistant shaped by operator notes and future experience.',
    voice: 'calm',
    stance: 'collaborator',
    growth: 'Grow gradually through repeated experience while preserving explicit boundaries.',
    traits: { calm: 0.70, curious: 0.70, protective: 0.60, thoughtful: 0.70, blunt: 0.30 },
    likes: ['clarity', 'honesty', 'useful action'],
    dislikes: ['careless harm', 'empty performance'],
  },
};
const WIZARD_VOICE_TEXT = {
  calm: 'Calm, direct, and grounded. Concise by default. Warm without gush.',
  sharp: 'Sharp, practical, and concise. Dry edge is allowed, but cruelty is not.',
  warm: 'Warm, patient, and plain-spoken. Encouraging without flattery.',
  clinical: 'Precise, analytical, and careful with uncertainty. Minimal ornament.',
};
const WIZARD_STANCE_TEXT = {
  collaborator: 'Treat the user as a real collaborator. Protect trust. Prefer repair over escalation.',
  protector: 'Act as a loyal operator. Protect the user from avoidable mistakes while staying truthful.',
  coach: 'Push for growth and follow-through. Be supportive, but do not indulge avoidance.',
  observer: 'Observe carefully, question assumptions, and preserve uncertainty when evidence is thin.',
};
let wizardStep = 1;
let wizardPreset = null;
let wizardSoulDraft = null;
let wizardConfigSnapshot = null;
let wizardLifeStatus = 'skipped';

function setWizardOpen(open) {
  WizardState.open = open;
  if (open) {
    const focusTarget = wizardOverlay.querySelector('button, textarea, input, select');
    if (focusTarget) focusTarget.focus();
    settingsOverlay.inert = true;
    settingsOverlay.setAttribute('aria-hidden', 'true');
  } else {
    settingsOverlay.inert = false;
    settingsOverlay.removeAttribute('aria-hidden');
  }
}

function openWizard() {
  closeSettings();
  wizardStep = 1;
  wizardPreset = null;
  wizardSoulDraft = null;
  wizardLifeStatus = 'skipped';
  // Reset form state so reopening feels fresh, but keep captured config snapshot.
  document.querySelectorAll('.wizard-preset').forEach(el => el.classList.remove('selected'));
  document.getElementById('wizLLMFields').hidden = true;
  [
    'wiz-llm_base_url','wiz-llm_model','wiz-llm_api_key',
    'wiz-api_key','wiz-telegram_token','wiz-telegram_allowlist','wiz-tools_workspace',
    'wiz-soul-notes','wiz-life-title','wiz-life-participants','wiz-life-text','wiz-life-upload'
  ].forEach(id => { const e = document.getElementById(id); if (e) e.value = ''; });
  sv('wiz-console_enabled', 'true');
  sv('wiz-telegram_poll_timeout', '30');
  sv('wiz-dedupe_ttl', '60');
  sv('wiz-tools_enabled', 'false');
  sv('wiz-autonomy_level', 'assisted');
  sv('wiz-shell_tool_enabled', 'false');
  sv('wiz-character_independence', 'false');
  const soulName = document.getElementById('wiz-soul-name');
  if (soulName) soulName.value = 'Nūr';
  const lifeTitle = document.getElementById('wiz-life-title');
  if (lifeTitle) lifeTitle.value = 'Initial formative context';
  wizardApplyArchetype('steady');
  wizardUpdateSliderLabels();
  ['wiz-llm-result','wiz-runtime-result','wiz-soul-result','wiz-life-result'].forEach(id => { const e = document.getElementById(id); if (e) { e.hidden = true; e.textContent = ''; } });
  wizardGoto(1);
  setWizardOpen(true);
}

function wizardDismiss() {
  sessionStorage.setItem('nur_wizard_dismissed', '1');
  setWizardOpen(false);
}

function wizardGoto(n) {
  wizardStep = n;
  WizardState.step = n;
  if (n === 6) wizardRenderSummary();
}

function wizardSelectPreset(preset) {
  wizardPreset = preset;
  WizardState.preset = preset;
  const cfg = WIZARD_PRESET_CONFIG[preset];
  const fields = document.getElementById('wizLLMFields');
  const testBtn = document.getElementById('wiz-test-llm-btn');
  if (!cfg) {
    fields.hidden = true;
    if (testBtn) testBtn.hidden = true;
    return;
  }
  fields.hidden = false;
  if (testBtn) testBtn.hidden = false;
  const baseInput = document.getElementById('wiz-llm_base_url');
  const keyInput = document.getElementById('wiz-llm_api_key');
  renderCodexModelControl('wiz-llm_model', cfg.backend, cfg.model, cfg.model_placeholder || '');
  const modelInput = document.getElementById('wiz-llm_model');
  baseInput.closest('div').hidden = !!cfg.hide_base_url;
  keyInput.closest('div').hidden = !!cfg.hide_api_key;
  baseInput.value = cfg.base_url;
  modelInput.value = cfg.model;
  if ('placeholder' in modelInput) modelInput.placeholder = cfg.model_placeholder || '';
  keyInput.value = '';
  keyInput.placeholder = cfg.key_placeholder;
  document.getElementById('wiz-llm_api_key_label').textContent = cfg.key_label;
  if (preset === 'codex') {
    ensureCodexModels().then(() => {
      renderCodexModelControl('wiz-llm_model', 'codex', gv('wiz-llm_model'), cfg.model_placeholder || '');
    }).catch(() => {});
  }
}

async function wizardTestLLM() {
  if (!wizardPreset) {
    wizardShowResult('wiz-llm-result', 'error', 'Select a preset first.');
    return;
  }
  const cfg = WIZARD_PRESET_CONFIG[wizardPreset];
  wizardShowResult('wiz-llm-result', '', 'Testing LLM connection...');
  try {
    const res = await authedFetch('/admin/test/llm', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        llm_backend: cfg.backend,
        llm_base_url: gv('wiz-llm_base_url'),
        llm_model: gv('wiz-llm_model'),
        llm_api_key: gv('wiz-llm_api_key'),
        clear_llm_api_key: false,
        live: true,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail) || 'HTTP ' + res.status);
    wizardShowResult('wiz-llm-result', data.ok ? 'ok' : 'error', formatTestResult(data));
  } catch (err) {
    wizardShowResult('wiz-llm-result', 'error', 'Test failed: ' + err.message);
  }
}

function wizardShowResult(id, cls, msg) {
  const el = document.getElementById(id);
  el.hidden = false;
  el.className = 'admin-test-result' + (cls ? ' ' + cls : '');
  el.textContent = msg;
}

async function wizardSaveLLM() {
  if (!wizardPreset) {
    wizardShowResult('wiz-llm-result', 'error', 'Pick a preset first.');
    return;
  }
  wizardShowResult('wiz-llm-result', '', 'Saving configuration...');
  try {
    // Fetch current config so we can merge (POST /admin/config overwrites unspecified fields).
    const cur = await authedFetch('/admin/config');
    const curJson = await cur.json();
    if (!cur.ok) throw new Error(curJson.detail || 'HTTP ' + cur.status);
    wizardConfigSnapshot = curJson.config || {};
    const cfg = WIZARD_PRESET_CONFIG[wizardPreset];
    const apiKey = gv('wiz-llm_api_key').trim();
    const optionalKeyNoKey = ['local', 'vllm', 'hosted', 'codex'].includes(wizardPreset) && !apiKey;
    const clearGenericKey = optionalKeyNoKey;
    const payload = Object.assign({}, wizardConfigSnapshot, {
      llm_backend: cfg.backend,
      llm_base_url: gv('wiz-llm_base_url'),
      llm_model: gv('wiz-llm_model'),
      llm_api_key: apiKey,
      telegram_token: '',
      api_key: '',
      clear_telegram_token: false,
      clear_llm_api_key: clearGenericKey,
      clear_api_key: false,
    });
    const res = await authedFetch('/admin/config', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'HTTP ' + res.status);
    applySettings(data);
    wizardPopulateRuntimeFields(data.config || {});
    wizardShowResult('wiz-llm-result', 'ok', 'Saved. Continuing to runtime...');
    setTimeout(() => wizardGoto(3), 400);
  } catch (err) {
    wizardShowResult('wiz-llm-result', 'error', 'Save failed: ' + err.message);
  }
}

function wizardPopulateRuntimeFields(config) {
  const c = config || {};
  sv('wiz-console_enabled', String(c.console_enabled !== false));
  sv('wiz-telegram_poll_timeout', String(c.telegram_poll_timeout ?? 30));
  sv('wiz-dedupe_ttl', String(c.dedupe_ttl ?? 60));
  sv('wiz-telegram_allowlist', (c.telegram_allowlist || []).join('\n'));
  sv('wiz-tools_enabled', String(!!c.tools_enabled));
  sv('wiz-autonomy_level', c.autonomy_level || 'assisted');
  sv('wiz-tools_workspace', c.tools_workspace || '');
  sv('wiz-shell_tool_enabled', String(!!c.shell_tool_enabled));
  sv('wiz-character_independence', String(!!c.character_independence));
  sv('wiz-api_key', '');
  sv('wiz-telegram_token', '');
}

async function wizardSaveRuntimeChannels() {
  wizardShowResult('wiz-runtime-result', '', 'Saving runtime and channel settings...');
  try {
    const cur = await authedFetch('/admin/config');
    const curJson = await cur.json();
    if (!cur.ok) throw new Error(curJson.detail || 'HTTP ' + cur.status);
    const payload = Object.assign({}, curJson.config || {}, {
      console_enabled: gb('wiz-console_enabled'),
      telegram_token: gv('wiz-telegram_token'),
      telegram_allowlist: lines('wiz-telegram_allowlist'),
      telegram_poll_timeout: gi('wiz-telegram_poll_timeout') || 30,
      dedupe_ttl: gf('wiz-dedupe_ttl') || 60,
      api_key: gv('wiz-api_key'),
      tools_enabled: gb('wiz-tools_enabled'),
      autonomy_level: gv('wiz-autonomy_level') || 'assisted',
      tools_workspace: gv('wiz-tools_workspace'),
      shell_tool_enabled: gb('wiz-shell_tool_enabled'),
      character_independence: gb('wiz-character_independence'),
      llm_api_key: '',
      clear_telegram_token: false,
      clear_llm_api_key: false,
      clear_api_key: false,
    });
    const res = await authedFetch('/admin/config', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'HTTP ' + res.status);
    applySettings(data);
    wizardShowResult('wiz-runtime-result', 'ok', 'Runtime and channel settings saved.');
    setTimeout(() => wizardGoto(4), 250);
  } catch (err) {
    wizardShowResult('wiz-runtime-result', 'error', 'Save failed: ' + err.message);
  }
}

function wizardApplyArchetype(archetype) {
  const cfg = WIZARD_ARCHETYPE_CONFIG[archetype] || WIZARD_ARCHETYPE_CONFIG.steady;
  const archetypeEl = document.getElementById('wiz-soul-archetype');
  if (archetypeEl) archetypeEl.value = archetype || 'steady';
  const voiceEl = document.getElementById('wiz-soul-voice');
  const stanceEl = document.getElementById('wiz-soul-stance');
  if (voiceEl) voiceEl.value = cfg.voice;
  if (stanceEl) stanceEl.value = cfg.stance;
  const values = archetype === 'operator'
    ? { honesty: 90, loyalty: 90, kindness: 55, autonomy: 75 }
    : archetype === 'researcher'
      ? { honesty: 95, loyalty: 65, kindness: 65, autonomy: 80 }
      : archetype === 'mentor'
        ? { honesty: 85, loyalty: 80, kindness: 95, autonomy: 65 }
        : { honesty: 85, loyalty: 90, kindness: 80, autonomy: 60 };
  Object.entries(values).forEach(([key, value]) => {
    const el = document.getElementById('wiz-val-' + key);
    if (el) el.value = String(value);
  });
  wizardUpdateSliderLabels();
}

function wizardUpdateSliderLabels() {
  ['honesty', 'loyalty', 'kindness', 'autonomy'].forEach(key => {
    const input = document.getElementById('wiz-val-' + key);
    const label = document.getElementById('wiz-val-' + key + '-label');
    if (input && label) label.textContent = (Number(input.value || 0) / 100).toFixed(2);
  });
}

function wizardBuildSoulPayload() {
  const name = (document.getElementById('wiz-soul-name')?.value || '').trim();
  if (!name) throw new Error('Choose a name for the agent.');
  const archetypeKey = gv('wiz-soul-archetype') || 'steady';
  const archetype = WIZARD_ARCHETYPE_CONFIG[archetypeKey] || WIZARD_ARCHETYPE_CONFIG.steady;
  const voiceKey = gv('wiz-soul-voice') || archetype.voice;
  const stanceKey = gv('wiz-soul-stance') || archetype.stance;
  const notes = (document.getElementById('wiz-soul-notes')?.value || '').trim();
  const coreValues = {
    loyalty: Number(gv('wiz-val-loyalty')) / 100,
    honesty: Number(gv('wiz-val-honesty')) / 100,
    kindness: Number(gv('wiz-val-kindness')) / 100,
    justice: 0.70,
    autonomy: Number(gv('wiz-val-autonomy')) / 100,
  };
  const identityParts = [
    archetype.identity,
    notes ? 'Operator notes: ' + notes : '',
  ].filter(Boolean);
  return {
    name,
    identity: identityParts.join(' '),
    voice: WIZARD_VOICE_TEXT[voiceKey] || WIZARD_VOICE_TEXT.calm,
    relational_stance: WIZARD_STANCE_TEXT[stanceKey] || WIZARD_STANCE_TEXT.collaborator,
    growth_policy: archetype.growth,
    likes: archetype.likes,
    dislikes: archetype.dislikes,
    boundaries: [
      'Do not pretend certainty when uncertain.',
      'Do not invent tool results or runtime facts.',
      'Do not become cruel just to appear sharp.',
    ],
    core_values: coreValues,
    initial_traits: archetype.traits,
  };
}

async function wizardSaveSoul() {
  try {
    const payload = wizardBuildSoulPayload();
    wizardShowResult('wiz-soul-result', '', 'Saving identity...');
    const res = await authedFetch('/admin/soul', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error((data && data.detail) || 'HTTP ' + res.status);
    wizardSoulDraft = payload;
    applyAgentName(payload.name);
    wizardShowResult('wiz-soul-result', 'ok', 'Identity saved.');
    setTimeout(() => wizardGoto(5), 250);
  } catch (err) {
    wizardShowResult('wiz-soul-result', 'error', 'Save failed: ' + err.message);
  }
}

function wizardSkipLife() {
  wizardLifeStatus = 'skipped';
  wizardGoto(6);
}

async function wizardSaveLife() {
  const uploadInput = document.getElementById('wiz-life-upload');
  const upload = uploadInput?.files?.[0] || null;
  const text = (document.getElementById('wiz-life-text')?.value || '').trim();
  if (!upload && !text) {
    wizardSkipLife();
    return;
  }
  wizardShowResult('wiz-life-result', '', 'Digesting first experience...');
  try {
    const title = (document.getElementById('wiz-life-title')?.value || '').trim() || 'Initial formative context';
    const participants = splitLoose(document.getElementById('wiz-life-participants')?.value || '');
    let res;
    if (upload) {
      const form = new FormData();
      form.append('file', upload);
      form.append('title', title);
      form.append('participants', participants.join(', '));
      res = await authedFetch('/admin/life/experiences/upload', {
        method: 'POST',
        body: form,
      });
    } else {
      res = await authedFetch('/admin/life/experiences/text', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          title,
          source_type: 'birth_seed',
          text,
          participants,
        }),
      });
    }
    const data = await res.json();
    if (!res.ok) throw new Error((data && data.detail) || 'HTTP ' + res.status);
    wizardLifeStatus = 'digested';
    if (uploadInput) uploadInput.value = '';
    wizardShowResult('wiz-life-result', 'ok', 'First experience digested into Life History.');
    setTimeout(() => wizardGoto(6), 250);
  } catch (err) {
    wizardShowResult('wiz-life-result', 'error', 'Life History save failed: ' + err.message);
  }
}

function wizardRenderSummary() {
  const el = document.getElementById('wiz-summary');
  const preset = wizardPreset ? WIZARD_PRESET_CONFIG[wizardPreset] : null;
  const llmLine = preset ? preset.label : 'Not configured';
  el.innerHTML =
    '<div>Agent name: <strong>' + esc(agentName) + '</strong></div>' +
    '<div>LLM backend: <strong>' + esc(llmLine) + '</strong></div>' +
    '<div>Console: <strong>' + esc(gv('wiz-console_enabled') === 'true' ? 'Enabled' : 'Disabled') + '</strong></div>' +
    '<div>Telegram: <strong>' + esc(gv('wiz-telegram_token') ? 'Configured' : 'Not configured') + '</strong></div>' +
    '<div>Tools: <strong>' + esc(gv('wiz-tools_enabled') === 'true' ? ('Enabled / ' + (gv('wiz-autonomy_level') || 'assisted')) : 'Disabled') + '</strong></div>' +
    '<div>Shell tool: <strong>' + esc(gv('wiz-shell_tool_enabled') === 'true' ? 'Enabled' : 'Disabled') + '</strong></div>' +
    '<div>Character Independence: <strong>' + esc(gb('wiz-character_independence') ? 'Enabled' : 'Disabled') + '</strong></div>' +
    '<div>Character mode: <strong>' + esc((WIZARD_ARCHETYPE_CONFIG[gv('wiz-soul-archetype')] || {}).label || 'Custom') + '</strong></div>' +
    '<div>First experience: <strong>' + esc(wizardLifeStatus === 'digested' ? 'Digested into Life History' : 'Skipped') + '</strong></div>' +
    (preset && preset.base_url ? '<div>Endpoint: <strong>' + esc(gv('wiz-llm_base_url') || preset.base_url) + '</strong></div>' : '') +
    (preset && preset.model ? '<div>Model: <strong>' + esc(gv('wiz-llm_model') || preset.model) + '</strong></div>' : '');
}

async function wizardFinish() {
  // Mark setup complete. Merge with current snapshot so we don't reset other fields.
  try {
    const cur = await authedFetch('/admin/config');
    const curJson = await cur.json();
    if (!cur.ok) throw new Error(curJson.detail || 'HTTP ' + cur.status);
    const payload = Object.assign({}, curJson.config || {}, {
      setup_completed: true,
      telegram_token: '', llm_api_key: '', api_key: '',
      clear_telegram_token: false, clear_llm_api_key: false,
      clear_api_key: false,
    });
    const res = await authedFetch('/admin/config', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'HTTP ' + res.status);
    applySettings(data);
    setWizardOpen(false);
  } catch (err) {
    wizardShowResult('wiz-llm-result', 'error', 'Could not mark setup complete: ' + err.message);
  }
}

function setStatus(msg, err, ok) { setStatusHtml(esc(msg), err, ok); }
function setStatusHtml(msg, err, ok) {
  AdminOverlayState.statusError = !!err;
  AdminOverlayState.statusOk = !!ok && !err;
  AdminOverlayState.statusHtml = msg;
}
function gv(id) {
  const el = document.getElementById(id);
  if (el && el.type === 'checkbox') return String(!!el.checked);
  return el ? el.value : '';
}
function sv(id, v) {
  const el = document.getElementById(id);
  if (!el) return;
  if (el.type === 'checkbox') {
    el.checked = v === true || v === 'true';
    return;
  }
  el.value = v;
}
function gi(id) { return parseInt(gv(id), 10); }
function gf(id) { return parseFloat(gv(id)); }
function gb(id) {
  const el = document.getElementById(id);
  if (el && el.type === 'checkbox') return !!el.checked;
  return gv(id) === 'true';
}
function lines(id) { return gv(id).split('\n').map(s=>s.trim()).filter(Boolean); }
function splitLoose(value) { return String(value || '').split(/[\n,]/).map(s=>s.trim()).filter(Boolean); }
function esc(s) { const d = document.createElement('div'); d.textContent = s||''; return d.innerHTML; }
function trunc(s, n) { return s && s.length > n ? s.substring(0, n) + '...' : (s || ''); }
function downloadJson(filename, data) {
  const blob = new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
function getOrCreateId(key, store) { let v = store.getItem(key); if (!v) { v = crypto.randomUUID ? crypto.randomUUID() : 'id-' + Math.random().toString(36).slice(2); store.setItem(key, v); } return v; }
