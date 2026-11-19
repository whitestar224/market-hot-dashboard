"""浮层「一秒钟抖一次」的回归探针（假浮层 + 真桥接，不依赖 GMGN 登录态）。

背景（2026-11-19）：用户报「浮窗会出现两个版本，第一个版本字稍大一点，过一会儿就变成
字体很小的版本」。诊断埋点（`.runtime-cache/smart_money_restart.log` 里的 `_diag?ev=`）
看出来是一个 1 秒级的死循环：

    ev=render shown=4oduvmdiWG… hov=4oduvmdiWG/hover ptr=m4ckeq8s8j
    ev=stale  shown=4oduvmdiWG… hov=4oduvmdiWG/hover ptr=m4ckeq8s8j
    ev=render … ev=stale … （每 1~6 秒一轮）

根因：叙事浮层**盖住了指针**。`caFromPointer()` 会跳过浮层再往下面穿透，于是穿透到
的永远是「浮层背后那张卡片/列表行」——另一枚币。`staleTarget()` 的 C 判据拿它跟
「浮层正在显示的币」比，就 1 秒判一次「换币」：`resetRoot()` 把 GMGN 原生文案还原
（原生字号 ~11px = 用户说的「字体很小的版本」），下一轮又从缓存重绘（15px）—— 如此往复。

新版的两道闸：
  1. `caFromPointer()`：指针最上面那层就是浮层时，直接返回 null（穿透答案无意义）；
  2. `staleTarget()`：`hoverTarget()` 的纯几何判据（why='hover'）说「还是这个币」时，
     在 C 判据之前就 return false。
另外 `renderNarrative()` 现在把正文包在 `.gigax-narrative-body` 里并显式给字号，
所以即使容器被 React 冲掉，也不会掉回原生小字（丢了容器会就地重排）。

用法：
    C:/Python314/python.exe tools/gigax_flicker_check.py            # 现版本，期望 stale=0
    C:/Python314/python.exe tools/gigax_flicker_check.py --old      # 打回 1.7.3 语义做 A/B
    C:/Python314/python.exe tools/gigax_flicker_check.py --secs 25

判定：`styles` = 浮层正文的样式变过几次（0 = 从头到尾只有一版），`stales` = 判定换币次数。
A/B 时旧版必须复现出 stale>0，新版必须 0 —— 否则说明这个探针没有真的测到那个 bug。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gmgn_dom_probe import Mcp, unwrap   # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USERSCRIPT = os.path.join(ROOT, "tools", "gmgn-gigax-narrative.user.js")
OUT_JS = r"C:\gmgn-probe\js\flicker.js"

# 浮层里显示的币（桥接里有叙事）与被浮层盖住的那张卡片（另一枚币）
CA_SHOWN = "9hjBLBrc7WZW2H8UQvBAvG5WiTZotXUv2Bdcn4f4pump"
CA_BEHIND = "4oduvmdiWGDv33kDvKHiGTX1UFseMNoB8g9yZ1h4pump"

# 旧版语义：把两道闸拆掉，其余一字不改
OLD_PATCHES = [
    ("if (stack.length && root && (root === stack[0] || root.contains(stack[0]))) return null;",
     "if (false) return null;"),
    ("for (const r of knownRoots) {", "for (const r of []) {"),
    ("if (h && h.ca === shown && h.why === 'hover') return false;", "if (false) return false;"),
]

SHIMS = r"""
  window.__reqs = [];
  try { for (const k of Object.keys(localStorage)) if (k.startsWith('gm_')) localStorage.removeItem(k); } catch (e) {}
  const __ls = window.localStorage;
  window.GM_getValue = (k, d) => { try { const v = __ls.getItem('gm_' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } };
  window.GM_setValue = (k, v) => { try { __ls.setItem('gm_' + k, JSON.stringify(v)); } catch (e) {} };
  window.GM_registerMenuCommand = () => {};
  window.GM_xmlhttpRequest = (o) => {
    window.__reqs.push(String(o.url));
    fetch(o.url, { cache: 'no-store' }).then(r => r.text()).then(t => {
      if (o.onload) o.onload({ responseText: t, status: 200, readyState: 4 });
    }).catch(e => { if (o.onerror) o.onerror(e); });
    return { abort() {} };
  };
"""

FIXTURE = r"""
  const CA_SHOWN = '__CA_SHOWN__';
  const CA_BEHIND = '__CA_BEHIND__';
  const SECS = __SECS__;
  const DROP = __DROP__;

  const wrap = document.createElement('div');
  wrap.style.cssText = 'position:fixed;inset:0;z-index:2147483000;background:#111;';
  wrap.innerHTML = `
    <div data-trenches-token-address="${CA_SHOWN}" data-trenches-token-chain="sol" data-trenches-token-key="${CA_SHOWN}-sol"
         style="position:absolute;left:600px;top:200px;width:320px;height:140px;z-index:1;background:#1b1b22;color:#eee;font-size:14px;">
      <a href="/sol/token/${CA_SHOWN}" style="color:#8cf;">SHOWNCOIN</a>
      <span style="font-size:12px;">$12.3万 +4.5%</span>
    </div>
    <div data-trenches-token-address="${CA_BEHIND}" data-trenches-token-chain="sol" data-trenches-token-key="${CA_BEHIND}-sol"
         style="position:absolute;left:700px;top:240px;width:320px;height:140px;z-index:5;background:#232330;color:#eee;font-size:14px;">
      <a href="/sol/token/${CA_BEHIND}" style="color:#8cf;">BEHINDCOIN</a>
      <span style="font-size:12px;">$9.8万 -1.2%</span>
    </div>
    <div class="tooltip-pop" id="fake-pop"
         style="position:fixed;left:740px;top:250px;width:420px;height:320px;z-index:50;background:#101018;border:1px solid #333;padding:14px;">
      <div style="color:#eee;font-size:16px;font-weight:700;margin-bottom:8px;">SHOWNCOIN</div>
      <div class="narr-body" style="font-size:11px;line-height:1.4;color:#ccc;">这是 GMGN 原生生成的一段代币介绍占位文字，用来模拟浮层正文节点。它没有任何锚点词，长度也够长，所以脚本应该把它当成要替换的那一段。</div>
      <div class="narr-law" style="color:#666;font-size:10px;margin-top:10px;">免责声明：人工智能生成内容，投资前请自行研究。</div>
    </div>`;
  document.body.appendChild(wrap);
  document.body.style.overflow = 'hidden';

  const P = { x: 760, y: 270 };
  const bodyEl = () => document.querySelector('#fake-pop .narr-body');

  // 1) 指针落点（脚本的 lastPointer 只认 mousemove）
  for (const t of [document, wrap]) {
    t.dispatchEvent(new MouseEvent('mousemove', { clientX: P.x, clientY: P.y, bubbles: true }));
  }
  // 2) 先让脚本记下「hover 的是 SHOWNCOIN 那张卡」（真实场景里浮层就是它弹出来的）
  const cardA = document.querySelector('[data-trenches-token-address="' + CA_SHOWN + '"]');
  cardA.dispatchEvent(new MouseEvent('mouseover', { clientX: P.x, clientY: P.y, bubbles: true }));
  // 3) 指针仍在原处，但浮层此刻已经盖上来（真实场景：浮层跟着卡片弹在指针上方）

  const snap = () => {
    const b = bodyEl();
    if (!b) return null;
    // 量的是「我们写进去的那一层」：有容器就量容器，否则量宿主节点（=原生长什么样）
    const w = b.querySelector('.gigax-narrative-body');
    const t = w || b;
    return {
      fs: getComputedStyle(t).fontSize,
      ls: getComputedStyle(t).lineHeight,
      hostFs: getComputedStyle(b).fontSize,
      wrap: !!w,
      head: (b.innerText || '').slice(0, 24),
    };
  };

  const styles = [];
  let lastKey = '';
  const tick = () => {
    const s = snap();
    const key = s ? [s.fs, s.ls, s.wrap, s.head].join('|') : 'null';
    if (key !== lastKey) { lastKey = key; styles.push({ at: Date.now(), ...s }); }
  };
  const iv = setInterval(tick, 150);
  tick();

  // 模拟「React 把我们的容器冲掉，只剩纯文本」（旧版写法的遗留状态）。
  // 新版应该发现容器丢了 → 按缓存就地重排 → 字号回到 15px。
  if (DROP) setTimeout(() => {
    const b = bodyEl();
    if (!b) return;
    const t = b.innerText || '';
    b.innerHTML = '【GIGA X 叙事】 ' + t.slice(t.indexOf('】') + 1);
  }, 6000);

  return new Promise(resolve => {
    setTimeout(() => {
      clearInterval(iv);
      const reqs = window.__reqs || [];
      const ev = (name) => reqs.filter(u => u.includes('_diag?ev=' + name)).length;
      const narrative = reqs.filter(u => u.includes('/narrative?'));
      resolve(JSON.stringify({
        styles,
        stales: ev('stale'),
        renders: ev('render'),
        nodetect: ev('nodetect'),
        narratives: narrative.map(u => u.replace(/^.*[?&]address=/, '').slice(0, 12)),
        final: snap(),
        secs: SECS,
      }, null, 1));
    }, SECS * 1000);
  });
"""


def build_js(src: str, secs: int, drop: bool = False) -> str:
    body = ('() => {\n' + SHIMS + '\n' + src + '\n' + FIXTURE + '\n}\n')
    return (body.replace('__CA_SHOWN__', CA_SHOWN)
                .replace('__CA_BEHIND__', CA_BEHIND)
                .replace('__SECS__', str(secs))
                .replace('__DROP__', 'true' if drop else 'false'))


def main():
    ap = argparse.ArgumentParser(description="浮层抖动回归探针（假浮层 + 真桥接）")
    ap.add_argument("--old", action="store_true", help="打回 1.7.3 语义（拆掉两道闸）做 A/B")
    ap.add_argument("--secs", type=int, default=16, help="观察窗口（秒）")
    ap.add_argument("--url", default="http://127.0.0.1:8765/api/gigax/_diag?ev=fixture",
                    help="必须与桥接同源；用 /api/gigax/_diag 这种「干净 404 文本页」，页面自带 DOM 才不会干扰")
    ap.add_argument("--drop", action="store_true",
                    help="6 秒时把 .gigax-narrative-body 冲掉，验「容器丢了会不会自愈」")
    ap.add_argument("--expect", choices=["clean", "dirty", "heal"], default=None,
                    help="断言：clean = 只允许一版样式且 stale=0；dirty = 必须复现抖动；"
                         "heal = 配合 --drop，末尾必须重新带上容器且 15px")
    a = ap.parse_args()

    src = open(USERSCRIPT, encoding="utf-8").read()
    if a.old:
        for old, new in OLD_PATCHES:
            if old not in src:
                raise SystemExit("补丁没打上（源码变了？）：" + old[:60])
            src = src.replace(old, new, 1)

    os.makedirs(os.path.dirname(OUT_JS), exist_ok=True)
    with open(OUT_JS, "w", encoding="utf-8") as f:
        f.write(build_js(src, a.secs, a.drop))
    print(f"[fixture] {'OLD(1.7.3 语义)' if a.old else 'CURRENT'} → {OUT_JS}")

    m = Mcp()
    try:
        m.init()
        m.call("navigate_page", {"pageId": 1, "url": a.url, "timeout": 60000})
        time.sleep(2)
        print("[probe] 注入脚本 + 假浮层，观察", a.secs, "秒…")
        out = unwrap(m.call("evaluate_script", {
            "pageId": 1,
            "function": open(OUT_JS, encoding="utf-8").read(),
        }, timeout=a.secs + 120))
    finally:
        m.close()

    data = out if isinstance(out, dict) else json.loads(out)
    print(json.dumps(data, ensure_ascii=False, indent=1))

    styles = data.get("styles") or []
    final = data.get("final") or {}
    stales = int(data.get("stales") or 0)
    ok = True
    if final.get("fs") and final.get("fs") != "15px":
        print(f"!! 最终字号 {final.get('fs')} ≠ 15px")
        ok = False
    if not final.get("wrap"):
        print("!! 最终正文没有 .gigax-narrative-body 容器")
        ok = False
    if a.expect == "clean":
        if stales:
            print(f"!! 仍在抖动：stale={stales}")
            ok = False
        # 第一版是「还没被替换的原生文案」，正常；从那之后只允许一版
        # 「GIGA X 叙事加载中…」也是 GIGA X，别把它算成一版
        after = [s for s in styles if "【GIGA X 叙事" in (s.get("head") or "")]
        if len(after) > 1:
            print(f"!! 替换后样式变了 {len(after)} 次（用户说的「两个版本」）")
            ok = False
    elif a.expect == "heal":
        if not (final.get("wrap") and final.get("fs") == "15px"):
            print("!! 容器被冲掉后没自愈回 15px")
            ok = False
    elif a.expect == "dirty":
        if not stales:
            print("!! 旧语义没复现出抖动 —— 探针没测到那个 bug，别拿它当验收")
            ok = False
    print(("PASS" if ok else "FAIL") + f"  styles={len(styles)} stales={stales} renders={data.get('renders')}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
