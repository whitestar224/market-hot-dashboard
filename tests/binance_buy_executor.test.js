// Real shared executor + real browser validator, with every wallet/network boundary mocked.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {createHash, webcrypto} = require('node:crypto');
const core = require('../monitor-buy-core.js');
const OWNER = '0x'+'12'.repeat(20), TOKEN = '0x'+'34'.repeat(20);
const ZERO = '0x'+'0'.repeat(40), ROUTER = '0xb44446b0c8e56988c34f7ff73ae904982b5fdda5';
const hash = q => createHash('sha256').update(core.stableJson(q)).digest('hex');

function fixture(approvals=0) {
  const source={chainId:56,address:approvals?TOKEN:ZERO,owner:OWNER,decimals:approvals?6:18};
  const target={chainId:56,address:'0x'+'ab'.repeat(20),decimals:6};
  const intent={source,target,sender:OWNER,recipient:OWNER,amount:approvals?'10000000':'10000000000000000',slippageBps:500};
  const tx={from:OWNER,to:ROUTER,value:approvals?'0':intent.amount,data:'0xad43f73d'+'00'.repeat(352),
    gas:'200000',gasPrice:'1000000',minReceiveAmount:'9500000',slippagePercent:'5',signatureData:null};
  const quote={provider:'binance-web3',raw:{executionMode:'SWAP',rfq:null,tx},
    details:{sender:OWNER,recipient:OWNER,currencyIn:{currency:source,amount:intent.amount},
      currencyOut:{currency:target,amount:'10000000',minimumAmount:'9500000'},slippageTolerance:{total:'500'}},steps:[]};
  for(let j=0;j<approvals;j++) quote.steps.push({id:'approval',kind:'transaction',items:[{data:{from:OWNER,to:TOKEN,chainId:56,value:'0',
    data:'0x095ea7b3'+ROUTER.slice(2).padStart(64,'0')+BigInt(approvals===2&&j===0?0:intent.amount).toString(16).padStart(64,'0')}}]});
  quote.steps.push({id:'swap',kind:'transaction',items:[{data:{from:OWNER,to:ROUTER,chainId:56,value:tx.value,data:tx.data}}]});
  quote.execution={swapContract:ROUTER,targetAddress:target.address,recipient:OWNER,receiverMode:'connected-wallet',
    approval:{required:approvals>0,resetRequired:approvals===2,tokenAddress:source.address,tokenSymbol:'',spender:ROUTER,
      amount:intent.amount,amountFormatted:approvals?'10':'0.01',exactAmount:true},dexes:[],verification:'pinned-router-exact-calldata'};
  return {execution:{intent,quote,expiresAt:Date.now()+60000}, preview:{orderId:'fixture',provider:quote.provider,
    target,source,recipient:OWNER,details:quote.details,executionSummary:structuredClone(quote.execution),quoteHash:hash(quote)}};
}

