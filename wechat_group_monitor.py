from __future__ import annotations

import hashlib
import importlib
import os
import re
import site
import sys
import threading
import time
import types
import unicodedata
from pathlib import Path
from typing import Any

import qq_onebot_bridge


GROUP_COUNT_RE = re.compile(r"\s*[（(]\d+[）)]\s*$")
TIME_RE = re.compile(r"^(?:\d{1,2}:\d{2}|昨天|星期[一二三四五六日天]|\d{1,2}月\d{1,2}日)$")
SYMBOL_RE = re.compile(r"(?<![A-Za-z0-9])\$?([A-Z][A-Z0-9]{1,9})(?![A-Za-z0-9])")

CHAT_PLATFORM_LABELS = {"wechat": "微信", "qq": "QQ"}

STRONG_TERMS = (
    "上币", "上线", "新合约", "永续", "公告", "回购", "空投", "解锁", "黑客", "攻击",
    "etf", "收购", "融资", "合作", "主网", "测试网", "降息", "加息", "监管", "诉讼",
    "清算", "爆仓", "增持", "减持", "暂停提币", "恢复提币", "快照", "申领", "tge",
    "listing", "launch", "launchpool", "上所", "下架", "做市", "销毁", "质押",
)
MARKET_TERMS = (
    "币安", "binance", "okx", "bitget", "aster", "交易所", "代币", "公链", "链上",
    "主力", "成交量", "持仓量", "资金费率", "流动性", "热门榜", "涨幅榜", "板块", "叙事",
    "比特币", "以太坊", "美联储", "sec", "现货", "合约", "期权", "ipo",
)
NOISE_TERMS = ("哈哈", "早上好", "晚安", "收到", "好的", "在吗", "谢谢", "表情", "撤回了一条消息")
OCR_SKIP_TEXT = {
    "发送", "聊天信息", "语音聊天", "视频聊天", "查看更多消息", "以下为新消息",
    "搜索", "通讯录", "收藏", "朋友圈", "小程序", "手机端已登录", "文件传输助手",
}

_OCR_ENGINE: Any = None
_OCR_ENGINE_ERROR = ""
_OCR_ENGINE_LOCK = threading.Lock()
_WECHAT_SEND_LOCK = threading.Lock()
_WECHAT_UIA_DRIVER: Any = None
_WECHAT_UIA_DRIVER_ERROR = ""


class WechatDeliveryUncertainError(RuntimeError):
    """The send action ran, but the delivered message could not be read back."""


def _load_wechat_uia_driver() -> tuple[Any, str]:
    """Load the reviewed WeChat 4.x UIA driver from the local runtime bundle."""
    global _WECHAT_UIA_DRIVER, _WECHAT_UIA_DRIVER_ERROR
    if _WECHAT_UIA_DRIVER is not None:
        return _WECHAT_UIA_DRIVER, ""
    if _WECHAT_UIA_DRIVER_ERROR:
        return None, _WECHAT_UIA_DRIVER_ERROR

    runtime_root = Path(__file__).resolve().parent / ".runtime-tools"
    package_dir = runtime_root / "wechatauto-inspect" / "wechatauto"
    dependency_dir = runtime_root / "wechatauto-packages"
    if not (package_dir / "uia_driver.py").is_file():
        _WECHAT_UIA_DRIVER_ERROR = "缺少微信 4.x 本地发送驱动"
        return None, _WECHAT_UIA_DRIVER_ERROR
    try:
        if dependency_dir.is_dir():
            site.addsitedir(str(dependency_dir))
        package = sys.modules.get("wechatauto")
        if package is None:
            package = types.ModuleType("wechatauto")
            package.__path__ = [str(package_dir)]
            sys.modules["wechatauto"] = package
        driver_module = importlib.import_module("wechatauto.uia_driver")
        _WECHAT_UIA_DRIVER = driver_module.WeChatUIA
        return _WECHAT_UIA_DRIVER, ""
    except Exception as exc:
        _WECHAT_UIA_DRIVER_ERROR = str(exc)
        return None, _WECHAT_UIA_DRIVER_ERROR


