const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../wallets.js'), 'utf8');
const address = '0x' + '12'.repeat(20);
class DetailEvent extends Event { constructor(type, options = {}) { super(type); this.detail = options.detail; } }
function provider(initial = []) {
  const listeners = new Map();
  return { accounts: initial, requests: [], isBinance: true,
    on(name, fn) { const list = listeners.get(name) || []; list.push(fn); listeners.set(name, list); },
    emit(name, value) { (listeners.get(name) || []).forEach(fn => fn(value)); },
    async request(data) {
      this.requests.push(data.method);
      if (data.method === 'eth_accounts') return this.accounts;
      if (data.method === 'eth_chainId') return '0x38';
      if (data.method === 'eth_requestAccounts') return this.accounts = [address];
      throw Error('Unexpected wallet method: ' + data.method);
    }
  };
}
function page(evm, storage = new Map(), others = {}, globals = {}) {
  const window = Object.assign(new EventTarget(), { binancew3w: { ethereum: evm }, ...others });
  const document = Object.assign(new EventTarget(), { querySelector: () => null });
  const context = { window, document, Event, CustomEvent: DetailEvent, setTimeout, clearTimeout, AbortController,
    localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) }, ...globals };
  vm.runInNewContext(source, context);
  return { window, document, wallets: window.XingyunWallets, storage };
}

test('global passive restore warms only the active wallet without permissions or signing', async () => {
  const evm = provider([address]), other = provider(['0x' + '34'.repeat(20)]), calls = [], timers = new Map(); let id = 0;
  const { wallets, storage } = page(evm, new Map(), { okxwallet: other,
    fetch: async (url, options) => { calls.push({url, ...JSON.parse(options.body)}); return {ok:true}; }
  }, { setTimeout: fn => { timers.set(++id, fn); return id; }, clearTimeout: id => timers.delete(id) });
  await wallets.initialize();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/monitor-buy/balances-warm');
  assert.equal(calls[0].walletProvider, 'binance');
  assert.equal(calls[0].wallets.evm, address);
  assert.equal(calls[0].preferredChain, 56);
  await wallets.initialize(); assert.equal(calls.length, 1);
  wallets.warmBalances(8453); assert.equal(calls.at(-1).preferredChain, 8453);
  wallets.setActive('okx');
  assert.equal(calls.at(-1).walletProvider, 'okx');
  assert.equal(calls.at(-1).wallets.evm, other.accounts[0]);
  const count = calls.length;
  evm.emit('accountsChanged', ['0x' + '56'.repeat(20)]);
  assert.equal(calls.length, count);
  wallets.disconnect('okx'); await wallets.initialize();
  assert.equal(calls.length, count);
  assert.ok(!JSON.stringify([...storage]).includes(address));
  assert.ok([...evm.requests, ...other.requests].every(method => ['eth_accounts', 'eth_chainId'].includes(method)));
});

