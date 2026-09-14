'use strict';

const root = document.getElementById('app');
const bootstrap = JSON.parse(root.dataset.bootstrap || '{}');
const token = bootstrap.token;
const $ = id => document.getElementById(id);

const state = {
  file: null,
  artifact: null,
  artifacts: [],
  officeArtifacts: [],
  officeArtifact: null,
  shell: null,
  ai: null,
  capacity: null,
  freeDiscovery: [],
  intelligenceSearch: { candidates: [], errors: [], query: "" },
  chat: { conversations: [], activeId: null, messages: [], context: null },
  dirty: false,
  preview: false,
  lastPlan: null,
  ui: {
    view: 'home',
    homeMode: 'team',
    selectedPortfolioId: null,
    routingMode: 'balanced',
    qualityFloor: 0.75,
    themePreference: localStorage.getItem('w1-theme') || 'light',
  },
};

const api = async (path, options = {}) => {
  const headers = { Authorization: `Bearer ${token}`, ...(options.headers || {}) };
  if (options.body) headers['Content-Type'] = 'application/json';
  const res = await fetch(path, { ...options, headers });
  const body = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  if (!res.ok) throw Object.assign(new Error(body.message || body.error || `HTTP ${res.status}`), { body, status: res.status });
  return body.result ?? body;
};

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function toast(message, bad = false) {
  const el = $('toast');
  el.textContent = message;
  el.style.borderColor = bad ? 'var(--danger)' : 'color-mix(in srgb,var(--accent) 38%,var(--line))';
  el.classList.remove('hidden');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.add('hidden'), 3800);
}

function effectiveTheme(preference = state.ui.themePreference) {
  if (preference === 'system') return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  return preference === 'dark' ? 'dark' : 'light';
}

function applyTheme(preference, persist = true) {
  state.ui.themePreference = preference;
  if (persist) localStorage.setItem('w1-theme', preference);
  const theme = effectiveTheme(preference);
  document.documentElement.dataset.theme = theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = theme === 'dark' ? '#090d12' : '#f4f0e8';
  document.querySelectorAll('[data-set-theme]').forEach(btn => btn.classList.toggle('active', btn.dataset.setTheme === preference));
}

function toggleTheme() {
  applyTheme(effectiveTheme() === 'dark' ? 'light' : 'dark');
}

function showView(view) {
  state.ui.view = view;
  document.querySelectorAll('.nav-item[data-view]').forEach(btn => btn.classList.toggle('active', btn.dataset.view === view));
  document.querySelectorAll('.app-view[data-view-page]').forEach(page => page.classList.toggle('active', page.dataset.viewPage === view));
  document.querySelector('.product-main')?.scrollTo({ top: 0, behavior: 'instant' });
}

function providerGlyph(providerId, fallback = 'AI') {
  const labels = { openai: 'OAI', anthropic: 'C', gemini: 'G', google: 'G', xai: 'X', grok: 'X', 'local-openai-compatible': 'LOCAL', local: 'LOCAL' };
  return labels[String(providerId || '').toLowerCase()] || String(fallback || 'AI').slice(0, 4).toUpperCase();
}

