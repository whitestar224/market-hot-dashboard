# -*- coding: utf-8 -*-
"""GigaX Bearer token 抓取工具（手机 APP 用）。

GigaX 的叙事接口需要 Privy access token：
    GET https://app-api.gigax.fun/api/data/token/narrative?chain=...&address=...

APK 没有 networkSecurityConfig、也不可调试，Android 7+ 不信任用户 CA。
所以本工具提供两条路：

  proxy : 本机 HTTPS 中间人代理（手机 WiFi 走代理 + 把 CA 装成系统证书）
          -> 抓到 Authorization: Bearer xxx 后落盘，还能顺手 dump 叙事响应
  pull  : 手机已 root 时直接用 adb 从 App 私有目录里捞 JWT（无需 MITM）
  test  : 用抓到的 token 实测叙事接口

用法：
  C:/Python314/python.exe tools/gigax_token_capture.py ca
  C:/Python314/python.exe tools/gigax_token_capture.py proxy --port 8888
  C:/Python314/python.exe tools/gigax_token_capture.py pull
  C:/Python314/python.exe tools/gigax_token_capture.py test --chain bsc --address 0x...
  C:/Python314/python.exe tools/gigax_token_capture.py status
"""

from __future__ import annotations

import argparse
import datetime as _dt
import http.client
import json
import os
import re
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".runtime-cache" / "gigax-mitm"
CA_CERT = STATE / "ca.crt"
CA_KEY = STATE / "ca.key"
TOKEN_FILE = STATE / "captured.json"
CAPTURE_LOG = STATE / "requests.jsonl"

API_HOST = "app-api.gigax.fun"
NARRATIVE_PATH = "/api/data/token/narrative"

LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# 通用小工具
# --------------------------------------------------------------------------- #
def _ensure_state() -> None:
    STATE.mkdir(parents=True, exist_ok=True)


def _local_ips() -> list[str]:
    """列出手机可能访问到的局域网 IP。

    不要只用 UDP connect 拿「默认路由出口」——用户开着 Proton 时它返回的
    是隧道地址 10.2.0.2，手机根本连不上。这里枚举所有网卡并排序：
    192.168.* > 172.16~31.* > 其它 10.* > 其它，同时过滤 Proton 的 10.2.0.x。
    """
    ips: list[str] = []
    try:
        import psutil

        for _name, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if getattr(a, "family", None) != socket.AF_INET:
                    continue
                ip = a.address
                if ip.startswith("127.") or ip.startswith("169.254."):
                    continue
                if ip.startswith("10.2.0."):  # Proton VPN 隧道
                    continue
                ips.append(ip)
    except Exception:
        pass  # psutil 缺失时退回 UDP 探测

    def rank(ip: str) -> int:
        if ip.startswith("192.168."):
            return 0
        if re.match(r"^172\.(1[6-9]|2\d|3[01])\.", ip):
            return 1
        if ip.startswith("10."):
            return 2
        return 3

    ips = sorted(set(ips), key=lambda x: (rank(x), x))
    if not ips:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("223.5.5.5", 80))
            ips = [s.getsockname()[0]]
        except Exception:
            ips = ["127.0.0.1"]
        finally:
            s.close()
    return ips


def _print_box(lines: list[str]) -> None:
    width = max(len(x) for x in lines) + 4
    print("+" + "-" * width + "+")
    for line in lines:
        print("| " + line.ljust(width - 2) + " |")
    print("+" + "-" * width + "+")