function harness(options={}) {
  const f=fixture(options.approvals||0), events=[], sent=[], hashes=[], claims=new Set(), receiptTransports=[];
  let now=Date.now(), timer;
  const context={window:{MonitorBuyCore:core}, Buffer, TextEncoder, crypto:webcrypto,
    Date:class extends Date {static now(){return now;}},
    setTimeout:fn=>{timer=fn;return 1;},clearTimeout:()=>{timer=null;},
    createClient:()=>{throw Error('Binance MUST NOT create or invoke Relay');},
    createPublicClient:config=>({waitForTransactionReceipt:async args=>{
      receiptTransports.push(config.transport.kind);
      events.push('receipt');
      if(options.receipt) return options.receipt(args,f,()=>{now=f.execution.expiresAt+1;});
      return {status:'success'};
    }}),custom:provider=>({kind:'wallet',provider}),http:url=>({kind:'http',url}),defineChain:x=>x};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(require.resolve('../tools/monitor-buy-executor-entry.js'),'utf8')
    .replace(/^import .*;\r?\n/gm,'').replace('export async function executeBuy','async function executeBuy'),context);
  const provider={request:async args=>{
    events.push(args.method);
    if(args.method==='eth_sendTransaction') {
      sent.push(args.params[0]);
      return options.send?options.send(args):'0x'+String(sent.length).padStart(64,'0');
    }
    if(options.request) { const value=options.request(args); if(value!==undefined)return value; }
    return {eth_chainId:'0x38',eth_estimateGas:'0x5208',eth_getBalance:'0xde0b6b3a7640000',
      eth_gasPrice:'0xf4240',eth_call:'0x989680'}[args.method];
  }};
  const run=()=>context.executeBuy({...f,networks:[{id:56,name:'BNB',currency:{},rpcUrl:'https://example.invalid'}],
    wallet:{provider:()=>provider,currentAddresses:async()=>({evm:options.owner?options.owner():OWNER})},
    api:async(action,payload)=>{
      events.push(action);
      if(options.api) await options.api(action,payload);
      if(action==='claim-step') {if(claims.has(payload.fingerprint))throw Error('重复请求');claims.add(payload.fingerprint);}
    },onHash:h=>hashes.push(h),onProgress:()=>{},onStage:()=>{}});
  return {...f,run,events,sent,hashes,receiptTransports,expire:()=>{now=f.execution.expiresAt+1;},timeout:()=>timer?.()};
}

