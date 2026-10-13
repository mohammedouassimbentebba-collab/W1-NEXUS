/**
 * W1™ NEXUS STUDIO — Client Controller (app.js)
 * 
 * Manages reactive state, Server-Sent Events (SSE) telemetry, dynamic
 * SVG DAG rendering, and live benchmark metrics.
 */

document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  initSSE();
  loadBenchmarks();
  loadDAG();
  initSandboxRunner();
});

// -----------------------------------------------------------------------------
// 1. Tab Navigation
// -----------------------------------------------------------------------------
function initTabs() {
  const tabBtns = document.querySelectorAll('.tab-btn');
  const tabPanes = document.querySelectorAll('.tab-pane');

  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const target = btn.getAttribute('data-tab');

      tabBtns.forEach(b => b.classList.remove('active'));
      tabPanes.forEach(p => p.classList.remove('active'));

      btn.classList.add('active');
      const pane = document.getElementById(`pane-${target}`);
      if (pane) pane.classList.add('active');

      if (target === 'dag') {
        loadDAG();
      }
    });
  });
}

// -----------------------------------------------------------------------------
// 2. Real-Time Telemetry via Server-Sent Events (SSE)
// -----------------------------------------------------------------------------
function initSSE() {
  const ssePill = document.getElementById('sse-pill');

  try {
    const evtSource = new EventSource('/api/events');

    evtSource.addEventListener('connected', (e) => {
      console.log('[SSE Connected]:', e.data);
      if (ssePill) ssePill.classList.add('connected');
    });

    evtSource.addEventListener('dispatch', (e) => {
      const data = JSON.parse(e.data);
      appendSandboxLog(`\n[EVENT DISPATCH]: ${data.command} (${new Date(data.timestamp).toLocaleTimeString()})`);
    });

    evtSource.addEventListener('execution_result', (e) => {
      const data = JSON.parse(e.data);
      appendSandboxLog(`[EXECUTION RESULT]: Exit=${data.exit_code} | Duration=${data.elapsed_ms}ms | Backend=${data.backend}\n${data.stdout || ''}`);
    });

    evtSource.onerror = () => {
      console.warn('[SSE Connection error]');
    };
  } catch (err) {
    console.error('[SSE Init failed]:', err);
  }
}

// -----------------------------------------------------------------------------
// 3. Dynamic Interactive DAG Visualizer
// -----------------------------------------------------------------------------
async function loadDAG() {
  const svg = document.getElementById('dag-svg');
  if (!svg) return;

  try {
    const res = await fetch('/api/dag');
    const data = await res.json();

    renderSVG_DAG(svg, data);
  } catch (err) {
    console.error('[Failed to load DAG]:', err);
  }
}

function renderSVG_DAG(svg, data) {
  svg.innerHTML = ''; // Clear existing elements

  const nodes = data.nodes || [];
  const edges = data.edges || [];

  const width = svg.clientWidth || 900;
  const height = 450;
  const nodeCount = nodes.length;
  const spacingX = width / (nodeCount + 1);

  // Position nodes linearly across pipeline with vertical offsets
  const nodePositions = {};
  nodes.forEach((n, idx) => {
    const x = spacingX * (idx + 1);
    const y = height / 2 + (idx % 2 === 0 ? -40 : 40);
    nodePositions[n.id] = { x, y, label: n.label, status: n.status, hash: n.hash };
  });

  // Draw Edges (with glowing arrows)
  edges.forEach(edge => {
    const from = nodePositions[edge.from];
    const to = nodePositions[edge.to];
    if (from && to) {
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', from.x);
      line.setAttribute('y1', from.y);
      line.setAttribute('x2', to.x);
      line.setAttribute('y2', to.y);
      line.setAttribute('stroke', '#38bdf8');
      line.setAttribute('stroke-width', '2');
      line.setAttribute('stroke-dasharray', '4,4');
      line.setAttribute('opacity', '0.6');
      svg.appendChild(line);
    }
  });

  // Draw Nodes
  nodes.forEach(n => {
    const pos = nodePositions[n.id];
    if (!pos) return;

    // Group
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.style.cursor = 'pointer';

    // Outer Glow Ring
    const circleGlow = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    circleGlow.setAttribute('cx', pos.x);
    circleGlow.setAttribute('cy', pos.y);
    circleGlow.setAttribute('r', '32');
    circleGlow.setAttribute('fill', 'rgba(56, 189, 248, 0.12)');
    circleGlow.setAttribute('stroke', '#38bdf8');
    circleGlow.setAttribute('stroke-width', '1.5');
    g.appendChild(circleGlow);

    // Inner Circle
    const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    circle.setAttribute('cx', pos.x);
    circle.setAttribute('cy', pos.y);
    circle.setAttribute('r', '22');
    circle.setAttribute('fill', '#0f172a');
    circle.setAttribute('stroke', n.status === 'SEALED' ? '#10b981' : '#38bdf8');
    circle.setAttribute('stroke-width', '2');
    g.appendChild(circle);

    // Node Text
    const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    text.setAttribute('x', pos.x);
    text.setAttribute('y', pos.y + 50);
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('fill', '#f8fafc');
    text.setAttribute('font-size', '12px');
    text.setAttribute('font-family', 'Plus Jakarta Sans, sans-serif');
    text.setAttribute('font-weight', '600');
    text.textContent = pos.label;
    g.appendChild(text);

    // Hash text
    const hashText = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    hashText.setAttribute('x', pos.x);
    hashText.setAttribute('y', pos.y + 66);
    hashText.setAttribute('text-anchor', 'middle');
    hashText.setAttribute('fill', '#64748b');
    hashText.setAttribute('font-size', '10px');
    hashText.setAttribute('font-family', 'JetBrains Mono, monospace');
    hashText.textContent = pos.hash;
    g.appendChild(hashText);

    svg.appendChild(g);
  });
}