test('balance warmup stops while hidden and starts with the new account when visible', async () => {
  const evm = provider(), calls = [], timers = new Map(); let id = 0;
  const { wallets, document } = page(evm, new Map(), {
    fetch: async (url, options) => { calls.push(JSON.parse(options.body)); return {ok:true}; }
  }, { setTimeout: fn => { timers.set(++id, fn); return id; }, clearTimeout: id => timers.delete(id) });
  await wallets.initialize(); assert.equal(calls.length, 0);
  document.hidden = true;
  evm.accounts = [address]; evm.emit('accountsChanged', evm.accounts);
  assert.equal(calls.length, 0);
  document.hidden = false; document.dispatchEvent(new Event('visibilitychange'));
  assert.equal(calls.length, 1);
  evm.accounts = ['0x' + '56'.repeat(20)]; evm.emit('accountsChanged', evm.accounts);
  assert.equal(calls.at(-1).wallets.evm, evm.accounts[0]);
  assert.equal(calls.length, 2);
  wallets.disconnect('binance');
});
test('global authorization is recovered by passive account reads across pages and refresh', async () => {
  const evm = provider(), storage = new Map();
  const first = page(evm, storage); await first.wallets.initialize();
  assert.equal(evm.requests.filter(x => x === 'eth_requestAccounts').length, 0);
  await first.wallets.connect('binance');
  const second = page(evm, storage); await second.wallets.initialize();
  assert.equal(second.wallets.snapshot('binance').evm, address);
  await second.wallets.connect('binance');
  assert.equal(evm.requests.filter(x => x === 'eth_requestAccounts').length, 1);
  assert.ok(!JSON.stringify([...storage]).includes(address));
});
test('multiple wallets remain connected and explicit disconnect survives passive restore', async () => {
  const evm = provider([address]), okx = provider(['0x' + '34'.repeat(20)]);
  const { wallets, storage, window } = page(evm, new Map(), { okxwallet: okx });
  await wallets.initialize();
  await wallets.connect('okx');
  assert.equal(wallets.connected().length, 2);
  assert.equal(wallets.activeKey(), 'okx');
  wallets.disconnect('binance'); await wallets.initialize();
  assert.equal(wallets.snapshot('binance').evm, '');
  await assert.rejects(wallets.currentAddresses('binance'), /断开/);
  const next = page(evm, storage); await next.wallets.initialize();
  assert.equal(next.wallets.snapshot('binance').evm, '');
  storage.set('xingyunWalletV1:active', 'binance');
  const event = new Event('storage'); event.key = 'xingyunWalletV1:active'; window.dispatchEvent(event);
  assert.equal(wallets.activeKey(), 'binance');
});
test('revocation and account changes update independently without silently switching the active wallet', async () => {
  const evm = provider([address]), okx = provider([address]);
  const { wallets } = page(evm, new Map(), { okxwallet: okx }); await wallets.initialize();
  wallets.setActive('okx');
  evm.accounts = []; evm.emit('accountsChanged', []);
  assert.equal(wallets.snapshot('binance').evm, '');
  assert.equal(wallets.activeKey(), 'okx');
  assert.equal(wallets.snapshot('okx').evm, address);
});
test('concurrent connection attempts produce one permission request and optional Solana never prompts', async () => {
  const evm = provider(); let solPrompts = 0;
  const { wallets } = page(evm, new Map(), { binancew3w: { ethereum: evm, solana: { connect: async () => { solPrompts++; } } } });
  await wallets.initialize();
  await Promise.all([wallets.connect('binance', false, true), wallets.connect('binance', false, true)]);
  assert.equal(evm.requests.filter(x => x === 'eth_requestAccounts').length, 1);
  assert.equal(solPrompts, 0);
});
test('disconnect while a connection is pending cannot be undone by its late completion', async () => {
  const evm = provider(); const { wallets } = page(evm); await wallets.initialize();
  let resolve, entered;
  const started = new Promise(r => entered = r);
  const original = evm.request.bind(evm);
  evm.request = req => req.method === 'eth_requestAccounts' ? new Promise(r => { resolve = r; entered(); }) : original(req);
  const pending = wallets.connect('binance'); await started;
  wallets.disconnect('binance'); resolve([address]);
  await assert.rejects(pending, /断开/);
  assert.equal(wallets.snapshot('binance').evm, '');
});
test('every authenticated page loads the same wallet module before page code', () => {
  for (const name of fs.readdirSync(path.join(__dirname, '..')).filter(x => x.endsWith('.html'))) {
    const html = fs.readFileSync(path.join(__dirname, '..', name), 'utf8');
    if (!html.includes('./auth.js')) continue;
    assert.ok(html.includes('./wallets.js'), name);
    assert.ok(html.indexOf('./wallets.js') < html.indexOf('./auth.js'), name);
  }
});
test('wallet and account controls use a utility rail outside the balanced navigation grid', () => {
  const authSource = fs.readFileSync(path.join(__dirname, '../auth.js'), 'utf8');
  const styles = fs.readFileSync(path.join(__dirname, '../styles.css'), 'utf8');
  assert.match(source, /topbarUtilitiesNode/);
  assert.match(source, /utilities\.appendChild\(navButton\)/);
  assert.match(authSource, /topbar-utilities/);
  assert.match(styles, /grid-template-columns:\s*repeat\(7,/);
  assert.match(styles, /\.global-wallet-trigger\s*\{[^}]*order:\s*10/s);
});
test('full dashboard navigation is evenly split into two rows of seven links', () => {
  for (const name of ['index.html', 'gainers.html', 'turnover.html', 'newboards.html', 'listings.html', 'newsflash.html', 'briefs.html', 'rss.html', 'xwatch.html', 'price-watch.html', 'event-flow.html', 'strategy.html', 'todo.html', 'legal.html']) {
    const html = fs.readFileSync(path.join(__dirname, '..', name), 'utf8');
    const nav = html.match(/<nav class="page-nav"[\s\S]*?<\/nav>/)?.[0] || '';
    assert.equal((nav.match(/class="nav-link/g) || []).length, 14, name);
    assert.match(nav, /href="\.\/event-flow\.html"/, name);
  }
});
