"""RootData 机构投资 → 「未发行 + 运营中」项目（首页项目榜的机构下拉框）。

**为什么不用机构页 HTML（2026-11-14 实测）**：``/investors/detail/<slug>?k=<b64(id)>``
是纯 SSR，286 个投资项目全在 RSC 负载里，但每个项目对象只有 7 个字段
（``briefIntd`` / ``imgUrl`` / ``intd`` / ``itemId`` / ``itemName`` / ``operateStatus``
/ ``facRounds``）—— **没有发币状态**。页内「Token 状态 / 运营状态」筛选是纯客户端
state，参数不写进 URL（``?lssuingStatus=0``、``?isToken=0``、``?token=noToken`` 一律
仍回 287 条）。

**真正的数据源是它自己的接口**：``POST https://www.rootdata.com/pc/data/org_invest_item_list``
body ``{id, page, limit, lssuingStatus, yyStatus}``：
``lssuingStatus`` 0=未发行 / 1=已发行，``yyStatus`` 1=运营中 / 4=停止运营。
实测 YZi Labs(229)：全部 287、未发行+运营中 **82**、已发行+运营中 162、剩下 43 是停止运营。

接口两处加密（密钥硬编码在前端 chunk ``b11c43ec763aa01e.js`` 里）：
1. **请求**：body 用 RSA(PKCS#1 v1.5) 加密成 ``content``，再对 ``md5("content=<b64>")``
   做 RSA-SHA1 签名得到 ``sign`` —— 少一步都回 ``result:111 签名错误``。
2. **响应**：``data`` 是 AES-128-**ECB** 密文（key ``shjkhd9477k@326h``，PKCS#7）。

⚠️ **密钥可能轮换**，轮换后这个模块会整体失效。所以：只在磁盘快照过期时拉一次，
失败一律保留旧数据并**明说年龄**（``status="stale"``），绝不假装成「0 个未发币项目」。
"""
from __future__ import annotations
from app.core.paths import runtime_dir

import base64
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

PERSIST_CACHE_DIR = runtime_dir()
SNAPSHOT_PATH = PERSIST_CACHE_DIR / "rootdata_investor.json"

API_URL = "https://www.rootdata.com/pc/data/org_invest_item_list"

#: 机构名单。``id`` 是 RootData 的 orgId，``k`` 是详情页 URL 里的 ``k`` 参数
#: （``base64url(orgId)`` 去 padding 之前的样子，这里直接存字符串免得再算）。
#: 加新机构：把 orgId 填上就行，``slug`` 用于拼详情链接。
INVESTORS: tuple[dict[str, Any], ...] = (
    {
        "id": 229,
        "value": "yzi-labs",
        "label": "YZi Labs",
        "slug": "YZi Labs",
    },
)
INVESTOR_BY_VALUE = {item["value"]: item for item in INVESTORS}

#: 快照有效期（默认 1 小时）。RootData 一天也就动几次，没必要勤拉。
TTL_SECONDS = max(600, int(float(os.getenv("ROOTDATA_INVESTOR_TTL_SECONDS", "3600") or 3600)))
#: 一次最多塞几行给前端。**窗口是前端的事**（卡片里固定露 10 行、其余下滑看），
#: 所以这里要给全量（YZi Labs 实测 82 条），只留一个防爆上限。
ROW_LIMIT = max(1, int(float(os.getenv("ROOTDATA_INVESTOR_ROW_LIMIT", "150") or 150)))
#: 单次请求最多要多少条（实测 500 够装下 287 个）。
FETCH_LIMIT = max(50, int(float(os.getenv("ROOTDATA_INVESTOR_FETCH_LIMIT", "500") or 500)))

