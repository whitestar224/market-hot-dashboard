const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const core = require('../monitor-buy-core.js');
const root = path.resolve(__dirname, '..');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');

test('a quote failure exits the 91 percent working state and exposes a retry without signing', () => {
  const ui = read('monitor-buy.js'), fields = new Map();
  const node = { classList: { add: value => fields.set('class', value) },
    querySelector: key => { if (!fields.has(key)) fields.set(key, { setAttribute(name, value) { this[name] = value; } }); return fields.get(key); } };
  const retry = {};
  const context = { progressTimer: 1, progressState: {text: '跨链 AI 复核'}, clearInterval: () => fields.set('stopped', true),
    dialog: { querySelector: key => key === '[data-buy-progress]' ? node : retry } };
  vm.createContext(context);
  vm.runInContext(ui.slice(ui.indexOf('  function stopProgress()'), ui.indexOf('  function resetProgress()')), context);
  context.pauseQuoteProgress({message: '跨链 AI 复核超时', stage: 'AI 复核路线'});
  assert.equal(fields.get('stopped'), true);
  assert.equal(fields.get('class'), 'is-paused');
  assert.equal(fields.get('[data-progress-value]').textContent, '已停止');
  assert.match(fields.get('[data-progress-label]').textContent, /跨链 AI 复核超时/);
  assert.match(fields.get('[data-progress-elapsed]').textContent, /AI 复核路线.*未发起钱包请求/);
  assert.equal(fields.get('[role=progressbar]')['aria-busy'], 'false');
  assert.match(retry.textContent, /重新获取报价/);
  assert.match(ui, /preview = null; pauseQuoteProgress\(error\)/);
});

test('passive wallet refresh preserves a stopped quote, actual account change invalidates it', () => {
  const ui = read('monitor-buy.js'), window = new EventTarget();
  let owner = '0x' + 'ab'.repeat(20), invalidations = 0, message = '';
  window.XingyunMonitorWallets = {activeKey: () => 'binance', snapshot: () => ({evm:owner, solana:''})};
  const select = {};
  const context = {window, busy:false, executing:false, connectingForBuy:false, activeKey:'binance',
    walletIdentity:JSON.stringify(['binance', owner, '']),
    dialog:{open:true, querySelector: () => select}, invalidate: () => invalidations++, notice: text => message=text};
  vm.createContext(context);
  vm.runInContext(ui.slice(ui.indexOf('  function selectedWalletIdentity()'), ui.indexOf('  let quoteAbort'))
    + ui.slice(ui.indexOf('  window.addEventListener("xingyun:wallet-change"'), ui.indexOf('  window.MonitorBuy =')), context);
  window.dispatchEvent(new Event('xingyun:wallet-change'));
  owner = owner.toUpperCase(); window.dispatchEvent(new Event('xingyun:wallet-change'));
  assert.equal(invalidations, 0);
  owner = '0x' + 'cd'.repeat(20); window.dispatchEvent(new Event('xingyun:wallet-change'));
  assert.equal(invalidations, 1);
  assert.match(message, /账户已变化/);
  assert.equal(select.value, 'binance');
  context.busy=true; owner='0x'+'ef'.repeat(20);window.dispatchEvent(new Event('xingyun:wallet-change'));
  assert.equal(invalidations,2,'real account changes also cancel a pending quote/Buy');
});