function formatMoney(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return '—';
  if (n === 0) return '$0.00';
  if (n < 0.01) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(2)}`;
}

function formatTokens(value) {
  if (value === null || value === undefined) return 'unknown';
  const n = Number(value);
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function routingFromSlider(value) {
  const n = Number(value);
  if (n <= 32) return 'maximum_free';
  if (n >= 68) return 'maximum_quality';
  return 'balanced';
}

function routingLabel(mode) {
  return ({ maximum_free: 'Maximum Free', maximum_quality: 'Maximum Quality', local_only: 'Local Only', custom_budget: 'Custom Budget', balanced: 'Balanced' }[mode] || mode);
}

function modeToTeamMode(mode) {
  return ({ solo: 'solo', team: 'challenge', parallel: 'parallel', verify: 'verify', challenge: 'challenge' }[mode] || 'challenge');
}

function inferDomain(text) {
  const s = String(text || '').toLowerCase();
  if (/(code|python|javascript|typescript|react|bug|api|database|sql|program|software|repo|git)/.test(s)) return 'coding';
  if (/(research|study|paper|evidence|compare|market|analy[sz]e|sources|report)/.test(s)) return 'research';
  if (/(design|ui|ux|brand|visual|logo|interface)/.test(s)) return 'design';
  return 'general';
}

function selectedPortfolio() {
  const portfolios = state.shell?.portfolios || [];
  if (!portfolios.length) return null;
  return portfolios.find(p => p.portfolio_id === state.ui.selectedPortfolioId) || portfolios[portfolios.length - 1];
}

function modelById(modelId) {
  return (state.shell?.models || []).find(m => m.model_id === modelId);
}

function connectionByProvider(providerId) {
  return (state.ai?.connections || []).find(c => c.provider_id === providerId);
}

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

/* ---------- workspace files & artifact studio ---------- */
function fileIcon(entry) {
  if (entry.kind === 'directory') return '▸';
  const ext = entry.name.split('.').pop().toLowerCase();
  return ({ py: 'Py', js: 'JS', ts: 'TS', json: '{}', md: 'M', csv: '▦', pdf: 'P', png: '▧', jpg: '▧', jpeg: '▧' }[ext] || '·');
}

function renderTree(entries, parent) {
  for (const entry of entries) {
    const node = document.createElement('div');
    node.className = `tree-node ${entry.kind === 'directory' ? 'dir' : 'file'}`;
    node.innerHTML = `<span>${fileIcon(entry)}</span><span>${escapeHtml(entry.name)}</span>`;
    parent.appendChild(node);
    if (entry.kind === 'directory') {
      const children = document.createElement('div');
      children.className = 'tree-children';
      children.style.display = 'none';
      parent.appendChild(children);
      node.onclick = () => { children.style.display = children.style.display === 'none' ? 'block' : 'none'; };
      renderTree(entry.children || [], children);
    } else {
      node.onclick = () => openFile(entry.path);
    }
  }
}

async function loadTree() {
  const data = await api('/api/v1/tree?depth=6');
  const tree = $('file-tree');
  tree.innerHTML = '';
  renderTree(data.entries || [], tree);
  if (!(data.entries || []).length) tree.textContent = 'Workspace is empty.';
}

function updateLines() {
  const count = ($('editor').value.match(/\n/g) || []).length + 1;
  $('line-numbers').textContent = Array.from({ length: count }, (_, i) => i + 1).join('\n');
}

async function openFile(path) {
  try {
    const doc = await api(`/api/v1/file?path=${encodeURIComponent(path)}`);
    state.file = doc;
    state.dirty = false;
    $('active-tab').textContent = path;
    $('file-meta').textContent = `${doc.kind} • ${doc.size_bytes} bytes • ${doc.content_hash.slice(0, 12)}`;
    $('editor').value = doc.text ?? '';
    $('editor').disabled = doc.text === null;
    $('save-draft').disabled = doc.text === null;
    updateLines();
    $('file-state').textContent = '';
    const match = state.artifacts.find(a => a.path === path);
    if (match) {
      const history = await api(`/api/v1/artifact?artifact_id=${encodeURIComponent(match.artifact_id)}`);
      state.artifact = history.current;
      state.artifact.reviews = history.reviews;
      state.artifact.comments = history.comments;
    } else state.artifact = null;
    renderArtifactInspector();
    if (state.preview) showPreview();
  } catch (err) { toast(err.message, true); }
}

function renderArtifactInspector() {
  setText('active-artifact', state.artifact ? `${state.artifact.artifact_id}@${state.artifact.version || state.artifact.current_version}` : 'None');
  setText('artifact-hash', state.artifact?.content_hash || state.file?.content_hash || '—');
  $('review-history').innerHTML = state.artifact?.reviews?.map(r => `<div><b>${escapeHtml(r.outcome)}</b> — ${escapeHtml(r.reviewer)}<br>${escapeHtml(r.rationale)}</div>`).join('<hr>') || 'No reviews yet.';
}

function renderArtifacts() {
  $('artifact-list').innerHTML = state.artifacts.map(a => `<div class="artifact-item" data-id="${escapeHtml(a.artifact_id)}"><b>${escapeHtml(a.title)}</b><span>${escapeHtml(a.path)} • v${a.current_version} • ${escapeHtml(a.status)}</span></div>`).join('') || '<div class="empty-state compact">No workspace artifacts yet.</div>';
  document.querySelectorAll('.artifact-item[data-id]').forEach(el => el.onclick = () => openArtifact(el.dataset.id));
}

function renderOfficeArtifacts() {
  $('office-artifact-list').innerHTML = (state.officeArtifacts || []).map(a => `<div class="artifact-item" data-office-id="${escapeHtml(a.artifact_id)}"><b>${escapeHtml(a.title)}</b><span>${escapeHtml(a.kind)} • v${a.current_version} • ${escapeHtml(a.status)}</span></div>`).join('') || '<div class="empty-state compact">No Office artifacts yet.</div>';
  document.querySelectorAll('[data-office-id]').forEach(el => el.onclick = () => openOfficeArtifact(el.dataset.officeId));
}

async function openOfficeArtifact(id) {
  const data = await api(`/api/v1/office/artifact?artifact_id=${encodeURIComponent(id)}`);
  state.officeArtifact = data.current;
  state.file = null;
  state.artifact = null;
  $('active-tab').textContent = `${id} [${data.current.kind} v${data.current.version}]`;
  $('file-meta').textContent = `Universal Artifact • ${data.current.model_hash.slice(0, 12)}`;
  $('editor').disabled = false;
  $('editor').value = JSON.stringify(data.current.model, null, 2);
  $('save-draft').disabled = true;
  updateLines();
  renderArtifactInspector();
  showView('files');
}

async function createOfficeFromEditor() {
  let model;
  try { model = JSON.parse($('editor').value); }
  catch (_) { return toast('The editor does not contain a valid artifact JSON model.', true); }
  const data = await api('/api/v1/office/create', { method: 'POST', body: JSON.stringify({ model, created_by: 'local-author' }) });
  toast(`Created ${data.artifact_id}@${data.version}`);
  await loadState();
  await openOfficeArtifact(data.artifact_id);
}

async function reviewOffice() {
  if (!state.officeArtifact) return toast('Open an Office artifact first.', true);
  const data = await api('/api/v1/office/review', { method: 'POST', body: JSON.stringify({ artifact_id: state.officeArtifact.artifact_id, version: state.officeArtifact.version, reviewer: $('office-reviewer').value, outcome: 'approved', rationale: $('office-rationale').value }) });
  toast(`Review: ${data.outcome}`);
  await loadState();
  await openOfficeArtifact(state.officeArtifact.artifact_id);
}

async function exportOffice() {
  if (!state.officeArtifact) return toast('Open an Office artifact first.', true);
  const fmt = $('office-format').value;
  let output = $('office-output').value.trim();
  if (!output.endsWith('.' + fmt)) output = `exports/${state.officeArtifact.artifact_id}.${fmt}`;
  const data = await api('/api/v1/office/export', { method: 'POST', body: JSON.stringify({ artifact_id: state.officeArtifact.artifact_id, format: fmt, output_path: output, exported_by: 'local-owner', issue_action_approval: true }) });
  toast(`Exported: ${data.output_path}`);
  await Promise.all([loadTree(), loadState()]);
}

async function openArtifact(id) {
  const data = await api(`/api/v1/artifact?artifact_id=${encodeURIComponent(id)}`);
  state.artifact = data.current;
  state.artifact.reviews = data.reviews;
  state.artifact.comments = data.comments;
  state.file = { path: data.current.path, kind: data.current.kind, size_bytes: new Blob([data.current.content]).size, content_hash: data.current.content_hash, text: data.current.content };
  $('editor').value = data.current.content;
  $('editor').disabled = false;
  $('save-draft').disabled = false;
  $('active-tab').textContent = `${data.current.path} [draft v${data.current.version}]`;
  $('file-meta').textContent = `Artifact ${id} • ${data.current.status}`;
  updateLines();
  renderArtifactInspector();
  showView('files');
}

function dialog(title, html, confirm) {
  $('dialog-title').textContent = title;
  $('dialog-body').innerHTML = html;
  document.querySelector('.dialog-card').classList.toggle('ai-dialog', /AI|Connect|model|team|Adaptive/i.test(title));
  $('dialog').classList.remove('hidden');
  $('dialog-confirm').onclick = async () => {
    try { await confirm(); $('dialog').classList.add('hidden'); }
    catch (err) { toast(err.message, true); }
  };
}

async function createDraft() {
  if (!state.file || state.file.text === null) { showView('files'); return toast('Open a text file first.', true); }
  if (!state.shell?.desktop?.allow_operations) return toast('Operations mode is required to create artifacts.', true);
  const suggested = state.file.path.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 50) || 'artifact';
  dialog('Create Artifact', `<input id="d-id" value="${suggested}" placeholder="artifact-id"><input id="d-title" value="${escapeHtml(state.file.path)}" placeholder="Title"><input id="d-author" value="local-author" placeholder="Author">`, async () => {
    const result = await api('/api/v1/artifacts/draft', { method: 'POST', body: JSON.stringify({ artifact_id: $('d-id').value, path: state.file.path, title: $('d-title').value, created_by: $('d-author').value, content: $('editor').value }) });
    toast(`Created ${result.artifact_id}@${result.version}`);
    await loadState();
    await openArtifact(result.artifact_id);
  });
}

async function saveVersion() {
  if (!state.artifact) return createDraft();
  const result = await api('/api/v1/artifacts/version', { method: 'POST', body: JSON.stringify({ artifact_id: state.artifact.artifact_id, content: $('editor').value, created_by: 'local-author', expected_parent_hash: state.artifact.content_hash }) });
  toast(`Saved version ${result.version}`);
  await loadState();
  await openArtifact(result.artifact_id);
}

async function submitReview() {
  if (!state.artifact) return toast('No Artifact is open.', true);
  const result = await api('/api/v1/artifacts/review', { method: 'POST', body: JSON.stringify({ artifact_id: state.artifact.artifact_id, version: state.artifact.version, reviewer: $('review-author').value, outcome: $('review-outcome').value, rationale: $('review-rationale').value }) });
  toast(`Review recorded: ${result.outcome}`);
  await openArtifact(state.artifact.artifact_id);
}

async function publishArtifact() {
  if (!state.artifact) return toast('No Artifact is open.', true);
  const result = await api('/api/v1/artifacts/publish', { method: 'POST', body: JSON.stringify({ artifact_id: state.artifact.artifact_id, published_by: 'local-owner', issue_action_approval: true }) });
  toast(`Published through ${result.action_id}`);
  await Promise.all([loadTree(), loadState()]);
  await openArtifact(state.artifact.artifact_id);
}

async function showPreview() {
  if (!state.file) return;
  state.preview = true;
  $('editor-wrap').classList.add('hidden');
  $('preview').classList.remove('hidden');
  const data = await api(`/api/v1/preview?path=${encodeURIComponent(state.file.path)}`).catch(() => state.file);
  if (data.table) {
    $('preview').innerHTML = `<table>${[data.table.header, ...data.table.rows].map((row, i) => `<tr>${row.map(v => `<${i ? 'td' : 'th'}>${escapeHtml(v)}</${i ? 'td' : 'th'}>`).join('')}</tr>`).join('')}</table>`;
  } else $('preview').innerHTML = `<pre>${escapeHtml(data.text ?? JSON.stringify(data.structured || data, null, 2))}</pre>`;
}

function showEditor() {
  state.preview = false;
  $('preview').classList.add('hidden');
  $('editor-wrap').classList.remove('hidden');
}

/* ---------- conversation history & token-bounded context ---------- */
function renderChatConversations() {
  const items = state.chat.conversations || [];
  if (!state.chat.activeId && items.length) state.chat.activeId = items[0].conversation_id;
  const select = $('chat-conversation');
  if (!select) return;
  select.innerHTML = items.map(c => `<option value="${escapeHtml(c.conversation_id)}" ${c.conversation_id === state.chat.activeId ? 'selected' : ''}>${escapeHtml(c.title || c.conversation_id)} · ${c.message_count || 0}</option>`).join('') || '<option value="chat-main">New conversation</option>';
  setText('chat-history-status', `${items.reduce((n,c)=>n+Number(c.message_count||0),0)} local turns`);
}

function renderChatMessages() {
  const box = $('chat-messages');
  if (!box) return;
  const messages = state.chat.messages || [];
  box.innerHTML = messages.map(m => `<div class="chat-message ${escapeHtml(m.role)}"><small>${escapeHtml(m.role)}${m.model_id ? ` · ${escapeHtml(m.model_id)}` : ''} · ~${m.token_estimate || 0} tokens</small><p>${escapeHtml(m.content)}</p></div>`).join('') || '<div class="chat-empty"><div class="chat-orb">W1</div><h2>Your full history stays local.</h2><p>Add a turn to start the durable conversation ledger.</p></div>';
  box.scrollTop = box.scrollHeight;
}

async function loadChatMessages() {
  const id = state.chat.activeId || $('chat-conversation')?.value || 'chat-main';
  state.chat.activeId = id;
  const data = await api(`/api/v1/chat/messages?conversation_id=${encodeURIComponent(id)}`);
  state.chat.messages = data.messages || [];
  renderChatMessages();
}

async function newConversation() {
  state.chat.activeId = `chat-${Date.now().toString(36)}`;
  state.chat.messages = [];
  state.chat.context = null;
  renderChatConversations();
  renderChatMessages();
  $('chat-input')?.focus();
}

async function saveChatMessage() {
  const content = $('chat-input').value.trim();
  if (!content) return;
  if (!state.shell?.desktop?.allow_operations) return toast('Operations mode is required to save conversation turns.', true);
  const id = state.chat.activeId || `chat-${Date.now().toString(36)}`;
  state.chat.activeId = id;
  await api('/api/v1/chat/message', { method: 'POST', body: JSON.stringify({ conversation_id: id, role: 'user', content, title: content.slice(0, 42) }) });
  $('chat-input').value = '';
  await loadState();
  await loadChatMessages();
  toast('Turn saved locally. It was not promoted into long-term factual memory.');
}

async function buildChatContext() {
  const id = state.chat.activeId || $('chat-conversation')?.value;
  if (!id) return toast('Create a conversation first.', true);
  const budget = Number($('chat-token-budget').value || 2400);
  const query = (state.chat.messages || []).filter(x => x.role === 'user').at(-1)?.content || '';
  const pack = await api(`/api/v1/chat/context?conversation_id=${encodeURIComponent(id)}&token_budget=${budget}&query=${encodeURIComponent(query)}`);
  state.chat.context = pack;
  const pct = pack.full_history_tokens ? Math.round((pack.estimated_tokens_saved / pack.full_history_tokens) * 100) : 0;
  $('chat-context-metrics').innerHTML = `<span>Full history <b>${pack.full_history_tokens}</b></span><span>Pack <b>${pack.estimated_tokens}</b></span><span>Saved <b>${pct}%</b></span>`;
  setText('chat-history-status', `${pack.omitted_messages} old turns omitted from prompt`);
  toast(`Context pack ready • ${pack.estimated_tokens}/${pack.token_budget} estimated tokens`);
}

/* ---------- free intelligence discovery ---------- */
function renderFreeDiscovery() {
  const box = $('free-discovery-results');
  if (!box) return;
  const findings = state.freeDiscovery || [];
  box.innerHTML = findings.map((f, i) => {
    const models = f.models || [];
    let action = '';
    if (f.target_id === 'ollama-local' && ['available','detected'].includes(f.status) && models.length) action = `<button data-adopt-discovery="${i}">Adopt ${models.length} local model${models.length === 1 ? '' : 's'}</button>`;
    else if (f.third_party && ['auth_required','available','detected'].includes(f.status)) action = `<button data-connect-gateway="${i}">Connect this local gateway</button>`;
    return `<div class="discovery-item"><span><b>${escapeHtml(f.display_name)}</b><small>${escapeHtml(f.capacity_source)} • ${models.length} models${f.message ? ` • ${escapeHtml(f.message)}` : ''}</small></span><span class="discovery-status">${escapeHtml(f.status)}</span>${action}</div>`;
  }).join('') || '<div class="empty-state compact">No compatible free/local capacity was discovered.</div>';
  document.querySelectorAll('[data-adopt-discovery]').forEach(btn => btn.onclick = () => adoptDiscovery(Number(btn.dataset.adoptDiscovery)));
  document.querySelectorAll('[data-connect-gateway]').forEach(btn => btn.onclick = () => connectDiscoveredGateway(Number(btn.dataset.connectGateway)));
}

async function scanFreeCapacity() {
  if (!state.shell?.desktop?.allow_operations) return toast('Operations mode is required for a live discovery scan.', true);
  $('discover-free').disabled = true;
  try {
    const result = await api('/api/v1/ai/free-discovery/scan', { method: 'POST', body: JSON.stringify({ include_third_party: $('discover-third-party').checked, scan_connected: $('discover-connected').checked }) });
    state.freeDiscovery = result.findings || [];
    renderFreeDiscovery();
    const available = state.freeDiscovery.filter(x => ['available','models_discovered','detected','auth_required'].includes(x.status)).length;
    toast(`Discovery complete • ${available} usable or connectable source${available === 1 ? '' : 's'} found.`);
    return state.freeDiscovery;
  } catch (err) { toast(err.message, true); return []; }
  finally { $('discover-free').disabled = false; }
}

async function adoptDiscovery(index) {
  const finding = state.freeDiscovery[index];
  if (!finding) return;
  const result = await api('/api/v1/ai/free-discovery/adopt-local', { method: 'POST', body: JSON.stringify({ finding }) });
  toast(`Adopted ${result.count || 0} local model(s) into Dreamer routing.`);
  await loadState();
}

function connectDiscoveredGateway(index) {
  const finding = state.freeDiscovery[index];
  if (!finding) return;
  const chatEndpoint = String(finding.endpoint || '').replace(/\/models(?:\?.*)?$/, '/chat/completions');
  const connectionId = `${finding.target_id}-${Date.now().toString(36)}`;
  dialog(`Connect ${finding.display_name}`, `<div class="connection-warning">This is an optional user-run third-party gateway. W1 stores the gateway key in the native credential vault and does not import browser sessions.</div><input id="gateway-id" value="${escapeHtml(connectionId)}" placeholder="connection-id"><input id="gateway-label" value="${escapeHtml(finding.display_name)}" placeholder="Display name"><input id="gateway-endpoint" value="${escapeHtml(chatEndpoint)}" placeholder="Loopback chat endpoint"><input id="gateway-secret" type="password" autocomplete="off" placeholder="Gateway API key">`, async () => {
    const secret = $('gateway-secret').value;
    if (!secret) throw new Error('Gateway API key required');
    await api('/api/v1/ai/connection/api-key', { method: 'POST', body: JSON.stringify({ connection_id: $('gateway-id').value, provider_id: 'custom-openai-compatible', label: $('gateway-label').value, endpoint: $('gateway-endpoint').value, secret }) });
    $('gateway-secret').value = '';
    toast('Gateway connected. Discover its models, then register only the capacity you trust.');
    await loadState();
  });
}

/* ---------- local-first intelligence search ---------- */
function selectedIntelligenceSources() {
  const values = [];
  if ($('search-openrouter')?.checked) values.push('openrouter');
  if ($('search-github')?.checked) values.push('github');
  if ($('search-huggingface')?.checked) values.push('huggingface');
  return values;
}

function renderIntelligenceSearch() {
  const box = $('intelligence-search-results');
  if (!box) return;
  const candidates = state.intelligenceSearch?.candidates || [];
  const errors = state.intelligenceSearch?.errors || [];
  const query = state.intelligenceSearch?.query || '';
  const assessments = state.intelligenceAssessments || {};
  const activationRecords = new Map((state.shell?.intelligence_search?.activations || []).map(x => [x.candidate_id, x]));
  setText('intelligence-search-status', `${candidates.length} candidates${query ? ` for “${query}”` : ''}${errors.length ? ` • ${errors.length} source error(s)` : ''} • verified activation gate • local cache • no W1-owned server`);
  box.innerHTML = candidates.slice(0, 60).map(item => {
    const meta = item.metadata || {};
    const extra = item.source_id === 'github' ? `${meta.stars || 0}★${meta.license ? ` • ${meta.license}` : ''}` : item.source_id === 'huggingface' ? `${meta.downloads || 0} downloads${meta.license ? ` • ${meta.license}` : ''}` : item.model_id || '';
    const safeUrl = /^https:\/\/(openrouter\.ai|github\.com|huggingface\.co)\//.test(item.url || '') ? item.url : '#';
    const assessment = assessments[item.candidate_id];
    const activation = activationRecords.get(item.candidate_id);
    const gate = activation?.status === 'activated' ? `activated • ${activation.profile_id || ''}` : assessment?.state || 'not assessed';
    const action = activation?.status === 'activated'
      ? '<button disabled>Activated</button>'
      : assessment?.activation_allowed
        ? `<button data-intelligence-activate="${escapeHtml(item.candidate_id)}">Activate verified model</button>`
        : `<button data-intelligence-assess="${escapeHtml(item.candidate_id)}">Assess activation</button>`;
    return `<article class="intelligence-result"><div class="result-source">${escapeHtml(item.source_id)}</div><div class="result-main"><b>${escapeHtml(item.title)}</b><small>${escapeHtml(item.description || '')}</small><em>${escapeHtml(extra)}</em><em class="activation-gate">${escapeHtml(gate)}</em></div><div class="result-badges"><span>${escapeHtml(item.free_status)}</span><span>${escapeHtml(item.review_status)}</span><strong>${Math.round(Number(item.score || 0) * 100)}</strong></div><a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">Review source ↗</a>${action}</article>`;
  }).join('') || '<div class="empty-state compact">No matching candidates. Try a broader query or another source.</div>';
  document.querySelectorAll('[data-intelligence-assess]').forEach(el => el.onclick = async () => {
    try {
      const assessment = await api('/api/v1/ai/intelligence-search/assess', { method: 'POST', body: JSON.stringify({ candidate_id: el.dataset.intelligenceAssess }) });
      state.intelligenceAssessments = state.intelligenceAssessments || {};
      state.intelligenceAssessments[el.dataset.intelligenceAssess] = assessment;
      renderIntelligenceSearch();
      const message = assessment.activation_allowed ? 'Activation ready. Provider model discovery matches the catalog candidate.' : `Activation blocked • ${assessment.state}`;
      toast(message, !assessment.activation_allowed);
    } catch (err) { toast(err.message, true); }
  });
  document.querySelectorAll('[data-intelligence-activate]').forEach(el => el.onclick = async () => {
    try {
      const assessment = (state.intelligenceAssessments || {})[el.dataset.intelligenceActivate] || {};
      const result = await api('/api/v1/ai/intelligence-search/activate', { method: 'POST', body: JSON.stringify({ candidate_id: el.dataset.intelligenceActivate, connection_id: assessment.connection_id || null }) });
      toast(`Activated as ${result.record?.profile_id || 'model profile'} • third-party-free routing still requires explicit opt-in.`);
      await loadState();
    } catch (err) { toast(err.message, true); }
  });
}