// -----------------------------------------------------------------------------
// 4. Benchmark Matrix Population
// -----------------------------------------------------------------------------
async function loadBenchmarks() {
  const tbody = document.getElementById('benchmark-tbody');
  if (!tbody) return;

  try {
    const res = await fetch('/api/benchmarks');
    const data = await res.json();

    const modeA = data.mode_a_standalone || [];
    const modeB = data.mode_b_governed || [];
    const modeC = data.mode_c_collaborative || [];

    const rows = [];

    // Map tasks
    const tasks = [
      { id: 'NEXUS-DIST-01', name: 'Raft Consensus & Split-Brain' },
      { id: 'NEXUS-CONC-02', name: 'Lock-Free ABA & Rust EBR' },
      { id: 'NEXUS-DAG-03', name: 'Cycle Detection & Rollback' },
    ];

    tasks.forEach(t => {
      const recA = modeA.find(r => r.task_id === t.id) || {};
      const recB = modeB.find(r => r.task_id === t.id) || {};
      const recC = modeC.find(r => r.task_id === t.id) || {};

      rows.push(createBenchRow(t.name, 'Mode A (Raw)', recA.score, recA.latency_ms, recA.total_tokens, recA.merkle_blake3));
      rows.push(createBenchRow(t.name, 'Mode B (Governed)', recB.score, recB.latency_ms, recB.total_tokens, recB.combined_root_blake3));
      rows.push(createBenchRow(t.name, 'Mode C (Swarm)', recC.score, recC.latency_ms, recC.total_tokens, recC.consensus_merkle_root_blake3));
    });

    tbody.innerHTML = rows.join('');
  } catch (err) {
    console.error('[Benchmark Load Error]:', err);
  }
}

function createBenchRow(taskName, mode, score, latency, tokens, hash) {
  const badgeClass = score >= 90 ? 'badge-success' : (score > 0 ? 'badge-warning' : 'badge-danger');
  const scoreText = (score !== undefined) ? `${score}%` : 'N/A';
  const latencyText = latency ? `${latency.toFixed(0)} ms` : '—';
  const tokensText = tokens ? tokens.toLocaleString() : '—';
  const hashText = hash ? `${hash.substring(0, 16)}...` : '—';

  return `
    <tr>
      <td><strong>${taskName}</strong></td>
      <td>${mode}</td>
      <td><span class="badge ${badgeClass}">${scoreText}</span></td>
      <td>${latencyText}</td>
      <td>${tokensText}</td>
      <td><code>${hashText}</code></td>
    </tr>
  `;
}

// -----------------------------------------------------------------------------
// 5. C Sandbox Terminal Controller
// -----------------------------------------------------------------------------
function initSandboxRunner() {
  const btn = document.getElementById('btn-sandbox-run');
  const input = document.getElementById('sandbox-input');

  if (!btn || !input) return;

  async function run() {
    const cmd = input.value.trim();
    if (!cmd) return;

    appendSandboxLog(`\n$ ${cmd}`);
    btn.disabled = true;
    btn.textContent = 'Executing...';

    try {
      const metaToken = document.querySelector('meta[name="nexus-auth-token"]');
      const token = metaToken ? metaToken.getAttribute('content') : '';
      const headers = { 'Content-Type': 'application/json' };
      if (token) {
        headers['X-Nexus-Auth-Token'] = token;
      }

      const res = await fetch('/api/execute', {
        method: 'POST',
        headers,
        body: JSON.stringify({ command: cmd }),
      });
      const data = await res.json();
      if (!res.ok) {
        appendSandboxLog(`[SECURITY/HTTP ${res.status}]: ${data.error || 'Execution rejected'}`);
        return;
      }
      appendSandboxLog(`[Exit Code: ${data.exit_code} | Duration: ${data.elapsed_ms}ms | Backend: ${data.backend}]\n${data.stdout || ''}${data.stderr || ''}`);
    } catch (err) {
      appendSandboxLog(`[ERROR]: ${err.message}`);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Execute Sandboxed';
    }
  }

  btn.addEventListener('click', run);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') run();
  });
}

function appendSandboxLog(msg) {
  const logBox = document.getElementById('sandbox-log');
  if (logBox) {
    logBox.textContent += msg + '\n';
    logBox.scrollTop = logBox.scrollHeight;
  }
}
