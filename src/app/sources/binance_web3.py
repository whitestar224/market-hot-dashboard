"""Official Binance Web3 read/quote client. No signing or broadcast endpoint.

API credentials are not wallet keys. Windows DPAPI binds saved credentials to
the current Windows account; they are never sent to the browser or AI.
"""
import base64
import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
from urllib.parse import urlencode, quote

import requests

PREFIX = "/build"
HOST = "https://web3.binance.com"
ALLOWED = {
    ("GET", "/api/v1/dex/aggregator/supported/chain"),
    ("GET", "/api/v1/dex/aggregator/quote"),
    ("GET", "/api/v1/dex/aggregator/quote-and-swap"),
    ("GET", "/api/v1/dex/aggregator/swap"),
    ("GET", "/api/v1/dex/aggregator/approve-transaction"),
    ("POST", "/api/v1/dex/pre-transaction/simulate"),
    ("GET", "/api/v1/dex/balance/all-token-balances-by-address"),
    ("POST", "/api/v1/dex/balance/token-balances-by-address"),
}


def credential_path():
    base = os.environ.get("LOCALAPPDATA")
    if not base or os.name != "nt":
        raise ValueError("本机加密配置需要 Windows")
    return Path(base) / "XingyunShe" / "binance-web3.credentials"


def protect(data, decrypt=False):
    if os.name != "nt":
        raise ValueError("本机加密配置需要 Windows")
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    output = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ValueError("无法读取本机加密凭证，请在当前 Windows 账户重新配置")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


def validate_credentials(key, secret):
    if not all(isinstance(s, str) and 8 <= len(s) <= 512 and re.fullmatch(r"[!-~]+", s) for s in (key, secret)):
        raise ValueError("请填写有效的 Web3 API Key 和 Secret，不要填写钱包助记词或私钥")
    return key, secret


def save_credentials(key, secret, path=None):
    key, secret = validate_credentials(key.strip(), secret.strip())
    target = Path(path) if path else credential_path()
    encrypted = protect(json.dumps({"key": key, "secret": secret}).encode())
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".new")
    temporary.write_bytes(encrypted)
    temporary.replace(target)


def load_credentials():
    key, secret = os.getenv("OC_API_KEY", ""), os.getenv("OC_SECRET_KEY", "")
    if key or secret:
        return validate_credentials(key, secret)
    try:
        stored = json.loads(protect(credential_path().read_bytes(), decrypt=True))
        return validate_credentials(stored.get("key"), stored.get("secret"))
    except FileNotFoundError:
        raise ValueError("尚未在本机配置币安 Web3 API") from None
    except Exception:
        raise ValueError("本机币安 Web3 凭证不可用，请重新配置") from None


def signed_request(key, secret, method, path, params=None, body=None, timestamp=None):
    if (method, path) not in ALLOWED:
        raise ValueError("不允许访问此接口")
    validate_credentials(key, secret)
    query_string = urlencode(params or {}, quote_via=quote)
    request_path = PREFIX + path + ("?" + query_string if query_string else "")
    raw_body = json.dumps(body, ensure_ascii=False, separators=(",", ":")) if body is not None else ""
    if method == "GET" and raw_body:
        raise ValueError("GET 请求不能包含正文")
    timestamp = timestamp or datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    signature = base64.b64encode(hmac.new(secret.encode(),
        (timestamp + method + request_path + raw_body).encode(), hashlib.sha256).digest()).decode()
    return HOST + request_path, {"X-OC-APIKEY": key, "X-OC-TIMESTAMP": timestamp, "X-OC-SIGN": signature,
        "X-OC-NONCE": secrets.token_hex(16), "Content-Type": "application/json"}, raw_body


class BinanceWeb3Client:
    def __init__(self, credentials=load_credentials, http=None):
        self.session = requests.Session() if http is None else None
        self.credentials, self.http = credentials, http or self.session.request
        self.clock_offset = 0.0

    def request(self, method, path, params=None, body=None):
        credentials = self.credentials()
        for attempt in range(2):
            try:
                timestamp = (datetime.now(timezone.utc) + timedelta(seconds=self.clock_offset)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                url, headers, raw = signed_request(*credentials, method, path, params, body, timestamp)
                sent_at = datetime.now(timezone.utc)
                response = self.http(method, url, headers=headers, data=raw.encode() if raw else None,
                                     timeout=(4, 10), allow_redirects=False)
                received_at = datetime.now(timezone.utc)
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                payload = payload if isinstance(payload, dict) else {}
                code = str(payload.get("code", ""))
                if response.status_code == 401 and code == "40103" and attempt == 0:
                    # Fixed HTTPS host only. Correct request timestamps, never the
                    # OS clock or recv-window. Retry one READ with a new nonce.
                    try:
                        stamp = payload.get("timestamp")
                        remote = (datetime.fromtimestamp(float(stamp)/1000, timezone.utc)
                                  if isinstance(stamp, (int, float)) and 1e12 < stamp < 1e14
                                  else parsedate_to_datetime(response.headers.get("Date", "")))
                        midpoint = sent_at + (received_at - sent_at)/2
                        offset = (remote - midpoint).total_seconds()
                    except (TypeError, ValueError, OverflowError):
                        raise ValueError("币安接口时间校验失败，请同步本机时间") from None
                    if abs(offset) > 120:
                        raise ValueError("本机时间偏差过大，请先同步系统时间")
                    self.clock_offset = offset
                    continue
                errors = {"40101": "币安 Web3 API Key 无效或已停用，请检查是否为 Web3 专用凭证",
                          "40102": "币安 Web3 签名校验失败，请核对 Key 与 Secret 是否配套",
                          "40103": "币安接口时间校验失败，请同步本机时间",
                          "40104": "币安 Web3 API 缺少所需权限",
                          "40401": "币安报价已过期，请重新获取报价",
                          "40462": "币安交易与报价不一致，已停止",
                          "40001": "币安 Web3 报价参数校验失败（40001），不代表该代币不受支持"}
                # Match only known messages; never echo arbitrary vendor text,
                # headers, URLs or credential values into the UI/logs.
                if code == "40001" and payload.get("msg") == "autoSlippage and slippagePercent are mutually exclusive, pass only one":
                    errors[code] = "币安报价参数冲突：自动滑点与固定滑点不能同时传入（40001）"
                if response.status_code != 200:
                    raise ValueError(errors.get(code) or {401: "币安 Web3 凭证或签名未通过", 403: "币安 Web3 接口权限或地区限制",
                        429: "币安 Web3 接口暂时限流"}.get(response.status_code, "币安 Web3 接口暂不可用"))
                if payload.get("success") is not True or code != "0":
                    raise ValueError(errors.get(code) or "币安 Web3 接口未返回成功结果，请检查权限与本机时间")
                return payload.get("data")
            except requests.RequestException:
                # Quotes and capability reads are safe to retry once: this client
                # has no signing/broadcast endpoint and every retry gets a nonce.
                if method == "GET" and attempt == 0:
                    continue
                raise ValueError("连接币安 Web3 接口超时或失败（只读报价已自动重试 1 次）") from None
            except (json.JSONDecodeError, TypeError):
                raise ValueError("币安 Web3 接口返回格式异常") from None

    def check_connection(self):
        result = self.request("GET", "/api/v1/dex/aggregator/supported/chain", {"binanceChainId": "56"})
        if not isinstance(result, (dict, list)) or not result:
            raise ValueError("币安 Web3 未返回 BNB 链能力，暂不启用交易")
        return {"ok": True, "message": "币安 Web3 接口连接成功；尚未发起交易"}