async function runIntelligenceSearch() {
  if (!state.shell?.desktop?.allow_operations) return toast('Operations mode is required for live public-catalog search.', true);
  const query = $('intelligence-query').value.trim() || 'free llm api';
  const sources = selectedIntelligenceSources();
  if (!sources.length) return toast('Select at least one search source.', true);
  $('intelligence-search').disabled = true;
  setText('intelligence-search-status', 'Searching directly from this computer…');
  try {
    const report = await api('/api/v1/ai/intelligence-search/search', { method: 'POST', body: JSON.stringify({ query, sources, limit_per_source: 12 }) });
    state.intelligenceSearch = { candidates: report.candidates || [], errors: report.errors || [], query: report.query || query };
    renderIntelligenceSearch();
    toast(`Intelligence search complete • ${report.count || 0} candidate(s). Nothing was auto-routed.`);
  } catch (err) {
    setText('intelligence-search-status', `Search failed • ${err.message}`);
    toast(err.message, true);
  } finally { $('intelligence-search').disabled = false; }
}

/* ---------- AI connections ---------- */
function renderConnections() {
  const ai = state.ai || { catalog: [], connections: [] };
  const connections = ai.connections || [];
  const catalog = ai.catalog || [];
  const connectedProviders = new Set(connections.filter(x => x.enabled !== false).map(x => x.provider_id));
  $('connection-summary').innerHTML = `<span>${connections.length} connections</span><span>${(state.shell?.models || []).length} models</span><span>${(ai.teams || []).length} teams</span>`;
  $('provider-catalog').innerHTML = catalog.map(p => `
    <div class="provider-connect-card ${connectedProviders.has(p.provider_id) ? 'connected' : ''}">
      <div class="provider-glyph">${escapeHtml(p.icon_text || providerGlyph(p.provider_id))}</div>
      <div><b>${escapeHtml(p.display_name)}</b><small>${escapeHtml((p.supported_auth_modes || []).join(' / ') || 'local')}</small></div>
      <button data-connect-provider="${escapeHtml(p.provider_id)}">${connectedProviders.has(p.provider_id) ? 'Add another' : 'Connect'}</button>
    </div>`).join('');
  $('connection-list').innerHTML = connections.map(c => `
    <div class="ai-connection-item">
      <b>${escapeHtml(c.display_name)}</b><span>${escapeHtml(c.provider_id)} • ${escapeHtml(c.auth_mode)} • ${escapeHtml(c.status)} • ${(c.models || []).length} models</span>
      <div class="mini-actions"><button data-add-model="${escapeHtml(c.connection_id)}">+ Add model</button>${(c.discovered_models || []).length ? `<small>${c.discovered_models.length} discovered</small>` : ''}</div>
    </div>`).join('') || '<div class="empty-state compact">No AI connections yet.</div>';
  document.querySelectorAll('[data-connect-provider]').forEach(el => el.onclick = () => openProviderConnection(el.dataset.connectProvider));
  document.querySelectorAll('[data-add-model]').forEach(el => el.onclick = () => openAddModel(el.dataset.addModel));
  renderHomeConnections();
}

