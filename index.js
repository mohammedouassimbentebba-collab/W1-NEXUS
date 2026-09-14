/**
 * W1™ NEXUS Core Library
 * Autonomous Intelligence Discovery & Verification Protocol
 * Licensed under MPL-2.0
 */

const fs = require('fs');
const path = require('path');

const VERSION = '0.1.0';
const PROTOCOL_VERSION = '1.1';

/**
 * Returns list of available W1-CIP JSON schemas.
 */
function getAvailableSchemas() {
  const dir = path.join(__dirname, 'schemas', 'w1-cip', '0.1');
  if (fs.existsSync(dir)) {
    return fs.readdirSync(dir).filter(f => f.endsWith('.schema.json')).map(f => f.replace('.schema.json', ''));
  }
  return ['goal-contract', 'team-plan', 'task', 'evidence', 'challenge', 'review', 'verification', 'decision', 'final-result'];
}

/**
 * Loads a W1-CIP JSON schema by name.
 */
function loadSchema(name) {
  const filePath = path.join(__dirname, 'schemas', 'w1-cip', '0.1', `${name}.schema.json`);
  if (fs.existsSync(filePath)) {
    return JSON.parse(fs.readFileSync(filePath, 'utf-8'));
  }
  throw new Error(`Schema not found: ${name}`);
}

module.exports = {
  VERSION,
  PROTOCOL_VERSION,
  getAvailableSchemas,
  loadSchema
};
