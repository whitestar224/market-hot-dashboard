"""用 chrome-devtools-mcp 打开真实 GMGN 页面并读取 DOM —— 别再靠截图猜结构。

为什么需要这个工具：gmgn-gigax-narrative.user.js 的「币和叙事对不上」折腾了十几版，
根因全是**猜 DOM**。真机一读才发现：战壕卡里根本没有 `a[href*="/token/"]`
（币名是纯文本），地址其实挂在 `data-trenches-token-address` 上。所以任何取址改动
都必须先跑这个工具验一遍真实 DOM。

前置（只需一次）：
    mkdir C:/tmp-gmgn-mcp && cd C:/tmp-gmgn-mcp && npm i chrome-devtools-mcp@latest

用法：
    # 数据属性普查 + 分区域命中自测（默认，这就是那个 bug 的回归测试）
    C:/Python314/python.exe tools/gmgn_dom_probe.py

    # 跑自己的探测脚本（JS 里写一个 () => ... 表达式）
    C:/Python314/python.exe tools/gmgn_dom_probe.py --js C:/path/probe.js

    # 顺便存一张截图
    C:/Python314/python.exe tools/gmgn_dom_probe.py --shot C:/gmgn-probe/shot.png

    # 换页面（默认战壕页）
    C:/Python314/python.exe tools/gmgn_dom_probe.py --url "https://gmgn.ai/bsc/token/0x…"

注意：
* 用的是**临时独立 profile**（--isolated），不碰你正在用的 Chrome 和 Tampermonkey 登录态。
* 窗口放在屏幕外（-3200,-3200），不会弹出来打扰你，跑完自己关掉。
* 未登录态**看不到 AI 叙事浮层**（那是登录功能），所以这个工具验的是「卡片 → 地址」这一段。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time

MCP_ROOT = r"C:\tmp-gmgn-mcp\node_modules\chrome-devtools-mcp"
MCP_ENTRY = os.path.join(MCP_ROOT, "out", "Default", "gen", "src", "bin", "chrome-devtools-mcp.js")
# 代理：默认**不走代理**（2026-11-22 实测本机直连 gmgn.ai 返回 200）。
# 以前这里硬钉 `http://127.0.0.1:7891`，Clash 换端口后整个探针只能拿到
# `chrome-error://chromewebdata/`（2026-11-22 那次 DOM 探测就是这么瞎的）。
# 需要代理时：set GMGN_PROBE_PROXY=http://127.0.0.1:<port>
PROXY = str(os.getenv("GMGN_PROBE_PROXY", "") or "").strip()
WORKSPACE = r"C:\gmgn-probe"

# ---------------------------------------------------------------- 内置探针

CENSUS_JS = r"""() => {
  const ATTRS = ['data-trenches-token-address','data-token-address','data-row-key','data-recycler-key'];
  const SEL = ATTRS.map(a => '[' + a + ']').join(',');
  const out = { url: location.href, title: document.title.slice(0, 60), attrs: {}, holders: 0, samples: [] };
  const bag = {};
  for (const node of document.querySelectorAll(SEL)) {
    for (const a of ATTRS) {
      const v = node.getAttribute(a);
      if (!v) continue;
      bag[a] = bag[a] || [];
      bag[a].push(v);
    }
  }
  for (const k of Object.keys(bag)) {
    out.attrs[k] = { count: bag[k].length, distinct: new Set(bag[k]).size, sample: bag[k][0] };
  }
  const hs = [...document.querySelectorAll(SEL)];
  out.holders = hs.length;
  for (const node of hs.slice(0, 6)) {
    const r = node.getBoundingClientRect();
    out.samples.push({
      w: Math.round(r.width), h: Math.round(r.height),
      x: Math.round(r.x), y: Math.round(r.y),
      tokens: node.querySelectorAll('a[href*="/token/"]').length,
      ca: node.getAttribute('data-trenches-token-address') || node.getAttribute('data-row-key') || '',
      text: (node.innerText || '').replace(/\s+/g, ' ').slice(0, 60),
    });
  }
  return JSON.stringify(out, null, 1);
}"""

# 「指针 → elementsFromPoint → 取址」全链路自测：故意不看链接，只看 data 属性。
SELFTEST_JS = r"""() => {
  const ATTRS = ['data-trenches-token-address','data-token-address','data-row-key','data-recycler-key'];
  const SEL = ATTRS.map(a => '[' + a + ']').join(',');
  const CHAINS = ['sol','eth','bsc','base','robinhood','blast','tron','arbitrum','polygon'];
  const isEvm = ca => /^0x[a-fA-F0-9]{40}$/.test(ca || '');
  const isSol = ca => /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(ca || '');
  const normCa = ca => (isEvm(ca) ? ca.toLowerCase() : ca);

  function parseCaKey(raw) {
    const s = String(raw || '').trim();
    if (!s) return null;
    const i = s.indexOf(':');
    let chain = null, ca = s;
    if (i > 0) {
      const head = s.slice(0, i).toLowerCase();
      if (CHAINS.indexOf(head) >= 0) { chain = head; ca = s.slice(i + 1); }
    }
    if (!ca || !(isEvm(ca) || isSol(ca))) return null;
    return { chain, ca };
  }
  function caFromDataAttrs(el) {
    if (!el || !el.closest) return null;
    let node = null;
    try { node = el.closest(SEL); } catch (e) { return null; }
    if (!node) return null;
    let h = 0;
    try { h = node.getBoundingClientRect().height || 0; } catch (e) { h = 0; }
    if (h > 400) return null;
    for (const attr of ATTRS) {
      const raw = node.getAttribute(attr);
      if (!raw) continue;
      const p = parseCaKey(raw);
      if (!p) continue;
      let chain = p.chain;
      if (!chain) chain = String(node.getAttribute('data-trenches-token-chain') || '').toLowerCase() || null;
      return { chain, ca: normCa(p.ca), src: attr };
    }
    return null;
  }
  function resolveAt(x, y) {
    for (const el of (document.elementsFromPoint(x, y) || [])) {
      const hit = caFromDataAttrs(el);
      if (hit) return hit;
    }
    return null;
  }

  const stat = { trench: { cards: 0, points: 0, hit: 0, miss: 0, wrong: 0 },
                 rows:   { cards: 0, points: 0, hit: 0, miss: 0, wrong: 0 } };
  const missDetail = [];

  function run(sel, bucket, minH) {
    for (const node of document.querySelectorAll(sel)) {
      const r = node.getBoundingClientRect();
      if (r.width < 120 || r.height < minH || r.top < 0 || r.bottom > innerHeight) continue;
      const truth = caFromDataAttrs(node);
      if (!truth) continue;
      stat[bucket].cards++;
      for (const [fx, fy] of [[0.5, 0.5], [0.25, 0.3], [0.75, 0.3], [0.5, 0.8]]) {
        const x = r.left + r.width * fx, y = r.top + r.height * fy;
        stat[bucket].points++;
        const got = resolveAt(x, y);
        if (!got) {
          stat[bucket].miss++;
          if (missDetail.length < 4) {
            missDetail.push({ x: Math.round(x), y: Math.round(y), truth: truth.ca,
              top: (document.elementsFromPoint(x, y) || []).slice(0, 3)
                .map(e => e.tagName + '.' + String(e.className || '').slice(0, 40)) });
          }
          continue;
        }
        if (got.ca === truth.ca) stat[bucket].hit++;
        else { stat[bucket].wrong++; missDetail.push({ wrong: true, truth: truth.ca, got: got.ca }); }
      }
    }
  }
  run('[data-trenches-token-address]', 'trench', 60);
  run('[data-row-key]', 'rows', 20);
  return JSON.stringify({ stat, missDetail }, null, 1);
}"""

PROBES = {"census": CENSUS_JS, "selftest": SELFTEST_JS}


# ---------------------------------------------------------------- MCP 客户端

class Mcp:
    def __init__(self, extra=None):
        if not os.path.isfile(MCP_ENTRY):
            raise SystemExit(
                "找不到 chrome-devtools-mcp。先装一次：\n"
                f"  mkdir {os.path.dirname(MCP_ROOT)}\n"
                f"  cd {os.path.dirname(MCP_ROOT)}\n"
                "  npm i chrome-devtools-mcp@latest")
        os.makedirs(WORKSPACE, exist_ok=True)
        args = ["node", MCP_ENTRY, "--isolated",
                "--viewport=1600x1000", "--chromeArg=--window-position=-3200,-3200",
                "--chromeArg=--no-first-run", "--usageStatistics=false",
                f"--workspace={WORKSPACE}"] + (extra or [])
        if PROXY:
            args.insert(2, f"--proxyServer={PROXY}")
        env = dict(os.environ)
        env["CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS"] = "1"
        self.p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, cwd=os.path.dirname(MCP_ROOT),
                                  env=env, bufsize=0)
        self._id = 0
        self._err = []
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        for line in self.p.stderr:
            s = line.decode("utf-8", "replace").rstrip()
            if s:
                self._err.append(s)
                if len(self._err) > 300:
                    self._err.pop(0)

    def request(self, method, params=None, timeout=180):
        self._id += 1
        mid = self._id
        self.p.stdin.write((json.dumps({"jsonrpc": "2.0", "id": mid, "method": method,
                                        "params": params or {}}) + "\n").encode())
        self.p.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.p.stdout.readline()
            if not line:
                raise SystemExit("MCP 进程退出：" + " | ".join(self._err[-8:]))
            line = line.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get("id") != mid:
                continue
            if "error" in msg:
                raise RuntimeError(json.dumps(msg["error"], ensure_ascii=False)[:1200])
            return msg.get("result")
        raise TimeoutError(method)

    def init(self):
        self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "gmgn-dom-probe", "version": "1.0"}})
        self.p.stdin.write((json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized",
                                        "params": {}}) + "\n").encode())
        self.p.stdin.flush()

    def call(self, name, args=None, timeout=180):
        r = self.request("tools/call", {"name": name, "arguments": args or {}}, timeout)
        txt = "\n".join(c.get("text", "") for c in (r.get("content") or []) if c.get("type") == "text")
        return ("ERROR: " if r.get("isError") else "") + txt

    def close(self):
        try:
            self.p.stdin.close()
        except Exception:
            pass
        try:
            self.p.terminate()
            self.p.wait(timeout=10)
        except Exception:
            try:
                self.p.kill()
            except Exception:
                pass


def unwrap(text):
    """MCP 把返回值包在 ```json 代码块里，剥出来。"""
    if "```json" in text:
        body = text.split("```json", 1)[1].split("```", 1)[0]
        try:
            return json.loads(json.loads(body))
        except Exception:
            try:
                return json.loads(body)
            except Exception:
                return body
    return text