function openProviderConnection(providerId) {
  const p = (state.ai?.catalog || []).find(x => x.provider_id === providerId);
  if (!p) return;
  if (!state.shell?.desktop?.allow_operations) return toast('Restart Desktop with --allow-operations to add connections.', true);
  const baseId = `${providerId}-${Date.now().toString(36)}`;
  if (p.family === 'local') {
    dialog(`Connect ${p.display_name}`, `<div class="connection-warning">Local runtimes must stay on loopback. W1 does not require a W1-owned server.</div><input id="ai-c-id" value="${baseId}" placeholder="connection-id"><input id="ai-c-label" value="${escapeHtml(p.display_name)}" placeholder="Display name"><input id="ai-c-endpoint" value="${escapeHtml(p.default_endpoint || ['http:', '//127.0.0.1:11434/v1/chat/completions'].join(''))}" placeholder="Loopback endpoint">`, async () => {
      await api('/api/v1/ai/connection/local', { method: 'POST', body: JSON.stringify({ connection_id: $('ai-c-id').value, label: $('ai-c-label').value, endpoint: $('ai-c-endpoint').value }) });
      toast('Local AI connected.');
      await loadState();
    });
    return;
  }
  const supportsOAuth = (p.supported_auth_modes || []).includes('oauth');
  const oauthAccounts = (state.ai?.oauth_accounts || []).filter(a => a.provider_id === providerId && a.status === 'active');
  const canOAuth = supportsOAuth && oauthAccounts.length;
  const canNone = (p.supported_auth_modes || []).includes('none');
  const authOptions = `<select id="ai-auth-mode"><option value="api_key">API credential</option>${canOAuth ? '<option value="oauth">Existing OAuth account</option>' : ''}${canNone ? '<option value="none">No authentication</option>' : ''}</select>`;
  const accountOptions = canOAuth ? `<select id="ai-account">${oauthAccounts.map(a => `<option value="${escapeHtml(a.account_id)}">${escapeHtml(a.display_name || a.account_id)}</option>`).join('')}</select>` : '';
  const oauthNotice = supportsOAuth && !canOAuth ? '<div class="connection-warning"><b>OAuth-capable provider.</b><br>No active OAuth account exists in the W1 Credential Broker yet. API credential remains available; interactive OAuth authorization will be wired as a dedicated product flow.</div>' : '';
  const endpointInput = p.family === 'custom' ? `<input id="ai-c-endpoint" value="${escapeHtml(p.default_endpoint || '')}" placeholder="provider.example/v1/chat/completions">` : '';
  dialog(`Connect ${p.display_name}`, `<div class="connection-warning">W1 stores API/OAuth secrets in the OS-native Credential Broker, not in the AI Connections database. App subscriptions are not assumed to grant API access.</div>${oauthNotice}<input id="ai-c-id" value="${baseId}" placeholder="connection-id"><input id="ai-c-label" value="${escapeHtml(p.display_name)}" placeholder="Display name">${endpointInput}${authOptions}${accountOptions}<input id="ai-secret" type="password" autocomplete="off" placeholder="API credential (API credential mode only)">`, async () => {
    const mode = $('ai-auth-mode').value;
    const endpoint = $('ai-c-endpoint')?.value || undefined;
    if (mode === 'oauth') await api('/api/v1/ai/connection/account', { method: 'POST', body: JSON.stringify({ connection_id: $('ai-c-id').value, provider_id: providerId, account_id: $('ai-account').value, label: $('ai-c-label').value, endpoint }) });
    else if (mode === 'none') await api('/api/v1/ai/connection/custom', { method: 'POST', body: JSON.stringify({ connection_id: $('ai-c-id').value, label: $('ai-c-label').value, endpoint }) });
    else {
      const secret = $('ai-secret').value;
      if (!secret) throw new Error('API credential required');
      await api('/api/v1/ai/connection/api-key', { method: 'POST', body: JSON.stringify({ connection_id: $('ai-c-id').value, provider_id: providerId, label: $('ai-c-label').value, secret, endpoint }) });
      $('ai-secret').value = '';
    }
    toast('AI connection created.');
    await loadState();
  });
}

