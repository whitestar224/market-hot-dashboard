"""机会标记（蓝底卡片）误标的回归探针：历史结论不许翻案。

背景（2026-11-19，用户：`这个叙事并没有命中关键词，怎么就被标记了呢`
`0x70c35410b062cafb251a4826e451a99859057777` 慈善CZ）：
桥接返回的正文 110 字，用脚本同一套词表跑，**强词 0 命中、弱词 0 命中**，卡片却是蓝的。
根因在 `paintHotCards()`：

    for (const [key, entry] of cache) {        // 当前正文
      if (!entry || !entry.text) continue;
      const hits = matchHot(entry.text);
      if (!hits.length) continue;             // ← 不命中就 continue，币没进 marks
      marks.set(ca, hits);
    }
    for (const ca of Object.keys(hotIndex)) {  // 历史结论（24h）
      if (marks.has(ca)) continue;             // ← 于是这里把它标回去了
      marks.set(ca, v.h.split('、'));
    }

GIGA X 会重写叙事，上一版命中过的词（`首个`/`首发`…）被 `hotIndex` 记住 24 小时，
于是「当前正文明明不命中，卡片还是蓝的」。

修法：`known` 集合（这一轮正文已拿到手的币）——`marks.has(ca) || known.has(ca)` 都不许补位，
并且当前正文不命中时 `dropHot(ca)` 立刻删掉那条历史结论。

用法：
    C:/Python314/python.exe tools/gigax_hotmark_check.py            # 现版本，期望「不标」
    C:/Python314/python.exe tools/gigax_hotmark_check.py --old      # 打回 1.7.5 之前，期望「误标」

做法：把 `hotIndex` 预置一条假命中 → 注入真实 userscript → 放一张真实 CA 的卡片 →
等脚本自己从桥接取到正文（不命中）→ 看卡片上有没有 `.gigax-hot-card`。
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
OUT_JS = r"C:\gmgn-probe\js\hotmark.js"

CA = "0x70c35410b062cafb251a4826e451a99859057777"   # 慈善CZ（bsc），正文不命中任何关键词
CA_NO_NARRATIVE = "0x1111111111111111111111111111111111111111"   # 桥接明确说没有叙事
# 只出现在「持仓表」里的那个币：**行情榜里没有它的卡片**，所以它是持仓豁免唯一能测到的样本
# （同一个币在榜上也有卡片时，cardCache 会把榜上那张复用到持仓行，持仓行根本不会被单独处理）
CA_HOLD_ONLY = "0x2222222222222222222222222222222222222222"
# 叙事里明确提到「agent id / AI 智能体」的币（bsc）—— 验证 AI 词走**白色**标记
CA_AGENT = "0xe5881c31162488d335aab82a20cd4bf156487777"
FAKE_HIT = "首个"                                     # 假装「上一版正文」命中过它

# 旧语义：拆掉 known 这道闸（补位不再看「当前正文已经取到」）
OLD_PATCHES = [
    ("if (marks.has(ca) || known.has(ca)) continue;", "if (marks.has(ca)) continue;"),
    ("if (!hits.length) { dropHot(ca); continue; }", "if (!hits.length) continue;"),
    # 持仓豁免也一起拆掉，`--holdings --old` 才能证明这条闸真的在起作用
    ("if (inHoldingsPanel(card)) return;", "if (false) return;"),
]

SHIMS = r"""
  window.__reqs = [];
  const __ls = window.localStorage;
  try { for (const k of Object.keys(__ls)) if (k.startsWith('gm_')) __ls.removeItem(k); } catch (e) {}
  window.GM_getValue = (k, d) => { try { const v = __ls.getItem('gm_' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } };
  window.GM_setValue = (k, v) => { try { __ls.setItem('gm_' + k, JSON.stringify(v)); } catch (e) {} };
  window.GM_registerMenuCommand = () => {};
  window.GM_xmlhttpRequest = (o) => {
    window.__reqs.push(String(o.url));
    if (__FAIL_NET__ && String(o.url).includes('/narrative')) {   // 模拟断网：请求一律失败
      setTimeout(() => { if (o.onerror) o.onerror({ error: 'simulated offline' }); }, 30);
      return { abort() {} };
    }
    fetch(o.url, { cache: 'no-store' }).then(r => r.text()).then(t => {
      if (o.onload) o.onload({ responseText: t, status: 200, readyState: 4 });
    }).catch(e => { if (o.onerror) o.onerror(e); });
    return { abort() {} };
  };
  // 预置一条「历史结论」：前端 GM 存储里已经有这个币的命中记录
  __ls.setItem('gm_hotIndex', JSON.stringify({
    '__CA__': { h: '__HIT__', ts: Date.now() },
    '__CA_HOLD__': { h: '__HIT__', ts: Date.now() },
  }));
  // 标记开关：1.7.7 起默认开；这里显式写 on 并把两个一次性迁移都标成已完成，结果才可复现
  __ls.setItem('gm_hotMark', JSON.stringify('on'));
  __ls.setItem('gm_hotMarkOnMigrated', JSON.stringify('v1'));
"""

FIXTURE = r"""
  const CA = '__CA__';
  const CA_HOLD = '__CA_HOLD__';
  const SECS = __SECS__;
  const wrap = document.createElement('div');
  wrap.style.cssText = 'position:fixed;inset:0;z-index:2147483000;background:#111;';
  wrap.innerHTML = `
    <table id="hd" style="position:absolute;left:200px;top:380px;width:520px;color:#ccc;font-size:12px;">
      <thead><tr><th>币种</th><th>当前买入</th><th>当前卖出</th><th>未实现</th></tr></thead>
      <tbody>
        <tr id="hd-row" style="background:#16161d;">
            <td style="height:32px;padding:6px 8px;"><a href="/bsc/token/${CA_HOLD}" style="color:#8cf;">持仓币ONLY</a></td>
            <td>$1.3万</td><td>$0</td><td>-2.1%</td></tr>
      </tbody>
    </table>
    <div data-trenches-token-address="${CA}" data-trenches-token-chain="bsc" data-trenches-token-key="${CA}-bsc"
         style="position:absolute;left:200px;top:200px;width:320px;height:140px;z-index:1;background:#1b1b22;color:#eee;">
      <a href="/bsc/token/${CA}" style="color:#8cf;">慈善CZ</a>
      <span style="font-size:12px;">$1.3万</span>
    </div>
`;
  document.body.appendChild(wrap);

  const card = () => document.querySelector('[data-trenches-token-address="' + CA + '"]');
  const shots = [];
  const iv = setInterval(() => {
    const c = card();
    if (!c) return;
    const shot = {
      at: Date.now(),
      hot: c.classList.contains('gigax-hot-card'),
      title: (c.getAttribute('title') || '').slice(0, 40),
      hit: c.dataset.gigaxHit || '',
    };
    const prev = shots[shots.length - 1];
    if (!prev || prev.hot !== shot.hot || prev.title !== shot.title) shots.push(shot);
  }, 200);

  return new Promise(resolve => {
    setTimeout(() => {
      clearInterval(iv);
      const reqs = window.__reqs || [];
      const nb = reqs.filter(u => u.includes('/narrative'));
      const hit = reqs.filter(u => u.includes('_diag?ev=paint'));
      resolve(JSON.stringify({
        shots,
        marked: !!(card() && (card().classList.contains('gigax-hot-card')
                              || card().classList.contains('gigax-ai-card'))),
        aiMarked: !!(card() && card().classList.contains('gigax-ai-card')),
        cardStyle: (() => {
          const c = card();
          if (!c) return null;
          const cs = getComputedStyle(c);
          return { bg: cs.backgroundColor, ring: cs.boxShadow.slice(0, 60) };
        })(),
        blueMarked: !!(card() && card().classList.contains('gigax-hot-card')),
        hotNodes: [...document.querySelectorAll('.gigax-hot-card,.gigax-ai-card')].map(e => e.tagName + '#' + (e.id || '') + '|' + (e.textContent || '').slice(0, 14)),
        holdingsMarked: (() => {
          // 标可能落在行上，也可能落在整张表上（cardFromAnchor 会爬到「像卡片」的那层），
          // 所以行、表、以及表内任何子树都要查
          const tbl = document.getElementById('hd');
          const row = document.getElementById('hd-row');
          if (!tbl || !row) return null;
          for (const el of [row, tbl, ...tbl.querySelectorAll('*')]) {
            if (el.classList && (el.classList.contains('gigax-hot-card')
                || el.classList.contains('gigax-ai-card'))) return true;
          }
          return false;
        })(),
        title: card() ? (card().getAttribute('title') || '').slice(0, 60) : '',
        narratives: nb.map(u => u.replace(/^.*[?&]address=/, '').slice(0, 12)),
        paintBeacons: hit.length,
        paintBeat: hit.length ? decodeURIComponent(hit[hit.length - 1].split('hit=')[1] || '') : '',
        secs: SECS,
        ls: {
          hotMark: localStorage.getItem('gm_hotMark'),
          hotIndex: (localStorage.getItem('gm_hotIndex') || '').slice(0, 120),
          onMig: localStorage.getItem('gm_hotMarkOnMigrated'),
        },
      }, null, 1));
    }, SECS * 1000);
  });
"""


def build(src: str, secs: int, ca: str = CA, fail_net: bool = False) -> str:
    return ('() => {\n' + SHIMS + '\n' + src + '\n' + FIXTURE + '\n}\n') \
        .replace('__FAIL_NET__', 'true' if fail_net else 'false') \
        .replace('__CA__', ca).replace('__CA_HOLD__', CA_HOLD_ONLY) \
        .replace('__HIT__', FAKE_HIT).replace('__SECS__', str(secs))


def main():
    ap = argparse.ArgumentParser(description="机会标记误标回归探针")
    ap.add_argument("--old", action="store_true", help="打回旧语义（补位不看当前正文）")
    ap.add_argument("--secs", type=int, default=18, help="观察窗口（秒）")
    ap.add_argument("--fallback", action="store_true",
                    help="模拟断网（叙事请求全失败 = 不知道）：补位兜底仍应标记")
    ap.add_argument("--ai", action="store_true",
                    help="用真实提到 agent id / AI 智能体的币，验证 AI 词走白色标记")
    ap.add_argument("--holdings", action="store_true",
                    help="同屏放一张行情卡 + 一行持仓表（同一个币）：行情卡要标，持仓行不许标")
    ap.add_argument("--narrative-none", action="store_true",
                    help="换成「桥接明确没叙事」的地址：1.7.6 起不许补位（期望不标）")
    ap.add_argument("--url", default="http://127.0.0.1:8765/api/gigax/_diag?ev=fixture")
    a = ap.parse_args()

    src = open(USERSCRIPT, encoding="utf-8").read()
    if a.old:
        for old, new in OLD_PATCHES:
            if old not in src:
                raise SystemExit("补丁没打上（源码变了？）：" + old[:60])
            src = src.replace(old, new, 1)

    os.makedirs(os.path.dirname(OUT_JS), exist_ok=True)
    with open(OUT_JS, "w", encoding="utf-8") as f:
        ca = CA_NO_NARRATIVE if a.narrative_none else (CA_AGENT if a.ai else CA)
        f.write(build(src, a.secs, ca, fail_net=a.fallback or a.holdings))
    print(f"[fixture] {'OLD' if a.old else 'CURRENT'} → {OUT_JS}")

    m = Mcp()
    try:
        m.init()
        m.call("navigate_page", {"pageId": 1, "url": a.url, "timeout": 60000})
        time.sleep(2)
        print("[probe] 注入脚本 + 预置历史命中，观察", a.secs, "秒…")
        out = unwrap(m.call("evaluate_script", {
            "pageId": 1, "function": open(OUT_JS, encoding="utf-8").read()}, timeout=a.secs + 120))
    finally:
        m.close()

    d = out if isinstance(out, dict) else json.loads(out)
    print(json.dumps(d, ensure_ascii=False, indent=1))

    ok = True
    if not d.get("narratives"):
        print("!! 一个叙事请求都没发（预取没覆盖到这张卡），这次结果不能当验收")
        ok = False
    if a.ai:
        if not d.get("aiMarked"):
            print("!! AI 词没有走白色标记（看 hotNodes / title）")
            ok = False
        if d.get("blueMarked"):
            print("!! AI 币同时被标成蓝色了（应当只有白色）")
            ok = False
    elif a.holdings:
        if not d.get("marked"):
            print("!! 行情卡没标上（这一条同时依赖断网兜底，先看 --fallback）")
            ok = False
        if d.get("holdingsMarked"):
            print("!! 持仓行被标了 —— 持仓面板豁免没生效")
            ok = False
    elif a.narrative_none:
        if d.get("marked"):
            print("!! 桥接明确说「没有叙事」，却仍然按历史结论标了")
            ok = False
    elif a.fallback:
        if d.get("aiMarked"):
            print("!! 非 AI 命中却用了白色标记")
        if not d.get("marked"):
            print("!! 补位兜底被弄坏了：正文取不到时应当仍然按历史结论标上")
            ok = False
    elif a.old:
        if not d.get("marked"):
            print("!! 旧语义没复现误标 —— 探针没测到那个 bug")
            ok = False
    else:
        if d.get("marked"):
            print("!! 仍然被历史结论误标：" + (d.get("title") or ""))
            ok = False
    print(("PASS" if ok else "FAIL") + f"  marked={d.get('marked')} shots={len(d.get('shots') or [])}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