def normalize_group_name(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    return GROUP_COUNT_RE.sub("", normalized)


def normalize_chat_platform(value: Any) -> str:
    platform = str(value or "wechat").strip().lower()
    return "qq" if platform in {"qq", "q群", "qqun", "tencent-qq"} else "wechat"


def _sender_match_key(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z\u3400-\u9fff]", "", _stable_text(value)).lower()


def sender_filter_matches(expected: Any, observed: Any) -> bool:
    wanted = _sender_match_key(expected)
    actual = _sender_match_key(observed)
    if not wanted or not actual:
        return False
    return wanted == actual or (len(wanted) >= 3 and wanted in actual)


def _platform_name(platform: Any) -> str:
    return CHAT_PLATFORM_LABELS[normalize_chat_platform(platform)]


def _stable_text(value: Any) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip()


def message_fingerprint(group_name: str, sender: str, content: str, marker: str = "") -> str:
    raw = "\n".join((
        normalize_group_name(group_name),
        _stable_text(sender),
        _stable_text(content),
        _stable_text(marker),
    ))
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()


def extract_candidate_symbols(text: Any) -> list[str]:
    blocked = {"USDT", "USDC", "USD", "BTC", "ETH", "API", "APP", "ETF", "IPO", "SEC", "TGE", "AI"}
    found: list[str] = []
    for match in SYMBOL_RE.findall(str(text or "")):
        symbol = match.upper()
        if symbol in blocked or symbol in found:
            continue
        found.append(symbol)
    return found[:8]


def candidate_rule_score(text: Any) -> int:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) < 6:
        return 0
    lowered = value.lower()
    if any(term in lowered for term in NOISE_TERMS) and len(value) < 24:
        return 0
    strong = sum(1 for term in STRONG_TERMS if term in lowered)
    market = sum(1 for term in MARKET_TERMS if term in lowered)
    symbols = len(extract_candidate_symbols(value))
    links = 1 if re.search(r"https?://", value) else 0
    numbers = 1 if re.search(r"(?:\d+(?:\.\d+)?%|\d+(?:\.\d+)?[万亿])", value) else 0
    return min(100, strong * 32 + market * 13 + symbols * 12 + links * 8 + numbers * 7)


def _walk(control: Any, depth: int = 0, limit: int = 1500) -> list[Any]:
    if control is None or depth > 10 or limit <= 0:
        return []
    result = [control]
    try:
        children = control.GetChildren() or []
    except Exception:
        children = []
    for child in children:
        if len(result) >= limit:
            break
        result.extend(_walk(child, depth + 1, limit - len(result)))
    return result


def _group_match_key(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z\u3400-\u9fff]", "", normalize_group_name(value)).lower()


def _ocr_group_matches(expected: str, observed: str) -> bool:
    wanted = _group_match_key(expected)
    actual = _group_match_key(observed)
    if not wanted or not actual:
        return False
    return wanted == actual or (len(wanted) >= 3 and wanted in actual) or (len(actual) >= 3 and actual in wanted)


def normalize_ocr_rows(raw_result: Any) -> list[dict[str, Any]]:
    """Convert RapidOCR result shapes into stable bounding-box rows."""
    if raw_result is None:
        return []
    payload = raw_result[0] if isinstance(raw_result, tuple) else raw_result
    if hasattr(payload, "txts") and hasattr(payload, "boxes"):
        payload = list(zip(payload.boxes, payload.txts, getattr(payload, "scores", [])))
    if not isinstance(payload, (list, tuple)):
        return []
    rows: list[dict[str, Any]] = []
    for item in payload:
        try:
            box, text, score = item[0], item[1], item[2]
            points = list(box)
            xs = [float(point[0]) for point in points]
            ys = [float(point[1]) for point in points]
            cleaned = _stable_text(text)
            if not cleaned or not xs or not ys:
                continue
            rows.append({
                "text": cleaned,
                "score": float(score),
                "left": min(xs),
                "top": min(ys),
                "right": max(xs),
                "bottom": max(ys),
            })
        except (IndexError, TypeError, ValueError):
            continue
    return rows


def _message_payload(group_name: str, sender: str, content: str, captured_at: int, platform: str) -> dict[str, Any]:
    normalized_platform = normalize_chat_platform(platform)
    marker = normalized_platform
    return {
        "sender": _stable_text(sender) or "群成员",
        "content": _stable_text(content),
        "capturedAt": int(captured_at),
        "marker": marker,
        "platform": normalized_platform,
        "hash": message_fingerprint(group_name, sender, content, marker),
    }


