"""跨列同名隐藏的回归探针：已开盘列里有的币，内盘 / 待发射列必须藏掉，**不许闪**，**也不许留空**。

背景（2026-11-22，用户连提三次）：
  ① `已迁移卡片列表中有的币，如果在内盘出现了同名的，把内盘里同名的去掉` + `内盘和待发射的都隐藏`
  ② `衔接上啊，不要空在这里啊` —— 藏掉的卡片在列表里留了一个洞
  ③ `下面一大片黑不溜秋的` —— 洞补上了，但**可见内容不够铺满视口**，底部空一大块

战壕页实测（chrome-devtools-mcp，未登录，BSC，1600×1000）：
  · 三列**并排**，卡片左边距 273 / 610 / 946，列标题 `新创建` / `即将打满` / `已开盘`
    （＝ 内盘 / 待发射 / 已迁移；页面上**没有**「内盘」这些字，别按文字找）。
  · 行是 `position:absolute; top:0; transform:translateY(n*144)` 的**虚拟列表项**，
    内层容器高度 = 总行数×144（实测 4320 = 30 行），但**只挂 10 行**：视口 767 / 144 ≈ 5.3 行 + 缓冲 4 行。
    所以 `display:none` 只会在原地留一个洞（②）；
    藏掉 5 行以上，剩下 5 行×144=720 就铺不满 767 的视口 → 底部一大片黑（③）。
  · 列容器的 `clientHeight` 决定挂几行：把 height 从 767 调到 1487，挂载行数 10 → 15（滚动后仍稳定）；
    加 `margin-bottom:-extra` 布局足迹不变，`clip-path` 把多出来那块裁掉。1.9.2 就靠这个把预算做大。

第一版另外两个抖动 bug（--old 复现）：
  ⓐ 被藏的行 `rect` 全 0 → 下一轮进不了候选名单 → 被判「不需要藏」整块放回 → 再藏，一秒一闪；
  ⓑ 行节点自己也带 `data-recycler-key`（同样匹配 CA_DATA_SEL），`cardInRow()` 先匹配到**行本身**，
     读到的是「这一行当前装的是哪个币」的真实地址 → 永远判「不重复」→ 放回（只在 CA 判定上现形）。

本工具的做法：铺一张**和真机同构**的三列战壕榜（每列 14 行、视口 700 高、只挂 `ceil(700/144)+4=9` 行，
`ResizeObserver` 跟着 clientHeight 增减挂载行数 —— 就是真机那个虚拟列表），注入真实 userscript，然后：
  · 期望藏：A 列 2 张（一张同名、一张同址）、B 列 5 张（同名，得靠「撑高」把预算做大才藏得下）
  · 期望留：A 列其余、B 列其余、已开盘列全部
  · 隐藏数在 5 秒采样里**不许变**（变了就是闪）
  · **每列可见行的顶边必须 0/144/288… 连续**（洞要补上，②）
  · **每列最后一张可见卡片必须够到列底**（不许留黑块，③）
  · 开关关掉后：卡片全部回来、`margin-top` / `height` / `clip-path` 全部清干净

用法：
    C:/Python314/python.exe tools/gigax_dedupe_check.py
    C:/Python314/python.exe tools/gigax_dedupe_check.py --old     # 打回第一版，必须复现「抖 + 藏不住」
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gmgn_dom_probe import Mcp, unwrap   # noqa: E402

USERSCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "tools", "gmgn-gigax-narrative.user.js")
OUT_JS = r"C:\gmgn-probe\js\dedupe.js"
VIEW_H = 700            # fixture 里列的自然高度（真机 767，取整好算）

# 已开盘（最右列）：AAA / BBB / DDD / EEE 四个币
M1_CA = "0x7777777777777777777777777777777777777777"
M2_CA = "0x8888888888888888888888888888888888888888"
M3_CA = "0x9999999999999999999999999999999999999999"
M4_CA = "0xcccccccccccccccccccccccccccccccccccccccc"

# 内盘（左列）：A1 同名撞 AAA、A2 卡片地址撞 BBB，其余不撞
A1_CA = "0x1111111111111111111111111111111111111111"
A2_CA = M2_CA
A3_CA = "0x3333333333333333333333333333333333333333"
A4_CA = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
A5_CA = "0x1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a"

# 待发射（中间列）：B1~B5 全是同名撞车（AAA/BBB/DDD/EEE/CCC），B6 起不撞
B1_CA = "0x4444444444444444444444444444444444444444"
B2_CA = "0x5555555555555555555555555555555555555555"
B3_CA = "0x6666666666666666666666666666666666666666"
B4_CA = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
B5_CA = "0x2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b2b"
B6_CA = "0x3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b3b"

EXPECT_HIDDEN = ["row-A1", "row-A2", "row-B1", "row-B2", "row-B3", "row-B4", "row-B5"]
EXPECT_VISIBLE = ["row-A3", "row-A4", "row-A5", "row-A6",
                  "row-B6", "row-B7", "row-B8",
                  "row-M1", "row-M2", "row-M3", "row-M4"]

# 第一版的两个 bug（--old 用来证明探针真的能测到它们）
OLD_PATCHES = [
    # ⓐ 放回只看「这一轮收到的卡片」
    ("        const dup = (!!ca && dedupeSeen.cas.has(ca)) || (!!sym && dedupeSeen.names.has(sym));\n"
     "        if (!dup) {\n"
     "          restoreRow(row);\n"
     "          changed = true;\n"
     "        }",
     "        if (true) { restoreRow(row); changed = true; continue; }"),
    # ⓑ 行节点优先（真实页面上就是它把地址判定废掉的）
    ("      const inner = row.querySelector ? row.querySelector(CA_DATA_SEL) : null;\n"
     "      if (inner && inner !== row) return inner;\n"
     "      return row.matches && row.matches(CA_DATA_SEL) ? row : null;",
     "      if (row.matches && row.matches(CA_DATA_SEL)) return row;\n"
     "      return row.querySelector ? row.querySelector(CA_DATA_SEL) : null;"),
]

SHIMS = r"""
  const store = {
    dedupeMigrated: true, hotMark: 'off', replacePopup: false,
    autoPrefetch: false, enabled: true, customKw: [],
  };
  window.GM_getValue = (k, d) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : d);
  window.GM_setValue = (k, v) => { store[k] = v; };
  window.GM_registerMenuCommand = () => {};
  window.GM_xmlhttpRequest = () => {};
  window.__store = store;
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  // ---- 合成一张「和真机同构」的三列战壕榜 ----
  // 关键三点（都来自 2026-11-22 实测）：行是绝对定位 + transform:translateY(n*144)；
  // 内层高度 = 全部行数×144；**只挂 ceil(clientHeight/144)+4 行**（真机 767 视口 → 10 行），
  // clientHeight 一变就跟着增减挂载 —— 这就是「撑高才能多挂几行」的来源。
  const H = 144;
  const OVER = 4;
  const VIEW = __VIEW__;
  const DEFS = __DEFS__;

  const mkRow = (sp, i) => {
    const el = document.createElement('div');
    el.id = sp[0];
    el.setAttribute('data-recycler-key', 'bsc:' + sp[2]);
    el.style.cssText = 'position:absolute;top:0;left:0;width:326px;height:' + H +
      'px;transform:translateY(' + (i * H) + 'px);';
    el.innerHTML = '<div data-trenches-token-address="' + sp[1] + '" data-trenches-token-chain="bsc"' +
      ' style="width:326px;height:' + H + 'px;background:#1b1b22;color:#eee;">' +
      '<span class="min-w-0 shrink-0 truncate whitespace-nowrap font-medium">' + sp[3] + '</span>' +
      '<div>Tax 3%</div></div>';
    return el;
  };

  const makeCol = (left, specs) => {
    const col = document.createElement('div');
    col.className = 'h-full grow min-h-0 gmgn-scrollbar';
    col.style.cssText = 'position:absolute;left:' + left + 'px;top:0;width:340px;height:' + VIEW +
      'px;overflow:hidden;';
    const inner = document.createElement('div');
    inner.style.cssText = 'position:relative;height:' + (specs.length * H) + 'px;';
    col.appendChild(inner);
    const rows = [];
    const mount = () => {
      const need = Math.min(specs.length, Math.ceil((col.clientHeight || VIEW) / H) + OVER);
      while (rows.length < need) { const i = rows.length; const r = mkRow(specs[i], i); inner.appendChild(r); rows.push(r); }
      while (rows.length > need) { rows.pop().remove(); }     // 真机是回收复用，这里直接删，够用
      window.__mounted = window.__mounted || {};
      window.__mounted[left] = rows.length;
    };
    mount();
    try { new ResizeObserver(() => mount()).observe(col); } catch (e) { /* ignore */ }
    return col;
  };

  const fix = document.createElement('div');
  fix.id = 'fix';
  fix.style.cssText = 'position:fixed;inset:0;z-index:2147482900;background:#111;';
  fix.appendChild(makeCol(20, DEFS.A));
  fix.appendChild(makeCol(380, DEFS.B));
  fix.appendChild(makeCol(740, DEFS.M));
  document.body.appendChild(fix);