function openAddModel(connectionId) {
  if (!state.shell?.desktop?.allow_operations) return toast('Operations mode required.', true);
  const c = (state.ai?.connections || []).find(x => x.connection_id === connectionId);
  if (!c) return;
  const discovered = c.discovered_models || [];
  const suggested = discovered[0] || '';
  const defaultSource = c.provider_id === 'local-openai-compatible' ? 'local' : 'paid';
  dialog(`Add model · ${c.display_name}`, `
    <input id="ai-model-id" value="${connectionId}-model" placeholder="W1 model id">
    <input id="ai-model-name" value="${escapeHtml(suggested)}" placeholder="Provider model name">
    <input id="ai-model-label" placeholder="Display name">
    <input id="ai-model-roles" value="producer" placeholder="roles: producer,reviewer">
    <input id="ai-model-domains" value="general" placeholder="domains: coding,research">
    <label class="dialog-label">Capacity source</label>
    <select id="ai-cap-source"><option value="paid" ${defaultSource === 'paid' ? 'selected' : ''}>Paid API</option><option value="official_free">Official free tier</option><option value="subscription_entitlement">Verified subscription/API entitlement</option><option value="promotional">Promotional credits</option><option value="third_party_free">Third-party free pool</option><option value="local" ${defaultSource === 'local' ? 'selected' : ''}>Local model</option></select>
    <label class="dialog-label">Quality estimate (0–1)</label><input id="ai-quality" type="number" min="0" max="1" step="0.01" value="0.70">
    <label class="dialog-label">Terms / provenance</label><select id="ai-terms"><option value="official">Official</option><option value="verified_third_party">Verified third-party</option><option value="unknown">Unknown</option><option value="blocked">Blocked</option></select>
    <label><input id="ai-entitlement-verified" type="checkbox" style="width:auto"> API entitlement explicitly verified (only relevant to subscription entitlement)</label>
    <div class="two-col"><input id="ai-input-cost" type="number" min="0" step="0.0001" value="0" placeholder="Input $/1M"><input id="ai-output-cost" type="number" min="0" step="0.0001" value="0" placeholder="Output $/1M"></div>`, async () => {
      const roles = $('ai-model-roles').value.split(',').map(x => x.trim()).filter(Boolean);
      const domains = $('ai-model-domains').value.split(',').map(x => x.trim()).filter(Boolean);
      const source = $('ai-cap-source').value;
      const quality = Number($('ai-quality').value || 0.7);
      const terms = $('ai-terms').value;
      const inputCost = Number($('ai-input-cost').value || 0);
      const outputCost = Number($('ai-output-cost').value || 0);
      const verified = $('ai-entitlement-verified').checked;
      const modelId = $('ai-model-id').value;
      await api('/api/v1/ai/model', { method: 'POST', body: JSON.stringify({ connection_id: connectionId, model_id: modelId, model_name: $('ai-model-name').value, display_name: $('ai-model-label').value || undefined, roles, domains, capabilities: { general: quality, ...Object.fromEntries(domains.map(d => [d, quality])) }, metadata: { capacity_source: source, terms_status: terms, input_cost_per_million: inputCost, output_cost_per_million: outputCost, api_entitlement_verified: verified } }) });
      await api('/api/v1/ai/capacity/observe', { method: 'POST', body: JSON.stringify({ model_id: modelId, source, quota_state: source === 'local' ? 'unlimited' : 'unknown', input_cost_per_million: inputCost, output_cost_per_million: outputCost, terms_status: terms, entitlement_verified: verified }) });
      toast('Model registered with Adaptive Capacity Router.');
      await loadState();
    });
}