def _find_adb() -> str | None:
    from shutil import which

    p = which("adb")
    if p:
        return p
    candidates = [
        os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
        r"C:\adb\adb.exe",
        r"C:\Program Files\Android\Sdk\platform-tools\adb.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return None


def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
        return p.returncode, out
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------- #
# CA 证书
# --------------------------------------------------------------------------- #
def ensure_ca() -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
    _ensure_state()
    if CA_CERT.exists() and CA_KEY.exists():
        cert = x509.load_pem_x509_certificate(CA_CERT.read_bytes())
        key = serialization.load_pem_private_key(CA_KEY.read_bytes(), password=None)
        return cert, key

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "GigaX Capture CA")]
                     )
    now = _dt.datetime.now(_dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(days=1))
        .not_valid_after(now + _dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    CA_CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    CA_KEY.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert, key


def _leaf_for(host: str, ca_cert: x509.Certificate, ca_key: ec.EllipticCurvePrivateKey):
    key = ec.generate_private_key(ec.SECP256R1())
    now = _dt.datetime.now(_dt.timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _dt.timedelta(days=1))
        .not_valid_after(now + _dt.timedelta(days=825))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )
    try:
        ip = __import__("ipaddress").ip_address(host)
        san = x509.IPAddress(ip)
    except ValueError:
        san = x509.DNSName(host)
    builder = builder.add_extension(x509.SubjectAlternativeName([san]), critical=False)
    cert = builder.sign(ca_key, hashes.SHA256())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def cmd_ca(_args) -> None:
    ensure_ca()
    _print_box([
        "CA 证书已生成：",
        str(CA_CERT),
        "",
        "Android 7+ 不信任用户 CA，必须装成系统证书（需 root）：",
        "  1) 把 ca.crt 推到手机：",
        "     adb push " + str(CA_CERT) + " /sdcard/gigax-ca.crt",
        "  2) 计算 subject hash（Git Bash / openssl）：",
        "     openssl x509 -subject_hash_old -in " + str(CA_CERT),
        "     # 取输出第一行 8 位十六进制，记作 HASH",
        "  3) 装进系统证书目录：",
        "     adb shell su -c 'mount -o rw,remount /system'",
        "     adb shell su -c 'cp /sdcard/gigax-ca.crt /system/etc/security/cacerts/<HASH>.0'",
        "     adb shell su -c 'chmod 644 /system/etc/security/cacerts/<HASH>.0'",
        "     adb reboot",
        "",
        "更省事：Magisk 装 MoveCertificate 模块后重启，",
        "再把 ca.crt 放到 /data/misc/user/0/cacerts-added/ 即可自动转成系统证书。",
        "",
        "没有 root：用雷电/MuMu 等自带 root 的模拟器，步骤同上。",
    ])


# --------------------------------------------------------------------------- #
# 抓取结果落盘
# --------------------------------------------------------------------------- #
def save_auth(host: str, path: str, auth: str) -> bool:
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else auth.strip()
    if not token.startswith("eyJ"):
        return False
    rec = {
        "capturedAt": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "host": host,
        "path": path,
        "token": token,
    }
    with LOCK:
        old = None
        if TOKEN_FILE.exists():
            try:
                old = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
            except Exception:
                old = None
        # 叙事接口的 token 优先；否则后来者覆盖
        if old and old.get("path", "").startswith(NARRATIVE_PATH) and not path.startswith(NARRATIVE_PATH):
            return True
        TOKEN_FILE.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        with CAPTURE_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"\n[CAPTURE] {host}{path}\n[CAPTURE] Bearer {token[:60]}...{token[-12:]}\n", flush=True)
    return True


def save_narrative(host: str, path: str, status: int, body: bytes) -> None:
    if NARRATIVE_PATH not in path:
        return
    out = STATE / "narrative_response.json"
    text = body.decode("utf-8", "replace")
    try:
        text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    except Exception:
        pass
    out.write_text(f"HTTP {status}\n{text}", encoding="utf-8")
    print(f"[CAPTURE] 叙事响应已写入 {out}", flush=True)


# --------------------------------------------------------------------------- #
# 极简 HTTPS MITM 代理
# --------------------------------------------------------------------------- #
def _read_headers(sock: socket.socket) -> tuple[bytes | None, dict[str, str]]:
    buf = bytearray()
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            return None, {}
        buf.extend(chunk)
        if len(buf) > 262144:
            return None, {}
    head, rest = bytes(buf).split(b"\r\n\r\n", 1)
    lines = head.decode("latin-1").split("\r\n")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return lines[0].encode("latin-1"), headers


def _read_body(sock: socket.socket, headers: dict[str, str], initial: bytes = b"") -> bytes:
    try:
        n = int(headers.get("content-length", "0") or "0")
    except ValueError:
        n = 0
    body = bytearray(initial)
    while len(body) < n:
        chunk = sock.recv(min(65536, n - len(body)))
        if not chunk:
            break
        body.extend(chunk)
    return bytes(body)


