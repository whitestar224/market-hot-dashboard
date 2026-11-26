"""GIGA X 叙事桥接 —— 供油猴脚本通过主服务 8765 调用。

背景：GIGA X 的 Bearer token 是 Privy 签的 1 小时 JWT，只存在手机模拟器的
Expo SecureStore 里（AES + Android Keystore），浏览器里的油猴脚本拿不到。
本模块读 .runtime-cache/gigax-mitm/captured.json，
  - 用 **refresh token** 直接向 Privy 续期（主力，不需要模拟器在线）
  - 续不动时才回落到 frida 从模拟器重新解一次（兜底，需要模拟器 + 已登录）

Privy 续期（已抓包验证，2026-11-18）：
  POST https://auth.privy.io/api/v1/sessions
  headers:
    privy-app-id: cmrnbnkya003b0djrl1e7g9qk
    privy-client-id: client-WY6bLQjVZ6MjHbve6k1iobBCZ8V7GmDcMWgY7hVDKDc5T   # app.config extra.privy.androidClientId
    privy-client: expo:0.74.3
    authorization: Bearer <当前 App access token（aud=App ID 的那个，不是 pat）>
    privy-ca-id / x-native-app-identifier: family.giga.app
  body: {"refresh_token": "..."}
  ->
    {"token": <新 access token>, "refresh_token": <同一个，实测不轮换>,
     "privy_access_token": <用 pat 作 authorization 时才有，那个只能调 auth.privy.io>,
     "session_update_action": "set"|"ignore"}

路由（在 server.py Handler.do_GET 里转发）：
  GET /api/gigax/narrative?chain=bsc&address=0x...  -> {ok, text, timeline, ...}
  GET /api/gigax/health                             -> token / refresh token 状态
  GET /api/gigax/refresh?address=0x...              -> 强制续期

抓/解 token 工具：
  tools/gigax_securestore_dump.py  （frida 在 App 进程内用 keystore key 解 SecureStore，快且不依赖刷新时机）
  tools/gigax_narrative.py         （frida attach + hook Cipher.doFinal，老方案）
  tools/gigax_tls_tap.py           （Conscrypt 明文抓包，逆向接口时用）
"""
from __future__ import annotations

import base64
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]  # src/app/api/gigax.py -> 项目根
STATE = ROOT / ".runtime-cache" / "gigax-mitm"
TOKEN_FILE = STATE / "captured.json"
API_HOST = "app-api.gigax.fun"
NARRATIVE_PATH = "/api/data/token/narrative"

# ---- Privy 续期参数（来自 APK assets/app.config 的 extra.privy，抓包核对过）----
PRIVY_HOST = "auth.privy.io"
PRIVY_SESSIONS_PATH = "/api/v1/sessions"
PRIVY_APP_ID = "cmrnbnkya003b0djrl1e7g9qk"
PRIVY_CLIENT_ID = "client-WY6bLQjVZ6MjHbve6k1iobBCZ8V7GmDcMWgY7hVDKDc5T"
PRIVY_CLIENT_VERSION = "expo:0.74.3"
PRIVY_CA_ID = "2b9f4e36-84ec-4928-a1db-073344e9a217"
NATIVE_APP_ID = "family.giga.app"

ADB = r"D:\leidian\LDPlayer14\adb.exe"
PY310 = r"C:\Users\ZhuanZ1\AppData\Local\Programs\Python\Python310\python.exe"
GRAB_SCRIPT = ROOT / "tools" / "gigax_narrative.py"
DUMP_SCRIPT = ROOT / "tools" / "gigax_securestore_dump.py"

CHAIN_CODES = {
    "sol": "sol", "eth": "eth", "bsc": "bsc", "base": "base",
    "robinhood": "robin", "arc": "arc", "elysium": "elysium",
}
_HEADERS = {
    "Accept": "application/json", "Content-Type": "application/json",
    "X-Language-Contract": "2",
    "X-Device-Id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
    "X-Platform": "android", "X-App-Version": "1.0.4", "X-App-Build": "53488198",
    "X-App-Store": "internal", "User-Agent": "okhttp/4.12.0",
}
# 提前多久续期（秒）。token 1 小时寿命，留 5 分钟余量。
REFRESH_MARGIN_SECONDS = 300

_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# token 存取
# --------------------------------------------------------------------------- #
def _jwt_claims(token: str) -> dict[str, Any]:
    try:
        p = token.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p))
    except Exception:
        return {}