def main():
    ap = argparse.ArgumentParser(description="用 chrome-devtools-mcp 读 GMGN 真实 DOM")
    ap.add_argument("--url", default="https://gmgn.ai/?chain=bsc")
    ap.add_argument("--probe", choices=sorted(PROBES), default="selftest")
    ap.add_argument("--js", help="自己的探测脚本文件（内容为 () => ... 表达式）")
    ap.add_argument("--wait", type=int, default=18, help="导航后等多久（秒）")
    ap.add_argument("--shot", help="截图保存路径")
    ap.add_argument("--hover-text", help="用无障碍快照里含这段文字的元素做一次真实 hover（触发浮层）")
    ap.add_argument("--hover-wait", type=int, default=3, help="hover 之后等几秒再跑 JS")
    a = ap.parse_args()

    source = open(a.js, encoding="utf-8").read() if a.js else PROBES[a.probe]

    m = Mcp()
    try:
        m.init()
        m.call("navigate_page", {"pageId": 1, "type": "url", "url": a.url, "timeout": 90000})
        time.sleep(a.wait)

        # 等页面真的渲染出卡片（最多再等 40 秒）
        for i in range(8):
            n = unwrap(m.call("evaluate_script", {
                "pageId": 1,
                "function": "() => document.querySelectorAll('[data-trenches-token-address],[data-row-key]').length"}))
            n = int(n) if isinstance(n, int) else 0
            if n:
                break
            print(f"[等待渲染 {i}] 还没卡片，再等…", file=sys.stderr)
            time.sleep(5)

        # 可选：真实 hover 一个元素，把 GMGN 的浮层调出来
        if a.hover_text:
            uid = None
            p_snap = os.path.join(WORKSPACE, "probe-snap.txt")
            for _ in range(3):
                m.call("take_snapshot", {"pageId": 1, "filePath": p_snap})
                txt = ""
                if os.path.isfile(p_snap):
                    txt = open(p_snap, encoding="utf-8", errors="replace").read()
                exact = None
                loose = None
                for line in txt.splitlines():
                    if "uid=" not in line or "RootWebArea" in line:
                        continue
                    if a.hover_text not in line:
                        continue
                    u = line.split("uid=", 1)[1].split()[0]
                    # 引号里以目标开头的（"DCTY …"）最准；否则先记第一个包含它的
                    body = line.split('"', 1)[1] if '"' in line else ""
                    if body.startswith(a.hover_text) and exact is None:
                        exact = u
                    if loose is None:
                        loose = u
                uid = exact or loose
                if uid:
                    break
                time.sleep(4)
            print(f"[hover] uid={uid} 目标={a.hover_text}", file=sys.stderr)
            if uid:
                try:
                    print(m.call("hover", {"pageId": 1, "uid": uid}), file=sys.stderr)
                except Exception as e:
                    print(f"[hover 失败] {e}", file=sys.stderr)
                time.sleep(a.hover_wait)

        print(unwrap(m.call("evaluate_script", {"pageId": 1, "function": source})))
        if a.shot:
            print(m.call("take_screenshot", {"pageId": 1, "filePath": a.shot}))
    finally:
        m.close()


if __name__ == "__main__":
    main()
