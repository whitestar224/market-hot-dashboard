const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const core = require('../monitor-buy-core.js');

// Run the complete production controller with asynchronous HTTP and wallet events.
// This DOM double implements only the surface used by the buy dialog; no wallet API is exposed.
class Element extends EventTarget {
  constructor(root) {
    super(); this.root = root || this; this.nodes = this.root === this ? new Set() : this.root.nodes;
    this.attrs = new Map(); this.style = {}; this.dataset = {}; this.hidden = false; this.disabled = false;
    this.classList = {add() {}, remove() {}, toggle() {}}; this.textContent = ''; this.open = false;
  }
  set innerHTML(value) {
    this.html = value;
    if (this.root === this) this.nodes.clear();
    for (const [, tag, attributes] of value.matchAll(/<([a-z]+)\b([^>]*)>/gi)) {
      const node = new Element(this.root); node.tag = tag;
      for (const [, name, quoted, bare] of attributes.matchAll(/([\w-]+)(?:="([^"]*)"|=([^\s>]+))?/g)) {
        node.attrs.set(name, quoted ?? bare ?? '');
      }
      node.hidden = node.hasAttribute('hidden'); node.disabled = node.hasAttribute('disabled');
      node.value = node.attrs.get('value') || '';
      this.nodes.add(node);
    }
  }
  get innerHTML() { return this.html || ''; }
  hasAttribute(name) { return this.attrs.has(name); }
  setAttribute(name, value) { this.attrs.set(name, String(value)); }
  matches(selector) {
    const match = selector.match(/^\[([^=\]]+)(?:=([^\]]+))?\]$/);
    return match && this.attrs.has(match[1]) && (match[2] === undefined || this.attrs.get(match[1]) === match[2]);
  }
  querySelectorAll(selector) { return [...this.nodes].filter(node => selector.split(',').some(part => node.matches(part.trim()))); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { return selector === this.tag ? this : null; }
  replaceChildren() { this.html = ''; }
  focus() {}
  showModal() { this.open = true; this.onShow?.(); }
  close() { this.open = false; this.dispatchEvent(new Event('close')); }
}