def _targeted_messages_from_rows(
    group_name: str,
    sender_filter: str,
    rows: list[dict[str, Any]],
    width: int,
    captured_at: int,
    platform: str,
) -> list[dict[str, Any]]:
    """Associate the first compact message block below each matching sender label."""
    ordered = sorted(rows, key=lambda row: (float(row.get("top") or 0), float(row.get("left") or 0)))
    messages: list[dict[str, Any]] = []
    for index, sender_row in enumerate(ordered):
        sender_text = _stable_text(sender_row.get("text"))
        if not sender_filter_matches(sender_filter, sender_text):
            continue
        inline = ""
        if _sender_match_key(sender_text) != _sender_match_key(sender_filter):
            inline = re.sub(re.escape(_stable_text(sender_filter)), "", sender_text, count=1, flags=re.IGNORECASE)
            inline = inline.lstrip(" ：:-—·")
        parts = [inline] if inline else []
        last_bottom = float(sender_row.get("bottom") or sender_row.get("top") or 0)
        sender_left = float(sender_row.get("left") or 0)
        for candidate in ordered[index + 1:]:
            text = _stable_text(candidate.get("text"))
            top = float(candidate.get("top") or 0)
            bottom = float(candidate.get("bottom") or top)
            left = float(candidate.get("left") or 0)
            if sender_filter_matches(sender_filter, text):
                break
            gap = top - last_bottom
            maximum_gap = 48.0 if not parts else 22.0
            if gap > maximum_gap:
                break
            if gap < -8.0:
                continue
            if left < sender_left - width * 0.035 or left > sender_left + width * 0.22:
                continue
            if not text or text in OCR_SKIP_TEXT or TIME_RE.match(text):
                continue
            parts.append(text)
            last_bottom = max(last_bottom, bottom)
        content = _stable_text(" ".join(parts))
        if not content:
            continue
        if candidate_rule_score(content) == 0 and not extract_candidate_symbols(content) and len(content) < 16:
            continue
        messages.append(_message_payload(group_name, sender_filter, content, captured_at, platform))
    deduped: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for message in messages:
        if message["hash"] in seen_hashes:
            continue
        seen_hashes.add(message["hash"])
        deduped.append(message)
    return deduped[-60:]


