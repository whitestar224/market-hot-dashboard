(function () {
  'use strict';
  const labels = {pending:'待播报',starting:'等待窗口显示',displayed:'已显示 · 未确认已读',failed:'显示失败 · 已保留',expired:'已过时效 · 仅作记录',read:'已读'};
  function safeLink(value) {
    try { const url = new URL(value, typeof location !== 'undefined' ? location.href : 'http://127.0.0.1:8765/'); return /^https?:$/.test(url.protocol) ? url.href : ''; } catch { return ''; }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {safeLink, labels};
  if (typeof document === 'undefined') return;
  const list=document.getElementById('list'),status=document.getElementById('status'),older=document.getElementById('older');
  const archive=document.getElementById('alertArchive');
  if(archive&&location.hash==='#alertArchive')archive.open=true;
  window.addEventListener('hashchange',()=>{if(archive&&location.hash==='#alertArchive')archive.open=true;});
  let unread=true,cursor=null,busy=false,generation=0;
  function el(tag,text,cls) {const node=document.createElement(tag);node.textContent=text;if(cls)node.className=cls;return node;}
  function card(item) {
    const article=el('article','',item.readAt?'is-read':'');
    const when=new Date(Number(item.eventTime)||item.createdAt).toLocaleString('zh-CN');
    article.append(el('div',`${item.source} · ${when}`,'meta'),el('h2',item.title),el('div',item.readAt?'已读':(labels[item.state]||'已保留'),'state'),el('p',item.body));
    if(item.reason)article.append(el('p',item.reason,'reason'));
    const actions=el('div','','actions'),url=safeLink(item.url);
    if(url){const link=el('a','查看来源 ↗');link.href=url;link.target='_blank';link.rel='noopener noreferrer';actions.append(link);}
    if(!item.readAt){const read=el('button','标为已读');read.type='button';read.onclick=async()=>{read.disabled=true;try{const res=await fetch('/api/alert-inbox/read',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:item.id})});const result=await res.json();if(!res.ok||!result.ok)throw Error(); await load(false);}catch{status.textContent='保存失败，请重试';read.disabled=false;}};actions.append(read);}
    article.append(actions);return article;
  }
  async function load(append=false){
    if(busy)return;busy=true;const version=generation;
    try {const res=await fetch(`/api/alert-inbox?unread=${unread?'1':'0'}${append&&cursor?`&before=${cursor}`:''}`,{signal:AbortSignal.timeout(8000)});const data=await res.json();if(!res.ok||!data.ok)throw Error();if(version!==generation)return;
      if(!append)list.replaceChildren();for(const item of data.items)list.append(card(item));cursor=data.nextBefore;older.hidden=!cursor;
      document.getElementById('unreadCount').textContent=data.unread;status.textContent=`${data.pending} 条待播报 · 自动更新`;
      if(!list.children.length)list.append(el('p',unread?'没有未读播报':'还没有播报记录','empty'));
    } catch {if(version===generation)status.textContent='连接暂时中断，记录保留，稍后自动重连';} finally {busy=false; if(version!==generation)load(false);}
  }
  document.querySelectorAll('[data-filter]').forEach(button=>button.onclick=()=>{unread=button.dataset.filter==='unread';generation++;cursor=null;document.querySelectorAll('[data-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));load(false);});
  older.onclick=()=>load(true);load();
  // Do not replace long/paginated history while the user is reading it.
  setInterval(()=>{if(!document.hidden&&(!archive?.open||window.scrollY<100))load(false);},5000);
})();