test('quote failure keeps its actual reason in the visible message and long funding copy is removed', async () => {
  const ui = read('monitor-buy.js'); let message = '', paused;
  const context = {busy:false, executing:false, generation:0, preview:null,quoteTask:null,pendingBuy:null,
    target:{chainId:56,identityKey:'56:test', identityExpiresAt:Date.now()+60000}, core:{validTarget:()=>true},
    config:{maxOrderUsd:200}, activeKey:'binance', sourceChains:[56],
    selectedWalletIdentity:()=> 'unchanged', window:{XingyunMonitorWallets:{activeKey:()=> 'binance',currentAddresses:async()=>({evm:'0x'+'ab'.repeat(20)})}},
    dialog:{querySelector:()=>({value:'10',replaceChildren(){}})}, invalidate:()=>{}, resetProgress:()=>{}, setBusy:()=>{}, updateProgress:()=>{},
    connect:async()=>({evm:'0x'+'ab'.repeat(20)}),
    api:async()=>{throw Error('BNB 原生币余额暂不可读');},
    pauseQuoteProgress:error=>paused=error, notice:text=>message=text};
  vm.createContext(context);
  vm.runInContext(ui.slice(ui.indexOf('  async function onClick('), ui.indexOf('  function renderQuote()')), context);
  await context.onClick({target:{closest:()=>({disabled:false,dataset:{},hasAttribute:name=>name==='data-buy-quote-button'})}});
  assert.match(message, /BNB 原生币余额暂不可读.*未发起钱包请求/);
  assert.equal(paused.message, 'BNB 原生币余额暂不可读');
  assert.doesNotMatch(ui, /具体原因见上方|先用目标链原生币直买|monitor-buy-funding/);
  assert.match(ui, /自动滑点最高 20%/);
});

test('a native fallback is clearly disclosed while stablecoin remains first choice', () => {
  const ui = read('monitor-buy.js');
  assert.match(ui, /首选付款方式不可用/);
  assert.match(ui, /fundingFallback\.crossChain/);
  assert.match(ui, /monitor-buy-payment-fallback/);
  assert.match(ui, /钱包确认前仍会再次进行链上试运行/);
});

test('warm balance cannot bypass the final live wallet checks before sending', async () => {
  const {preview, execution} = fixtures(); let adapter, sends = 0, claims = 0;
  const context = { window:{MonitorBuyCore:core}, Buffer, TextEncoder, crypto:require('node:crypto').webcrypto,
    createClient:()=>({actions:{execute:async value=>adapter=value.wallet}}), createPublicClient:()=>({}), http:()=>({}), defineChain:x=>x };
  vm.createContext(context);
  vm.runInContext(read('tools/monitor-buy-executor-entry.js').replace(/^import .*;\r?\n/gm,'').replace('export async function executeBuy','async function executeBuy'),context);
  const provider={request:async({method})=>{
    if(method==='eth_sendTransaction') sends++;
    return ({eth_chainId:'0x2105',eth_estimateGas:'0x5208',eth_getBalance:'0x0',eth_gasPrice:'0x1'})[method];
  }};
  await context.executeBuy({execution,preview,networks:[{id:8453,currency:{}}],
    wallet:{provider:()=>provider,currentAddresses:async()=>({evm:execution.intent.sender})},
    api:async()=>claims++,onHash:()=>{},onProgress:()=>{}});
  await assert.rejects(adapter.handleSendTransactionStep(8453,execution.quote.steps[0].items[0]),/余额不足/);
  assert.equal(sends,0); assert.equal(claims,0);
});

