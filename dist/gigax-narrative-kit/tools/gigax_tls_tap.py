"""抓 GigaX 的 HTTPS 明文请求（okhttp3 被 R8 改名 → 改 hook Conscrypt 的 SSL 输出流）。

要点：
- `--force-http1`：hook SSLParameters.setApplicationProtocols，从 ALPN 里剔掉 h2，
  逼服务器走 HTTP/1.1，这样 SSLOutputStream 里就是可读明文（否则是 HPACK 二进制）。
- `--spawn`：冷启动 App 再注入（老连接池里的 h2 连接会绕过 ALPN 改动，必须新连接）。
- 只打印/落盘含 `--filter` 的请求，默认全打。

用法：
  py310 tools/gigax_tls_tap.py --seconds 40 --force-http1
  py310 tools/gigax_tls_tap.py --seconds 40 --force-http1 --spawn --filter privy
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

ADB = (os.environ.get("GIGAX_ADB")
       or __import__("shutil").which("adb")
       or os.path.expandvars(r"%LOCALAPPDATA%/Android/Sdk/platform-tools/adb.exe")
       or "adb")
DEV = "emulator-5554"
PORT = 27044
PKG = "family.giga.app"
OUT = Path(".state/tls_tap.txt")

JS_TMPL = r"""
var FILTER = __FILTER__;
var FORCE_H1 = __FORCE_H1__;
var buffers = {};
var installed = false;

function append(key, text) {
  var b = (buffers[key] || '') + text;
  if (b.length > 65536) b = b.slice(-32768);
  buffers[key] = b;
  var low = b.toLowerCase();
  if (FILTER && low.indexOf(FILTER) < 0) return;
  if (low.indexOf('http/1.') >= 0 || low.indexOf('post /') >= 0) {
    send('HTTP|' + b.slice(0, 6000));
    buffers[key] = '';
  }
}

function hookStream(cls) {
  try {
    var C = Java.use(cls);
    var JString = Java.use('java.lang.String');
    try {
      C.write.overload('[B').implementation = function (b) {
        try { append('' + this.hashCode(), JString.$new(b, 'ISO-8859-1')); } catch (e) {}
        return this.write(b);
      };
    } catch (e) {}
    C.write.overload('[B', 'int', 'int').implementation = function (b, off, len) {
      try {
        var s = '';
        for (var i = off; i < off + len; i++) s += String.fromCharCode((b[i] + 256) % 256);
        append('' + this.hashCode(), s);
      } catch (e) {}
      return this.write(b, off, len);
    };
    send('LOG|hooked ' + cls);
  } catch (e) { send('LOG|miss ' + cls + ' -> ' + e); }
}

function dropH2(arr) {
  var out = [];
  for (var i = 0; i < arr.length; i++) {
    var p = '' + arr[i];
    if (p === 'h2' || p === 'h2c') continue;
    out.push(p);
  }
  return arr.length === out.length ? arr : Java.array('java.lang.String', out);
}

function hookAlpn() {
  try {
    var SP = Java.use('javax.net.ssl.SSLParameters');
    SP.setApplicationProtocols.implementation = function (arr) {
      return this.setApplicationProtocols(dropH2(arr));
    };
    send('LOG|hooked SSLParameters.setApplicationProtocols');
  } catch (e) { send('LOG|miss SSLParameters -> ' + e); }
  try {
    var S = Java.use('com.android.org.conscrypt.ConscryptEngineSocket');
    S.setApplicationProtocols.implementation = function (arr) {
      return this.setApplicationProtocols(dropH2(arr));
    };
    send('LOG|hooked ConscryptEngineSocket.setApplicationProtocols');
  } catch (e) { send('LOG|miss ConscryptEngineSocket -> ' + e); }
}

function installAll() {
  if (FORCE_H1) hookAlpn();
  hookStream('com.android.org.conscrypt.ConscryptEngineSocket$SSLOutputStream');
  hookStream('com.android.org.conscrypt.ConscryptFileDescriptorSocket$SSLOutputStream');
}

function tryInstall() {
  if (installed) return;
  try {
    Java.perform(function () {
      if (installed) return;
      installAll();
      installed = true;
      send('LOG|ALL HOOKS INSTALLED');
    });
  } catch (e) {
    send('LOG|perform retry: ' + e);
  }
}
setInterval(tryInstall, 300);
setTimeout(tryInstall, 0);
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--filter", default="")
    ap.add_argument("--force-http1", action="store_true")
    ap.add_argument("--spawn", action="store_true", help="冷启动 App 再注入（换新连接池）")
    args = ap.parse_args()

    import frida

    js = JS_TMPL.replace("__FILTER__", json.dumps(args.filter.lower()) if args.filter else "null")
    js = js.replace("__FORCE_H1__", "true" if args.force_http1 else "false")

    subprocess.run([ADB, "-s", DEV, "forward", f"tcp:{PORT}", f"tcp:{PORT}"], timeout=20)
    dev = frida.get_device_manager().add_remote_device(f"127.0.0.1:{PORT}")

    if args.spawn:
        subprocess.run([ADB, "-s", DEV, "shell", "am force-stop", PKG], timeout=20)
        time.sleep(1)
        pid = dev.spawn([PKG])
        print(f"spawned pid={pid}")
    else:
        pids = subprocess.run([ADB, "-s", DEV, "shell", "pidof", PKG],
                              capture_output=True, timeout=20).stdout.decode().split()
        if not pids:
            print("GigaX 未运行（可加 --spawn）")
            return 1
        pid = int(pids[0])

    sess = dev.attach(pid)
    sc = sess.create_script(js)
    lines: list[str] = []

    def on(m, d):
        p = str(m.get("payload", ""))
        if p.startswith("HTTP|"):
            print("\n" + p[:3000])
            lines.append(p)
        else:
            print(p)

    sc.on("message", on)
    sc.load()
    if args.spawn:
        dev.resume(pid)
    print(f"观察 {args.seconds}s …", flush=True)
    end = time.time() + args.seconds
    while time.time() < end:
        time.sleep(1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n\n".join(lines), encoding="utf-8")
    print(f"\n落盘 {OUT}（{len(lines)} 条请求）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