def token_exp(token: str) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(_jwt_claims(token)["exp"]), tz=timezone.utc)
    except Exception:
        return None


def load_state() -> dict[str, Any]:
    try:
        data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_token() -> str | None:
    tok = load_state().get("token")
    return tok or None


def _save_state(**fields: Any) -> dict[str, Any]:
    STATE.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        state = load_state()
        state.update({k: v for k, v in fields.items() if v is not None})
        state["updatedAt"] = datetime.now().astimezone().isoformat(timespec="seconds")
        # 记下这条 refresh token 第一次进库的时间：Privy 默认 30 天到期，用来做临期提醒
        if state.get("refreshToken") and not state.get("refreshTokenFirstSeenAt"):
            state["refreshTokenFirstSeenAt"] = state["updatedAt"]
        tmp = TOKEN_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(TOKEN_FILE)
        return state


def _seconds_left(token: str | None) -> float | None:
    exp = token_exp(token) if token else None
    if not exp:
        return None
    return (exp - datetime.now(timezone.utc)).total_seconds()


# --------------------------------------------------------------------------- #
# Privy 续期
# --------------------------------------------------------------------------- #
def refresh_via_privy(timeout: int = 25) -> tuple[bool, str]:
    """用 refresh token 换新 access token。token 还没到期时 Privy 原样返回旧 token。"""
    state = load_state()
    token = state.get("token")
    refresh_token = state.get("refreshToken")
    if not refresh_token:
        return False, "没有 refresh token（先跑 tools/gigax_securestore_dump.py）"
    if not token:
        return False, "没有 access token，无法定位会话"

    body = json.dumps({"refresh_token": refresh_token}).encode()
    headers = {
        "privy-app-id": PRIVY_APP_ID,
        "privy-client-id": PRIVY_CLIENT_ID,
        "privy-client": PRIVY_CLIENT_VERSION,
        "authorization": "Bearer " + token,
        "privy-ca-id": state.get("privyCaId") or PRIVY_CA_ID,
        "x-native-app-identifier": NATIVE_APP_ID,
        "privy-retry-attempt": "0",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "okhttp/4.12.0",
    }
    url = f"https://{PRIVY_HOST}{PRIVY_SESSIONS_PATH}"
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        return False, f"Privy HTTP {e.code}: {detail}"
    except Exception as exc:
        return False, f"Privy 请求异常: {type(exc).__name__}: {exc}"

    try:
        data = json.loads(raw)
    except Exception:
        return False, f"Privy 返回非 JSON: {raw[:160]}"

    new_token = data.get("token") or data.get("privy_access_token") or ""
    new_refresh = data.get("refresh_token") or refresh_token
    if not new_token:
        return False, f"Privy 没给 token（action={data.get('session_update_action')}）"
    claims = _jwt_claims(new_token)
    if str(claims.get("att") or "") == "pat" or str(claims.get("aud") or "").startswith("https://"):
        # pat 只能调 auth.privy.io，app-api 会回 Invalid token
        return False, "Privy 只返回了 pat，不是 App token"

    _save_state(
        token=new_token,
        refreshToken=new_refresh,
        tokenExp=token_exp(new_token).isoformat() if token_exp(new_token) else None,
        lastRefreshAt=datetime.now().astimezone().isoformat(timespec="seconds"),
        source="privy-refresh",
    )
    return True, "已续期"


# --------------------------------------------------------------------------- #
# 兜底：从模拟器重新解 SecureStore
# --------------------------------------------------------------------------- #
_EMU_CACHE: dict[str, Any] = {"at": 0.0, "online": False}


def emulator_online(max_age_seconds: int = 60) -> bool:
    """adb 探活很慢（离线要等 6s 超时），缓存一分钟。"""
    now = time.time()
    if now - float(_EMU_CACHE.get("at") or 0) < max_age_seconds:
        return bool(_EMU_CACHE.get("online"))
    try:
        p = subprocess.run(
            [ADB, "-s", "emulator-5554", "shell", "getprop", "sys.boot_completed"],
            capture_output=True, timeout=6,
        )
        online = (p.stdout or b"").decode().strip() == "1"
    except Exception:
        online = False
    _EMU_CACHE["at"] = now
    _EMU_CACHE["online"] = online
    return online