test('only preverified monitor identities expose buy; missing or mismatched CA stays disabled', () => {
  const ca = '0x' + 'ab'.repeat(20);
  const row = {symbol:'TEST',chain:'bsc',contractAddress:ca};
  const proof = {status:'verified',expiresAt:Date.now()+60000,target:{symbol:'TEST',chainId:56,address:ca,identityKey:'56:'+ca}};
  assert.match(core.button(row), /disabled/);
  assert.doesNotMatch(core.button(row), /data-monitor-buy=/);
  assert.match(core.button({...row,buyIdentity:proof}), /data-monitor-buy=/);
  assert.match(core.button({...row,buyIdentity:{...proof,status:'pending'}}), /disabled/);
  assert.match(core.button({...row,buyIdentity:{...proof,expiresAt:0}}), /disabled/);
  assert.match(core.button({...row,chain:'base',buyIdentity:proof}), /disabled/);
  assert.match(core.button({...row,contractAddress:'0x'+'cd'.repeat(20),buyIdentity:proof}), /disabled/);
  const ui=read('monitor-buy.js');
  assert.match(ui,/已核验 CA<input readonly/);
  assert.doesNotMatch(ui,/api\("resolve"/);
  assert.doesNotMatch(ui,/target = \{ \.\.\.target, chainId/);
});

test('invalidating an abandoned quote releases busy state and removes the stale progress', () => {
  const ui=read('monitor-buy.js'), changes=[];
  const progress={hidden:false}, quote={replaceChildren:()=>changes.push('clear quote')};
  const context={executing:false,generation:3,preview:{},autoQuoteTimer:null,quoteTask:{},pendingBuy:{},quoteAbort:{abort:()=>changes.push('abort')},
    resetProgress:()=>changes.push('reset'), setBusy:value=>changes.push(['busy',value]),
    dialog:{querySelector:selector=>selector==='[data-buy-progress]'?progress:selector==='[data-buy-quote]'?quote:{}}};
  vm.createContext(context);
  vm.runInContext(ui.slice(ui.indexOf('  function invalidate()'),ui.indexOf('  function setBusy(')),context);
  context.invalidate();
  assert.equal(context.generation,4);
  assert.equal(context.preview,null);
  assert.equal(context.quoteTask,null);
  assert.equal(context.pendingBuy,null);
  assert.equal(progress.hidden,true);
  assert.deepEqual(changes,['abort','reset','clear quote',['busy',false]]);
  changes.length=0; context.executing=true; context.invalidate();
  assert.deepEqual(changes,[]); // A submitted transaction is not cancelled or automatically resent.
});

test('progress timers track the real stage without resetting on a repeated update', () => {
  const ui=read('monitor-buy.js'), nodes=new Map();
  const field=key=>{if(!nodes.has(key))nodes.set(key,{style:{},setAttribute(name,value){this[name]=value;}});return nodes.get(key);};
  const node={hidden:true,classList:{remove(){},toggle(){}},querySelector:field};
  let now=1000,tick;
  const context={executing:false,progressState:null,progressTimer:null,Date:{now:()=>now},Math,String,
    clearInterval(){},setInterval:fn=>{tick=fn;return 1;},notice(){},dialog:{querySelector:()=>node}};
  vm.createContext(context);
  vm.runInContext(ui.slice(ui.indexOf('  function updateProgress('),ui.indexOf('  function stopProgress(')),context);
  context.updateProgress(42,'balance','读取余额');
  now=17000;context.updateProgress(42,'balance','读取余额');
  assert.equal(context.progressState.phaseStarted,1000);
  tick();assert.match(field('[data-progress-elapsed]').textContent,/当前步骤 16 秒.*等待响应/);
  context.updateProgress(49,'route','读取路线');
  assert.equal(context.progressState.phaseStarted,17000);
  context.updateProgress(100,'quoted','报价已就绪');
  assert.equal(field('[role=progressbar]')['aria-busy'],'false');
});

test('identity binds chain + contract, preserves Solana case and does not guess symbols', () => {
  const ca = '0x56910d4409f3a0c78c64dd8d0545ff0705389870';
  assert.deepEqual(core.identity({symbol:'INDEX', tradeUrl:`https://web3.binance.com/en/token/robinhood/${ca}`}),
    {symbol:'INDEX', chainId:4663, address:ca, kind:'token'});
  assert.ok(!core.validTarget(core.identity({symbol:'RAYUSDT'})));
  const sol = '5ExRQUbJiZysXWG7KapGwsQmjYvx4hCeBgwAGvht5qab';
  assert.equal(core.identity({symbol:'SOL', network:'solana', contractAddress:sol}).address, sol);
  assert.ok(!core.validTarget({kind:'nft', chainId:1, address:ca}));
  assert.ok(!core.button({symbol:'<img onerror=x>'}).includes('<img'));
});

function fixtures() {
  const source = {chainId:8453,address:'0x'+'0'.repeat(40),owner:'0x'+'12'.repeat(20)};
  const target = {chainId:4663,address:'0x'+'34'.repeat(20)};
  const data = {from:source.owner,to:'0x'+'56'.repeat(20),value:'100',data:'0x1234',chainId:8453};
  const details = {sender:source.owner,recipient:source.owner,slippageTolerance:{total:'500'},
    currencyIn:{currency:source,amount:'100'},currencyOut:{currency:target,amount:'101',minimumAmount:'95'}};
  const preview = {orderId:'example',source,target,recipient:source.owner,details:structuredClone(details)};
  const execution = {expiresAt:Date.now()+60000,intent:{source,target,sender:source.owner,recipient:source.owner,amount:'100'},
    quote:{details,steps:[{kind:'transaction',items:[{data}]}]}};
  return {preview,execution};
}

test('browser rejects changed recipient, amount, token and expired or excessive-slippage quotes', () => {
  const {preview,execution} = fixtures();
  assert.equal(core.validateExecution(execution,preview),true);
  for (const edit of [x=>x.quote.details.recipient='0x'+'ff'.repeat(20),x=>x.quote.details.currencyIn.amount='101',
    x=>x.quote.details.currencyOut.currency.address='0x'+'ff'.repeat(20),x=>x.expiresAt=0,
    x=>x.quote.details.slippageTolerance.total='1000',x=>x.quote.steps[0].kind='signature']) {
    const changed=structuredClone(execution); edit(changed);
    assert.throws(()=>core.validateExecution(changed,preview));
  }
});

test('monitor integrations keep signing isolated from refresh and amounts in USDT', () => {
  const page = read('price-watch.js'), ui = read('monitor-buy.js'), html = read('price-watch.html');
  assert.ok((page.match(/monitorBuyButton\(/g)||[]).length >= 11);
  assert.ok(html.indexOf('monitor-buy-core.js') < html.indexOf('price-watch.js'));
  assert.match(ui,/document\.body\.appendChild\(dialog\)/);
  assert.match(ui,/输入 USDT 金额/);
  assert.match(ui,/api\("quote", \{ target, amountUsdt/);
  assert.match(ui,/executor \|\|= import/);
  assert.match(ui,/if \(executing\) return/);
  assert.match(ui,/授权与买入去向 · 已逐项核对/);
  assert.match(ui,/签名已发送至移动 App/);
  assert.match(ui,/钱包仍须本人签名/);
});

test('EVM executor only sends once; repeated or ambiguous submission cannot retry', async () => {
  let adapter, sends=0;
  const source = read('tools/monitor-buy-executor-entry.js').replace(/^import .*;\r?\n/gm,'').replace('export async function executeBuy','async function executeBuy');
  const {preview,execution} = fixtures();
  const claims = new Set();
  const provider = {request:async request => {
    if(request.method==='eth_accounts') return [execution.intent.sender];
    if(request.method==='eth_chainId') return '0x2105';
    if(request.method==='eth_estimateGas') return '0x5208';
    if(request.method==='eth_getBalance') return '0xffffffffffff';
    if(request.method==='eth_gasPrice') return '0x1';
    if(request.method==='eth_sendTransaction') { sends++; throw Error('ambiguous timeout'); }
    throw Error(request.method);
  }};
  const context = {window:{MonitorBuyCore:core},Buffer,TextEncoder,crypto:require('node:crypto').webcrypto,Map,Set,Date,
    createClient:()=>({actions:{execute:async value=>{adapter=value.wallet;}}}),
    createPublicClient:()=>({}), http:()=>({}), defineChain:x=>x };
  vm.createContext(context); vm.runInContext(source,context);
  await context.executeBuy({execution,preview,networks:[{id:8453,name:'Base',rpcUrl:'https://example.invalid',currency:{}}],
    wallet:{provider:()=>provider,currentAddresses:async()=>({evm:execution.intent.sender})},
    api:async(action,{fingerprint})=>{ if(action==='claim-step'){if(claims.has(fingerprint)) throw Error('duplicate'); claims.add(fingerprint);} },onHash:()=>{},onProgress:()=>{}});
  const item=execution.quote.steps[0].items[0];
  await assert.rejects(adapter.handleSendTransactionStep(8453,item),/ambiguous/);
  await assert.rejects(adapter.handleSendTransactionStep(8453,item),/重复/);
  assert.equal(sends,1);
  await assert.rejects(adapter.handleSignMessageStep(),/不允许/);
});

test('read-only preflight runs concurrently; hash reporting does not block receipt monitoring', async () => {
  const { preview, execution } = fixtures();
  let adapter, entered = [], released = false, hashReported = false;
  const stages = [], releases = [];
  const provider = { request: async ({ method }) => {
    if (method === 'eth_chainId') return '0x2105';
    if (['eth_estimateGas', 'eth_getBalance', 'eth_gasPrice'].includes(method)) {
      entered.push(method);
      return new Promise(resolve => {
        releases.push(() => resolve(method === 'eth_estimateGas' ? '0x5208' : method === 'eth_gasPrice' ? '0x1' : '0xffffffffff'));
        if (entered.length === 3) { released = true; releases.forEach(fn => fn()); }
      });
    }
    if (method === 'eth_sendTransaction') { assert.ok(released); assert.equal(stages.at(-1), 'wallet'); return '0x' + 'ab'.repeat(32); }
    throw Error(method);
  } };
  const context = { window: { MonitorBuyCore: core }, Buffer, TextEncoder, crypto: require('node:crypto').webcrypto,
    createClient: () => ({ actions: { execute: async x => { adapter = x.wallet; } } }),
    createPublicClient: () => ({}), http: () => ({}), defineChain: x => x };
  vm.createContext(context);
  vm.runInContext(read('tools/monitor-buy-executor-entry.js').replace(/^import .*;\r?\n/gm, '').replace('export async function executeBuy', 'async function executeBuy'), context);
  await context.executeBuy({ execution, preview, networks: [{ id: 8453, name: 'Base', currency: {} }],
    wallet: { provider: () => provider, currentAddresses: async () => ({ evm: execution.intent.sender }) },
    api: async action => { if (action === 'progress') return new Promise(() => {}); },
    onHash: () => { hashReported = true; }, onProgress: () => {}, onStage: (_, stage) => stages.push(stage) });
  const hash = await adapter.handleSendTransactionStep(8453, execution.quote.steps[0].items[0]);
  assert.equal(hash, '0x' + 'ab'.repeat(32));
  assert.equal(hashReported, true);
  assert.equal(entered.length, 3);
  assert.equal(stages.at(-1), 'submitted');
});

test('an empty wallet result is not treated as submitted and the transfer cannot be replayed', async () => {
  const { preview, execution } = fixtures(); let adapter, sends = 0, reports = 0;
  const context = { window: { MonitorBuyCore: core }, Buffer, TextEncoder, crypto: require('node:crypto').webcrypto,
    createClient: () => ({ actions: { execute: async x => { adapter = x.wallet; } } }),
    createPublicClient: () => ({}), http: () => ({}), defineChain: x => x };
  vm.createContext(context);
  vm.runInContext(read('tools/monitor-buy-executor-entry.js').replace(/^import .*;\r?\n/gm, '').replace('export async function executeBuy', 'async function executeBuy'), context);
  const provider = { request: async ({ method }) => {
    if (method === 'eth_sendTransaction') { sends++; return undefined; }
    return ({ eth_chainId: '0x2105', eth_estimateGas: '0x5208', eth_getBalance: '0xffffffffffff', eth_gasPrice: '0x1' })[method];
  } };
  await context.executeBuy({ execution, preview, networks: [{ id: 8453, currency: {} }],
    wallet: { provider: () => provider, currentAddresses: async () => ({ evm: execution.intent.sender }) },
    api: async () => {}, onHash: () => reports++, onProgress: () => {} });
  const item = execution.quote.steps[0].items[0];
  await assert.rejects(adapter.handleSendTransactionStep(8453, item), /有效交易哈希/);
  await assert.rejects(adapter.handleSendTransactionStep(8453, item), /重复/);
  assert.equal(sends, 1); assert.equal(reports, 0);
});

test('buy dialog differentiates missing wallet response and on-chain confirmation without retrying', () => {
  const ui = read('monitor-buy.js');
  assert.match(ui, /钱包尚未响应，未收到交易哈希/);
  assert.match(ui, /stage === "wallet"/);
  assert.match(ui, /data-buy-progress/);
  assert.match(ui, /aria-valuenow/);
  assert.match(ui, /quote-stream/);
  assert.match(ui, /quoteAbort\?\.abort/);
  assert.doesNotMatch(ui, /交易处理中：等待源链确认／跨链到账／目标币兑换/);
});

test('unconfirmed contract safety is scrolled into view and focused without weakening the gate', () => {
  const ui = read('monitor-buy.js'); let highlighted = false, scrolled = false, focused = false, invalid = '', message = '';
  const panel = { classList: { add: value => highlighted = value === 'is-required' }, scrollIntoView: options => { scrolled = options.block === 'center'; } };
  const input = { setAttribute: (name, value) => { if (name === 'aria-invalid') invalid = value; }, focus: () => focused = true };
  const context = { dialog: { querySelector: selector => selector === '[data-buy-risk]' ? panel : input }, notice: text => message = text };
  vm.createContext(context);
  vm.runInContext(ui.slice(ui.indexOf('  function requireSecurityAcknowledgement()'), ui.indexOf('  async function onClick(')), context);
  context.requireSecurityAcknowledgement();
  assert.equal(highlighted, true); assert.equal(scrolled, true); assert.equal(focused, true); assert.equal(invalid, 'true');
  assert.match(message, /勾选黄色.*不会发起钱包交易/);
  assert.match(ui, /if \(!preview\.security\.verified && !accepted\) \{ requireSecurityAcknowledgement\(\); return; \}/);
});

test('close stays enabled during a pending wallet request; reopening resumes the same dialog', async () => {
  const ui = read('monitor-buy.js');
  let closes = 0, opens = 0;
  const closeButton = { disabled: true }, confirmButton = { disabled: false };
  const dialog = { open: true, querySelectorAll: () => [],
    querySelector: selector => selector === '[data-buy-close]' ? closeButton : confirmButton,
    close() { this.open = false; closes++; }, showModal() { this.open = true; opens++; } };
  const context = { window:{XingyunMonitorWallets:{adapter:()=>({label:'Binance Wallet'})}}, activeKey:'binance', dialog, executing: true, busy: true, preview: {},pendingBuy:null,config:{maxOrderUsd:200}, invalidate: () => { throw Error('must keep original execution'); } };
  vm.createContext(context);
  vm.runInContext(ui.slice(ui.indexOf('  function setBusy('), ui.indexOf('  async function connect()'))
    + ui.slice(ui.indexOf('  async function onClick('), ui.indexOf('  function renderQuote()')), context);
  context.setBusy(true);
  assert.equal(closeButton.disabled, false);
  const button = { disabled: false, hasAttribute: x => x === 'data-buy-close', dataset: {} };
  await context.onClick({ target: { closest: () => button } });
  assert.equal(closes, 1);
  assert.equal(context.executing, true);
  await context.open({ symbol: 'OTHER' });
  assert.equal(opens, 1);
  assert.equal(context.executing, true);
});

test('quote progress parser handles split UTF-8 and rejects interrupted streams without a result', async () => {
  const ui = read('monitor-buy.js'), encode = new TextEncoder();
  const data = encode.encode(JSON.stringify({ type: 'progress', percent: 42, stage: '读取原生币余额' }) + '\n'
    + JSON.stringify({ type: 'result', payload: { ok: true, orderId: 'test' } }));
  const context = { AbortController, AbortSignal, TextDecoder, quoteAbort: null,
    fetch: async () => new Response(new ReadableStream({ start(controller) {
      for (let i = 0; i < data.length; i += 7) controller.enqueue(data.slice(i, i + 7)); controller.close();
    } })) };
  vm.createContext(context);
  vm.runInContext(ui.slice(ui.indexOf('  async function api('), ui.indexOf('  const amountLabel')), context);
  const progress = [], result = await context.api('quote', {}, item => progress.push(item));
  assert.equal(result.orderId, 'test');
  assert.equal(progress[0].stage, '读取原生币余额');
  context.fetch = async () => new Response('{"type":"progress","percent":42}\n');
  await assert.rejects(context.api('quote', {}, () => {}), /连接中断/);
});
