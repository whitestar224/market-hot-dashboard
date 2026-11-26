"""GigaX 叙事分析接口 —— 抓 token + 调用（frida 16 attach 方案，已实测可用）。

背景：
- 接口 GET https://app-api.gigax.fun/api/data/token/narrative?chain=<code>&address=<CA>
- 必须带 Authorization: Bearer <Privy access token>，匿名返回「登录状态无效」
- token 是 1 小时过期的 Privy JWT，存在 Expo SecureStore（AES + Android Keystore）
- 最可靠抓法：frida 16（client+server 都要 16.x，17.x 无 Java bridge）attach 到
  已登录的 GigaX 进程，hook javax.crypto.Cipher.doFinal，token 解密瞬间截获，
  抓到立刻用（绝不过期）

前置条件：
1. 雷电模拟器手动启动、GigaX 已登录、网络能连 app-api.gigax.fun
2. 模拟器已 root；frida-server 16 已 push 到 /data/local/tmp/fs16 并在监听
3. Python 3.10 装 frida==16.7.19（系统 3.14 装不了）

用法：
  # 抓新 token 并立即测一个币
  C:/Users/ZhuanZ1/AppData/Local/Programs/Python/Python310/python.exe tools/gigax_narrative.py grab --chain bsc --address 0x...
  # 用已抓 token 调接口（1 小时内有效）
  C:/Users/ZhuanZ1/Desktop/... python.exe tools/gigax_narrative.py call --chain bsc --address 0x...
"""
import argparse
import json
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".runtime-cache" / "gigax-mitm"
TOKEN_FILE = STATE / "captured.json"
HOOK_JS = Path(__file__).resolve().parent / "gigax_hook.js"
ADB = r"D:\leidian\LDPlayer14\adb.exe"
DEV = "emulator-5554"
PORT = 27044
FS_REMOTE = "/data/local/tmp/fs16"
API_HOST = "app-api.gigax.fun"
NARRATIVE_PATH = "/api/data/token/narrative"

CHAIN_CODES = {"sol": "sol", "eth": "eth", "bsc": "bsc", "base": "base",
               "robinhood": "robin", "arc": "arc", "elysium": "elysium"}

HEADERS_BASE = {
    "Accept": "application/json", "Content-Type": "application/json",
    "X-Language-Contract": "2", "X-Device-Id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
    "X-Platform": "android", "X-App-Version": "1.0.4", "X-App-Build": "53488198",
    "X-App-Store": "internal", "User-Agent": "okhttp/4.12.0",
}


def _adb(args, t=30):
    try:
        p = subprocess.run([ADB, "-s", DEV] + args, capture_output=True, timeout=t)
        return (p.stdout or b"").decode("utf-8", "replace")
    except Exception as e:
        return str(e)


def call_api(token: str, chain: str, address: str, path: str = NARRATIVE_PATH):
    code = CHAIN_CODES.get(chain.lower(), chain.lower())
    url = f"https://{API_HOST}{path}?chain={code}&address={address}"
    h = dict(HEADERS_BASE)
    h["Authorization"] = "Bearer " + token
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"


def cmd_call(args):
    if not TOKEN_FILE.exists():
        print("没有 token。先跑 grab。"); return
    token = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))["token"]
    status, body = call_api(token, args.chain, args.address, args.endpoint or NARRATIVE_PATH)
    print(f"HTTP {status}")
    try:
        print(json.dumps(json.loads(body), ensure_ascii=False, indent=2))
    except Exception:
        print(body[:3000])


def cmd_grab(args):
    import frida
    # 设备/服务检查
    if _adb(["shell", "getprop", "sys.boot_completed"]).strip() != "1":
        print("模拟器未就绪（请手动启动模拟器）"); return
    chk = _adb(["shell", f'su -c "netstat -tlnp 2>/dev/null | grep :{PORT}"'])
    if f":{PORT}" not in chk:
        print(f"frida-server16 未在 {PORT} 监听，请先 push 并启动：")
        print(f'  adb push <frida-server16> {FS_REMOTE}')
        print(f'  adb shell su -c "chmod 755 {FS_REMOTE}; nohup setsid {FS_REMOTE} -l 0.0.0.0:{PORT} >/dev/null 2>&1 </dev/null &"')
        return
    pids = _adb(["shell", "pidof", "family.giga.app"]).strip().split()
    if not pids:
        print("GigaX 未运行（请先手动打开并登录）"); return
    _adb(["forward", f"tcp:{PORT}", f"tcp:{PORT}"])

    state = {"token": None}
    def on_msg(msg, data):
        if msg.get("type") != "send":
            return
        pl = str(msg.get("payload", ""))
        tag, _, val = pl.partition("|") if "|" in pl else (pl, "", "")
        if tag == "DECRYPTED":
            tok = val.strip()
            i = tok.find("eyJ")
            if i > 0:
                tok = tok[i:]
            if tok.startswith("eyJ") and not state["token"]:
                state["token"] = tok
    try:
        m = frida.get_device_manager()
        d = m.add_remote_device(f"127.0.0.1:{PORT}")
        s = d.attach(int(pids[0]))
        sc = s.create_script(HOOK_JS.read_text(encoding="utf-8"))
        sc.on("message", on_msg)
        sc.load()
        print(f"[hook] attached pid={pids[0]}，请在模拟器里刷新/重新登录 GigaX 触发 token 解密...", flush=True)
        deadline = time.time() + 90
        while time.time() < deadline and not state["token"]:
            time.sleep(0.5)
    except Exception as e:
        print(f"frida 错误: {type(e).__name__}: {e}"); return
    if not state["token"]:
        print("未抓到 token"); return

    STATE.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({
        "token": state["token"], "capturedAt": time.strftime("%Y-%m-%d %H:%M:%S")
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 新 token 已保存（{len(state['token'])} chars）", flush=True)

    # 抓到立即测，避免 1 小时过期
    status, body = call_api(state["token"], args.chain, args.address, args.endpoint or NARRATIVE_PATH)
    out = STATE / "narrative_result.txt"
    out.write_text(f"HTTP {status}\n{body}", encoding="utf-8")
    print(f"[API] HTTP {status} -> {out}", flush=True)
    try:
        data = json.loads(body)
        if data.get("code") == 0 and data.get("data", {}).get("narrative"):
            n = data["data"]["narrative"]
            print(f"\n叙事正文: {n.get('narrative_text', '')[:500]}")
            print(f"分类: {n.get('category_tags')}  生成: {n.get('generated_at')}")
            print(f"时间线: {len(n.get('narrative_timeline', []))} 条")
    except Exception:
        print(body[:1000])


def main():
    p = argparse.ArgumentParser(description="GigaX 叙事分析接口工具")
    sub = p.add_subparsers(dest="cmd")
    g = sub.add_parser("grab", help="frida attach 抓新 token 并立即测接口")
    g.add_argument("--chain", default="bsc")
    g.add_argument("--address", required=True)
    g.add_argument("--endpoint", default=NARRATIVE_PATH)
    g.set_defaults(fn=cmd_grab)
    c = sub.add_parser("call", help="用已抓 token 调接口（1小时内）")
    c.add_argument("--chain", default="bsc")
    c.add_argument("--address", required=True)
    c.add_argument("--endpoint", default=NARRATIVE_PATH)
    c.set_defaults(fn=cmd_call)
    args = p.parse_args()
    if not hasattr(args, "fn"):
        p.print_help(); return
    args.fn(args)


if __name__ == "__main__":
    main()