#: 前端 chunk 里硬编码的那一对密钥，**轮换即失效**。
_RSA_PUBLIC_B64 = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCcsTTLN8owkUCNgMuegNMNa3rPuUfFc1nhU3u1RdRj9xEgW9N"
    "f7MvtnUFV8XO1B2v0P9bQPOOG3063zxKK2ycjO9RN0zmczQedFWpXOCkU5zBgXK3Obf/xRFBNTVwuhAXBc4eWM8"
    "Ibrv7Da6Hqs63EC0TuADErNW08LG66FL255QIDAQAB"
)
_RSA_PRIVATE_B64 = (
    "MIICdwIBADANBgkqhkiG9w0BAQEFAASCAmEwggJdAgEAAoGBALrahjsqWFxNInIYWMakRU0Aa5KEZE/PVP/qz25"
    "85WeDLdQdahbn9bjP0OeFLoWsp+bVcAp38MUaT/Qe/EWcEaVdy+x1Cmbv3zFkB3i24mQVCyrDr3gR/Y9U4Umz/r2"
    "6nt65T+tVr54IKutykdLSmqEkQKRLTENBWoWAtpCRtmjtAgMBAAECgYBWrIcXas0SA7W2txR3EDwVO9yqWj8txj7n"
    "d0RBPC4tlevsOLi9Z1vUHpII0v6Bg1Ve/iK6uGg3Bb2Qv87Fe1+zSZvhceGjaoJWispTu6RLw3rspeyX9lKVDTXH"
    "3qksubuGKs0u4KZgP6KrnmAEz1ur408VWznvRQthy5nC8ZqdAQJBAPINlbD7KzZjQXKxJH8deRqQiB0LfT4W602c"
    "Dt5LYPC2q7bDrJUz5aopeCO3KCUrzNE4t10C+qvyfNJsjd3yx48CQQDFnrepFg9ttqx2uCkNQm4lidwKrBTxL02H"
    "ctziwuVjKweSSKkT11VrdGvBA1SV/BLZz2XWcZ2+80tRFrAlxanDAkA3VJ2kQDYHj7bVReMG37DgPquuoAK4H7sY"
    "GZVYgTierdMgyyf3T06vkB0zZNnbbWC7btCThWvRxHN+Emx3kNjHAkEAltL11SSzDccXhVw0aJxEuH2o5LRFH3Y8"
    "s52km6SnsbILi0fNUwlLdkFaY+sIyZejQ30Yejwoaq4A05h/Q7TCRQJBAIRXev2oHDvfCUG9oBBfC38GoxuSCj5Q"
    "6GEa0Fq734qUZY/F8YdEu37UNYnOOi+54zzusVJR/ZI3xlnewvrtg9M="
)
_AES_KEY = b"shjkhd9477k@326h"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"
)

#: operateStatus 文案（前端 i18n 里那份表：1 运营中 / 2 已上线主网 / 3 计划上线 / 4 停止运营 / 5 测试网）。
_OPERATE_LABELS = {
    1: "运营中",
    2: "已上线主网",
    3: "计划上线",
    4: "停止运营",
    5: "已上线测试网",
}

_LOCK = threading.Lock()
#: 每个机构一套刷新状态（value → running / locked_until / message）。
#: **按机构分开**：以后加第二家机构时，一家在拉不该把另一家也挡住。
_REFRESH_STATE: dict[str, dict[str, Any]] = {}


def _refresh_state(investor_value: str) -> dict[str, Any]:
    return _REFRESH_STATE.setdefault(
        str(investor_value or ""), {"running": False, "locked_until": 0.0, "message": ""}
    )


# --------------------------------------------------------------------------- 加解密

def _load_keys():
    """延迟导入 ``cryptography``：没装也只是这个卡片空着，不该拖垮整站。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    public_key = serialization.load_der_public_key(base64.b64decode(_RSA_PUBLIC_B64))
    private_key = serialization.load_der_private_key(base64.b64decode(_RSA_PRIVATE_B64), password=None)
    return public_key, private_key, padding


def signed_body(payload: dict[str, Any]) -> dict[str, str]:
    """把业务参数包成 ``{content, sign}``。

    签名口径（对着前端 chunk 逐行复刻，2026-11-14 实测通过）：
    ``sign = RSA_SHA1(md5("content=" + content))``，少 md5 这层就是 ``result:111``。
    """
    from cryptography.hazmat.primitives import hashes

    public_key, private_key, padding = _load_keys()
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    content = base64.b64encode(public_key.encrypt(text.encode(), padding.PKCS1v15())).decode()
    digest = hashlib.md5(f"content={content}".encode()).hexdigest()
    sign = base64.b64encode(
        private_key.sign(digest.encode(), padding.PKCS1v15(), hashes.SHA1())
    ).decode()
    return {"content": content, "sign": sign}


def decrypt_data(raw: str) -> Any:
    """响应 ``data`` 是 AES-128-ECB + PKCS#7 的 base64。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    blob = base64.b64decode(raw)
    decryptor = Cipher(algorithms.AES(_AES_KEY), modes.ECB()).decryptor()
    plain = decryptor.update(blob) + decryptor.finalize()
    if plain and 1 <= plain[-1] <= 16:
        plain = plain[:-plain[-1]]
    return json.loads(plain.decode("utf-8"))


# --------------------------------------------------------------------------- 网络

