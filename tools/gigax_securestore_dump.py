"""从 GigaX 的 Expo SecureStore 里直接解出明文（含 privy refresh_token）。

原理：expo-secure-store 在 Android 上把值 AES-GCM 加密后写进
`/data/data/<pkg>/shared_prefs/SecureStore.xml`，密钥在 AndroidKeyStore
（alias 见 XML 的 keystoreAlias 字段，GigaX 是 `key_v1`）。
密钥不可导出，但可以**在 App 进程内**（frida attach）用同一把 keystore key
把 ct 解回明文 —— 不需要等 App 自己刷新 token。

用法：
  C:/Users/ZhuanZ1/AppData/Local/Programs/Python/Python310/python.exe \
      tools/gigax_securestore_dump.py [--pkg family.giga.app]

产物：.runtime-cache/gigax-mitm/securestore_dump.json
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".runtime-cache" / "gigax-mitm" / "securestore_dump.json"
ADB = r"D:\leidian\LDPlayer14\adb.exe"
DEV = "emulator-5554"
PORT = 27044

JS = r"""
rpc.exports.decrypt = function (payload) {
  var out = {};
  Java.perform(function () {
    var KeyStore = Java.use('java.security.KeyStore');
    var ks = KeyStore.getInstance('AndroidKeyStore');
    ks.load(null);
    var Cipher = Java.use('javax.crypto.Cipher');
    var GCMSpec = Java.use('javax.crypto.spec.GCMParameterSpec');
    var B64 = Java.use('android.util.Base64');
    var JString = Java.use('java.lang.String');
    var aliases = [];
    var it = ks.aliases();
    while (it.hasMoreElements()) aliases.push(String(it.nextElement()));
    out._aliases = aliases;
    // expo-secure-store 的 alias 实际是 `AES/GCM/NoPadding:key_v1:keystoreUnauthenticated`
    function resolveAlias(want) {
      for (var i = 0; i < aliases.length; i++) {
        var a = aliases[i];
        if (a === want || a.indexOf(':' + want + ':') >= 0 || a.indexOf(want) >= 0) return a;
      }
      return want;
    }
    payload.forEach(function (item) {
      try {
        var alias = resolveAlias(item.alias);
        var key = ks.getKey(alias, null);
        if (!key) { out[item.name] = {error: 'no key ' + item.alias + ' (aliases=' + aliases.join(',') + ')'}; return; }
        var iv = B64.decode(item.iv, 0);   // 0 = DEFAULT
        var ct = B64.decode(item.ct, 0);
        var c = Cipher.getInstance('AES/GCM/NoPadding');
        c.init(2, key, GCMSpec.$new(128, iv));  // 2 = DECRYPT_MODE
        var pt = c.doFinal(ct);
        // 用 base64 回传，避免 frida 把 java.lang.String 序列化成 <instance>
        out[item.name] = {b64: B64.encodeToString(pt, 0), len: pt.length};
      } catch (e) {
        out[item.name] = {error: String(e)};
      }
    });
  });
  return out;
};
"""


def adb(args, timeout=30):
    p = subprocess.run([ADB, "-s", DEV] + args, capture_output=True, timeout=timeout)
    return (p.stdout or b"").decode("utf-8", "replace")


def read_securestore(pkg: str) -> list[dict]:
    raw = adb(["shell", f"su -c 'cat /data/data/{pkg}/shared_prefs/SecureStore.xml'"])
    items = []
    for m in re.finditer(r'<string name="([^"]+)">(.*?)</string>', raw, re.S):
        name, body = m.group(1), html.unescape(m.group(2))
        try:
            d = json.loads(body)
        except Exception:
            continue
        items.append({
            "name": name,
            "ct": d.get("ct", ""),
            "iv": d.get("iv", ""),
            "alias": d.get("keystoreAlias") or "key_v1",
        })
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkg", default="family.giga.app")
    args = ap.parse_args()

    import frida

    pids = adb(["shell", "pidof", args.pkg]).strip().split()
    if not pids:
        print("GigaX 未运行，请先在模拟器里打开 App")
        return 1
    items = read_securestore(args.pkg)
    if not items:
        print("SecureStore.xml 读取失败或为空")
        return 1
    print(f"读到 {len(items)} 条 SecureStore 记录，attach pid={pids[0]} …")

    adb(["forward", f"tcp:{PORT}", f"tcp:{PORT}"])
    dev = frida.get_device_manager().add_remote_device(f"127.0.0.1:{PORT}")
    sess = dev.attach(int(pids[0]))
    script = sess.create_script(JS)
    script.load()
    result = script.exports_sync.decrypt(items)

    print("keystore aliases:", result.get("_aliases"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    for name, v in result.items():
        if name.startswith("_"):
            continue
        if "error" in v:
            print(f"  [FAIL] {name}: {v['error'][:120]}")
        else:
            plain = base64.b64decode(v["b64"]).decode("utf-8", "replace")
            preview = plain if len(plain) <= 60 else plain[:40] + f"…({len(plain)} chars)"
            print(f"  [OK] {name} = {preview}")
    print(f"\n落盘: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