def _grab_from_emulator() -> tuple[bool, str]:
    """frida 在 App 进程内用 keystore key 解 SecureStore，读出 token + refresh token。"""
    if not emulator_online():
        return False, "模拟器离线"
    try:
        p = subprocess.run(
            [PY310, str(DUMP_SCRIPT)], capture_output=True, timeout=180,
            cwd=str(ROOT),
        )
        out = (p.stdout or b"").decode("utf-8", "replace")
        dump_file = STATE / "securestore_dump.json"
        if not dump_file.exists():
            return False, (out.strip()[-200:] or "dump 无输出")
        dump = json.loads(dump_file.read_text(encoding="utf-8"))
        dec = lambda k: base64.b64decode(dump[k]["b64"]).decode()
        token = dec("key_v1-privy-token")
        refresh = dec("key_v1-privy-refresh_token")
        _save_state(
            token=token,
            refreshToken=refresh,
            tokenExp=token_exp(token).isoformat() if token_exp(token) else None,
            capturedAt=datetime.now().astimezone().isoformat(timespec="seconds"),
            source="emulator-dump",
        )
        return True, "已从模拟器读取"
    except subprocess.TimeoutExpired:
        return False, "读取模拟器超时（frida 卡住？）"
    except Exception as exc:
        return False, f"读取模拟器失败: {type(exc).__name__}: {exc}"


def refresh_token(_address: str | None = None) -> tuple[bool, str]:
    """对外保留的「强制续期」：先 Privy，不行再问模拟器。"""
    ok, msg = refresh_via_privy()
    if ok:
        return True, msg
    ok2, msg2 = _grab_from_emulator()
    if ok2:
        return True, f"{msg} → {msg2}"
    return False, f"{msg}；{msg2}"


def ensure_token(force: bool = False) -> tuple[str | None, str | None]:
    """返回 (token, error)。临近过期就续期。"""
    state = load_state()
    token = state.get("token")
    left = _seconds_left(token) if token else None
    if token and not force and left is not None and left > REFRESH_MARGIN_SECONDS:
        return token, None
    ok, msg = refresh_via_privy()
    if ok:
        return load_token(), None
    if token and left is not None and left > 0:
        # 续期失败但手上 token 还能用，先用着
        return token, None
    ok2, msg2 = _grab_from_emulator()
    if ok2:
        return load_token(), None
    return None, f"token 失效且续期失败：{msg}；{msg2}"


# --------------------------------------------------------------------------- #
# GIGA X 叙事接口
# --------------------------------------------------------------------------- #
def _call_api(token: str, chain: str, address: str) -> tuple[dict | None, str | None]:
    code = CHAIN_CODES.get((chain or "bsc").lower(), (chain or "bsc").lower())
    url = f"https://{API_HOST}{NARRATIVE_PATH}?chain={code}&address={address}"
    headers = dict(_HEADERS)
    headers["Authorization"] = "Bearer " + token
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    try:
        return json.loads(body), None
    except Exception:
        return None, f"非 JSON 响应: {body[:160]}"


_AUTH_HINTS = ("invalid token", "unauthorized", "token expired", "jwt", "登录", "token 无效", "token 失效")


def _is_token_error(data: dict | None) -> bool:
    """只有「鉴权失败」才算 token 错误。

    注意 `Invalid token address` 也带 "token" 三个字，但那是**地址不合法**
    （典型：base58 地址被小写化后 GIGA X 不认识）。以前把它当鉴权失败，
    每查一个坏地址就白触发一次 Privy 续期。
    """
    if not data:
        return True
    if data.get("code") == 0:
        return False
    msg = str(data.get("msg") or data.get("error") or "").strip().lower()
    if not msg:
        return True
    if "address" in msg:
        return False
    return any(k in msg for k in _AUTH_HINTS)