def _open(request: urllib.request.Request, timeout: int = 25):
    """直连优先，直连不通再试本地代理（Clash 在场时也能走）。"""
    errors: list[str] = []
    proxy = os.getenv("ROOTDATA_PROXY", "").strip()
    plans = [proxy] if proxy else []
    plans.append("")  # 直连
    plans.extend(["http://127.0.0.1:7890", "http://127.0.0.1:7897"])
    for candidate in plans:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": candidate, "https": candidate} if candidate else {})
        )
        try:
            return opener.open(request, timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 换一个出口再试
            errors.append(f"{candidate or '直连'}: {type(exc).__name__}")
    raise RuntimeError("RootData 接口不可达（" + "; ".join(errors[:3]) + "）")


def post_signed(path_payload: dict[str, Any], *, ref: str = "https://cn.rootdata.com/") -> dict[str, Any]:
    """调一个 ``pc/*`` 接口，返回解密后的 ``data``。"""
    body = signed_body(path_payload)
    request = urllib.request.Request(
        API_URL, data=json.dumps(body).encode(), method="POST"
    )
    request.add_header("User-Agent", _UA)
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    request.add_header("Origin", "https://cn.rootdata.com")
    request.add_header("Referer", "https://cn.rootdata.com/")
    request.add_header("lang", "zh")
    request.add_header("currency", "CNY")
    request.add_header("X-User-ID", str(uuid.uuid4()))
    request.add_header("ref", ref)
    with _open(request) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    result = payload.get("result")
    if result != 1:
        raise RuntimeError(f"RootData 接口返回 result={result}: {str(payload.get('message'))[:120]}")
    data = payload.get("data")
    if isinstance(data, str):
        data = decrypt_data(data)
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------- 解析

def _i18n(raw: Any) -> str:
    """``itemName`` / ``briefIntd`` / ``intd`` 都是「JSON 字符串」，取中文优先。"""
    if isinstance(raw, dict):
        mapping = raw
    else:
        text = str(raw or "").strip()
        if not text:
            return ""
        try:
            parsed = json.loads(text)
        except Exception:  # noqa: BLE001 —— 偶尔就是纯字符串
            return text[:200]
        if not isinstance(parsed, dict):
            return text[:200]
        mapping = parsed
    for key in ("cn_value", "tw_value", "zh_value", "en_value", "ja_value"):
        value = str(mapping.get(key) or "").strip()
        if value:
            return value
    for value in mapping.values():
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _b64_id(item_id: int) -> str:
    return urllib.parse.quote(base64.b64encode(str(item_id).encode()).decode(), safe="")


def detail_url(item_id: Any, name: str) -> str:
    """项目详情页：``/projects/detail/<slug>?k=<base64(itemId)>``（slug 规则抄的前端）。"""
    try:
        numeric = int(item_id)
    except (TypeError, ValueError):
        return ""
    if numeric <= 0:
        return ""
    slug = str(name or "").replace("/", "-").replace(".", "").strip()
    return f"https://cn.rootdata.com/projects/detail/{urllib.parse.quote(slug, safe='')}?k={_b64_id(numeric)}"


def _thumb(url: str) -> str:
    text = str(url or "").strip()
    if not text.startswith("http"):
        return ""
    return text.rsplit(".", 1)[0] + "_128x128.webp" if "." in text.rsplit("/", 1)[-1] else ""


def _normalize_item(item: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    name = _i18n(item.get("itemName"))
    if not name:
        return None
    operate = item.get("operateStatus")
    try:
        operate = int(operate)
    except (TypeError, ValueError):
        operate = 0
    icon = str(item.get("imgUrl") or "").strip()
    thumb = _thumb(icon)
    return {
        "itemId": int(item.get("itemId") or 0),
        "name": name[:80],
        "brief": _i18n(item.get("briefIntd"))[:160],
        "icon": thumb or icon,
        "iconCandidates": [icon] if thumb and icon else [],
        "operateStatus": operate,
        "operateLabel": _OPERATE_LABELS.get(operate, ""),
        "url": detail_url(item.get("itemId"), name),
        "acquired": int(item.get("label") or 0) == 2,
    }


def fetch_investor_items(investor_id: int) -> dict[str, Any]:
    """拉「未发行 + 运营中」的完整列表（一次请求）。"""
    data = post_signed({
        "id": int(investor_id),
        "page": 1,
        "limit": FETCH_LIMIT,
        "lssuingStatus": 0,
        "yyStatus": 1,
    })
    raw_items = data.get("investList") if isinstance(data.get("investList"), list) else []
    items = [row for row in (_normalize_item(item) for item in raw_items) if row]
    # **保持上游顺序**：接口按最近投资/热度排，自己在本地重排反而把「刚投的」埋掉。
    return {"items": items, "fetched": len(raw_items)}


# --------------------------------------------------------------------------- 快照

def read_snapshot() -> dict[str, Any]:
    try:
        raw = SNAPSHOT_PATH.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_snapshot(payload: dict[str, Any]) -> None:
    PERSIST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SNAPSHOT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(SNAPSHOT_PATH)


def snapshot_age_seconds(payload: dict[str, Any] | None = None) -> float:
    payload = payload if payload is not None else read_snapshot()
    stamp = payload.get("updatedAt") if isinstance(payload, dict) else None
    try:
        value = float(stamp)
    except (TypeError, ValueError):
        return float("inf")
    return max(0.0, time.time() - value) if value > 0 else float("inf")


def refresh(investor_value: str) -> dict[str, Any]:
    """同步抓一个机构并落快照。失败不影响其它机构，也保留原有快照。"""
    investor = INVESTOR_BY_VALUE.get(str(investor_value or ""))
    if not investor:
        raise KeyError(f"未知机构: {investor_value}")
    result = fetch_investor_items(int(investor["id"]))
    with _LOCK:
        snapshot = read_snapshot()
        investors = snapshot.get("investors") if isinstance(snapshot.get("investors"), dict) else {}
        investors[investor["value"]] = {
            "id": int(investor["id"]),
            "label": investor["label"],
            "slug": investor["slug"],
            "updatedAt": int(time.time()),
            "total": len(result["items"]),
            "items": result["items"],
            "error": "",
        }
        _write_snapshot({"updatedAt": int(time.time()), "investors": investors})
    return investors[investor["value"]]


def refresh_async(investor_value: str) -> bool:
    """后台补一次（自带闸门，不会堆线程）。返回 True = 真的派发了。"""
    state = _refresh_state(investor_value)
    if state["running"] or time.time() < float(state["locked_until"] or 0):
        return False

    def worker() -> None:
        state["running"] = True
        try:
            refresh(investor_value)
            state["message"] = ""
        except Exception as exc:  # noqa: BLE001
            state["message"] = f"{type(exc).__name__}: {str(exc)[:160]}"
            # 失败冷却 10 分钟，别每轮都去撞。
            state["locked_until"] = time.time() + 600
        finally:
            state["running"] = False

    state["running"] = True
    threading.Thread(target=worker, name=f"rootdata-investor-{investor_value}", daemon=True).start()
    return True


# --------------------------------------------------------------------------- 给榜单用

def board_rows(investor_value: str, limit: int = ROW_LIMIT) -> dict[str, Any]:
    """机构板块：把快照转成 ``renderRow`` 认的行。

    **未发行 + 运营中**是服务端筛出来的，这里只负责排版，不再二次过滤。
    行**全量下发**：卡片里固定露 10 行、剩下的在卡片内下滑查看（用户 2026-11-14），
    所以「只显示 10 条」是前端窗口的事，截断放在服务端就没法下滑了。
    """
    investor = INVESTOR_BY_VALUE.get(str(investor_value or ""))
    if not investor:
        return {"investor": str(investor_value or ""), "label": "", "rows": [], "total": 0,
                "status": "unavailable", "error": "未知机构"}
    snapshot = read_snapshot()
    investors = snapshot.get("investors") if isinstance(snapshot.get("investors"), dict) else {}
    entry = investors.get(investor["value"]) if isinstance(investors.get(investor["value"]), dict) else {}
    items = entry.get("items") if isinstance(entry.get("items"), list) else []
    age = float("inf")
    try:
        age = max(0.0, time.time() - float(entry.get("updatedAt") or 0))
    except (TypeError, ValueError):
        pass
    if not items:
        return {
            "investor": investor["value"],
            "label": investor["label"],
            "rows": [],
            "total": 0,
            "status": "unavailable",
            "updatedAt": int(entry.get("updatedAt") or 0),
            "error": str(entry.get("error") or _refresh_state(investor["value"]).get("message") or ""),
        }
    rows = []
    for index, item in enumerate(items[:limit], start=1):
        tags = [tag for tag in (item.get("operateLabel"), "已被收购" if item.get("acquired") else "") if tag]
        rows.append({
            "rank": index,
            "symbol": item.get("name") or "--",
            "name": item.get("brief") or "",
            "icon": item.get("icon") or "",
            "iconCandidates": item.get("iconCandidates") or [],
            "iconFallback": "none" if not item.get("icon") else "",
            # 价格列放「未发行」：这一栏原本是价格/热度，机构视角下最该先说的是「还没发币」。
            "turnover": "未发行",
            "change": "",
            "heat": 0,
            "tags": tags,
            "note": " · ".join(tags) or "运营中",
            "url": item.get("url") or "",
            "itemId": item.get("itemId") or 0,
            "tokenState": "noToken",
            "operateStatus": item.get("operateStatus") or 0,
        })
    return {
        "investor": investor["value"],
        "label": investor["label"],
        "rows": rows,
        "total": len(items),
        "status": "stale" if age > TTL_SECONDS else "ok",
        "updatedAt": int(entry.get("updatedAt") or 0),
        "ageSeconds": int(age) if age != float("inf") else None,
    }