class MitmHandler(socketserver.BaseRequestHandler):
    ca_cert = None
    ca_key = None
    upstream: str | None = None

    def handle(self) -> None:
        conn = self.request
        conn.settimeout(60)
        try:
            line, headers = _read_headers(conn)
            if not line:
                return
            parts = line.decode("latin-1").split()
            if not parts:
                return
            if parts[0].upper() == "CONNECT":
                self._do_connect(conn, parts[1])
            else:
                conn.sendall(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        except Exception as e:
            print(f"[PROXY] {type(e).__name__}: {e}", flush=True)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _do_connect(self, conn: socket.socket, hostport: str) -> None:
        host = hostport.split(":")[0]
        is_target = API_HOST in host
        print(f"[CONNECT] {hostport}{'  <== TARGET' if is_target else ''}", flush=True)
        cert_pem, key_pem = _leaf_for(host, self.ca_cert, self.ca_key)
        # 每个连接写唯一文件，避免 ThreadingTCPServer 并发下互相覆盖
        # 导致 load_cert_chain 读到不匹配的 cert/key（X509 KEY_VALUES_MISMATCH）
        STATE.mkdir(parents=True, exist_ok=True)
        import uuid
        tag = uuid.uuid4().hex
        cert_file = STATE / f"leaf-{tag}.crt"
        key_file = STATE / f"leaf-{tag}.key"
        cert_file.write_bytes(cert_pem)
        key_file.write_bytes(key_pem)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            ctx.load_cert_chain(str(cert_file), str(key_file))
        finally:
            # 私钥落盘即用即删，减少残留
            try:
                key_file.unlink()
            except OSError:
                pass
        conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        tls = ctx.wrap_socket(conn, server_side=True)

        while True:
            line, headers = _read_headers(tls)
            if not line:
                break
            parts = line.decode("latin-1").split()
            if len(parts) < 2:
                break
            method, path = parts[0], parts[1]
            body = _read_body(tls, headers)
            auth = headers.get("authorization", "")
            if auth:
                save_auth(host, path, auth)
            status, resp_headers, resp_body = self._forward(host, method, path, headers, body)
            save_narrative(host, path, status, resp_body)
            # 强制短连接，避免 keep-alive 解析复杂度
            clean = {k: v for k, v in resp_headers.items()
                     if k not in ("transfer-encoding", "connection", "content-length")}
            head = [f"HTTP/1.1 {status} Forwarded"]
            head += [f"{k}: {v}" for k, v in clean.items()]
            head += [f"Content-Length: {len(resp_body)}", "Connection: close", "", ""]
            tls.sendall(("\r\n".join(head)).encode("latin-1") + resp_body)
            break

    def _forward(self, host, method, path, headers, body):
        drop = {"host", "proxy-authorization", "proxy-connection", "connection",
                "content-length", "accept-encoding"}
        fwd = {k: v for k, v in headers.items() if k not in drop}
        fwd["host"] = host
        fwd["accept-encoding"] = "identity"
        ctx = ssl._create_unverified_context()
        if self.upstream:
            # 简易 HTTP 代理上游（如 Clash 7897）
            from urllib.parse import urlparse
            u = urlparse(self.upstream)
            c = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=60)
            c.request("CONNECT", f"{host}:443", headers={"Host": f"{host}:443"})
            c.getresponse().read()
            raw = c.sock
            tls_up = ctx.wrap_socket(raw, server_hostname=host)
            req = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in fwd.items())
            req += f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
            tls_up.sendall(req.encode("latin-1") + body)
            resp = http.client.HTTPResponse(tls_up)
            resp.begin()
            return resp.status, dict(resp.getheaders()), resp.read()
        c = http.client.HTTPSConnection(host, 443, context=ctx, timeout=60)
        c.request(method, path, body=body or None, headers=fwd)
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()


class MitmServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def cmd_proxy(args) -> None:
    ca_cert, ca_key = ensure_ca()
    MitmHandler.ca_cert = ca_cert
    MitmHandler.ca_key = ca_key
    MitmHandler.upstream = args.upstream
    srv = MitmServer(("0.0.0.0", args.port), MitmHandler)
    ips = _local_ips()
    _print_box([
        f"代理已启动：0.0.0.0:{args.port}",
        "可用局域网 IP（手机 WiFi 代理填第一个能 ping 通的）：" + ", ".join(ips),
        "注意：10.2.0.x 是 Proton 隧道地址，不能填。",
        "",
        f"手机端：WiFi → 代理(手动) → 主机名={ips[0]} 端口={args.port}",
        "然后打开 GigaX 并登录，随便点一下代币详情。",
        "",
        "如果 App 报网络错误 = CA 没装成系统证书，先执行：",
        "  C:/Python314/python.exe tools/gigax_token_capture.py ca",
        "",
        f"token 落盘：{TOKEN_FILE}",
        f"叙事响应：{STATE / 'narrative_response.json'}",
        "Ctrl+C 停止",
    ])
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[PROXY] stopped")
    finally:
        srv.server_close()


# --------------------------------------------------------------------------- #
# root 直读（无需 MITM）
# --------------------------------------------------------------------------- #
JWT_RE = re.compile(rb"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")