def messages_from_ocr_rows(
    group_name: str,
    rows: list[dict[str, Any]],
    width: int,
    height: int,
    captured_at: int | None = None,
    platform: str = "wechat",
    sender_filter: str = "",
) -> dict[str, Any]:
    """Validate the group title and turn visible OCR rows into message candidates."""
    wanted = normalize_group_name(group_name)
    normalized_platform = normalize_chat_platform(platform)
    platform_name = _platform_name(normalized_platform)
    title_rows = [
        row for row in rows
        if float(row.get("score") or 0) >= 0.35
        and float(row.get("top") or 0) <= max(135.0, height * 0.22)
        and float(row.get("right") or 0) >= width * 0.22
        and _ocr_group_matches(wanted, str(row.get("text") or ""))
    ]
    if not title_rows:
        return {
            "ok": False,
            "status": "group_not_open",
            "messages": [],
            "error": f"请在{platform_name}中打开群聊“{wanted}”",
        }
    title = min(title_rows, key=lambda row: (float(row.get("top") or 0), -float(row.get("score") or 0)))
    chat_left = width * 0.265
    chat_top = max(float(title.get("bottom") or 0) + 14.0, height * 0.105)
    chat_bottom = height * 0.84
    content_rows: list[dict[str, Any]] = []
    for row in rows:
        text = _stable_text(row.get("text"))
        left = float(row.get("left") or 0)
        top = float(row.get("top") or 0)
        right = float(row.get("right") or 0)
        bottom = float(row.get("bottom") or 0)
        score = float(row.get("score") or 0)
        if score < 0.43 or left < chat_left or top < chat_top or bottom > chat_bottom:
            continue
        if not text or len(text) > 800 or text in OCR_SKIP_TEXT or TIME_RE.match(text):
            continue
        if _ocr_group_matches(wanted, text) or right - left < 8 or bottom - top < 6:
            continue
        content_rows.append({**row, "text": text})

    now = int(captured_at or time.time())
    if _stable_text(sender_filter):
        messages = _targeted_messages_from_rows(
            wanted,
            _stable_text(sender_filter),
            content_rows,
            width,
            now,
            normalized_platform,
        )
        return {
            "ok": True,
            "status": "connected",
            "messages": messages,
            "error": "",
            "collectorMode": "window_ocr",
            "platform": normalized_platform,
            "senderFilter": _stable_text(sender_filter),
        }

    content_rows.sort(key=lambda row: (float(row.get("top") or 0), float(row.get("left") or 0)))
    merged: list[dict[str, Any]] = []
    for row in content_rows:
        if merged:
            previous = merged[-1]
            vertical_gap = float(row["top"]) - float(previous["bottom"])
            same_side = (float(row["left"]) < width * 0.67) == (float(previous["left"]) < width * 0.67)
            aligned = abs(float(row["left"]) - float(previous["left"])) <= max(48.0, width * 0.05)
            if -4.0 <= vertical_gap <= 13.0 and same_side and aligned:
                previous["text"] = _stable_text(f"{previous['text']} {row['text']}")
                previous["right"] = max(float(previous["right"]), float(row["right"]))
                previous["bottom"] = max(float(previous["bottom"]), float(row["bottom"]))
                previous["score"] = min(float(previous["score"]), float(row["score"]))
                continue
        merged.append(dict(row))

    messages: list[dict[str, Any]] = []
    for row in merged[-100:]:
        content = _stable_text(row.get("text"))
        if len(content) < 4 or (candidate_rule_score(content) == 0 and len(content) < 16):
            continue
        messages.append(_message_payload(wanted, "群成员", content, now, normalized_platform))
    return {
        "ok": True,
        "status": "connected",
        "messages": messages[-60:],
        "error": "",
        "collectorMode": "window_ocr",
        "platform": normalized_platform,
    }


def _chat_window_signature_matches(signature: str, platform: str) -> bool:
    value = str(signature or "").lower()
    if normalize_chat_platform(platform) == "qq":
        return bool(
            re.search(r"(?:^|[\\/\s])qq\.exe(?:$|\s)", value)
            or re.search(r"(?:^|[\\/\s])tim\.exe(?:$|\s)", value)
            or "腾讯qq" in value
            or "qqnt" in value
        )
    return "weixin.exe" in value or "wechat.exe" in value or "微信" in value


def _wechat_window_candidates(platform: str = "wechat") -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    candidates: list[dict[str, Any]] = []

    def process_path(hwnd: int) -> str:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return buffer.value
        finally:
            kernel32.CloseHandle(handle)
        return ""

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @enum_proc
    def visit(hwnd: int, _lparam: int) -> bool:
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width < 320 or height < 240:
            return True
        title_length = user32.GetWindowTextLengthW(hwnd)
        title_buffer = ctypes.create_unicode_buffer(max(1, title_length + 1))
        user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
        class_buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
        path = process_path(hwnd)
        signature = " ".join((title_buffer.value, class_buffer.value, path)).lower()
        if not _chat_window_signature_matches(signature, platform):
            return True
        candidates.append({
            "hwnd": int(hwnd),
            "width": width,
            "height": height,
            "visible": bool(user32.IsWindowVisible(hwnd)),
            "iconic": bool(user32.IsIconic(hwnd)),
        })
        return True

    user32.EnumWindows(visit, 0)
    candidates.sort(
        key=lambda item: (bool(item["visible"] and not item["iconic"]), item["width"] * item["height"]),
        reverse=True,
    )
    return candidates[:8]


