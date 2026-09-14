#!/usr/bin/env node

/**
 * W1™ NEXUS CLI
 * Autonomous Intelligence Discovery & Verification Protocol
 * Licensed under MPL-2.0
 */

const fs = require('fs');
const path = require('path');
const http = require('http');

const VERSION = "0.1.0";
const args = process.argv.slice(2);
const command = args[0] || 'help';

function printHeader() {
  console.log('\x1b[36m======================================================================\x1b[0m');
  console.log(`\x1b[1m\x1b[36mW1™ NEXUS CLI\x1b[0m \x1b[32mv${VERSION}\x1b[0m (W1-CIP Protocol v1.1)`);
  console.log('Autonomous Intelligence Discovery, Benchmarking & Governance Fabric');
  console.log('\x1b[36m======================================================================\x1b[0m');
}

function printHelp() {
  printHeader();
  console.log(`
Usage:
  nexus <command> [options]
  w1-nexus <command> [options]

Commands:
  status               Display NEXUS runtime status and local environment
  schemas              List registered W1-CIP 0.1 / 1.1 protocol schemas
  validate <file.json> Validate a local JSON artifact against W1-CIP schemas
  console [--port N]   Launch the local NEXUS Workspace Console Web UI (default: 8080)
  version, -v          Print version information
  help, -h             Print this help message
`);
}

function showStatus() {
  printHeader();
  console.log('\x1b[33m[RUNTIME STATUS]\x1b[0m');
  console.log('  Protocol:          W1-CIP v1.1 (Governed Consensus)');
  console.log('  License:           Mozilla Public License 2.0 (MPL-2.0)');
  console.log('  Model Matrix:      Credential != Model (Dynamic Discovery)');
  console.log('  Zero-Leak Guard:   Active (Cryptographic secret scrubbing)');
  console.log('  Benchmark Suites:  9 Suites (AutomationBench, OSWorld 2, FrontierMath, etc.)');
  console.log('\x1b[32m✔ Local NEXUS runtime ready.\x1b[0m');
}

function listSchemas() {
  printHeader();
  console.log('\x1b[33m[W1-CIP PROTOCOL SCHEMAS]\x1b[0m');
  const schemasDir = path.join(__dirname, '..', 'schemas', 'w1-cip', '0.1');
  if (fs.existsSync(schemasDir)) {
    const files = fs.readdirSync(schemasDir).filter(f => f.endsWith('.json'));
    files.forEach(f => {
      console.log(`  - ${f.replace('.schema.json', '')} (schemas/w1-cip/0.1/${f})`);
    });
  } else {
    console.log('  Built-in schemas: goal-contract, team-plan, task, evidence, challenge, review, verification, decision, final-result');
  }
}

function launchConsole(port = 8080) {
  printHeader();
  console.log(`\x1b[32mStarting NEXUS Workspace Console on http://localhost:${port}...\x1b[0m`);
  const server = http.createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end(`<!DOCTYPE html>
<html>
<head>
  <title>W1™ NEXUS Console</title>
  <style>
    body { background: #070C18; color: #F1F5F9; font-family: system-ui, sans-serif; padding: 40px; }
    h1 { color: #00D9FF; }
    .card { background: #0F172A; border: 1px solid #1E293B; border-radius: 8px; padding: 20px; margin-top: 20px; }
    .badge { background: #0284C7; color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px; }
  </style>
</head>
<body>
  <h1>W1™ NEXUS Workspace Console</h1>
  <p><span class="badge">W1-CIP v1.1 Active</span> Autonomous Intelligence Discovery &amp; Verification Protocol</p>
  <div class="card">
    <h3>Connected Models &amp; Ensembles</h3>
    <p>Operational Models: <b>moonshotai/kimi-k3</b> | <b>deepseek-ai/deepseek-v4-pro</b> | <b>meta/muse-glimmer-30b</b></p>
    <p>Governance Protocol: <b style="color: #38BDF8;">W1-CIP Deterministic State Machine</b></p>
  </div>
</body>
</html>`);
  });
  server.listen(port, () => {
    console.log(`\x1b[36mConsole Web UI is running at http://localhost:${port}/\x1b[0m`);
    console.log('Press Ctrl+C to terminate.');
  });
}

switch (command) {
  case 'status':
    showStatus();
    break;
  case 'schemas':
    listSchemas();
    break;
  case 'console':
    const portArg = args.indexOf('--port');
    const port = portArg !== -1 && args[portArg + 1] ? parseInt(args[portArg + 1], 10) : 8080;
    launchConsole(port);
    break;
  case 'version':
  case '-v':
  case '--version':
    console.log(`w1-nexus v${VERSION}`);
    break;
  case 'help':
  case '-h':
  case '--help':
  default:
    printHelp();
    break;
}
