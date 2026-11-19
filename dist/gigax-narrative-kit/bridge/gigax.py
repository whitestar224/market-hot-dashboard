"""GIGA X 叙事桥接 —— 可独立运行，也可嵌进已有服务。

它做两件事：
  1. 用 **refresh token** 向 Privy 续期，拿到能调 GIGA X 接口的 access token；
  2. 用这个 token 代调 `GET https://app-api.gigax.fun/api/data/token/narrative`，
     把结果整理成前端好用的 JSON。

token 是 1 小时过期的 JWT，refresh token 是 30 天的长期凭据（Privy 默认）。
两者都只存在本机 `.state/captured.json`，不写进代码。

依赖：Python 3.10+，标准库足够。只有从模拟器抓 token 时才需要 frida。
配置：见同目录 `config.example.json` 与 README。
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
STATE = Path(os.environ.get("GIGAX_STATE_DIR") or (HERE / ".state"))
TOKEN_FILE = STATE / "captured.json"
CONFIG_FILE = HERE / "config.json"

API_HOST = "app-api.gigax.fun"
NARRATIVE_PATH = "/api/data/token/narrative"
PRIVY_HOST = "auth.privy.io"
PRIVY_SESSIONS_PATH = "/api/v1/sessions"

CHAIN_CODES = {
    "sol": "sol", "eth": "eth", "bsc": "bsc", "base": "base",
    "robinhood": "robin", "arc": "arc", "elysium": "elysium",
}

# 提前多久续期（秒）。token 1 小时寿命，留 5 分钟余量。
REFRESH_MARGIN_SECONDS = 300

_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def load_config() -> dict[str, Any]:
    """config.json > 环境变量 > 空值。

    Privy 的几个 ID 不是密钥，但属于「这个 App 的身份」，所以不写死在代码里：
    从 APK 的 `assets/app.config` 里 `extra.privy` 拿，填进 config.json 即可。
    """
    cfg: dict[str, Any] = {}
    try:
        if CONFIG_FILE.exists():
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) or {}
    except Exception:
        cfg = {}
    env_map = {
        "privyAppId": "GIGAX_PRIVY_APP_ID",
        "privyClientId": "GIGAX_PRIVY_CLIENT_ID",
        "privyClientVersion": "GIGAX_PRIVY_CLIENT_VERSION",
        "privyCaId": "GIGAX_PRIVY_CA_ID",
        "nativeAppId": "GIGAX_NATIVE_APP_ID",
        "adb": "GIGAX_ADB",
        "fridaPython": "GIGAX_FRIDA_PYTHON",
    }
    for key, env in env_map.items():
        if not cfg.get(key) and os.environ.get(env):
            cfg[key] = os.environ[env]
    return cfg


def _find_adb() -> str:
    cfg = load_config()
    if cfg.get("adb"):
        return str(cfg["adb"])
    found = shutil.which("adb")
    if found:
        return found
    for guess in (
        os.path.expandvars(r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe"),
        "/usr/local/bin/adb",
        "/usr/bin/adb",
    ):
        if os.path.exists(guess):
            return guess
    return "adb"


def _frida_python() -> str:
    """跑 frida 的解释器。系统 Python 装不上 frida 时用单独一个 3.10 环境。"""
    cfg = load_config()
    return str(cfg.get("fridaPython") or os.sys.executable)


ADB = _find_adb()


# --------------------------------------------------------------------------- #
# token 状态
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
    return load_state().get("token") or None


def _save_state(**fields: Any) -> dict[str, Any]:
    STATE.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        state = load_state()
        state.update({k: v for k, v in fields.items() if v is not None})
        state["updatedAt"] = datetime.now().astimezone().isoformat(timespec="seconds")
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
    """用 refresh token 换 access token。没到期时 Privy 会原样返回旧 token。"""
    cfg = load_config()
    state = load_state()
    token = state.get("token")
    refresh_token = state.get("refreshToken")
    app_id = cfg.get("privyAppId")
    client_id = cfg.get("privyClientId")

    if not refresh_token:
        return False, "没有 refresh token（先跑 tools/gigax_securestore_dump.py）"
    if not token:
        return False, "没有 access token，无法定位会话"
    if not app_id or not client_id:
        return False, "缺少 privyAppId / privyClientId（见 config.example.json）"

    body = json.dumps({"refresh_token": refresh_token}).encode()
    headers = {
        "privy-app-id": str(app_id),
        "privy-client-id": str(client_id),
        "privy-client": str(cfg.get("privyClientVersion") or "expo:0.74.3"),
        "authorization": "Bearer " + token,
        "privy-ca-id": str(cfg.get("privyCaId") or _ca_id()),
        "x-native-app-identifier": str(cfg.get("nativeAppId") or ""),
        "privy-retry-attempt": "0",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "okhttp/4.12.0",
    }
    headers = {k: v for k, v in headers.items() if v}
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
        # pat 只能调 auth.privy.io，拿去调 app-api 会回 Invalid token
        return False, "Privy 只返回了 pat，不是 App token"

    exp = token_exp(new_token)
    _save_state(
        token=new_token,
        refreshToken=new_refresh,
        tokenExp=exp.isoformat() if exp else None,
        lastRefreshAt=datetime.now().astimezone().isoformat(timespec="seconds"),
        source="privy-refresh",
    )
    return True, "已续期"


# --------------------------------------------------------------------------- #
# 兜底：从模拟器重新解 SecureStore
# --------------------------------------------------------------------------- #
def emulator_online(max_age_seconds: int = 60) -> bool:
    """adb 探活很慢，缓存一分钟。"""
    now = time.time()
    cached = _EMU_CACHE.get("at") or 0
    if now - float(cached) < max_age_seconds:
        return bool(_EMU_CACHE.get("online"))
    try:
        p = subprocess.run([ADB, "shell", "getprop", "sys.boot_completed"],
                           capture_output=True, timeout=6)
        online = (p.stdout or b"").decode().strip() == "1"
    except Exception:
        online = False
    _EMU_CACHE["at"] = now
    _EMU_CACHE["online"] = online
    return online


_EMU_CACHE: dict[str, Any] = {"at": 0.0, "online": False}


def dump_from_emulator() -> tuple[bool, str]:
    """frida 在 App 进程内用 Keystore 的 key 解 SecureStore，读出 token + refresh token。"""
    if not emulator_online():
        return False, "模拟器离线"
    script = HERE.parent / "tools" / "gigax_securestore_dump.py"
    if not script.exists():
        return False, f"找不到 {script}"
    try:
        p = subprocess.run([_frida_python(), str(script)], capture_output=True, timeout=180,
                           cwd=str(script.parent.parent))
        out = (p.stdout or b"").decode("utf-8", "replace")
        dump_file = STATE / "securestore_dump.json"
        if not dump_file.exists():
            # dump 脚本默认写在 bridge/.state；GIGAX_STATE_DIR 改过时两边可能不一致
            dump_file = HERE / ".state" / "securestore_dump.json"
        if not dump_file.exists():
            return False, (out.strip()[-200:] or "dump 无输出")
        dump = json.loads(dump_file.read_text(encoding="utf-8"))
        dec = lambda k: base64.b64decode(dump[k]["b64"]).decode()
        token = dec("key_v1-privy-token")
        refresh = dec("key_v1-privy-refresh_token")
        exp = token_exp(token)
        _save_state(
            token=token,
            refreshToken=refresh,
            tokenExp=exp.isoformat() if exp else None,
            capturedAt=datetime.now().astimezone().isoformat(timespec="seconds"),
            source="emulator-dump",
        )
        return True, "已从模拟器读取"
    except subprocess.TimeoutExpired:
        return False, "读取模拟器超时（frida 卡住？）"
    except Exception as exc:
        return False, f"读取模拟器失败: {type(exc).__name__}: {exc}"


def refresh_token(_address: str | None = None) -> tuple[bool, str]:
    """强制续期：先 Privy，不行再问模拟器。"""
    ok, msg = refresh_via_privy()
    if ok:
        return True, msg
    ok2, msg2 = dump_from_emulator()
    if ok2:
        return True, f"{msg} → {msg2}"
    return False, f"{msg}；{msg2}"


def ensure_token(force: bool = False) -> tuple[str | None, str | None]:
    state = load_state()
    token = state.get("token")
    left = _seconds_left(token) if token else None
    if token and not force and left is not None and left > REFRESH_MARGIN_SECONDS:
        return token, None
    ok, _msg = refresh_via_privy()
    if ok:
        return load_token(), None
    if token and left is not None and left > 0:
        return token, None
    ok2, msg2 = dump_from_emulator()
    if ok2:
        return load_token(), None
    return None, f"token 失效且续期失败：{_msg}；{msg2}"


# --------------------------------------------------------------------------- #
# GIGA X 叙事接口
# --------------------------------------------------------------------------- #
def _ca_id() -> str:
    """Privy 的客户端分析 id（privy-ca-id）。没有就生成一个并复用。"""
    state = load_state()
    ca = state.get("privyCaId")
    if ca:
        return str(ca)
    ca = str(uuid.uuid4())
    _save_state(privyCaId=ca)
    return ca


def _device_id() -> str:
    """GIGA X 要求带 X-Device-Id；本地生成一次并复用，不写死在代码里。"""
    state = load_state()
    did = state.get("deviceId")
    if did:
        return str(did)
    did = str(uuid.uuid4())
    _save_state(deviceId=did)
    return did


def _api_headers() -> dict[str, str]:
    return {
        "Accept": "application/json", "Content-Type": "application/json",
        "X-Language-Contract": "2",
        "X-Device-Id": _device_id(),
        "X-Platform": "android", "X-App-Version": "1.0.4", "X-App-Build": "53488198",
        "X-App-Store": "internal", "User-Agent": "okhttp/4.12.0",
    }


def _call_api(token: str, chain: str, address: str) -> tuple[dict | None, str | None]:
    code = CHAIN_CODES.get((chain or "bsc").lower(), (chain or "bsc").lower())
    url = f"https://{API_HOST}{NARRATIVE_PATH}?chain={code}&address={address}"
    headers = _api_headers()
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

    `Invalid token address` 也带 "token" 三个字，但那是**地址不合法**
    （典型：base58 地址被小写化后 GIGA X 不认识），当鉴权失败处理会白触发一次续期。
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
    """定期续一次，避免 refresh token 30 天不用失效。"""
    if getattr(start_keepalive, "_started", False):
        return
    start_keepalive._started = True  # type: ignore[attr-defined]

    def _loop() -> None:
        time.sleep(60)
        warned_on: str | None = None
        while True:
            try:
                state = load_state()
                if state.get("refreshToken"):
                    ok, msg = refresh_via_privy()
                    if not ok:
                        print(f"[gigax] token 保活失败：{msg}", flush=True)
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
                                  f"请重登一次 GigaX 并跑 tools/gigax_securestore_dump.py", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[gigax] token 保活异常：{type(exc).__name__}: {exc}", flush=True)
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
    cfg = load_config()
    return {
        "ok": True,
        "configured": bool(cfg.get("privyAppId") and cfg.get("privyClientId")),
        "hasToken": bool(token),
        "tokenExp": exp.isoformat() if exp else None,
        "expired": bool(exp and exp < now),
        "expiresInSeconds": left,
        "hasRefreshToken": bool(state.get("refreshToken")),
        "refreshTokenAgeDays": age_days,
        "refreshTokenExpiringSoon": bool(age_days is not None and age_days >= 25),
        "lastRefreshAt": state.get("lastRefreshAt"),
        "source": state.get("source"),
        "emulatorOnline": emulator_online(),
    }