function harness() {
  const window = new EventTarget(), document = new EventTarget(), pending = [], dialogs = [];
  let active = 'binance', owner = '';
  document.body = {appendChild: node => dialogs.push(node)};
  document.createElement = () => new Element();
  const wallet = {activeKey: () => active, snapshot: () => ({evm: owner, solana: ''}),
    adapter: key => ({key, label:key==='binance'?'Binance Wallet':'OKX Wallet'}),
    list: () => [{key:'binance', label:'Binance Wallet', installed:true}, {key:'okx', label:'OKX Wallet', installed:true}],
    warmBalances() {}};
  window.MonitorBuyCore = core; window.XingyunMonitorWallets = wallet;
  const context = {window, document, AbortController, AbortSignal, TextDecoder, Event,
    localStorage:{getItem: () => null}, setInterval: () => 1, clearInterval() {},
    fetch: url => {
      assert.equal(url, '/api/monitor-buy/capabilities', 'opening or retrying must never quote, sign, or submit');
      return new Promise((resolve, reject) => pending.push({resolve, reject}));
    }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../monitor-buy.js'), 'utf8'), context);
  return {window, pending, open: row => window.MonitorBuy.open(row),
    get dialog() { return dialogs[0]; },
    node: selector => dialogs[0].querySelector(selector),
    walletChanged(key = active, address = owner) {
      active = key; owner = address; window.dispatchEvent(new Event('xingyun:wallet-change'));
    },
    complete(index = 0) {
      pending[index].resolve({ok:true, json:async () => ({ok:true, networks:[{id:56, name:'BNB'}], defaultChains:[56], maxOrderUsd:200})});
    },
    click(selector) {
      const node = dialogs[0].querySelector(selector);
      assert.ok(node, `missing ${selector}`); assert.equal(node.disabled, false);
      const event = new Event('click'); Object.defineProperty(event, 'target', {value:node});
      dialogs[0].dispatchEvent(event);
    }};
}
const target = symbol => ({symbol, chainId:56, address:'0x'+'ab'.repeat(20), decimals:18,
  identityKey:'56:'+'0x'+'ab'.repeat(20), identityExpiresAt:Date.now()+60000});
const settled = () => new Promise(resolve => setImmediate(resolve));

test('first-open passive account restoration cannot invalidate the loading form', async () => {
  const h = harness(), opening = h.open(target('4STOCK'));
  assert.equal(h.node('[data-buy-form]').hidden, true);
  h.walletChanged('binance', '0x'+'12'.repeat(20));
  h.walletChanged(); h.walletChanged();
  h.complete(); await opening;
  assert.equal(h.node('[data-buy-form]').hidden, false);
  assert.equal(h.node('[data-buy-loading]').hidden, true);
  assert.equal(h.node('[data-buy-message]').textContent, '');
  assert.equal(h.node('[data-buy-quote-button]').disabled, false);
  assert.equal(h.node('[data-buy-confirm]').disabled, true);
});

test('complete CA is visible beside the heading and valid amount enables Buy without a quote',async()=>{
  const h=harness(),row=target('4STOCK'),opening=h.open(row);h.complete();await opening;
  assert.match(h.dialog.innerHTML,new RegExp('data-buy-header-ca>'+row.address));
  assert.ok(h.node('[data-buy-copy-ca]'));
  h.node('[data-buy-amount]').value='10';
  h.walletChanged('binance','0x'+'12'.repeat(20));
  assert.equal(h.node('[data-buy-confirm]').disabled,false);
  assert.equal(h.node('[data-buy-confirm]').textContent,'用 Binance Wallet 买入');
});

test('wallet changes during initial loading are adopted when the form becomes ready', async () => {
  const h = harness(), opening = h.open(target('4STOCK'));
  h.walletChanged('okx', '0x'+'34'.repeat(20)); h.complete(); await opening;
  assert.equal(h.node('[data-buy-form]').hidden, false);
  assert.match(h.node('[data-buy-form]').innerHTML, /value="okx" selected/);
  h.walletChanged();
  assert.equal(h.node('[data-buy-message]').textContent, '');
  h.walletChanged('okx', '0x'+'56'.repeat(20));
  assert.match(h.node('[data-buy-message]').textContent, /账户已变化/);
});

test('a failed initial load stops the spinner and offers a working retry without a wallet request', async () => {
  const h = harness(), opening = h.open(target('4STOCK'));
  h.pending[0].reject(Error('网络连接暂不可用')); await opening;
  assert.equal(h.node('[data-buy-loading]').hidden, true);
  assert.equal(h.node('[data-buy-progress]').hidden, true);
  assert.equal(h.node('[data-buy-retry-open]').hidden, false);
  assert.equal(h.node('[data-buy-retry-open]').disabled, false);
  assert.match(h.node('[data-buy-message]').textContent, /网络连接暂不可用/);
  h.walletChanged('binance', '0x'+'12'.repeat(20)); h.walletChanged();
  assert.match(h.node('[data-buy-message]').textContent, /网络连接暂不可用/);
  assert.equal(h.node('[data-buy-retry-open]').hidden, false);
  h.click('[data-buy-retry-open]');
  assert.equal(h.pending.length, 2);
  h.walletChanged('binance', '0x'+'12'.repeat(20));
  h.complete(1); await settled();
  assert.equal(h.node('[data-buy-form]').hidden, false);
  assert.equal(h.node('[data-buy-retry-open]').hidden, true);
});

test('closing while loading stays possible and a late response cannot reopen the dialog', async () => {
  const h = harness(), opening = h.open(target('4STOCK'));
  h.click('[data-buy-close]'); assert.equal(h.dialog.open, false);
  h.complete(); await opening;
  assert.equal(h.dialog.open, false);
  assert.equal(h.node('[data-buy-form]').hidden, true);
});

test('a late result from an older open cannot replace or unlock the current loading form', async () => {
  const h = harness(), first = h.open(target('FIRST'));
  h.click('[data-buy-close]');
  const second = h.open(target('SECOND'));
  h.complete(0); await first;
  h.walletChanged('binance', '0x'+'12'.repeat(20));
  assert.equal(h.node('[data-buy-form]').hidden, true);
  h.complete(1); await second;
  assert.equal(h.node('[data-buy-form]').hidden, false);
  assert.match(h.dialog.innerHTML, /买入 SECOND/);
  assert.doesNotMatch(h.dialog.innerHTML, /买入 FIRST/);
  assert.equal(h.node('[data-buy-quote-button]').disabled, false);
});

test('an old load failure cannot hide the new progress or replace the new error state', async () => {
  const h = harness(), first = h.open(target('FIRST'));
  h.click('[data-buy-close]'); const second = h.open(target('SECOND'));
  h.pending[0].reject(Error('older request failed')); await first;
  assert.equal(h.node('[data-buy-loading]').hidden, false);
  assert.equal(h.node('[data-buy-progress]').hidden, false);
  assert.equal(h.node('[data-buy-retry-open]').hidden, true);
  assert.equal(h.node('[data-buy-message]').textContent, '');
  h.walletChanged('binance', '0x'+'12'.repeat(20)); h.complete(1); await second;
  assert.equal(h.node('[data-buy-form]').hidden, false);
});