/* ---------- capacity, teams & dashboard ---------- */
function renderCapacity() {
  const c = state.capacity || { observations: [], count: 0 };
  const observations = c.observations || [];
  const free = observations.filter(x => ['official_free', 'subscription_entitlement', 'promotional', 'third_party_free', 'local'].includes(x.source) && x.quota_state !== 'exhausted').length;
  $('capacity-summary').innerHTML = `<b>Collaboration-first routing</b><br>${c.count || 0} quota observations • ${free} free/local candidates<br><small>Consumer app subscriptions never imply API entitlement. Third-party free pools are opt-in.</small>`;
  setText('free-candidate-count', free);
  $('capacity-observations').innerHTML = observations.slice(0, 8).map(o => `<div class="capacity-observation"><span><b>${escapeHtml(o.model_id)}</b><small>${escapeHtml(o.source)} • ${escapeHtml(o.quota_state)}</small></span><small>${formatTokens(o.remaining_tokens)} tokens</small></div>`).join('') || '<div class="empty-state compact">No capacity observations yet.</div>';
}

function renderPortfolios() {
  const teams = state.ai?.teams || [];
  const portfolios = state.shell?.portfolios || [];
  const adaptive = portfolios.filter(p => p.metadata?.adaptive_capacity_router);
  const rows = [
    ...teams.map(t => ({ id: t.team_id, title: t.display_name || t.team_id, mode: t.mode, members: t.members || [], raw: null, source: 'manual' })),
    ...adaptive.map(p => ({ id: p.portfolio_id, title: p.display_name || p.portfolio_id, mode: p.strategy, members: p.members || [], raw: p, source: 'adaptive' })),
  ];
  $('portfolio-list').innerHTML = rows.map(p => `<div class="portfolio-item"><span><b>${p.source === 'adaptive' ? '⚡ ' : ''}${escapeHtml(p.title)}</b><small>${escapeHtml(p.mode)} • ${p.members.length} routed roles${p.raw?.metadata?.routing_mode ? ` • ${escapeHtml(p.raw.metadata.routing_mode)}` : ''}</small></span><button data-select-portfolio="${escapeHtml(p.id)}">Inspect</button></div>`).join('') || '<div class="empty-state compact">No AI team exists yet.</div>';
  document.querySelectorAll('[data-select-portfolio]').forEach(btn => btn.onclick = () => {
    state.ui.selectedPortfolioId = btn.dataset.selectPortfolio;
    renderTeamFlow();
    renderTopTeam();
  });
  renderTeamFlow();
}

function flowMembers() {
  if (state.lastPlan?.portfolio?.members) return state.lastPlan.portfolio.members;
  return selectedPortfolio()?.members || [];
}

function renderTeamFlow() {
  const members = flowMembers();
  const strategy = state.lastPlan?.portfolio?.strategy || selectedPortfolio()?.strategy || 'cooperative';
  const primary = members.filter(m => !m.fallback_for);
  const nodes = primary.length ? primary : members;
  const html = nodes.map(m => {
    const model = modelById(m.model_id) || {};
    const provider = model.provider_id || m.provider_id || 'AI';
    return `<div class="flow-node"><span class="provider-logo">${escapeHtml(providerGlyph(provider, provider))}</span><span><b>${escapeHtml((m.role || 'member').replaceAll('_', ' '))}</b><small>${escapeHtml(model.display_name || m.model_id)}</small></span></div>`;
  }).join('') || '<div class="empty-state compact">Create an AI team to visualize role handoffs.</div>';
  $('team-flow').innerHTML = html;
  $('team-flow-page').innerHTML = html;
  setText('flow-mode-label', strategy.replaceAll('_', ' '));
  setText('flow-title', strategy.includes('challenge') ? 'Challenge + Synthesis' : strategy.replaceAll('_', ' '));
  renderChatContext();
}

function renderTopTeam() {
  const p = selectedPortfolio();
  setText('top-team-name', p ? (p.display_name || p.portfolio_id || 'Adaptive routing') : 'Adaptive routing');
}

function renderActiveIntelligence() {
  const models = state.shell?.models || [];
  $('active-intelligence').innerHTML = models.slice(0, 6).map(m => `<div class="model-row"><span class="model-badge">${escapeHtml(providerGlyph(m.provider_id, m.provider_id))}</span><span><b>${escapeHtml(m.display_name || m.model_id)}</b><small>${escapeHtml(m.provider_id)} • ${escapeHtml((m.roles || []).slice(0, 2).join(', ') || 'general')}</small></span><span class="model-status">● Ready</span></div>`).join('') || '<div class="empty-state compact">Connect a provider and register a model to begin.</div>';
}

function renderHomeConnections() {
  const connections = state.ai?.connections || [];
  const html = connections.slice(0, 5).map(c => `<div class="connection-mini"><span class="provider-logo">${escapeHtml(providerGlyph(c.provider_id, c.provider_id))}</span><span><b>${escapeHtml(c.display_name)}</b><small>${escapeHtml(c.status)} • ${(c.models || []).length} models</small></span></div>`).join('');
  $('home-connections').innerHTML = html + `<button class="connection-mini add-connection-mini" data-go="connections"><span>＋ Add provider</span></button>`;
  wireGoButtons();
}

function renderRecentActivity() {
  const items = state.artifacts.slice(0, 5);
  $('recent-activity').innerHTML = items.map(a => `<div class="activity-item"><i class="activity-dot"></i><span><b>${escapeHtml(a.title)}</b><small>${escapeHtml(a.status)} • v${a.current_version} • ${escapeHtml(a.path)}</small></span></div>`).join('') || '<div class="empty-state compact">No artifact activity yet.</div>';
}

function renderChatContext() {
  const p = selectedPortfolio();
  const models = state.shell?.models || [];
  $('chat-team-context').innerHTML = `<div class="context-item"><small>Selected team</small><b>${escapeHtml(p?.display_name || p?.portfolio_id || 'Adaptive routing')}</b></div><div class="context-item"><small>Available models</small><b>${models.length}</b></div><div class="context-item"><small>Routing policy</small><b>${escapeHtml(routingLabel(state.ui.routingMode))}</b></div><div class="context-item"><small>Quality floor</small><b>${state.ui.qualityFloor.toFixed(2)}</b></div>`;
}

function renderPolicyControls() {
  setText('cost-policy-label', routingLabel(state.ui.routingMode));
  setText('routing-chip', routingLabel(state.ui.routingMode));
  setText('routing-policy-short', state.ui.routingMode === 'maximum_free' ? 'Free-first' : state.ui.routingMode === 'maximum_quality' ? 'Quality' : 'Balanced');
  setText('quality-floor-label', state.ui.qualityFloor.toFixed(2));
  setText('insight-quality', state.ui.qualityFloor.toFixed(2));
  setText('quality-impact', state.ui.qualityFloor >= .85 ? 'Maximum' : state.ui.qualityFloor >= .7 ? 'High' : 'Standard');
  renderChatContext();
}

