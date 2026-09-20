const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const ui = fs.readFileSync(path.join(root, 'monitor-buy.js'), 'utf8');
const server = fs.readFileSync(path.join(root, 'server.py'), 'utf8');

test('free session mode pins supported stablecoin contracts and makes them read-only', () => {
  assert.match(ui, /0xdac17f958d2ee523a2206206994597c13d831ec7/);
  assert.match(ui, /0x55d398326f99059ff775485246999027b3197955/);
  assert.match(ui, /0x833589fcd6edb6e08f4c7c32d4f71b54bda02913/);
  assert.match(ui, /<input readonly data-session-stable/);
  assert.match(ui, /<input readonly type="number" data-session-decimals/);
});

test('session confirmation goes to the local executor before any wallet executor code', () => {
  const start = ui.indexOf('async function confirm()');
  const end = ui.indexOf('async function track(', start);
  const confirm = ui.slice(start, end);
  const sessionStart = confirm.indexOf('if (preview.executionMode === SESSION_MODE)');
  const walletStart = confirm.indexOf('const wallets = window.XingyunMonitorWallets;', sessionStart);
  const sessionBranch = confirm.slice(sessionStart, walletStart);
  assert.match(sessionBranch, /api\("session-execute"/);
  assert.match(sessionBranch, /不会唤起手机钱包，也不会自动重发/);
  assert.doesNotMatch(sessionBranch, /executeBuy|currentAddresses|walletProvider/);
});

test('local signing endpoints require both a loopback peer and loopback Host', () => {
  assert.match(server, /action\.startswith\("session-"\)/);
  assert.match(server, /is_local_peer/);
  assert.match(server, /is_local_host/);
  assert.match(server, /if not is_local_peer or not is_local_host/);
});
