"""抓 GigaX(React Native) 的 OkHttp 出站请求，用来复刻 Privy 刷新请求。

hook okhttp3.Request$Builder.build()：打印 URL / 方法 / 关键头（privy-*、authorization）。
--expire 模式：把发往 app-api.gigax.fun 的 Authorization 换成传入的过期 token，
逼 App 内部触发 Privy 刷新，从而抓到 auth.privy.io 的刷新请求。

用法：
  py310 tools/gigax_req_tap.py --seconds 60
  py310 tools/gigax_req_tap.py --seconds 90 --expire <过期的旧 JWT>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

ADB = r"D:\leidian\LDPlayer14\adb.exe"
DEV = "emulator-5554"
PORT = 27044
PKG = "family.giga.app"

JS = r"""
var EXPIRE = %s;
var KEEP = /privy|authorization|x-|app-version|app-build|app-store|platform|device-id|language/i;
function eachHeaders(hs) {
  var out = [];
  for (var i = 0; i < hs.size(); i++) {
    var n = '' + hs.name(i), v = '' + hs.value(i);
    if (KEEP.test(n)) out.push(n + '=' + (v.length > 60 ? v.slice(0, 24) + '…' : v));
  }
  return out;
}
function install() {
  var Builder = Java.use('okhttp3.Request$Builder');
  Builder.build.implementation = function () {
    var req = this.build();
    try {
      var url = '' + req.url().toString();
      if (url.indexOf('privy') >= 0 || url.indexOf('gigax') >= 0) {
        var hs = req.headers();
        var auth = '' + hs.get('Authorization');
        var hs2 = eachHeaders(hs);
        var replaced = false;
        if (EXPIRE && url.indexOf('app-api.gigax.fun') >= 0 && auth.indexOf('Bearer') === 0) {
          var nb = req.newBuilder();
          nb.header('Authorization', 'Bearer ' + EXPIRE);
          req = nb.build();
          replaced = true;
        }
        send('REQ|' + req.method() + ' ' + url + (replaced ? '  [AUTH->EXPIRED]' : '') + '\n    ' + hs2.join('\n    '));
      }
    } catch (e) {}
    return req;
  };
  send('LOG|okhttp hook installed');
}
setImmediate(function(){ try { Java.perform(install); } catch (e) { send('ERR|' + e); } });
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--expire", default="")
    args = ap.parse_args()

    import frida

    js = JS.replace("var EXPIRE = %s;", "var EXPIRE = %s;" % (json.dumps(args.expire) if args.expire else "null"))

    pids = subprocess.run([ADB, "-s", DEV, "shell", "pidof", PKG],
                          capture_output=True, timeout=20).stdout.decode().split()
    if not pids:
        print("GigaX 未运行")
        return 1
    subprocess.run([ADB, "-s", DEV, "forward", f"tcp:{PORT}", f"tcp:{PORT}"], timeout=20)
    dev = frida.get_device_manager().add_remote_device(f"127.0.0.1:{PORT}")
    s = dev.attach(int(pids[0]))
    sc = s.create_script(js)
    seen = []

    def on_msg(m, data):
        if m.get("type") != "send":
            print("!!", m.get("stack") or m)
            return
        p = str(m.get("payload", ""))
        tag, _, body = p.partition("|")
        print(("  " if tag == "REQ" else "") + body)
        seen.append(p)

    sc.on("message", on_msg)
    sc.load()
    print(f"hook 已挂，观察 {args.seconds}s …", flush=True)
    end = time.time() + args.seconds
    while time.time() < end:
        time.sleep(1)
    Path(".runtime-cache/gigax-mitm/okhttp_tap.txt").write_text("\n".join(seen), encoding="utf-8")
    print("\n落盘 .runtime-cache/gigax-mitm/okhttp_tap.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
