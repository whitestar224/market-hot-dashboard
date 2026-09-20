const test=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const ui=fs.readFileSync(require.resolve('../monitor-buy.js'),'utf8');
test('amount debounce replaces previous read-only quote; close, edit and execution cancel stale work',()=>{
  const timers=new Map(), calls=[];let next=0;
  const ctx={autoQuoteTimer:null,generation:1,executing:false,config:{maxOrderUsd:200},
    dialog:{open:true,querySelector:()=>({value:'10'})},requestQuote:()=>calls.push('quote'),
    setTimeout:fn=>{timers.set(++next,fn);return next;},clearTimeout:id=>timers.delete(id)};
  vm.createContext(ctx);vm.runInContext(ui.slice(ui.indexOf('  function scheduleAutoQuote()'),ui.indexOf('  function prepareTarget(')),ctx);
  ctx.scheduleAutoQuote();ctx.scheduleAutoQuote();assert.equal(timers.size,1);
  [...timers.values()][0]();assert.equal(calls.length,1);assert.equal(calls[0],'quote');
  for (const change of [()=>ctx.generation++,()=>ctx.dialog.open=false,()=>ctx.executing=true]) {
    ctx.dialog.open=true;ctx.executing=false;timers.clear();ctx.scheduleAutoQuote();change();
    [...timers.values()][0]();assert.equal(calls.length,1);
  }
});
test('automatic quote uses passive global accounts and never connects or signs a wallet',async()=>{
  const calls=[];let notice='';
  const button={disabled:false,dataset:{},hasAttribute:n=>n==='data-buy-quote-button'};
  const ctx={busy:false,executing:false,generation:1,preview:null,quoteTask:null,pendingBuy:null,target:{chainId:56,identityKey:'ok',identityExpiresAt:Date.now()+60000},
    core:{validTarget:()=>true,SOLANA:792703809},config:{maxOrderUsd:200},activeKey:'binance',sourceChains:[56],
    dialog:{querySelector:()=>({value:'10',replaceChildren(){}})},invalidate:()=>{},resetProgress:()=>{},setBusy:()=>{},updateProgress:()=>{},
    selectedWalletIdentity:()=>'',window:{XingyunMonitorWallets:{activeKey:()=> 'binance',currentAddresses:async()=>{calls.push('passive');return {};}}},
    connect:async()=>{calls.push('connect');throw Error('unexpected authorization');},api:async()=>calls.push('api'),
    pauseQuoteProgress:()=>{},notice:text=>notice=text};
  vm.createContext(ctx);vm.runInContext(ui.slice(ui.indexOf('  function requestQuote('),ui.indexOf('  function renderQuote()')),ctx);
  await ctx.requestQuote();
  assert.deepEqual(calls,['passive']);assert.match(notice,/全局钱包/);
  ctx.window.XingyunMonitorWallets.currentAddresses=async()=>({evm:'0x'+'12'.repeat(20)});
  ctx.api=async(action,body)=>{calls.push(action);assert.equal(body.amountUsdt,'10');assert.equal(body.autoSlippage,true);assert.equal(body.slippageBps,2000);throw Error('read-only quote test');};
  await ctx.requestQuote();
  assert.deepEqual(calls,['passive','quote']);assert.match(notice,/read-only quote test/);
});