test('Binance native swap uses no Relay; live simulation precedes one durable claim and send',async()=>{
  const h=harness();assert.equal((await h.run()).status,'submitted');
  assert.equal(h.sent.length,1);assert.equal(h.hashes.length,1);
  assert(h.events.indexOf('preflight')<h.events.indexOf('claim-step'));
  assert(h.events.indexOf('eth_call')<h.events.indexOf('claim-step'));
  assert(h.events.indexOf('claim-step')<h.events.indexOf('eth_sendTransaction'));
  assert.equal(h.sent[0].to,ROUTER);assert.equal(h.sent[0].gasPrice,'0xf4240');
  await assert.rejects(h.run(),/重复/);assert.equal(h.sent.length,1);
});
test('exact stable approvals wait for their receipts before fresh swap simulation',async()=>{
  for(const approvals of [1,2]) {
    const h=harness({approvals});await h.run();
    assert.equal(h.sent.length,approvals+1);
    assert.equal(h.events.filter(x=>x==='preflight').length,1);
    assert(h.events.indexOf('receipt')<h.events.indexOf('preflight'));
    assert.equal(BigInt('0x'+h.sent[approvals-1].data.slice(-64)),10000000n);
    if(approvals===2)assert.equal(BigInt('0x'+h.sent[0].data.slice(-64)),0n);
    assert.deepEqual(h.receiptTransports,Array(approvals+1).fill('wallet'));
  }
});
test('failed approval and expiry after approval never proceed to swap',async()=>{
  for(const expired of [false,true]) {
    const h=harness({approvals:1,receipt:async(_,__,expire)=>{if(expired)expire();return {status:expired?'success':'reverted'};}});
    await assert.rejects(h.run(),expired?/过期/:/授权交易失败/);
    assert.equal(h.sent.length,1);assert(!h.events.includes('preflight'));
  }
});
test('preflight failure, fresh balance loss and excessive gas block wallet sends',async()=>{
  for(const options of [
    {api:async action=>{if(action==='preflight')throw Error('模拟失败');}},
    {request:({method,params})=>method==='eth_call'&&params?.[0]?.to===ROUTER?Promise.reject(Error('execution reverted')):undefined},
    {request:({method})=>method==='eth_getBalance'?'0x0':undefined},
    {request:({method})=>method==='eth_gasPrice'?'0x3b9aca00':undefined}
  ]) {
    const h=harness(options);await assert.rejects(h.run(),/模拟失败|链上试运行失败|余额不足|燃料费|Gas/);
    assert.equal(h.sent.length,0);assert(!h.events.includes('claim-step'));
  }
});
test('a low vendor gas quote no longer rejects a small real fee within the confirmed budget',async()=>{
  const h=harness({request:({method})=>method==='eth_gasPrice'?'0x3b9aca00':undefined});
  h.execution.quote.fees={gas:{maxAmount:'100000000000000'}};
  h.preview.quoteHash=hash(h.execution.quote);
  await h.run();assert.equal(h.sent.length,1);
  assert(BigInt(h.sent[0].gas)*BigInt(h.sent[0].gasPrice)<=100000000000000n);
});
test('confirmed 20 percent policy accepts lower auto slip but cannot alter an old 5 percent preview',async()=>{
  const h=harness();h.execution.intent.slippageBps=2000;h.preview.slippageBps=2000;
  h.execution.quote.raw.tx.slippagePercent='12';h.execution.quote.details.slippageTolerance.total='1200';
  h.preview.quoteHash=hash(h.execution.quote);await h.run();assert.equal(h.sent.length,1);
  const old=harness();old.execution.intent.slippageBps=2000;
  await assert.rejects(old.run(),/滑点/);assert.equal(old.sent.length,0);
});
test('quote hash and provider binding reject changed transactions before any wallet call',async()=>{
  for(const mutate of [h=>h.preview.quoteHash='0'.repeat(64),h=>h.preview.provider='relay']) {
    const h=harness();mutate(h);await assert.rejects(h.run(),/内容|交易服务/);assert.equal(h.events.length,0);
  }
});
test('displayed approval, target and recipient summary is bound before any wallet call',async()=>{
  for(const mutate of [h=>h.preview.executionSummary.approval.spender=TOKEN,
    h=>h.preview.executionSummary.targetAddress=TOKEN,h=>h.preview.executionSummary.recipient=TOKEN]) {
    const h=harness({approvals:1});mutate(h);
    await assert.rejects(h.run(),/授权对象、买入目标或收款地址/);
    assert.equal(h.events.length,0);assert.equal(h.sent.length,0);
  }
});
test('account change and unsuccessful chain switch block all sends',async()=>{
  for(const opts of [{owner:()=>TOKEN},{request:({method})=>method==='eth_chainId'?'0x1':undefined}]) {
    const h=harness(opts);await assert.rejects(h.run(),/账户发生变化|尚未切换/);assert.equal(h.sent.length,0);
  }
});
test('cancelled or empty wallet result is never retried',async()=>{
  for(const send of [async()=>{throw Error('user rejected');},async()=>undefined]) {
    const h=harness({send});await assert.rejects(h.run(),/user rejected|有效交易哈希/);
    await assert.rejects(h.run(),/重复/);assert.equal(h.sent.length,1);assert.equal(h.hashes.length,0);
  }
});
test('late wallet hash is remembered after timeout, without another send',async()=>{
  let resolve;
  const h=harness({send:()=>new Promise(r=>resolve=r)}), running=h.run();
  while(!resolve)await new Promise(r=>setImmediate(r));
  h.timeout();await assert.rejects(running,/尚未返回哈希/);
  resolve('0x'+'ab'.repeat(32));await new Promise(r=>setImmediate(r));
  assert.equal(h.hashes.length,1);await assert.rejects(h.run(),/重复/);assert.equal(h.sent.length,1);
});
test('replacement must match recipient, calldata, sender and exact payment',async()=>{
  const h=harness({approvals:1,receipt:async args=>{
    args.onReplaced({reason:'replaced',transaction:{hash:'0x'+'ab'.repeat(32),from:OWNER,to:TOKEN,input:'0xdeadbeef',value:0n}});
    return {status:'success'};
  }});
  await assert.rejects(h.run(),/替换了原交易/);assert.equal(h.sent.length,1);
});