def _capture_window_image(candidate: dict[str, Any]) -> Any:
    import ctypes
    from ctypes import wintypes
    from PIL import Image, ImageStat

    class BitmapInfoHeader(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
        ]

    class BitmapInfo(ctypes.Structure):
        _fields_ = [("bmiHeader", BitmapInfoHeader), ("bmiColors", wintypes.DWORD * 3)]

    hwnd = int(candidate["hwnd"])
    width = int(candidate["width"])
    height = int(candidate["height"])
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = wintypes.HDC
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi32.SelectObject.restype = wintypes.HANDLE
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
        wintypes.LPVOID, ctypes.POINTER(BitmapInfo), wintypes.UINT,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    window_dc = user32.GetWindowDC(hwnd)
    if not window_dc:
        return None
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(hwnd, memory_dc, 2):
            return None
        info = BitmapInfo()
        info.bmiHeader.biSize = ctypes.sizeof(BitmapInfoHeader)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0
        buffer = ctypes.create_string_buffer(width * height * 4)
        copied = gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(info), 0)
        if copied != height:
            return None
        image = Image.frombuffer("RGB", (width, height), buffer.raw, "raw", "BGRX", 0, 1).copy()
        if float(ImageStat.Stat(image.convert("L").resize((160, 90))).stddev[0]) < 1.2:
            return None
        return image
    finally:
        if previous:
            gdi32.SelectObject(memory_dc, previous)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)


def _rapid_ocr_engine() -> tuple[Any, str]:
    global _OCR_ENGINE, _OCR_ENGINE_ERROR
    if _OCR_ENGINE is not None or _OCR_ENGINE_ERROR:
        return _OCR_ENGINE, _OCR_ENGINE_ERROR
    with _OCR_ENGINE_LOCK:
        if _OCR_ENGINE is not None or _OCR_ENGINE_ERROR:
            return _OCR_ENGINE, _OCR_ENGINE_ERROR
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
            _OCR_ENGINE = RapidOCR()
        except Exception as exc:
            _OCR_ENGINE_ERROR = str(exc)
    return _OCR_ENGINE, _OCR_ENGINE_ERROR


def _collect_with_window_ocr(group_name: str, platform: str = "wechat", sender_filter: str = "") -> dict[str, Any]:
    normalized_platform = normalize_chat_platform(platform)
    platform_name = _platform_name(normalized_platform)
    if os.name != "nt":
        return {"ok": False, "status": "collector_unavailable", "messages": [], "error": f"仅支持 Windows {platform_name}"}
    candidates = _wechat_window_candidates(normalized_platform)
    if not candidates:
        return {
            "ok": False,
            "status": f"{normalized_platform}_not_running",
            "messages": [],
            "error": f"没有找到{platform_name}窗口",
        }
    engine, engine_error = _rapid_ocr_engine()
    if engine is None:
        return {
            "ok": False,
            "status": "collector_unavailable",
            "messages": [],
            "error": f"本机文字识别组件不可用：{engine_error}"[:320],
        }
    last_result: dict[str, Any] | None = None
    captured_any = False
    for candidate in candidates:
        try:
            image = _capture_window_image(candidate)
            if image is None:
                continue
            captured_any = True
            import numpy as np
            rows = normalize_ocr_rows(engine(np.asarray(image)))
            parsed = messages_from_ocr_rows(
                group_name,
                rows,
                image.width,
                image.height,
                platform=normalized_platform,
                sender_filter=sender_filter,
            )
            if parsed.get("ok"):
                parsed["windowVisible"] = bool(candidate.get("visible"))
                return parsed
            last_result = parsed
        except Exception as exc:
            last_result = {"ok": False, "status": "collector_error", "messages": [], "error": str(exc)}
    if last_result:
        return last_result
    return {
        "ok": False,
        "status": f"{normalized_platform}_window_hidden" if not captured_any else "collector_error",
        "messages": [],
        "error": f"{platform_name}窗口暂时无法读取，请保持群聊窗口打开",
    }


