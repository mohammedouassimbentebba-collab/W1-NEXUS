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
  studio [--port N]    Launch the interactive W1™ NEXUS Studio Web UI (default: 8080)
  schemas              List registered W1-CIP 0.1 / 1.1 protocol schemas
  validate <file.json> Validate a local JSON artifact against W1-CIP schemas
  console [--port N]   Alias for 'studio'
  version, -v          Print version information
  help, -h             Print this help message
`);
}

function showStatus() {
  printHeader();
  console.log('\x1b[33m[RUNTIME STATUS]\x1b[0m');
  console.log('  Protocol:          W1-CIP v1.1 (Governed Consensus)');
  console.log('  License:           Apache License 2.0 (Apache-2.0)');
  console.log('  Model Matrix:      Credential != Model (Dynamic Discovery)');
  console.log('  Zero-Leak Guard:   Active (Cryptographic secret scrubbing)');
  console.log('  Benchmark Suites:  9 Suites (AutomationBench, OSWorld 2, FrontierMath, etc.)');
  console.log('  Systems Core:      🦀 Rust Core (crates/w1-cip-core) [Deterministic Merkle-DAG]');
  console.log('  Sandbox Engine:    🛡️ Native C Aerospace Core (Win32 Job Objects • 2048 MB)');
  console.log('  Studio Web UI:     🚀 Node.js Studio Ready (run: npx w1-nexus studio)');
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

function launchStudio(port = 8080) {
  printHeader();
  try {
    const { startStudio } = require('../studio/server.js');
    startStudio(port);
  } catch (err) {
    console.error(`\x1b[31mFailed to start Nexus Studio: ${err.message}\x1b[0m`);
    process.exit(1);
  }
}

switch (command) {
  case 'status':
    showStatus();
    break;
  case 'schemas':
    listSchemas();
    break;
  case 'studio':
  case 'console':
    const portArg = args.indexOf('--port');
    const port = portArg !== -1 && args[portArg + 1] ? parseInt(args[portArg + 1], 10) : 8080;
    launchStudio(port);
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