def cmd_pull(_args) -> None:
    adb = _find_adb()
    if not adb:
        print("未找到 adb。请安装 Android platform-tools 并加入 PATH。")
        return
    rc, out = _run([adb, "devices"])
    print(out.strip() or "(no output)")
    if "device\n" not in out and not re.search(r"\bdevice\b", out):
        print("没有已授权设备；手机需开启 USB 调试并授权本机。")
        return
    pkg = "family.giga.app"
    rc, out = _run([adb, "shell", "su -c 'id'"])
    if "uid=0" not in out:
        print("设备没有 root：此模式不可用，请改用 proxy + 系统 CA，或 root 模拟器。")
        return
    remote = "/data/local/tmp/gigax-appdata"
    _run([adb, "shell", "su -c 'rm -rf " + remote + "'"])
    rc, out = _run([adb, "shell", "su -c 'cp -a /data/data/" + pkg + " " + remote + " && chmod -R 777 " + remote + "'"], 180)
    if rc != 0:
        print("复制 App 数据失败：", out[:500])
        return
    local = STATE / "appdata"
    local.mkdir(parents=True, exist_ok=True)
    rc, out = _run([adb, "pull", remote, str(local)], 300)
    print(out[-500:])
    found = []
    for p in local.rglob("*"):
        if p.is_file() and p.stat().st_size < 80 * 1024 * 1024:
            try:
                data = p.read_bytes()
            except Exception:
                continue
            for m in JWT_RE.finditer(data):
                found.append((str(p.relative_to(local)), m.group().decode("latin-1")))
    if not found:
        print("没找到明文 JWT；Privy 可能用 SecureStore(Keystore) 加密，只能走 proxy 抓包。")
        return
    seen = set()
    for rel, tok in found:
        if tok in seen:
            continue
        seen.add(tok)
        print(f"[{rel}] {tok[:70]}...{tok[-12:]}")
        rec = {
            "capturedAt": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "host": API_HOST, "path": NARRATIVE_PATH + " (pulled)", "token": tok,
        }
        TOKEN_FILE.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写入 {TOKEN_FILE}")
        break


# --------------------------------------------------------------------------- #
# 实测
# --------------------------------------------------------------------------- #
def cmd_test(args) -> None:
    if not TOKEN_FILE.exists():
        print("还没有抓到 token。先跑 proxy 或 pull。")
        return
    rec = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    token = rec["token"]
    url = (f"https://{API_HOST}{NARRATIVE_PATH}"
           f"?chain={args.chain}&address={args.address}")
    import urllib.request
    import urllib.error
    import ssl as _ssl
    ctx = _ssl.create_default_context()
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Language-Contract": "2",
        "X-Device-Id": args.device_id,
        "X-Platform": "android",
        "X-App-Version": "1.0.4",
        "X-App-Build": "53488198",
        "X-App-Store": "internal",
        "User-Agent": "GigaX/1.0.4 (android; family.giga.app)",
    }
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30, context=ctx)
        body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"ERR {type(e).__name__}: {e}")
        return
    try:
        print(json.dumps(json.loads(body), ensure_ascii=False, indent=2)[:8000])
    except Exception:
        print(body[:2000])


def cmd_status(_args) -> None:
    if not TOKEN_FILE.exists():
        print("未抓到 token。")
        return
    rec = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    print(json.dumps({**rec, "token": rec["token"][:40] + "..." + rec["token"][-12:]},
                     ensure_ascii=False, indent=2))


def main() -> None:
    p = argparse.ArgumentParser(description="GigaX Bearer token 抓取工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ca", help="生成 CA 并打印 Android 系统证书安装步骤").set_defaults(fn=cmd_ca)

    pp = sub.add_parser("proxy", help="启动 HTTPS MITM 代理抓 Authorization")
    pp.add_argument("--port", type=int, default=8888)
    pp.add_argument("--upstream", default=None, help="上游代理，例如 http://127.0.0.1:7897")
    pp.set_defaults(fn=cmd_proxy)

    sub.add_parser("pull", help="已 root 手机：从 App 数据直接提取 JWT").set_defaults(fn=cmd_pull)

    pt = sub.add_parser("test", help="用抓到的 token 实测叙事接口")
    pt.add_argument("--chain", default="bsc")
    pt.add_argument("--address", default="0x47a40095e473df5bac45e4016900f0b502c56666")
    pt.add_argument("--device-id", default="gigax-capture-001")
    pt.set_defaults(fn=cmd_test)

    sub.add_parser("status", help="查看已抓到的 token").set_defaults(fn=cmd_status)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
