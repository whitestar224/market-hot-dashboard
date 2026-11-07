"""CDP 小工具：开一个标签页、等页面稳定、把网络请求和渲染结果取回来。

只用于排查 RootData 的取数方式（WAF 滑块 + SPA），不是生产代码。
"""
import json
import sys
import time
import urllib.request
import uuid

from websocket import create_connection

PORT = 9222
TARGET = "https://www.rootdata.com/projects?evalSort=2"


def http_json(path, method="GET"):
    # loopback 必须绕开环境代理：会话里的 http_proxy(127.0.0.1:57568) 会把
    # 127.0.0.1:9222 也代理走，换来一个 502。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    with opener.open(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


class Tab:
    def __init__(self, url):
        target = http_json(f"/json/new?{urllib.parse.quote(url, safe='')}", "PUT")
        self.target_id = target["id"]
        self.ws = create_connection(target["webSocketDebuggerUrl"], timeout=30)
        self.pending = []
        self.network = []
        self.seq = 0

    def send(self, method, params=None):
        self.seq += 1
        self.ws.send(json.dumps({"id": self.seq, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.seq:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result") or {}
            self._harvest(msg)

    def _harvest(self, msg):
        if msg.get("method") == "Network.requestWillBeSent":
            req = msg["params"]["request"]
            self.network.append((req.get("method"), req.get("url")))

    def pump(self, seconds):
        end = time.time() + seconds
        self.ws.settimeout(0.5)
        while time.time() < end:
            try:
                self._harvest(json.loads(self.ws.recv()))
            except Exception:
                pass
        self.ws.settimeout(30)

    def eval(self, expression):
        result = self.send("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })
        return (result.get("result") or {}).get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def main():
    tab = Tab("about:blank")
    tab.send("Network.enable")
    tab.send("Page.enable")
    tab.send("Page.navigate", {"url": TARGET})
    tab.pump(12)
    info = tab.eval("""(() => ({
        title: document.title,
        url: location.href,
        len: document.body ? document.body.innerText.length : 0,
        head: document.body ? document.body.innerText.slice(0, 400) : "",
        captcha: document.body ? document.body.innerText.includes("Captcha") || !!document.querySelector("#CaptchaScript") : false,
        tables: document.querySelectorAll("table").length,
        rows: document.querySelectorAll("table tbody tr").length,
        links: document.querySelectorAll('a[href*="/projects/"]').length
    }))()""")
    print(json.dumps(info, ensure_ascii=False, indent=1))
    print("--- api-ish requests")
    seen = set()
    for method, url in tab.network:
        if url in seen:
            continue
        seen.add(url)
        if any(k in url for k in ("api", "project", "rank", "list", "search")):
            print(method, url[:200])
    tab.close()


if __name__ == "__main__":
    import urllib.parse
    main()