function renderOperationsState() {
  const enabled = !!state.shell?.desktop?.allow_operations;
  setText('insight-operations', enabled ? 'Enabled' : 'Read only');
  setText('computer-operations', enabled ? 'Enabled' : 'Read only');
  setText('computer-backend', state.shell?.desktop?.backend || '—');
  setText('task-ops-chip', enabled ? 'Operations enabled' : 'Read only');
  $('mode-pill').textContent = enabled ? 'OPERATIONS ENABLED' : 'READ ONLY';
  $('mode-pill').style.color = enabled ? 'var(--success)' : 'var(--warn)';
}

async function planHomeTask() {
  const prompt = $('home-prompt').value.trim();
  if (!prompt) return toast('Describe the goal first.', true);
  if (!state.shell?.desktop?.allow_operations) return toast('Task planning writes a portfolio. Restart Desktop with --allow-operations first.', true);
  if (!(state.shell?.models || []).length && $('auto-discover-free')?.checked) {
    const findings = await scanFreeCapacity();
    const local = findings.find(x => x.target_id === 'ollama-local' && ['available','detected'].includes(x.status) && (x.models || []).length);
    if (local) {
      await api('/api/v1/ai/free-discovery/adopt-local', { method: 'POST', body: JSON.stringify({ finding: local }) });
      await loadState();
    }
  }
  if (!(state.shell?.models || []).length) { showView('connections'); return toast('No eligible model is available yet. Free discovery results are shown in Connections.', true); }
  const teamMode = modeToTeamMode(state.ui.homeMode);
  const domain = inferDomain(prompt);
  try {
    $('home-plan').disabled = true;
    const result = await api('/api/v1/ai/capacity/plan', { method: 'POST', body: JSON.stringify({
      plan_id: `home-${Date.now().toString(36)}`,
      display_name: prompt.slice(0, 56),
      team_mode: teamMode,
      task: { task_id: `desktop-${Date.now()}`, title: prompt, phase: 'execution', role: 'producer', expected_output_type: 'text', domains: [domain] },
      policy: { mode: state.ui.routingMode, quality_floor: state.ui.qualityFloor, max_task_cost_usd: null, estimated_input_tokens: Math.max(1000, Math.round(prompt.length / 4) + 1200), estimated_output_tokens: 1800, producer_count: teamMode === 'solo' || teamMode === 'fallback' ? 1 : 2, fallback_per_role: 1, allow_paid: true, allow_local: true, allow_third_party_free: false, require_known_terms: true },
      save_portfolio: true,
    }) });
    state.lastPlan = result.capacity_plan;
    setText('insight-cost', `${formatMoney(state.lastPlan.estimated_cost_usd)} est.`);
    state.ui.selectedPortfolioId = state.lastPlan.portfolio.portfolio_id;
    toast(`Team planned • ${state.lastPlan.portfolio.strategy} • ${formatMoney(state.lastPlan.estimated_cost_usd)} estimated`);
    await loadState();
    renderTeamFlow();
  } catch (err) {
    toast(err.message, true);
  } finally { $('home-plan').disabled = false; }
}

function createAdaptiveTeam() {
  if (!state.shell?.desktop?.allow_operations) return toast('Operations mode required.', true);
  const models = state.shell?.models || [];
  if (!models.length) return toast('Add at least one model first.', true);
  dialog('Adaptive / Dreamer Team', `<input id="adaptive-name" value="Dreamer Team" placeholder="Team name"><input id="adaptive-domain" value="general" placeholder="Task domain: coding,research,general"><select id="adaptive-team-mode"><option value="challenge">Challenge + Synthesis</option><option value="verify">Verified Synthesis</option><option value="parallel">Parallel</option><option value="fallback">Fallback</option><option value="solo">Solo</option></select><select id="adaptive-routing-mode"><option value="balanced">Balanced</option><option value="maximum_free">Maximum Free</option><option value="maximum_quality">Maximum Quality</option><option value="local_only">Local Only</option><option value="custom_budget">Custom Budget</option></select><label class="dialog-label">Quality floor</label><input id="adaptive-quality" type="number" min="0" max="1" step="0.01" value="${state.ui.qualityFloor.toFixed(2)}"><label class="dialog-label">Max direct task cost (USD, blank = no cap)</label><input id="adaptive-budget" type="number" min="0" step="0.01" placeholder="0.25"><label><input id="adaptive-third-party" type="checkbox" style="width:auto"> Allow verified third-party free providers</label><div class="connection-warning">W1 preserves the collaboration topology. Quota exhaustion changes which model fills a role; it does not silently remove Challenger, Verifier or Synthesizer roles.</div>`, async () => {
    const mode = $('adaptive-team-mode').value;
    const routing = $('adaptive-routing-mode').value;
    const domain = $('adaptive-domain').value.trim() || 'general';
    const budget = $('adaptive-budget').value.trim();
    const result = await api('/api/v1/ai/capacity/plan', { method: 'POST', body: JSON.stringify({ plan_id: `adaptive-${Date.now().toString(36)}`, display_name: $('adaptive-name').value || 'Adaptive W1 Team', team_mode: mode, task: { task_id: `desktop-${Date.now()}`, title: 'Desktop adaptive task', phase: 'execution', role: 'producer', expected_output_type: 'text', domains: [domain] }, policy: { mode: routing, quality_floor: Number($('adaptive-quality').value || 0.65), max_task_cost_usd: budget === '' ? null : Number(budget), producer_count: mode === 'solo' || mode === 'fallback' ? 1 : 2, fallback_per_role: 1, allow_paid: true, allow_local: true, allow_third_party_free: $('adaptive-third-party').checked, require_known_terms: true }, save_portfolio: true }) });
    state.lastPlan = result.capacity_plan;
    state.ui.selectedPortfolioId = result.capacity_plan.portfolio.portfolio_id;
    setText('insight-cost', `${formatMoney(result.capacity_plan.estimated_cost_usd)} est.`);
    toast(`Adaptive team ready • ${result.capacity_plan.portfolio.strategy} • ${formatMoney(result.capacity_plan.estimated_cost_usd)} est.`);
    await loadState();
  });
}

function createTeam() {
  if (!state.shell?.desktop?.allow_operations) return toast('Operations mode required.', true);
  const models = state.shell?.models || [];
  if (!models.length) return toast('Add at least one model to Connections.', true);
  const rows = models.map(m => `<div class="team-role-row"><label><input type="checkbox" data-team-model="${escapeHtml(m.model_id)}"> ${escapeHtml(m.display_name || m.model_id)} <small>${escapeHtml(m.provider_id)}</small></label><select data-team-role="${escapeHtml(m.model_id)}"><option value="producer">Producer</option><option value="specialist">Specialist</option><option value="reviewer">Reviewer</option><option value="verifier">Verifier</option><option value="challenger">Challenger</option><option value="synthesizer">Synthesizer</option></select></div>`).join('');
  dialog('Build your AI team', `<input id="team-id" value="team-${Date.now().toString(36)}" placeholder="team-id"><input id="team-name" value="My AI Team" placeholder="Team name"><div class="ai-mode-grid">${['solo', 'fallback', 'parallel', 'verify', 'challenge'].map((m, i) => `<label><input type="radio" name="team-mode" value="${m}" ${i === 2 ? 'checked' : ''}>${m}</label>`).join('')}</div><div class="connection-warning">Challenge = producers → challenger sees their outputs → synthesizer sees candidates and objections. Verify adds an independent verifier.</div><div class="team-role-grid">${rows}</div>`, async () => {
    const members = [...document.querySelectorAll('[data-team-model]:checked')].map(el => {
      const roleSelect = [...document.querySelectorAll('[data-team-role]')].find(s => s.dataset.teamRole === el.dataset.teamModel);
      return { model_id: el.dataset.teamModel, role: roleSelect?.value || 'producer', priority: 100, required: false };
    });
    if (!members.length) throw new Error('Select at least one model');
    const mode = document.querySelector('input[name="team-mode"]:checked').value;
    const result = await api('/api/v1/ai/team', { method: 'POST', body: JSON.stringify({ team: { team_id: $('team-id').value, display_name: $('team-name').value, mode, members, max_parallel: 4, minimum_successful_producers: 1, cost_preference: 'balanced', privacy_preference: 'balanced' } }) });
    state.ui.selectedPortfolioId = result.compiled_portfolio?.portfolio_id || null;
    toast('AI Team created.');
    await loadState();
  });
}