def _collect_with_ui_automation(group_name: str, platform: str = "wechat", sender_filter: str = "") -> dict[str, Any]:
    """Read only text exposed by a visible chat window through Windows UI Automation."""
    wanted = normalize_group_name(group_name)
    normalized_platform = normalize_chat_platform(platform)
    platform_name = _platform_name(normalized_platform)
    if not wanted:
        return {"ok": False, "status": "group_not_configured", "messages": [], "error": "未配置群聊名称"}
    try:
        import uiautomation as auto  # type: ignore
    except Exception as exc:
        return {"ok": False, "status": "collector_unavailable", "messages": [], "error": str(exc)}

    try:
        windows = []
        for candidate in _wechat_window_candidates(normalized_platform):
            try:
                windows.append(auto.ControlFromHandle(int(candidate["hwnd"])))
            except Exception:
                continue
        if not windows:
            root = auto.GetRootControl()
            for item in root.GetChildren() or []:
                name = str(getattr(item, "Name", "") or "")
                class_name = str(getattr(item, "ClassName", "") or "")
                signature = f"{name} {class_name}".lower()
                matched = (
                    (normalized_platform == "wechat" and ("微信" in name or "wechat" in signature or "weixin" in signature))
                    or (normalized_platform == "qq" and ("qq" in signature or "腾讯" in name or "tim" in signature))
                    or _ocr_group_matches(wanted, name)
                )
                if matched:
                    windows.append(item)
        if not windows:
            return {
                "ok": False,
                "status": f"{normalized_platform}_not_running",
                "messages": [],
                "error": f"没有找到可见的{platform_name}窗口",
            }

        target_window = None
        controls: list[Any] = []
        for window in windows:
            current = _walk(window)
            names = [normalize_group_name(getattr(node, "Name", "")) for node in current]
            if wanted in names or wanted in normalize_group_name(getattr(window, "Name", "")):
                target_window, controls = window, current
                break
        if target_window is None:
            return {"ok": False, "status": "group_not_open", "messages": [], "error": f"请在{platform_name}中打开群聊“{wanted}”"}

        rect = getattr(target_window, "BoundingRectangle", None)
        left = float(getattr(rect, "left", 0) or 0)
        width = max(1.0, float(getattr(rect, "right", 0) or 0) - left)
        chat_left = left + width * 0.25

        if _stable_text(sender_filter):
            now = int(time.time())
            targeted_messages: list[dict[str, Any]] = []
            for node in controls:
                if str(getattr(node, "ControlTypeName", "") or "") != "ListItemControl":
                    continue
                descendants = _walk(node, limit=90)
                values: list[str] = []
                for child in descendants:
                    child_name = _stable_text(getattr(child, "Name", ""))
                    if child_name and child_name not in values:
                        values.append(child_name)
                if not any(sender_filter_matches(sender_filter, value) for value in values):
                    continue
                content_parts = [
                    value for value in values
                    if not sender_filter_matches(sender_filter, value)
                    and not _ocr_group_matches(wanted, value)
                    and value not in OCR_SKIP_TEXT
                    and not TIME_RE.match(value)
                    and len(value) <= 800
                ]
                content = _stable_text(" ".join(content_parts))
                if not content:
                    combined = next((value for value in values if sender_filter_matches(sender_filter, value)), "")
                    content = re.sub(re.escape(_stable_text(sender_filter)), "", combined, count=1, flags=re.IGNORECASE)
                    content = content.lstrip(" ：:-—·")
                if not content or (candidate_rule_score(content) == 0 and not extract_candidate_symbols(content) and len(content) < 16):
                    continue
                targeted_messages.append(_message_payload(wanted, sender_filter, content, now, normalized_platform))
            if targeted_messages:
                unique = {message["hash"]: message for message in targeted_messages}
                return {
                    "ok": True,
                    "status": "connected",
                    "messages": list(unique.values())[-60:],
                    "error": "",
                    "collectorMode": "ui_automation",
                    "platform": normalized_platform,
                    "senderFilter": _stable_text(sender_filter),
                }

            positioned_rows: list[dict[str, Any]] = []
            window_top = float(getattr(rect, "top", 0) or 0)
            for node in controls:
                name = _stable_text(getattr(node, "Name", ""))
                node_rect = getattr(node, "BoundingRectangle", None)
                node_left = float(getattr(node_rect, "left", 0) or 0)
                node_top = float(getattr(node_rect, "top", 0) or 0)
                node_right = float(getattr(node_rect, "right", 0) or 0)
                node_bottom = float(getattr(node_rect, "bottom", 0) or 0)
                if not name or node_right <= node_left or node_bottom <= node_top:
                    continue
                positioned_rows.append({
                    "text": name,
                    "score": 1.0,
                    "left": node_left - left,
                    "top": node_top - window_top,
                    "right": node_right - left,
                    "bottom": node_bottom - window_top,
                })
            parsed = messages_from_ocr_rows(
                wanted,
                positioned_rows,
                int(width),
                max(1, int(float(getattr(rect, "bottom", 0) or 0) - window_top)),
                captured_at=now,
                platform=normalized_platform,
                sender_filter=sender_filter,
            )
            if parsed.get("ok"):
                parsed["collectorMode"] = "ui_automation"
                return parsed

        rows: list[tuple[float, float, str]] = []
        for node in controls:
            name = re.sub(r"\s+", " ", str(getattr(node, "Name", "") or "")).strip()
            if not name or name == wanted or TIME_RE.match(name) or len(name) > 1200:
                continue
            node_rect = getattr(node, "BoundingRectangle", None)
            x = float(getattr(node_rect, "left", 0) or 0)
            y = float(getattr(node_rect, "top", 0) or 0)
            if x < chat_left or y <= 0:
                continue
            control_type = str(getattr(node, "ControlTypeName", "") or "")
            if control_type not in {"TextControl", "ListItemControl", "DocumentControl", "PaneControl"}:
                continue
            rows.append((y, x, name))

        rows.sort(key=lambda row: (row[0], row[1]))
        unique_rows: list[tuple[float, float, str]] = []
        seen_names: set[tuple[int, str]] = set()
        for row in rows:
            key = (round(row[0] / 4), row[2])
            if key in seen_names:
                continue
            seen_names.add(key)
            unique_rows.append(row)

        messages: list[dict[str, Any]] = []
        for index, (y, _, content) in enumerate(unique_rows[-100:]):
            if content in {"发送", "聊天信息", "语音聊天", "视频聊天", "查看更多消息"}:
                continue
            if len(content) < 4 or candidate_rule_score(content) == 0 and len(content) < 16:
                continue
            messages.append(_message_payload(wanted, "群成员", content, int(time.time()), normalized_platform))
        return {
            "ok": True,
            "status": "connected",
            "messages": messages[-60:],
            "error": "",
            "collectorMode": "ui_automation",
            "platform": normalized_platform,
        }
    except Exception as exc:
        return {"ok": False, "status": "collector_error", "messages": [], "error": str(exc)}