def _shape(data: dict | None, call_err: str | None) -> dict[str, Any]:
    """把 GIGA X 的原始返回揉成前端要的形状（含 no-narrative / auth 三态）。"""
    if _is_token_error(data):
        return {"ok": False, "reason": "auth",
                "error": call_err or (data or {}).get("msg") or "token 无效"}

    msg = str((data or {}).get("msg") or "").strip()
    if msg and "address" in msg.lower():
        # 地址本身不合法（base58 被小写化、链号给错都会这样）——对前端来说等于「没这个币」
        return {"ok": False, "reason": "no-narrative", "status": "bad-address",
                "error": f"GIGA X 不认这个地址：{msg}"}

    wrap = data.get("data") or {}
    narrative = wrap.get("narrative") or {}
    text = narrative.get("narrative_text") or narrative.get("original_text") or ""
    if not text.strip():
        # GIGA X 对这个币还没生成叙事（status=generating）或者干脆没有。
        # 这不是错误：前端应该保留 GMGN 原文，而不是显示红字。
        retry_after = 0
        for src in (data, wrap):
            try:
                retry_after = int(src.get("retry_after_seconds") or 0) or retry_after
            except Exception:
                pass
        return {
            "ok": False,
            "reason": "no-narrative",
            "status": wrap.get("status") or "",
            "retryAfter": retry_after,
            "error": "GIGA X 暂无该币叙事",
        }

    return {
        "ok": True,
        "reason": None,
        "text": text,
        "originalText": narrative.get("original_text") or "",
        "categoryTags": narrative.get("category_tags") or [],
        "generatedAt": narrative.get("generated_at") or "",
        "timeline": [
            {"summary": t.get("summary") or "", "url": t.get("source_url") or ""}
            for t in (narrative.get("narrative_timeline") or [])
        ],
        "status": wrap.get("status") or "",
    }


# --------------------------------------------------------------------------- #
# 进程内缓存：油猴一次批量要十几二十个币，重复请求没必要再打 GIGA X
# --------------------------------------------------------------------------- #
_NARR_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_NARR_CACHE_LOCK = threading.Lock()
_CACHE_TTL_OK = 1800         # 有正文：30 分钟（叙事不会半小时就变）
_CACHE_TTL_EMPTY = 120       # 没叙事（还在生成）：2 分钟
_CACHE_MAX = 3000


def _cache_key(chain: str, address: str) -> str:
    return f"{(chain or 'bsc').lower()}:{address}"


def _cache_get(key: str) -> dict[str, Any] | None:
    with _NARR_CACHE_LOCK:
        hit = _NARR_CACHE.get(key)
        if not hit:
            return None
        ts, payload = hit
        ttl = _CACHE_TTL_OK if payload.get("ok") else _CACHE_TTL_EMPTY
        if time.time() - ts > ttl:
            _NARR_CACHE.pop(key, None)
            return None
        return payload


def _cache_put(key: str, payload: dict[str, Any]) -> None:
    # 只缓存「确定」的结果：鉴权/网络错误不缓存，否则一次抖动会连累十分钟
    if payload.get("reason") not in (None, "no-narrative"):
        return
    with _NARR_CACHE_LOCK:
        if len(_NARR_CACHE) > _CACHE_MAX:
            for k in list(_NARR_CACHE)[: _CACHE_MAX // 5]:
                _NARR_CACHE.pop(k, None)
        _NARR_CACHE[key] = (time.time(), payload)


def fetch_narrative(chain: str, address: str, allow_refresh: bool = True) -> dict[str, Any]:
    key = _cache_key(chain, address)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    token, err = ensure_token()
    if not token:
        return {"ok": False, "reason": "auth", "error": err or "没有可用 token"}
    data, call_err = _call_api(token, chain, address)
    if _is_token_error(data) and allow_refresh:
        token, err = ensure_token(force=True)
        if token:
            data, call_err = _call_api(token, chain, address)
    payload = _shape(data, call_err)
    _cache_put(key, payload)
    return payload


def fetch_narratives(items: list[tuple[str, str]], max_workers: int = 6) -> dict[str, Any]:
    """批量取叙事：一个 HTTP 请求，服务端并发去问 GIGA X。

    items = [("sol", "CA..."), ("bsc", "0x..."), ...]
    返回 {"ok": True, "results": {"sol:CA": {...}, ...}, "cached": n, "fetched": m}

    之前是前端一个币一个请求、串着发，一屏 20 个要十几秒；现在这里 6 路并发，
    一屏基本 1~2 秒内回来。
    """
    results: dict[str, Any] = {}
    todo: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for chain, address in items or []:
        chain = (chain or "bsc").strip().lower()
        address = (address or "").strip()
        if not address:
            continue
        key = _cache_key(chain, address)
        if key in seen:
            continue
        seen.add(key)
        hit = _cache_get(key)
        if hit is not None:
            results[key] = hit
            continue
        todo.append((chain, address, key))
    cached_n = len(results)
    if not todo:
        return {"ok": True, "results": results, "cached": cached_n, "fetched": 0}

    token, err = ensure_token()
    if not token:
        return {"ok": False, "reason": "auth", "error": err or "没有可用 token",
                "results": results, "cached": cached_n, "fetched": 0}

    def _one(chain: str, address: str) -> tuple[str, dict[str, Any]]:
        data, call_err = _call_api(token, chain, address)
        return _cache_key(chain, address), _shape(data, call_err)

    auth_failed = False
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 10))) as ex:
        futures = [ex.submit(_one, c, a) for c, a, _ in todo]
        for fut in futures:
            try:
                key, payload = fut.result()
            except Exception as exc:  # noqa: BLE001
                continue
            if payload.get("reason") == "auth":
                auth_failed = True
            _cache_put(key, payload)
            results[key] = payload

    if auth_failed:
        # 整批都判成鉴权失败时，强制续一次再重跑（只这一次，别递归）
        ok, _msg = refresh_via_privy()
        if ok:
            token2 = load_token()
            if token2:
                with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 10))) as ex:
                    futures = [ex.submit(lambda c=c, a=a: _shape(*_call_api(token2, c, a)))
                               for c, a, _ in todo]
                    for (chain, address, key), fut in zip(todo, futures):
                        try:
                            payload = fut.result()
                        except Exception:
                            continue
                        _cache_put(key, payload)
                        results[key] = payload
    return {"ok": True, "results": results, "cached": cached_n,
            "fetched": len(results) - cached_n}