test('a Buy click shares the exact in-flight quote; double clicks cannot submit twice',async()=>{
  let finish,quotes=0,confirmed=0;
  const ctx={generation:1,executing:false,pendingBuy:null,autoQuoteTimer:null,preview:null,busy:true,dialog:{open:true},
    activeKey:'binance',target:{chainId:56},core:{SOLANA:792703809},selectedWalletIdentity:()=> 'binance:owner',
    window:{XingyunMonitorWallets:{snapshot:()=>({evm:'0xowner'}),adapter:()=>({label:'Binance Wallet'})}},
    setBusy:()=>{},requestQuote:()=>{quotes++;return new Promise(resolve=>finish=resolve);},confirm:async()=>{confirmed++;}};
  vm.createContext(ctx);vm.runInContext(ui.slice(ui.indexOf('  async function requestBuy('),ui.indexOf('  function onChange(')),ctx);
  const first=ctx.requestBuy();await ctx.requestBuy();assert.equal(quotes,1);assert.equal(confirmed,0);
  finish({ok:true});await first;assert.equal(confirmed,1);
});
test('editing, closing or changing the wallet cancels a pending Buy intent',async()=>{
  for(const cancel of [c=>{c.generation++;c.pendingBuy=null;},c=>c.dialog.open=false]){
    let finish,confirmed=0;
    const ctx={generation:1,executing:false,pendingBuy:null,autoQuoteTimer:null,preview:null,busy:true,dialog:{open:true},
      activeKey:'binance',target:{chainId:56},core:{SOLANA:792703809},selectedWalletIdentity:()=> 'binance:owner',
      window:{XingyunMonitorWallets:{snapshot:()=>({evm:'0xowner'}),adapter:()=>({label:'Binance Wallet'})}},
      setBusy:()=>{},requestQuote:()=>new Promise(resolve=>finish=resolve),confirm:async()=>confirmed++};
    vm.createContext(ctx);vm.runInContext(ui.slice(ui.indexOf('  async function requestBuy('),ui.indexOf('  function onChange(')),ctx);
    const buying=ctx.requestBuy();cancel(ctx);finish({ok:true});await buying;assert.equal(confirmed,0);
  }
});
test('an explicit Buy click connects the active default wallet and continues to confirmation',async()=>{
  let connected=false,quotes=0,confirmed=0,message='';
  const connectButton={hidden:false};
  const ctx={generation:1,executing:false,pendingBuy:null,autoQuoteTimer:null,quoteTask:null,preview:null,busy:false,
    activeKey:'binance',target:{chainId:56},core:{SOLANA:792703809},walletIdentity:'',connectingForBuy:false,
    dialog:{open:true,querySelector:selector=>selector==='[data-buy-connect]'?connectButton:null},
    window:{XingyunMonitorWallets:{snapshot:()=>({evm:connected?'0xowner':''}),adapter:()=>({label:'Binance Wallet'})}},
    selectedWalletIdentity:()=>connected?'binance:owner':'binance:',setBusy:()=>{},notice:text=>message=text,
    connect:async()=>{connected=true;},requestQuote:async()=>{quotes++;return {ok:true};},confirm:async()=>{confirmed++;}};
  vm.createContext(ctx);vm.runInContext(ui.slice(ui.indexOf('  async function requestBuy('),ui.indexOf('  function onChange(')),ctx);
  await ctx.requestBuy();
  assert.equal(connected,true);assert.equal(quotes,1);assert.equal(confirmed,1);assert.equal(connectButton.hidden,true);
  assert.match(message,/Binance Wallet/);
});
test('in-flight quote calls share one HTTP request and passive lookup',async()=>{
  let finish,calls=0;
  const ctx={quoteTask:null,pendingBuy:null,generation:1,preview:null,target:{chainId:56,identityKey:'ok',identityExpiresAt:Date.now()+60000},
    config:{maxOrderUsd:200},activeKey:'binance',sourceChains:[56],core:{validTarget:()=>true,SOLANA:792703809},
    dialog:{querySelector:()=>({value:'10',replaceChildren(){}})},resetProgress:()=>{},setBusy:()=>{},updateProgress:()=>{},
    selectedWalletIdentity:()=>'',window:{XingyunMonitorWallets:{activeKey:()=> 'binance',currentAddresses:async()=>({evm:'0x'+'12'.repeat(20)})}},
    api:()=>{calls++;return new Promise(resolve=>finish=resolve);},renderQuote:()=>{},notice:()=>{},pauseQuoteProgress:()=>{}};
  vm.createContext(ctx);vm.runInContext(ui.slice(ui.indexOf('  function requestQuote('),ui.indexOf('  function renderQuote()')),ctx);
  const a=ctx.requestQuote(),b=ctx.requestQuote();assert.equal(a,b);
  await new Promise(resolve=>setImmediate(resolve));assert.equal(calls,1);
  finish({ok:true,expiresAt:Date.now()+60000});assert.equal((await a).ok,true);
  assert.equal(ctx.quoteTask,null);
});