def collect_visible_group_messages(group_name: str, platform: str = "wechat", sender_filter: str = "") -> dict[str, Any]:
    """Read a chat source, preferring the background OneBot channel for QQ."""
    wanted = normalize_group_name(group_name)
    if not wanted:
        return {"ok": False, "status": "group_not_configured", "messages": [], "error": "未配置群聊名称"}
    normalized_platform = normalize_chat_platform(platform)
    if normalized_platform == "qq" and qq_onebot_bridge.onebot_enabled():
        onebot_result = qq_onebot_bridge.collect_qq_onebot_messages(wanted, sender_filter)
        if onebot_result.get("ok"):
            return onebot_result
        fallback_enabled = str(os.getenv("QQ_UI_FALLBACK_ENABLED", "1")).strip().lower() in {
            "1", "true", "yes", "on",
        }
        if not fallback_enabled:
            return onebot_result
    accessibility_result = _collect_with_ui_automation(wanted, normalized_platform, sender_filter)
    if accessibility_result.get("ok"):
        return accessibility_result
    ocr_result = _collect_with_window_ocr(wanted, normalized_platform, sender_filter)
    if ocr_result.get("ok"):
        return ocr_result
    if str(ocr_result.get("status") or "") == "collector_unavailable":
        return accessibility_result
    return ocr_result


def _allow_builtin_file_helper_without_title(target_name: str, controls: list[Any]) -> bool:
    """Allow WeChat 4's built-in file helper when Qt exposes no chat controls.

    The exception is intentionally limited to WeChat's unique built-in contact. Any
    other recipient still requires a visible, exact title match before sending.
    """
    if _stable_text(target_name) != "文件传输助手":
        return False
    ignored_root_names = {"WxTrayIconMessageWindow"}
    for node in controls:
        name = _stable_text(getattr(node, "Name", ""))
        control_type = _stable_text(getattr(node, "ControlTypeName", ""))
        if name and name not in ignored_root_names and control_type != "WindowControl":
            return False
    return True