def start_keepalive(interval_minutes: int = 30) -> None:
    """后台保活：定期用 refresh token 换一次 token。

    Privy 的 refresh token 默认 30 天不用就失效，而每次续期都会刷新这条链，
    所以每 30 分钟戳一次就能长期不过期。失败只记日志，不抛。
    """
    if getattr(start_keepalive, "_started", False):
        return
    start_keepalive._started = True  # type: ignore[attr-defined]

    def _loop() -> None:
        # 启动后先等 60 秒，别和首屏请求抢
        time.sleep(60)
        warned_on: str | None = None
        while True:
            try:
                state = load_state()
                if not state.get("refreshToken"):
                    time.sleep(interval_minutes * 60)
                    continue
                ok, msg = refresh_via_privy()
                if not ok:
                    print(f"[gigax] token 保活失败：{msg}")
                first_seen = state.get("refreshTokenFirstSeenAt")
                if first_seen:
                    today = datetime.now().astimezone().strftime("%Y-%m-%d")
                    try:
                        age = (datetime.now(timezone.utc)
                               - datetime.fromisoformat(first_seen)).total_seconds() / 86400
                    except Exception:
                        age = 0
                    if age >= 25 and warned_on != today:
                        warned_on = today
                        print(f"[gigax] refresh token 已使用 {age:.0f} 天，Privy 默认 30 天到期："
                              f"请开一次模拟器重登 GigaX 并跑 tools/gigax_securestore_dump.py")
            except Exception as exc:  # noqa: BLE001
                print(f"[gigax] token 保活异常：{type(exc).__name__}: {exc}")
            time.sleep(interval_minutes * 60)

    threading.Thread(target=_loop, name="gigax-token-keepalive", daemon=True).start()


# --------------------------------------------------------------------------- #
# 健康检查
# --------------------------------------------------------------------------- #
def health() -> dict[str, Any]:
    state = load_state()
    token = state.get("token")
    exp = token_exp(token) if token else None
    now = datetime.now(timezone.utc)
    left = int((exp - now).total_seconds()) if exp else None
    age_days: float | None = None
    first_seen = state.get("refreshTokenFirstSeenAt")
    if first_seen:
        try:
            age_days = round((now - datetime.fromisoformat(first_seen)).total_seconds() / 86400, 1)
        except Exception:
            age_days = None
    return {
        "ok": True,
        "hasToken": bool(token),
        "tokenExp": exp.isoformat() if exp else None,
        "expired": bool(exp and exp < now),
        "expiresInSeconds": left,
        "hasRefreshToken": bool(state.get("refreshToken")),
        # refresh token 是 Privy 签的 30 天长期凭据；超过 25 天就该重登一次 App 再 dump 一份
        "refreshTokenAgeDays": age_days,
        "refreshTokenExpiringSoon": bool(age_days is not None and age_days >= 25),
        "lastRefreshAt": state.get("lastRefreshAt"),
        "source": state.get("source"),
        "emulatorOnline": emulator_online(),
    }