/* ---------- terminal / git ---------- */
function parseCommand(value) {
  const parts = value.match(/(?:[^\s"]+|"[^"]*")+/g) || [];
  return parts.map(x => x.startsWith('"') ? x.slice(1, -1) : x);
}

async function terminal(mode) {
  const argv = parseCommand($('terminal-input').value);
  if (!argv.length) return;
  try {
    const path = mode === 'plan' ? '/api/v1/terminal/plan' : '/api/v1/terminal/run';
    const body = { action_id: `desktop-${Date.now()}`, argv, approve: mode === 'run', issued_by: 'local-owner' };
    const data = await api(path, { method: 'POST', body: JSON.stringify(body) });
    $('terminal-output').textContent = mode === 'plan' ? JSON.stringify(data, null, 2) : `${data.stdout || ''}${data.stderr ? '\n' + data.stderr : ''}\n[${data.status} exit=${data.exit_code}]`;
  } catch (err) { $('terminal-output').textContent = JSON.stringify(err.body || { error: err.message }, null, 2); }
}

async function loadDiff() {
  const data = await api(`/api/v1/git/diff${state.file ? `?path=${encodeURIComponent(state.file.path)}` : ''}`);
  $('git-diff').textContent = data.diff || data.error || 'No changes.';
}

/* ---------- state & rendering ---------- */
async function loadState() {
  const data = await api('/api/v1/state');
  state.shell = data;
  state.ai = data.ai_connections || { catalog: [], connections: [], teams: [], oauth_accounts: [] };
  state.capacity = data.adaptive_capacity || { observations: [], count: 0 };
  state.chat.conversations = data.conversation_context?.conversations || [];
  state.intelligenceSearch.candidates = data.intelligence_search?.candidates || state.intelligenceSearch.candidates || [];
  state.artifacts = data.artifacts || [];
  state.officeArtifacts = data.office_artifacts || [];
  setText('workspace-name', data.workspace.name);
  setText('connection-count', (state.ai.connections || []).length);
  setText('model-count', data.models.length);
  setText('portfolio-count', (state.ai.teams || []).length || data.portfolios.length);
  setText('artifact-count', data.artifacts.length);
  setText('office-count', state.officeArtifacts.length);
  setText('shell-backend', data.desktop.backend);
  setText('settings-version', data.desktop.version);
  renderArtifacts();
  renderOfficeArtifacts();
  renderConnections();
  renderIntelligenceSearch();
  renderPortfolios();
  renderCapacity();
  renderActiveIntelligence();
  renderRecentActivity();
  renderPolicyControls();
  renderOperationsState();
  renderTopTeam();
  renderChatConversations();
}

function wireGoButtons() {
  document.querySelectorAll('[data-go]').forEach(btn => btn.onclick = () => showView(btn.dataset.go));
}

function wire() {
  document.querySelectorAll('.nav-item[data-view]').forEach(btn => btn.onclick = () => showView(btn.dataset.view));
  wireGoButtons();
  $('theme-toggle').onclick = toggleTheme;
  document.querySelectorAll('[data-set-theme]').forEach(btn => btn.onclick = () => applyTheme(btn.dataset.setTheme));
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change', () => { if (state.ui.themePreference === 'system') applyTheme('system', false); });
  $('workspace-selector').onclick = () => showView('files');
  $('team-selector').onclick = () => showView('teams');

  document.querySelectorAll('[data-home-mode]').forEach(btn => btn.onclick = () => {
    state.ui.homeMode = btn.dataset.homeMode;
    document.querySelectorAll('[data-home-mode]').forEach(x => x.classList.toggle('active', x === btn));
  });
  $('cost-policy-slider').oninput = e => { state.ui.routingMode = routingFromSlider(e.target.value); renderPolicyControls(); };
  $('quality-floor-slider').oninput = e => { state.ui.qualityFloor = Number(e.target.value) / 100; renderPolicyControls(); };
  $('home-plan').onclick = planHomeTask;
  $('home-prompt').addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') planHomeTask(); });

  $('editor').addEventListener('input', () => { state.dirty = true; $('file-state').textContent = '● modified'; updateLines(); });
  $('editor').addEventListener('scroll', () => { $('line-numbers').scrollTop = $('editor').scrollTop; });
  $('collapse-all').onclick = () => document.querySelectorAll('.tree-children').forEach(x => x.style.display = 'none');
  $('new-draft').onclick = createDraft;
  $('office-create').onclick = createOfficeFromEditor;
  $('office-review').onclick = reviewOffice;
  $('office-export').onclick = exportOffice;
  $('save-draft').onclick = saveVersion;
  $('submit-review').onclick = submitReview;
  $('publish-artifact').onclick = publishArtifact;
  $('preview-toggle').onclick = () => state.preview ? showEditor() : showPreview();
  $('diff-toggle').onclick = async () => { await loadDiff(); showView('developer'); };
  $('load-diff').onclick = loadDiff;
  $('terminal-plan').onclick = () => terminal('plan');
  $('terminal-run').onclick = () => terminal('run');
  $('refresh').onclick = init;
  $('reload-ai').onclick = loadState;
  $('discover-free').onclick = scanFreeCapacity;
  $('intelligence-search').onclick = runIntelligenceSearch;
  $('intelligence-query').addEventListener('keydown', e => { if (e.key === 'Enter') runIntelligenceSearch(); });
  $('chat-new').onclick = newConversation;
  $('chat-save').onclick = saveChatMessage;
  $('chat-input').addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') saveChatMessage(); });
  $('chat-conversation').onchange = async e => { state.chat.activeId = e.target.value; await loadChatMessages(); };
  $('chat-token-budget').oninput = e => setText('chat-token-budget-label', e.target.value);
  $('chat-build-context').onclick = buildChatContext;
  $('new-team').onclick = createTeam;
  $('new-adaptive-team').onclick = createAdaptiveTeam;
  $('dialog-cancel').onclick = () => $('dialog').classList.add('hidden');
  $('dialog').addEventListener('click', e => { if (e.target === $('dialog')) $('dialog').classList.add('hidden'); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') $('dialog').classList.add('hidden'); });
}

let wired = false;
async function init() {
  applyTheme(state.ui.themePreference, false);
  try {
    if (!wired) { wire(); wired = true; }
    await Promise.all([loadState(), loadTree()]);
    if (state.chat.conversations.length) await loadChatMessages();
    $('connection-dot').style.background = 'var(--success)';
    setText('runtime-label', 'Ready');
  } catch (err) {
    toast(err.message, true);
    $('connection-dot').style.background = 'var(--danger)';
    setText('runtime-label', 'Needs attention');
  }
}

if ('serviceWorker' in navigator) navigator.serviceWorker.register('/assets/sw.js').catch(() => {});
init();