def _open_exact_wechat_chat(driver: Any, target: str) -> bool:
    """Open one exact WeChat conversation without trusting keyboard focus."""
    if _stable_text(driver.current_chat()) == target:
        return True
    window = getattr(driver, "_win", None)
    search = driver._search_box(window) if window is not None else None
    if search is None:
        return False
    driver._paste_into(search, target, clear=True)
    time.sleep(0.8)
    result_list = window.ListControl(AutomationId="search_list")
    if not result_list.Exists(1.2, 0.2):
        driver._paste_into(search, "", clear=True)
        return False
    exact_matches = [
        item
        for item in result_list.GetChildren()
        if _stable_text(getattr(item, "Name", "")) == target
        and str(getattr(item, "AutomationId", "") or "").startswith("search_item_")
    ]
    # Never guess between homonymous contacts. The built-in file helper has one
    # unique function result and normal contacts must also resolve uniquely.
    if len(exact_matches) != 1:
        driver._paste_into(search, "", clear=True)
        return False
    exact_matches[0].Click()
    time.sleep(0.8)
    return _stable_text(driver.current_chat()) == target


def _restore_wechat_window() -> bool:
    """Restore WeChat from its tray-hidden top-level window."""
    if os.name != "nt":
        return False
    candidates = _wechat_window_candidates("wechat")
    if not candidates:
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = int(candidates[0]["hwnd"])
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.BringWindowToTop(hwnd)
        time.sleep(0.4)
        return bool(user32.IsWindowVisible(hwnd))
    except Exception:
        return False


def _wechat_message_visible(driver: Any, message: str, timeout: float = 6.0) -> bool:
    """Confirm delivery by reading the exact text back from the chat message list."""
    expected = _stable_text(message)
    deadline = time.time() + max(0.5, float(timeout))
    while time.time() < deadline:
        message_list = driver._message_list()
        if message_list is not None:
            for item in message_list.GetChildren():
                class_name = str(getattr(item, "ClassName", "") or "")
                observed = _stable_text(getattr(item, "Name", ""))
                if class_name != "mmui::ChatTextItemView":
                    continue
                if observed == expected:
                    return True
                # WeChat 4 truncates the UIA Name of long bubbles by a few final
                # characters. A long, unique prefix still proves that this exact
                # just-sent bubble was rendered; short partial matches do not.
                if (
                    len(observed) >= 48
                    and len(observed) >= int(len(expected) * 0.9)
                    and expected.startswith(observed)
                ):
                    return True
        time.sleep(0.3)
    return False


def send_text_to_wechat(target_name: str, content: str) -> dict[str, Any]:
    """Send once and only report success after the exact bubble is read back."""
    target = _stable_text(target_name) or "文件传输助手"
    message = _stable_text(content)
    if not message:
        raise ValueError("转发内容不能为空")
    if os.name != "nt":
        raise RuntimeError("微信转发仅支持 Windows")
    try:
        import uiautomation as auto  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"微信发送组件不可用：{exc}") from exc
    driver_class, driver_error = _load_wechat_uia_driver()
    if driver_class is None:
        raise RuntimeError(f"微信 4.x 发送组件不可用：{driver_error}")

    with _WECHAT_SEND_LOCK:
        previous_clipboard = ""
        try:
            previous_clipboard = str(auto.GetClipboardText() or "")
        except Exception:
            previous_clipboard = ""
        try:
            if not _restore_wechat_window():
                raise RuntimeError("微信主窗口处于关闭或锁定状态，消息未发送")
            driver = driver_class(timeout=15.0, search_timeout=2.0)
            if not driver.ensure_window(wake=True):
                raise RuntimeError("没有找到可访问的微信窗口，消息已保留等待重试")
            if not _open_exact_wechat_chat(driver, target):
                raise RuntimeError(f"未能唯一确认微信接收方“{target}”，消息未发送")
            if _stable_text(driver.current_chat()) != target:
                raise RuntimeError(f"微信接收方“{target}”校验失败，消息未发送")
            if not driver.send_text(message):
                raise RuntimeError(f"微信接收方“{target}”的输入框未完成发送")
            if not _wechat_message_visible(driver, message):
                raise WechatDeliveryUncertainError(
                    f"微信已执行发送，但未能从“{target}”对话读回原文；为避免重复，已停止自动重发"
                )
            return {
                "ok": True,
                "target": target,
                "sentAt": int(time.time()),
                "deliveryConfirmed": True,
                "confirmation": "chat-bubble-readback",
            }
        finally:
            try:
                auto.SetClipboardText(previous_clipboard)
            except Exception:
                pass