"""

FIXTURE = r"""
  const HID = '[data-gigax-deduped="1"]';
  const row = id => document.getElementById(id);
  const isHidden = id => { const r = row(id); return !!(r && (r.matches(HID) || r.closest(HID))); };
  const count = () => document.querySelectorAll(HID).length;
  const ids = __IDS__;
  const cols = () => [...document.querySelectorAll('.gmgn-scrollbar')].sort((a, b) =>
    a.getBoundingClientRect().left - b.getBoundingClientRect().left);

  // 一列的样子：可见行的顶边（相对列顶）、间距、连续吗（洞补上了吗）、最后一张够到列底吗（没黑块吗）
  const colInfo = i => {
    const col = cols()[i];
    if (!col) return null;
    const base = col.getBoundingClientRect().top;
    const vis = [...col.querySelectorAll('[data-recycler-key]')]
      .filter(el => !el.matches(HID) && el.getBoundingClientRect().height > 0)
      .map(el => Math.round(el.getBoundingClientRect().top - base))
      .sort((a, b) => a - b);
    const gaps = vis.map((t, j) => (j ? t - vis[j - 1] : t));
    const last = vis.length ? vis[vis.length - 1] + H : 0;
    return {
      tops: vis, gaps,
      clean: !vis.length || (vis[0] === 0 && gaps.slice(1).every(g => g === H)),
      lastBottom: last,
      fills: last >= VIEW,
      visible: vis.length,
      hidden: col.querySelectorAll(HID).length,
      mounted: col.querySelectorAll('[data-recycler-key]').length,
      style: { h: col.style.height, mb: col.style.marginBottom, clip: col.style.clipPath },
    };
  };
  const margins = () => [...document.querySelectorAll('[data-recycler-key]')]
    .map(el => el.style.marginTop).filter(Boolean);

  await sleep(3000);                       // 让脚本跑几轮（撑高要跨轮才生效）
  const firstHidden = ids.filter(isHidden);
  const trace = [];
  for (let i = 0; i < 10; i++) {           // 5 秒采样：闪的话隐藏数一定变
    await sleep(500);
    trace.push(count());
  }
  const colsInfo = [0, 1, 2].map(colInfo);
  const sw = document.querySelector('#gigax-kw-panel .kw-dedupe-switch');
  let off = null;
  let on = null;
  if (sw) {
    sw.click();                            // 关：藏起来的必须全部放回，margin/height/clip 都要清干净
    await sleep(2400);
    off = { hidden: ids.filter(isHidden), count: count(), saved: __SAVED__,
            margins: margins(), cols: [0, 1, 2].map(colInfo) };
    sw.click();                            // 再开：必须藏回来，洞必须补上，还不许留黑块
    await sleep(3000);
    on = { hidden: ids.filter(isHidden), count: count(), saved: __SAVED__,
           cols: [0, 1, 2].map(colInfo) };
  }
  return JSON.stringify({
    firstHidden, trace,
    flips: trace.reduce((n, v, i) => (i && v !== trace[i - 1] ? n + 1 : n), 0),
    cols: colsInfo, before: { hidden: ids.filter(isHidden), margins: margins() },
    off, on,
    panel: !!document.getElementById('gigax-kw-panel'),
    switchFound: !!sw,
  }, null, 1);
