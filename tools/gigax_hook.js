// GigaX token hook v3 — attach 模式，VM 已就绪，全覆盖诊断
'use strict';
function emit(tag, val) { try { send(tag + '|' + val); } catch (e) {} }
emit('LOG', 'v3 script loaded');

var _installed = false;
function installHooks() {
  if (_installed) return;
  _installed = true;
  emit('LOG', 'Java.perform START');

  // 1) Cipher.doFinal —— token 从 Keystore 解密的瞬间
  try {
    var Cipher = Java.use('javax.crypto.Cipher');
    Cipher.doFinal.overload('[B').implementation = function (input) {
      var ret = this.doFinal(input);
      try {
        var s = Java.use('java.lang.String').$new(ret, 'UTF-8');
        if (s && s.indexOf('eyJ') >= 0) emit('DECRYPTED', s);
      } catch (e) {}
      return ret;
    };
    emit('LOG', 'Cipher.doFinal([B) OK');
  } catch (e) { emit('LOG', 'Cipher ERR ' + String(e).slice(0,100)); }

  // 2) OkHttp Request.Builder —— 每个请求的 Authorization 头
  function hookBuilder(cn) {
    try {
      var C = Java.use(cn);
      var ms = C.class.getDeclaredMethods().map(function(m){return m.getName();});
      emit('LOG', cn + ' methods=' + ms.slice(0,20).join(','));
      ['addHeader','header'].forEach(function(name){
        try {
          C[name].overload('java.lang.String','java.lang.String').implementation = function(n,v){
            if (n && n.toLowerCase()==='authorization') emit('AUTH', v);
            return this[name](n,v);
          };
        } catch(e) {}
      });
      emit('LOG', cn + ' hooked');
    } catch (e) { emit('LOG', 'miss ' + cn + ' ' + String(e).slice(0,80)); }
  }
  hookBuilder('okhttp3.Request$Builder');
  hookBuilder('okhttp3.Headers$Builder');
  hookBuilder('okhttp3.Request');

  // 3) Map.put —— Authorization 落 map 的瞬间（兜底）
  ['java.util.HashMap','java.util.LinkedHashMap'].forEach(function(cn){
    try {
      var M = Java.use(cn);
      M.put.overload('java.lang.Object','java.lang.Object').implementation = function(k,v){
        var ks = (k==null?'':String(k)), vs = (v==null?'':String(v));
        if (ks.toLowerCase()==='authorization' && vs.indexOf('eyJ')>=0) emit('AUTH', vs);
        return this.put(k,v);
      };
    } catch(e) {}
  });
  emit('LOG', 'ALL HOOKS INSTALLED');
}

// attach 已运行进程时，顶层 Java.perform 可能拿不到 VM 线程。
// 用 Java.performNow 立即执行 + setImmediate 兜底 + Java.enumerateClassLoaders 触发 VM 挂载。
function tryInstall() {
  try { Java.perform(installHooks); }
  catch (e) {
    emit('LOG', 'perform err ' + String(e).slice(0,100));
    try { Java.performNow(installHooks); } catch (e2) {}
  }
}
setImmediate(tryInstall);
setTimeout(tryInstall, 500);
setTimeout(tryInstall, 2000);