"""


def defs() -> dict:
    def row(rid, ca, row_ca, sym):
        return [rid, ca, row_ca, sym]

    a = [row("row-A1", A1_CA, A1_CA, "AAA"),      # 同名撞已开盘 AAA
         row("row-A2", A2_CA, A2_CA, "XXX"),      # 卡片地址撞已开盘 BBB（行 key 仍是自己的）
         row("row-A3", A3_CA, A3_CA, "ZZZ"),
         row("row-A4", A4_CA, A4_CA, "QQQ"),
         row("row-A5", A5_CA, A5_CA, "WWW1")]
    for i in range(6, 15):                        # 填充行：不撞任何名字
        ca = "0xa%039d" % i
        a.append(row(f"row-A{i}", ca, ca, f"NEWA{i}"))
    b = [row("row-B1", B1_CA, B1_CA, "AAA"),
         row("row-B2", B2_CA, B2_CA, "BBB"),
         row("row-B3", B3_CA, B3_CA, "DDD"),
         row("row-B4", B4_CA, B4_CA, "EEE"),
         row("row-B5", B5_CA, B5_CA, "BBB"),
         row("row-B6", B6_CA, B6_CA, "YYY")]
    for i in range(7, 15):
        ca = "0xb%039d" % i
        b.append(row(f"row-B{i}", ca, ca, f"NEWB{i}"))
    m = [row("row-M1", M1_CA, M1_CA, "AAA"),
         row("row-M2", M2_CA, M2_CA, "BBB"),
         row("row-M3", M3_CA, M3_CA, "DDD"),
         row("row-M4", M4_CA, M4_CA, "EEE")]
    for i in range(5, 15):
        ca = "0xc%039d" % i
        m.append(row(f"row-M{i}", ca, ca, f"NEWM{i}"))
    return {"A": a, "B": b, "M": m}


def build(src: str, old: bool) -> str:
    body = src
    if old:
        for a, b in OLD_PATCHES:
            if a not in body:
                raise SystemExit("补丁没打上（源码变了？）：" + a[:70])
            body = body.replace(a, b, 1)
    d = defs()
    shims = SHIMS.replace("__DEFS__", json.dumps(d, ensure_ascii=False))
    fixture = (FIXTURE
               .replace("__IDS__", json.dumps(EXPECT_HIDDEN + EXPECT_VISIBLE))
               .replace("__SAVED__", "window.__store.dedupeMigrated"))
    out = "async () => {\n" + shims + "\n" + body + "\n" + fixture + "\n}\n"
    return out.replace("__VIEW__", str(VIEW_H))


def verdict(d: dict) -> bool:
    ok = True
    if not d.get("panel"):
        print("!! 关键词面板没挂上（缺 #gigax-kw-panel）")
        ok = False
    if not d.get("switchFound"):
        print("!! 面板里没有同名隐藏的开关（.kw-dedupe-switch）")
        ok = False

    fh = d.get("firstHidden") or []
    first = set(fh if isinstance(fh, list) else str(fh).split(","))
    miss = [x for x in EXPECT_HIDDEN if x not in first]
    extra = [x for x in EXPECT_VISIBLE if x in first]
    if miss:
        print("!! 该藏的没藏：" + ",".join(miss))
        ok = False
    if extra:
        print("!! 不该藏的藏了：" + ",".join(extra))
        ok = False

    if d.get("flips"):
        print(f"!! 隐藏数在采样里变了 {d['flips']} 次（{d.get('trace')}）—— 闪烁，说明「放回」判据又漏了")
        ok = False

    cols = d.get("cols") or []
    if len(cols) < 3:
        print(f"!! 只测到 {len(cols)} 列，补洞 / 黑块都没法判")
        ok = False
    for i, c in enumerate(cols):
        if not c:
            print(f"!! 第 {i} 列没测到")
            ok = False
            continue
        if not c.get("clean"):
            print(f"!! 第 {i} 列还有洞（可见行顶部 {c.get('tops')}，间距 {c.get('gaps')}）")
            ok = False
        if not c.get("fills"):
            print(f"!! 第 {i} 列底部一大块黑：最后一张只到 {c.get('lastBottom')}px，列高 {VIEW_H}"
                  f"（可见 {c.get('visible')} 张 / 挂载 {c.get('mounted')} 行 / 藏 {c.get('hidden')} 张）")
            ok = False

    off = d.get("off") or {}
    on = d.get("on") or {}
    if off:
        if off.get("count"):
            print("!! 开关关掉后还有卡片藏着：" + (",".join(off.get("hidden") or []) or "（数不上）"))
            ok = False
        if off.get("saved") is not False:
            print("!! 开关状态没落盘：" + repr(off.get("saved")))
            ok = False
        if off.get("margins"):
            print("!! 开关关掉后行上还留着 margin-top（补洞没清）：" + str(off.get("margins")))
            ok = False
        for i, c in enumerate(off.get("cols") or []):
            st = (c or {}).get("style") or {}
            h = (st.get("h") or "").strip()
            hpx = int(h[:-2]) if h.endswith("px") and h[:-2].isdigit() else 0
            if hpx > VIEW_H or st.get("mb") or st.get("clip"):
                print(f"!! 开关关掉后第 {i} 列还留着撑高的痕迹：{st}")
                ok = False
    if on:
        if set(on.get("hidden") or []) != set(EXPECT_HIDDEN):
            print("!! 再打开后藏回来的不是那几张：" + (",".join(on.get("hidden") or []) or "（空）"))
            ok = False
        if on.get("saved") is not True:
            print("!! 开关状态没落盘：" + repr(on.get("saved")))
            ok = False
        for i, c in enumerate(on.get("cols") or []):
            if not c:
                continue
            if not c.get("clean"):
                print(f"!! 重新打开后第 {i} 列还有洞（可见行顶部 {c.get('tops')}）")
                ok = False
            if not c.get("fills"):
                print(f"!! 重新打开后第 {i} 列底部还有黑块（只到 {c.get('lastBottom')}px）")
                ok = False
    return ok


def main():
    ap = argparse.ArgumentParser(description="跨列同名隐藏的回归探针")
    ap.add_argument("--old", action="store_true", help="打回第一版（期望复现「抖 + 地址那条藏不住」）")
    ap.add_argument("--url", default="http://127.0.0.1:8765/api/gigax/_diag?ev=fixture")
    a = ap.parse_args()

    src = open(USERSCRIPT, encoding="utf-8").read()
    os.makedirs(os.path.dirname(OUT_JS), exist_ok=True)
    with open(OUT_JS, "w", encoding="utf-8") as f:
        f.write(build(src, a.old))
    print(f"[fixture] {'OLD' if a.old else 'CURRENT'} → {OUT_JS}")

    m = Mcp()
    try:
        m.init()
        m.call("navigate_page", {"pageId": 1, "url": a.url, "timeout": 60000})
        time.sleep(2)
        print("[probe] 注入脚本，铺三列合成战壕榜（虚拟列表：只挂 ceil(高/144)+4 行），观察…")
        out = unwrap(m.call("evaluate_script", {
            "pageId": 1, "function": open(OUT_JS, encoding="utf-8").read()}, timeout=240))
    finally:
        m.close()

    raw = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)
    try:
        d = out if isinstance(out, dict) else json.loads(out)
    except Exception:
        print("[原始返回] " + (raw or "（空）")[:4000])
        raise
    print(json.dumps(d, ensure_ascii=False, indent=1))

    ok = verdict(d)
    if a.old:
        if ok:
            print("!! 旧版居然全过了 —— 探针没测到那两个 bug（补丁没生效？）")
            return 1
        print("   （旧版符合预期：这正是 2026-11-22 那两个抖动 bug）")
        return 0
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
