#!/usr/bin/env python3
"""战壕悬浮窗 —— 常驻桌面的半透明面板，Alt+Q 切换显示/隐藏，**直接左键拖动**移动。

默认就显示（像游戏小地图一直挂着）：按一次 Alt+Q 隐藏，再按一次显示。
面板默认是**普通窗口**（不做点击穿透），在面板上按住左键拖动即可移动，拖完的位置记在
%TEMP%\\xys-trench-overlay.pos，重启仍回到原处；`--reset-position` 可清掉记忆。

为什么要用「全局轮询」而不是 Tk 的 <B1-Motion>：面板是置顶无边框常显窗，还要支持
可选的「点击穿透」模式（XYS_TRENCH_OVERLAY_CLICKTHROUGH=1，见下）。穿透模式下窗口
收不到任何鼠标事件，Tk 的绑定全部失效；统一走轮询（GetAsyncKeyState + GetCursorPos +
命中矩形判断）两种模式就都能拖。另外设了 4px 阈值：单纯点一下不会挪面板。

默认不穿透的代价：面板会吃掉它盖住那一块区域的所有鼠标点击（浏览器标签条之类点不动）。
换成穿透（--click-through）就能让鼠标「穿过」面板去操作下面的窗口，代价是左键不能再直接
拖面板，得按住 Alt 拖。要留给用户的面板由 server.py 拉起，只能用环境变量控制这个开关。

一次列出战壕榜上**最新开盘的 3 个**标的及其中文 AI 叙事（按 poolCreatedAt 倒序，
榜单第 1 条就是面板第 ① 条）。

数据来源与战壕榜页面完全一致：
  GET  /api/onchain-trenches        -> 战壕榜最新行（poolCreatedAt 倒序，最新的在最前）
  POST /api/exchange-ai-narratives  -> 币安 AI 中文叙事（按合约逐条缓存）

GMGN 的战壕接口本身不返回任何叙事字段（实测 114 个字段里没有），所以叙事一律
走币安 AI 通道，与前端 price-watch.js 的口径一致。

用法：
  python trench_overlay.py                  # 常驻，默认显示，Alt+Q 切换，左键直接拖动
  python trench_overlay.py --status         # 只打一次数据报告后退出（排查用）
  python trench_overlay.py --selfcheck      # 环境/热键/端口自检
  python trench_overlay.py --logtail 20     # 取日志尾部（排查用）
  python trench_overlay.py --preview 8      # 用样例数据渲染面板 8 秒（看版式用）
  python trench_overlay.py --reset-position # 忘掉拖动过的位置，回到左上角
  python trench_overlay.py --click-through  # 改成点击穿透（纯装饰，左键穿到下面窗口）
"""
from __future__ import annotations

import argparse
import atexit
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_PORT = 8765
DEFAULT_HOTKEY = "alt+q"
DEFAULT_ALPHA = 0.90
# 固定面板宽度：小地图式的窄条，不随最长叙事行被撑宽。
PANEL_WIDTH = 396
TEXT_WRAP_PX = 352
# 拖动时的轮询间隔：40ms（25fps）拖起来一顿一顿，16ms 才跟手。
DRAG_POLL_MS = 16
# 起手后必须先移动这么多像素才算拖动。没有这个阈值时，单纯在面板上点一下
# （光电鼠标随手就会抖 1~3px）也会把面板挪一点，用户会以为面板「自己会跳」。
DRAG_THRESHOLD_PX = 4

MAX_CARDS = 3
# 服务端 /api/onchain-trenches 的 pageSize 下限就是 12。
ROWS_TO_FETCH = 12
# 叙事只问要显示的那几条（display_rows）：战壕批在服务端走 2 并发小池子，
# 多问没有意义，还白白拖慢面板第一次出内容。
NARRATIVE_CHARS = 110
NARRATIVE_MAX_LINES = 4

FEED_TTL_SECONDS = 45
STATUS_TTL_SECONDS = 600
HTTP_TIMEOUT = 25
MUTEX_NAME = "Local\\xys-trench-overlay"
PID_FILE = Path(tempfile.gettempdir()) / "xys-trench-overlay.pid"
LOG_FILE = Path(tempfile.gettempdir()) / "xys-trench-overlay.log"
LOG_MAX_BYTES = 256 * 1024
# 拖动后的面板位置（"x,y"）。和 pid / 日志一样放临时目录：它只是运行态备忘，
# 真丢了最多回到左上角，不影响功能；`--reset-position` 就是删这个文件。
POSITION_FILE = Path(tempfile.gettempdir()) / "xys-trench-overlay.pos"

RANK_GLYPHS = ("①", "②", "③", "④", "⑤")

# 深色半透明面板配色：在任何应用上都能读，接近游戏小地图的观感。
BG_PANEL = "#0b1015"
BG_HEADER = "#121a22"
# 卡片与面板同色：半透明叠在任意桌面上时，色差会变成一条条灰带。
BG_CARD = "#0b1015"
BG_DIVIDER = "#1e2a35"
FG_TITLE = "#e8eef4"
FG_TEXT = "#c3d0dc"
FG_MUTED = "#7d8fa1"
FG_ACCENT = "#ffd166"
FG_UP = "#ff6b6b"      # A 股/国内口径：涨为红
FG_DOWN = "#3ddc84"    # 跌为绿

# 只对 loopback 生效的直连 opener：本机服务绝不能走系统代理。
_LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_LOG_LOCK = threading.Lock()


def log_line(message: str) -> None:
    """把启动过程与异常写进临时目录的日志文件。

    悬浮窗由 ``pythonw.exe`` 拉起，没有控制台，``print`` 一律看不见。少了这个日志，
    「启动失败」和「根本没启动」在外部看起来完全一样，只能靠猜。
    """
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}"
    try:
        with _LOG_LOCK:
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX_BYTES:
                LOG_FILE.write_text("", encoding="utf-8")
            with LOG_FILE.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
    except OSError:
        pass
    try:
        print(line, file=sys.stderr)
    except Exception:
        pass


def read_log_tail(lines: int = 20) -> str:
    try:
        text = LOG_FILE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.strip().splitlines()[-lines:])


# --------------------------------------------------------------------------- #
# 热键：用 GetAsyncKeyState 轮询，绝不 RegisterHotKey
# --------------------------------------------------------------------------- #
# RegisterHotKey 会把按键从系统里吃掉（前台应用收不到），用户当初就是因为
# Tab 会全局吞键才改成 Alt+Q。轮询只读按键状态、不拦截，按住/松开天然好判定，
# 也不需要在 Tk 的消息循环里插入 WndProc。
_ALPHABET_VK = {chr(code): code for code in range(ord("A"), ord("Z") + 1)}
_DIGIT_VK = {str(digit): ord(str(digit)) for digit in range(10)}
EXTRA_VK = {"space": 0x20, "tab": 0x09, "`": 0xC0, "~": 0xC0}
KEY_VK = {**_ALPHABET_VK, **_DIGIT_VK, **EXTRA_VK}

# 修饰键 -> 需要同时按下的虚拟键码集合
MODIFIER_VK = {
    "alt": ((0x12,), "Alt"),        # VK_MENU，左右 Alt 任一
    "ctrl": ((0x11,), "Ctrl"),      # VK_CONTROL
    "control": ((0x11,), "Ctrl"),
    "shift": ((0x10,), "Shift"),    # VK_SHIFT
    "win": ((0x5B, 0x5C), "Win"),
}

# VK_LBUTTON。拖动要用：拖动统一走全局轮询（穿透模式下 Tk 收不到任何鼠标事件，
# <Button-1>/<B1-Motion> 全部失效），普通窗口模式也用同一套实现，两条路只差一个开关。
VK_LBUTTON = 0x01


class HotkeyError(ValueError):
    pass


def parse_hotkey(value: str) -> tuple[tuple[tuple[int, ...], ...], int, str]:
    """把 ``"alt+q"`` 解析成 ``(修饰键码组, 主键码, 显示名)``。"""
    parts = [part.strip().casefold() for part in str(value or "").split("+") if part.strip()]
    if not parts:
        raise HotkeyError("热键不能为空")
    main_token = parts[-1].upper()
    modifier_tokens = parts[:-1]
    if main_token not in KEY_VK:
        raise HotkeyError(f"不支持的按键：{main_token}（只支持 A-Z、0-9、Space、Tab）")
    if not modifier_tokens:
        raise HotkeyError("请至少加一个修饰键（Alt/Ctrl/Shift/Win），避免误触")
    modifier_vks: list[tuple[int, ...]] = []
    labels: list[str] = []
    for token in modifier_tokens:
        if token not in MODIFIER_VK:
            raise HotkeyError(f"不支持的修饰键：{token}")
        vks, label = MODIFIER_VK[token]
        if label in labels:
            continue
        modifier_vks.append(vks)
        labels.append(label)
    display = "+".join([*labels, main_token.upper()])
    return tuple(modifier_vks), KEY_VK[main_token], display


def _user32():
    if os.name != "nt":
        return None
    try:
        return ctypes.windll.user32
    except Exception:
        return None


def hotkey_is_down(modifier_vks: tuple[tuple[int, ...], ...], main_vk: int) -> bool:
    """热键当前是否处于按下状态（只读，不拦截）。"""
    api = _user32()
    if api is None:
        return False
    for group in modifier_vks:
        if not any(api.GetAsyncKeyState(int(vk)) & 0x8000 for vk in group):
            return False
    return bool(api.GetAsyncKeyState(int(main_vk)) & 0x8000)


# --------------------------------------------------------------------------- #
# 指针与位置：拖动 / 位置记忆
# --------------------------------------------------------------------------- #
def modifiers_down(modifier_vks: tuple[tuple[int, ...], ...]) -> bool:
    """修饰键是否全部按下（只看修饰键，不看主键）。"""
    api = _user32()
    if api is None:
        return False
    return all(
        any(api.GetAsyncKeyState(int(vk)) & 0x8000 for vk in group)
        for group in modifier_vks
    )


def left_button_down() -> bool:
    """鼠标左键是否按下（只读，不拦截）。"""
    api = _user32()
    return bool(api.GetAsyncKeyState(VK_LBUTTON) & 0x8000) if api is not None else False


def cursor_position() -> tuple[int, int] | None:
    """屏幕坐标下的光标位置；取不到返回 None。"""
    api = _user32()
    if api is None:
        return None
    try:
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        point = POINT()
        if not api.GetCursorPos(ctypes.byref(point)):
            return None
        return int(point.x), int(point.y)
    except Exception:
        return None


def work_area() -> tuple[int, int, int, int] | None:
    """主显示器工作区 ``(left, top, right, bottom)``，已排除任务栏。

    只用于「首次落位」——默认位置贴在左上角，不能压到任务栏。
    """
    api = _user32()
    if api is None:
        return None
    try:
        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long),
            ]

        rect = RECT()
        api.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        if rect.right <= rect.left or rect.bottom <= rect.top:
            return None
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    except Exception:
        return None


def virtual_screen() -> tuple[int, int, int, int] | None:
    """整个虚拟桌面（含所有显示器）的 ``(left, top, right, bottom)``。

    越界保护必须用它而不是主屏工作区：用户有副屏时，面板本来就该能拖过去。
    """
    api = _user32()
    if api is None:
        return None
    try:
        # SM_XVIRTUALSCREEN/SM_YVIRTUALSCREEN/SM_CXVIRTUALSCREEN/SM_CYVIRTUALSCREEN
        left = int(api.GetSystemMetrics(76))
        top = int(api.GetSystemMetrics(77))
        width = int(api.GetSystemMetrics(78))
        height = int(api.GetSystemMetrics(79))
        if width <= 0 or height <= 0:
            return None
        return left, top, left + width, top + height
    except Exception:
        return None


def clamp_position(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    """把面板位置夹回虚拟桌面内。

    面板是置顶无边框的常显窗，一旦整块拖到屏幕外，用户就再也没有抓回来的办法
    （没有标题栏、任务栏也没有条目）。所以每次落位都夹一次，而不是只在加载时校验。
    """
    x, y = int(x), int(y)
    area = virtual_screen()
    if area is None:
        return x, y
    left, top, right, bottom = area
    max_x = max(left, right - int(width))
    max_y = max(top, bottom - int(height))
    return max(left, min(x, max_x)), max(top, min(y, max_y))


def read_saved_position() -> tuple[int, int] | None:
    """读回上次拖动后的位置；文件不存在或内容坏掉都当作没有。"""
    try:
        raw = POSITION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    x_text, _, y_text = raw.partition(",")
    try:
        return int(x_text), int(y_text)
    except (TypeError, ValueError):
        return None


def write_saved_position(x: int, y: int) -> None:
    """记下拖动后的位置。写不进去不算错误，只是下次回到左上角。"""
    try:
        POSITION_FILE.write_text(f"{int(x)},{int(y)}", encoding="utf-8")
    except OSError:
        pass


def clear_saved_position() -> bool:
    """删掉位置记忆，返回是否真的删掉了（本来就没有则 False）。"""
    try:
        POSITION_FILE.unlink()
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# 数据：战壕榜最新行 + 币安 AI 叙事
# --------------------------------------------------------------------------- #
def compact_usd(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number <= 0:
        return "—"
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if number >= limit:
            return f"${number / limit:.1f}{suffix}"
    return f"${number:.0f}"


def format_percent(value: object) -> tuple[str, str]:
    """返回 ``(文本, 色调)``；色调取 up/down/""，涨幅按国内口径用红色。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "", ""
    if number > 0:
        return f"+{number:.1f}%", "up"
    if number < 0:
        return f"{number:.1f}%", "down"
    return "0.0%", ""


def truncate_text(value: object, limit: int = NARRATIVE_CHARS) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _char_cost(char: str) -> float:
    """显示宽度：CJK/全角记 1 格，拉丁记半格。"""
    return 0.5 if ord(char) < 0x2E80 else 1.0


def wrap_display_text(value: object, *, budget: float = 25.5, max_lines: int = 4) -> str:
    """按显示宽度折行后返回带 ``\\n`` 的文本。

    Tk 的 ``wraplength`` 只把空格当断点：「$RETAIL 是社区驱动型代币…」这种
    「短英文词 + 一大段中文」会把英文词单独丢到一行，白白浪费一行高度。
    自己折行可以按格子填满每一行，也让卡片高度可控。
    """
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    lines: list[str] = []
    current: list[str] = []
    width = 0.0
    truncated = False
    for index, char in enumerate(text):
        cost = _char_cost(char)
        if current and width + cost > budget:
            lines.append("".join(current).rstrip())
            current, width = [], 0.0
            if len(lines) >= max_lines:
                truncated = bool(text[index:].strip())
                break
            if char == " ":
                continue
        current.append(char)
        width += cost
    else:
        if current:
            lines.append("".join(current).rstrip())
    if not lines:
        return ""
    if truncated:
        tail = lines[-1]
        lines[-1] = (tail[:-1] if len(tail) > 1 else "") + "…"
    return "\n".join(lines)


def clean_narrative(value: object) -> str:
    """战壕叙事必须中文：拉丁字母明显占优的一律丢弃（服务端闸门的本地双保险）。"""
    text = truncate_text(value)
    if len(text) < 12:
        return text
    latin = sum(1 for char in text if ("a" <= char.casefold() <= "z"))
    cjk = sum(1 for char in text if "\u3400" <= char <= "\u9fff")
    if latin >= 18 and latin >= max(12, cjk * 4):
        return ""
    return text


def trench_row_key(row: dict) -> str:
    contract = str(row.get("contractAddress") or row.get("contract") or "").strip()
    if not contract:
        return ""
    network = str(row.get("network") or row.get("chain") or "").strip().casefold()
    return f"{network}:{contract}"


def format_age(value) -> str:
    """把 ``ageMinutes`` 变成「3分钟前」这类短文本。"""
    try:
        minutes = float(value)
    except (TypeError, ValueError):
        return ""
    if minutes < 0:
        return ""
    if minutes < 1:
        return "刚刚"
    if minutes < 60:
        return f"{int(minutes)}分钟前"
    if minutes < 60 * 24:
        return f"{int(minutes // 60)}小时前"
    return f"{int(minutes // (60 * 24))}天前"


def display_rows(rows: list[dict], limit: int = MAX_CARDS) -> list[tuple[str, dict]]:
    """按榜单顺序取最新的 ``limit`` 行（跳过没有合约地址或重复的），最新在前。"""
    picked: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for row in rows:
        key = trench_row_key(row)
        if not key or key in seen:
            continue
        seen.add(key)
        picked.append((key, row))
        if len(picked) >= limit:
            break
    return picked


def build_cards(rows: list[dict], narratives: dict[str, str]) -> list[dict]:
    """把战壕行 + 叙事拼成展示卡片。

    榜单按 ``poolCreatedAt`` 倒序、最新的在最前，所以面板必须**就是最新的
    MAX_CARDS 个**：第 ① 条 = 榜单第 1 条。

    曾经的做法是「优先挑有叙事的行占名额」，结果新开盘的币还没生成叙事时会被
    更老的币顶掉，面板显示的根本不是最新的三个（用户一眼就看出来了）。叙事只是
    挂在卡片上的内容，缺了就如实说明，绝不能拿它当筛选条件。
    """
    cards: list[dict] = []
    for key, row in display_rows(rows):
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        change_text, change_tone = format_percent(metrics.get("priceChangeH1"))
        cards.append({
            "key": key,
            "symbol": str(row.get("symbol") or row.get("name") or "未知标的")[:24],
            "name": str(row.get("name") or "")[:40],
            "network": str(row.get("network") or row.get("chain") or ""),
            "ageText": format_age(row.get("ageMinutes")),
            "marketCap": compact_usd(metrics.get("marketCapUsd") or metrics.get("fdvUsd")),
            "changeText": change_text,
            "changeTone": change_tone,
            "narrative": narratives.get(key, ""),
        })
    for index, card in enumerate(cards):
        card["rank"] = RANK_GLYPHS[index] if index < len(RANK_GLYPHS) else f"{index + 1}."
    return cards


def _read_json(request: urllib.request.Request, timeout: int) -> dict:
    try:
        with _LOOPBACK_OPENER.open(request, timeout=timeout) as response:
            return json.loads(response.read(4_000_000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read(4_000_000).decode("utf-8"))
        except Exception:
            return {}
    except Exception:
        return {}


def fetch_trench_rows(port: int, *, page_size: int = ROWS_TO_FETCH) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/onchain-trenches?page=1&pageSize={max(12, int(page_size))}",
        headers={"Accept": "application/json", "User-Agent": "XingyunSociety/trench-overlay"},
    )
    return _read_json(request, HTTP_TIMEOUT)


def fetch_narratives(port: int, rows: list[dict]) -> dict[str, str]:
    """一次批量取回中文叙事，返回 ``{row_key: 叙事}``。"""
    items = []
    for row in rows:
        key = trench_row_key(row)
        if not key:
            continue
        items.append({
            "key": key,
            "sourceId": "gmgn-trenches",
            "chain": str(row.get("network") or row.get("chain") or ""),
            "contractAddress": str(row.get("contractAddress") or row.get("contract") or ""),
            "symbol": str(row.get("symbol") or row.get("name") or ""),
        })
    if not items:
        return {}
    body = json.dumps({"items": items}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/exchange-ai-narratives",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "XingyunSociety/trench-overlay",
        },
        method="POST",
    )
    payload = _read_json(request, HTTP_TIMEOUT * 4)
    result: dict[str, str] = {}
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        if not key:
            continue
        text = clean_narrative(item.get("exchangeAiNarrative"))
        if text:
            result[key] = text
    return result


class TrenchFeed:
    """进程内缓存的战壕数据源；刷新永远在后台线程，绝不阻塞 UI。"""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = int(port)
        self._lock = threading.Lock()
        self._cards: list[dict] = []
        self._error = ""
        self._fetched_at = 0.0
        self._refreshing = False
        self._narratives: dict[str, str] = {}

    def snapshot(self) -> tuple[list[dict], str, float]:
        with self._lock:
            return [dict(card) for card in self._cards], self._error, self._fetched_at

    def age_seconds(self) -> float:
        with self._lock:
            if not self._fetched_at:
                return float("inf")
            return max(0.0, time.time() - self._fetched_at)

    def is_fresh(self, ttl: float = FEED_TTL_SECONDS) -> bool:
        return self.age_seconds() <= ttl

    def refreshing(self) -> bool:
        with self._lock:
            return self._refreshing

    def refresh(self, on_done=None) -> bool:
        """后台拉起一次刷新；已在刷新中则直接返回 False。"""
        with self._lock:
            if self._refreshing:
                return False
            self._refreshing = True

        def worker() -> None:
            error = ""
            cards: list[dict] = []
            try:
                payload = fetch_trench_rows(self.port)
                rows = [row for row in (payload.get("items") or []) if isinstance(row, dict)]
                if not rows:
                    statuses = payload.get("sourceStatus") or {}
                    failed = [key.split("/")[0] for key, value in statuses.items() if value != "ok"]
                    error = (
                        f"战壕榜暂无数据{f'（{len(failed)} 条链异常）' if failed else ''}"
                        if statuses else "战壕榜暂无数据"
                    )
                    if payload.get("rateLimited"):
                        error = f"GMGN 限流冷却中，约 {int(payload.get('retryAfterSeconds') or 0)} 秒后恢复"
                    elif payload.get("error"):
                        error = str(payload.get("error"))[:80]
                else:
                    # 只问要显示的那几条：面板永远是最新的 MAX_CARDS 个。
                    candidates = [row for _, row in display_rows(rows)]
                    try:
                        fresh = fetch_narratives(self.port, candidates)
                    except Exception:
                        fresh = {}
                    with self._lock:
                        for key, text in fresh.items():
                            self._narratives[key] = text
                        known = dict(self._narratives)
                    cards = build_cards(rows, known)
                    if not cards:
                        error = "战壕行缺少合约地址，无法生成面板"
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"[:120]
            with self._lock:
                if cards:
                    self._cards = cards
                    self._fetched_at = time.time()
                self._error = error
                self._refreshing = False
            if on_done is not None:
                try:
                    on_done()
                except Exception:
                    pass

        try:
            threading.Thread(target=worker, name="trench-overlay-feed", daemon=True).start()
        except Exception:
            with self._lock:
                self._refreshing = False
            return False
        return True


# --------------------------------------------------------------------------- #
# 面板
# --------------------------------------------------------------------------- #
class TrenchOverlay:
    def __init__(
        self,
        feed: TrenchFeed,
        *,
        hotkey: str = DEFAULT_HOTKEY,
        alpha: float = DEFAULT_ALPHA,
        click_through: bool = False,
    ):
        self.feed = feed
        self.modifier_vks, self.main_vk, self.hotkey_label = parse_hotkey(hotkey)
        self.alpha = max(0.35, min(1.0, float(alpha)))
        # 默认**不做**点击穿透：面板是普通窗口，左键按在面板上就能直接拖。
        # 穿透只有纯装饰场景才需要（鼠标要穿到下面窗口去操作），那时左键拖不动，
        # 改用 Alt+左键 —— 所以两种模式共用同一套轮询拖动，只差一个门槛。
        self.click_through = bool(click_through)
        self.root = None
        self._cards_host = None
        self._header_note = None
        self._footer_note = None
        self._visible = False
        self._held = False
        self._logged_toggle = False
        self._last_auto_refresh = 0.0
        self._poll_interval_ms = 40
        # 拖动状态。Tk 收不到 <B1-Motion>（穿透模式下更是收不到任何鼠标事件），所以拖动
        # 完全由 _poll_pointer() 全局轮询驱动；_position 是「面板左上角」的唯一真源，
        # 渲染/刷新都从它出发，拖过之后不会被 _place_panel() 冲回左上角。
        self._position: tuple[int, int] | None = read_saved_position()
        self._panel_size = (PANEL_WIDTH, 0)
        self._dragging = False
        # 左键按下的那一刻：既用来算「面板左上角 vs 光标」的偏移，也用来判断
        # 这次到底是「点一下」还是「拖动」（位移超过 DRAG_THRESHOLD_PX 才算拖）。
        # _press_seen 记录「这一轮按下有没有判过起手位置」，防止按住从面板外拖进来时误判。
        self._press_origin: tuple[int, int] | None = None
        self._press_seen = False
        self._drag_offset = (0, 0)

    # -- 生命周期 ---------------------------------------------------------- #
    def run(self, *, preview_seconds: float = 0.0) -> int:
        try:
            import tkinter as tk  # noqa: F401
        except Exception as exc:
            log_line(f"tkinter 不可用：{exc!r}")
            print(f"tkinter 不可用：{exc}", file=sys.stderr)
            return 2
        self._build()
        # 启动就先取一次，让面板一出现就有内容。
        self.feed.refresh(on_done=self._on_feed_done)
        # 默认就显示：它是常驻小地图，不是「按需呼出」的浮层。
        self.show()
        position = self._position or (0, 0)
        log_line(
            f"面板已就绪：热键 {self.hotkey_label}、端口 {self.feed.port}、pid {os.getpid()}、"
            f"位置 {position[0]},{position[1]}、"
            f"模式 {'点击穿透（Alt+左键拖动）' if self.click_through else '普通窗口（左键直接拖动）'}"
        )
        # 轮询循环一律启动，preview 模式也不例外：这样 `--preview` 就是一次真实的
        # 拖动/热键演习（不用打扰正在运行的那个实例），而不是只能看的一张贴图。
        self.root.after(self._poll_interval_ms, self._poll_hotkey)
        if preview_seconds > 0:
            self.root.after(int(preview_seconds * 1000), self._quit)
        try:
            self.root.mainloop()
        except Exception as exc:
            log_line(f"主循环异常退出：{exc!r}")
            return 5
        log_line("主循环已退出。")
        return 0

    def _quit(self) -> None:
        if self.root is not None:
            self.root.destroy()

    def _build(self) -> None:
        import tkinter as tk

        root = tk.Tk()
        root.title("战壕悬浮窗")
        root.overrideredirect(True)
        root.configure(bg=BG_PANEL)
        root.attributes("-topmost", True)
        try:
            root.attributes("-toolwindow", True)
        except Exception:
            pass
        try:
            root.attributes("-alpha", self.alpha)
        except Exception:
            pass
        root.withdraw()
        self.root = root
        self._apply_no_activate()

        header = tk.Frame(root, bg=BG_HEADER)
        header.pack(fill="x")
        tk.Label(
            header, text="⚡ 战壕热榜", bg=BG_HEADER, fg=FG_ACCENT,
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(side="left", padx=(12, 8), pady=(8, 7))
        self._header_note = tk.Label(
            header, text="币安 AI 叙事", bg=BG_HEADER, fg=FG_MUTED,
            font=("Microsoft YaHei UI", 9),
        )
        self._header_note.pack(side="right", padx=(8, 12), pady=(8, 7))

        tk.Frame(root, bg=BG_DIVIDER, height=1).pack(fill="x")

        self._cards_host = tk.Frame(root, bg=BG_PANEL)
        self._cards_host.pack(fill="both", expand=True)

        footer = tk.Frame(root, bg=BG_HEADER)
        footer.pack(fill="x", side="bottom")
        self._footer_note = tk.Label(
            footer, text=self._footer_text(),
            bg=BG_HEADER, fg=FG_MUTED, font=("Microsoft YaHei UI", 8),
        )
        self._footer_note.pack(side="left", padx=(12, 8), pady=(6, 7))
        self._render()

    def _footer_text(self) -> str:
        hint = "按住 Alt 拖动移动" if self.click_through else "左键直接拖动移动"
        return f"{self.hotkey_label} 显示 / 隐藏 · {hint}"

    def _apply_no_activate(self) -> None:
        """置顶显示但不抢焦点；**只有点击穿透模式**才加 WS_EX_TRANSPARENT。

        默认（普通窗口）必须能收到鼠标：面板要支持「左键按住直接拖」。加了透明扩展样式
        的窗口收不到任何鼠标消息，命中测试会直接穿到下面窗口，左键就永远拖不动它。

        开穿透的场景是「把它当纯装饰」：那时左上角那块区域（浏览器标签条之类）必须
        点得动，代价是左键拖不动面板，得按住 Alt —— 这正是穿透模式仍然保留轮询拖动的原因。
        """
        api = _user32()
        if api is None or self.root is None:
            return
        try:
            hwnd = int(self.root.winfo_id())
            parent = api.GetParent(hwnd)
            target = int(parent) if parent else hwnd
            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_TRANSPARENT = 0x00000020
            style = api.GetWindowLongW(target, GWL_EXSTYLE)
            # 默认不点穿透：左键要能直接拖面板。
            extra = WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
            if self.click_through:
                extra |= WS_EX_TRANSPARENT
            api.SetWindowLongW(target, GWL_EXSTYLE, style | extra)
        except Exception:
            pass

    # -- 显隐 -------------------------------------------------------------- #
    def show(self) -> None:
        if self.root is None or self._visible:
            return
        self._render()
        # 必须先落位再 deiconify：overrideredirect 窗口一旦显示就直接可见，位置若是
        # 后设的，会在 (0,0) 先闪一帧再跳到正确位置（拖动过后尤其明显）。
        self._place_panel()
        self.root.deiconify()
        # deiconify 之后必须再补一次扩展样式：窗口还没映射时 GetParent(winfo_id()) 返回 0，
        # 样式会写到内层窗口；而 deiconify 会让 Tk 建出一个真正可见的外层窗口，鼠标命中
        # 测试读的正是外层。少了这一句，点击穿透就是「写了个标记但完全没生效」。
        self._apply_no_activate()
        try:
            self.root.lift()
            self.root.attributes("-topmost", True)
        except Exception:
            pass
        self._visible = True

    def hide(self) -> None:
        if self.root is None or not self._visible:
            return
        # 隐藏时若还在拖着，先收尾：否则 _dragging 会卡在 True，轮询继续跑 16ms
        # 且热键被一直跳过。
        self._end_drag()
        self.root.withdraw()
        self._visible = False

    # -- 位置 -------------------------------------------------------------- #
    def _default_position(self) -> tuple[int, int]:
        """左上角对齐工作区并留一点边距（自动避开任务栏）。"""
        margin_x, margin_y = 16, 14
        area = work_area()
        if area is None:
            return margin_x, margin_y
        return area[0] + margin_x, area[1] + margin_y

    def _place_panel(self) -> None:
        """宽度恒定，高度按内容自适应，并落到「当前应该在的位置」。

        位置来自 self._position：拖动过就是拖动后的位置，没拖过才是左上角。
        卡片数量变化会改高度，所以每次渲染都要重新落位——但**不能**因此把拖动的
        位置冲掉，这正是把「左上角」和「当前位置」拆开的原因。
        """
        root = self.root
        if root is None:
            return
        root.update_idletasks()
        width = PANEL_WIDTH
        height = max(80, int(root.winfo_reqheight()))
        self._panel_size = (width, height)
        x, y = self._position if self._position is not None else self._default_position()
        x, y = clamp_position(x, y, width, height)
        self._position = (x, y)
        root.geometry(f"{width}x{height}+{x}+{y}")

    # -- 拖动 -------------------------------------------------------------- #
    def _poll_pointer(self) -> None:
        """左键拖动面板（普通窗口模式；穿透模式退化为 Alt+左键）。

        拖动只能全局轮询：面板是置顶无边框窗，穿透模式下 Tk 收不到任何鼠标事件，
        普通模式也没必要为它单独维护一套 <B1-Motion> 绑定。流程是
        「左键按下 + 光标落在面板矩形内 → 记下起手点 → 位移超过 4px 才算真正拖动」。

        4px 阈值不是可有可无的：光电鼠标单击时随手会抖 1~3px，没有阈值就会变成
        「每次点面板都挪一点点」，看起来像面板自己在跳。
        """
        if self.root is None or not self._visible:
            self._end_drag()
            return
        if not left_button_down():
            self._end_drag()
            return
        point = cursor_position()
        if point is None:
            return
        if not self._dragging:
            # 穿透模式下左键要照常穿给下面的窗口（这才是开穿透的意义），
            # 所以那种模式必须按住 Alt 才允许拖；普通窗口模式直接左键就拖。
            if self.click_through and not modifiers_down(self.modifier_vks):
                return
            if self._press_origin is None:
                # 起手位置只在「按下」的那一次判定。少了这个 _press_seen，用户从面板
                # 外面（比如浏览器）按下去、按住拖过面板时就会被判成「在面板上起手」，
                # 面板会跟着乱跑。
                if self._press_seen:
                    return
                self._press_seen = True
                if not self._point_inside(point):
                    return
                origin_x, origin_y = self._position or self._default_position()
                self._press_origin = point
                self._drag_offset = (point[0] - origin_x, point[1] - origin_y)
                return
            if max(
                abs(point[0] - self._press_origin[0]),
                abs(point[1] - self._press_origin[1]),
            ) < DRAG_THRESHOLD_PX:
                return  # 还在阈值内：当作「点了一下」，不动面板
            self._dragging = True
        self._move_panel_to(point[0] - self._drag_offset[0], point[1] - self._drag_offset[1])

    def _point_inside(self, point: tuple[int, int]) -> bool:
        """光标是否落在面板矩形内（拖动只能从面板上起手）。"""
        if self._position is None:
            return False
        width, height = self._panel_size
        x, y = self._position
        return x <= point[0] < x + width and y <= point[1] < y + height

    def _move_panel_to(self, x: int, y: int) -> None:
        if self.root is None:
            return
        width, height = self._panel_size
        x, y = clamp_position(x, y, width, height)
        self._position = (x, y)
        try:
            self.root.geometry(f"{width}x{height}+{x}+{y}")
        except Exception as exc:
            log_line(f"拖动面板失败：{exc!r}")

    def _end_drag(self) -> None:
        """松开左键或面板隐藏时收尾：清起手点 + 位置落盘 + 记一行日志。

        每次拖动都记日志是有意的：用户报「拖不动」时，日志里没有这行就能直接判定
        「拖动根本没起手」，不用再猜。
        """
        self._press_origin = None
        self._press_seen = False
        if not self._dragging:
            return
        self._dragging = False
        if self._position is None:
            return
        write_saved_position(*self._position)
        log_line(f"拖动结束：面板位置 {self._position[0]},{self._position[1]}（已记忆）。")

    # -- 热键轮询 ---------------------------------------------------------- #
    def _poll_hotkey(self) -> None:
        """每 40ms 探一次按键/光标状态，绝不阻塞、绝不吞键。

        任何异常都必须落盘：原来的 ``except Exception: pass`` 会把「按键判定正确
        但面板渲染失败」变成完全静默的现象——用户看到的就是「按 Alt+Q 没反应」，
        且日志里一个字都没有。
        """
        if self.root is None:
            return
        try:
            self._poll_pointer()
            # 拖动中不判热键：手正按在左键上拖动，顺手蹭到 Alt+Q 会把面板收走，
            # 这种「拖到一半面板消失」非常突兀，等松手再判。
            if not self._dragging:
                self._handle_hotkey(hotkey_is_down(self.modifier_vks, self.main_vk))
            self._auto_refresh_if_stale()
        except Exception as exc:
            log_line(f"热键轮询异常：{exc!r}")
        finally:
            if self.root is not None:
                # 拖动中把轮询加密到 16ms：键盘 40ms 绰绰有余，但光标 40ms 会明显拖影。
                interval = DRAG_POLL_MS if self._dragging else self._poll_interval_ms
                try:
                    self.root.after(interval, self._poll_hotkey)
                except Exception:
                    pass

    def _handle_hotkey(self, down: bool) -> None:
        """Alt+Q 是**切换**：按一次隐藏，再按一次显示。

        只在「按下」的上升沿动作，松开只是把状态复位，所以按住不放不会来回闪。
        """
        if not down:
            self._held = False
            return
        if self._held:
            return
        self._held = True
        try:
            if self._visible:
                self.hide()
                shown = False
            else:
                self.show()
                shown = True
        except Exception as exc:
            log_line(f"切换面板失败：{exc!r}")
            return
        if not self._logged_toggle:
            self._logged_toggle = True
            log_line(f"首次 {self.hotkey_label} 切换：面板{'已显示' if shown else '已隐藏'}。")

    def _auto_refresh_if_stale(self, interval: float = 5.0) -> None:
        """面板常显时要自己保鲜：定期看一眼缓存是否过期，过期就后台补一次。

        每 5 秒才检查一次，真正的重复请求由 TrenchFeed 的单飞标志挡掉。
        """
        if not self._visible:
            return
        now = time.time()
        if now - self._last_auto_refresh < interval:
            return
        self._last_auto_refresh = now
        if not self.feed.is_fresh():
            self.feed.refresh(on_done=self._on_feed_done)

    def _on_feed_done(self) -> None:
        if self.root is None:
            return
        try:
            self.root.after(0, self._refresh_visible)
        except Exception:
            pass

    def _refresh_visible(self) -> None:
        if self._visible:
            self._render()
            self._place_panel()

    # -- 渲染 -------------------------------------------------------------- #
    def _render(self) -> None:
        if self._cards_host is None:
            return
        import tkinter as tk

        for child in list(self._cards_host.winfo_children()):
            child.destroy()
        cards, error, fetched_at = self.feed.snapshot()
        if self._header_note is not None:
            stamp = time.strftime("%H:%M:%S", time.localtime(fetched_at)) if fetched_at else "--:--:--"
            note = f"币安 AI · {stamp}"
            if error:
                note = f"{error} · {stamp}"
            self._header_note.configure(text=note, fg=FG_UP if error else FG_MUTED)

        if not cards:
            tk.Label(
                self._cards_host,
                text=wrap_display_text(
                    "读取中…" if self.feed.refreshing() else (error or "战壕榜暂无数据"),
                    max_lines=3,
                ),
                bg=BG_PANEL, fg=FG_MUTED, font=("Microsoft YaHei UI", 10),
                justify="left", wraplength=TEXT_WRAP_PX, anchor="w",
            ).pack(fill="x", padx=14, pady=(14, 16))
            return

        for index, card in enumerate(cards):
            if index:
                tk.Frame(self._cards_host, bg=BG_DIVIDER, height=1).pack(fill="x", padx=12)
            self._render_card(card)

    def _render_card(self, card: dict) -> None:
        import tkinter as tk

        block = tk.Frame(self._cards_host, bg=BG_CARD)
        block.pack(fill="x", padx=10, pady=(8, 6))

        head = tk.Frame(block, bg=BG_CARD)
        head.pack(fill="x", padx=4, pady=(2, 0))
        tk.Label(
            head, text=card["rank"], bg=BG_CARD, fg=FG_ACCENT,
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(side="left")
        tk.Label(
            head, text=card["symbol"], bg=BG_CARD, fg=FG_TITLE,
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side="left", padx=(5, 6))
        meta = " · ".join(part for part in (card.get("network"), card.get("ageText")) if part)
        if meta:
            tk.Label(
                head, text=meta, bg=BG_CARD, fg=FG_MUTED,
                font=("Microsoft YaHei UI", 8),
            ).pack(side="left", pady=(3, 0))
        change_tone = card.get("changeTone") or ""
        if card.get("changeText"):
            tk.Label(
                head, text=card["changeText"], bg=BG_CARD,
                fg=FG_UP if change_tone == "up" else FG_DOWN if change_tone == "down" else FG_MUTED,
                font=("Microsoft YaHei UI", 9, "bold"),
            ).pack(side="right", pady=(3, 0))
        tk.Label(
            head, text=card["marketCap"], bg=BG_CARD, fg=FG_TEXT,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="right", padx=(0, 10), pady=(3, 0))

        narrative = card.get("narrative")
        if narrative:
            body_text = narrative
            body_fg = FG_TEXT
        else:
            body_text = "币安 AI 叙事接口未返回该币的中文叙事（新开盘时常见）。"
            body_fg = FG_MUTED
        tk.Label(
            block, text=wrap_display_text(body_text, max_lines=NARRATIVE_MAX_LINES),
            bg=BG_CARD, fg=body_fg,
            font=("Microsoft YaHei UI", 10), justify="left",
            anchor="w", wraplength=TEXT_WRAP_PX,
        ).pack(fill="x", padx=4, pady=(3, 5))


# --------------------------------------------------------------------------- #
# 单实例 / PID
# --------------------------------------------------------------------------- #
_SINGLETON_HANDLE = None


def acquire_single_instance() -> bool:
    global _SINGLETON_HANDLE
    if os.name != "nt":
        return True
    try:
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            return True
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            return False
        _SINGLETON_HANDLE = handle
        return True
    except Exception:
        return True


def write_pid_file() -> None:
    try:
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass


def clear_pid_file() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def overlay_disabled() -> bool:
    """``XYS_TRENCH_OVERLAY=0`` 可以彻底关掉悬浮窗（不需要改代码）。"""
    return str(os.environ.get("XYS_TRENCH_OVERLAY") or "").strip().lower() in {"0", "false", "off", "no"}


def click_through_requested() -> bool:
    """``XYS_TRENCH_OVERLAY_CLICKTHROUGH=1`` 把面板切成点击穿透（纯装饰）模式。

    server.py 拉起的面板没法传命令行参数，所以这个开关只能走环境变量；
    ``--click-through`` 是给手动调试用的等价入口。
    """
    value = str(os.environ.get("XYS_TRENCH_OVERLAY_CLICKTHROUGH") or "").strip().lower()
    return value in {"1", "true", "on", "yes"}


def spawn_detached(*, root: Path | None = None, source: str = "server") -> dict:
    """把悬浮窗作为独立进程拉起来（由 server.py 启动时调用）。

    悬浮窗与 server.py 是两个进程，所以它必须由启动 server.py 的一方负责拉起：
    service_guard、直接 ``python server.py``、打包版都走这里，避免又变成
    「重启了服务却什么都没发生」。**绝不抛异常**——它只是附加功能，失败只回reason。

    返回 ``{"started": bool, "reason": str, "pid": int | None}``。
    """
    if os.name != "nt":
        return {"started": False, "reason": "only supported on Windows", "pid": None}
    if overlay_disabled():
        return {"started": False, "reason": "disabled by XYS_TRENCH_OVERLAY", "pid": None}
    script = (root or Path(__file__).resolve().parent) / "trench_overlay.py"
    if not script.is_file():
        return {"started": False, "reason": f"script not found: {script}", "pid": None}
    # 必须用 pythonw：python.exe 会给悬浮窗多挂一个黑色控制台窗口。
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    launcher = str(pythonw) if pythonw.is_file() else "pythonw.exe"
    flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
    )
    env = dict(os.environ)
    env["XYS_TRENCH_OVERLAY_SOURCE"] = source
    try:
        child = subprocess.Popen(
            [launcher, str(script)],
            cwd=str(script.parent),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
    except OSError as exc:
        reason = f"{type(exc).__name__}: {exc}"
        log_line(f"spawn 失败：{reason}")
        return {"started": False, "reason": reason, "pid": None}
    log_line(f"spawn：由 {source} 拉起 python={launcher} script={script.name} pid={child.pid}")
    return {"started": True, "reason": "spawned", "pid": child.pid}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def print_status(port: int) -> int:
    feed_rows = fetch_trench_rows(port)
    rows = [row for row in (feed_rows.get("items") or []) if isinstance(row, dict)]
    print(f"服务端口 {port} · 战壕 {len(rows)} 行 · total={feed_rows.get('total')} "
          f"· sourceStatus={feed_rows.get('sourceStatus')}")
    if feed_rows.get("error"):
        print("接口错误：", feed_rows["error"])
    if not rows:
        return 1
    targets = [row for _, row in display_rows(rows)]
    narratives = fetch_narratives(port, targets)
    print(f"叙事命中 {len(narratives)}/{len(targets)}")
    for card in build_cards(rows, narratives):
        flag = "有叙事" if card["narrative"] else "无叙事"
        print(f"  {card['rank']} {card['symbol']:<14} {card.get('ageText', ''):<7} "
              f"{card['marketCap']:>9} {card['changeText']:>8} [{flag}] {card['narrative'][:52]}")
    return 0


def selfcheck(port: int, hotkey: str, click_through: bool = False) -> int:
    """一次性自检：把「启动了但按住没反应」拆成可读的结论。"""
    import platform

    ok = True
    print(f"python      : {sys.executable}")
    print(f"version     : {platform.python_version()} ({platform.machine()})")
    print(f"log file    : {LOG_FILE}")
    pid_text = PID_FILE.read_text(encoding="utf-8").strip() if PID_FILE.exists() else "(不存在，说明没在运行)"
    print(f"pid file    : {PID_FILE} -> {pid_text}")
    saved = read_saved_position()
    print(f"position    : {POSITION_FILE} -> "
          f"{f'{saved[0]},{saved[1]}' if saved else '(未拖动过，落在左上角)'}")
    print(f"panel mode  : {'点击穿透（左键穿到下面窗口，需按 Alt 拖动）' if click_through else '普通窗口（左键直接拖动）'}")

    try:
        import tkinter  # noqa: F401
        print("tkinter     : OK")
    except Exception as exc:
        ok = False
        print(f"tkinter     : 缺失（{exc}）-> 悬浮窗无法创建，请用 C:\\Python314 的 python")

    try:
        modifier_vks, main_vk, label = parse_hotkey(hotkey)
        print(f"hotkey      : {label} (modifier={modifier_vks} main=0x{main_vk:02X})")
    except HotkeyError as exc:
        ok = False
        print(f"hotkey      : 无效（{exc}）")

    api = _user32()
    if api is None:
        ok = False
        print("user32      : 不可用 -> 无法轮询按键")
    else:
        alt_down = bool(api.GetAsyncKeyState(0x12) & 0x8000)
        q_down = bool(api.GetAsyncKeyState(0x51) & 0x8000)
        print(f"key state   : Alt={'按下' if alt_down else '抬起'} Q={'按下' if q_down else '抬起'}（此刻实时值）")
        print(f"drag        : 光标 {'OK' if cursor_position() else '取不到'} · "
              f"左键 {'按下' if left_button_down() else '抬起'} · 虚拟桌面 {virtual_screen()}")

    started = time.monotonic()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/onchain-trenches?page=1&pageSize=12",
            headers={"Accept": "application/json", "User-Agent": "XingyunSociety/trench-overlay"},
        )
        payload = _read_json(request, 5)
        rows = [row for row in (payload.get("items") or []) if isinstance(row, dict)]
        elapsed = int((time.monotonic() - started) * 1000)
        print(f"monitor api : OK · {len(rows)} 行 · {elapsed}ms · sourceStatus={payload.get('sourceStatus')}")
    except Exception as exc:
        ok = False
        print(f"monitor api : 失败（{exc!r}）-> 请确认后台服务正在 {port} 端口运行")

    print(f"结论        : {'可以启动' if ok else '存在问题，见上面带 -> 的行'}")
    return 0 if ok else 1


def preview_cards() -> list[dict]:
    rows = [
        {
            "network": "solana", "contractAddress": "PreviewA", "symbol": "BLUEPRINT",
            "ageMinutes": 1, "metrics": {"marketCapUsd": 47_545.3, "priceChangeH1": 12.4},
        },
        {
            "network": "solana", "contractAddress": "PreviewB", "symbol": "RETAIL",
            "ageMinutes": 3, "metrics": {"marketCapUsd": 162_093, "priceChangeH1": -4.8},
        },
        {
            "network": "bsc", "contractAddress": "PreviewC", "symbol": "FUND",
            "ageMinutes": 12, "metrics": {"marketCapUsd": 31_418.6, "priceChangeH1": 3.1},
        },
    ]
    narratives = {
        "solana:PreviewA": (
            "BLUEPRINT 代币源于 blueprintools 的愿景，旨在通过区块链技术革新创意表达。"
            "标志展示带网格线的蓝色 B 字母，象征从概念到实现的蓝图过程。"
        ),
        "solana:PreviewB": (
            "$RETAIL 是社区驱动型代币，灵感来自零售投资热潮与迷因文化的结合，"
            "通过社交媒体营销、TikTok 推广与社区投票迅速传播。"
        ),
        "bsc:PreviewC": (
            "FUND 以早期募资叙事为核心，主打把散户资金聚合成可见的链上流动性池，"
            "并计划用部分手续费回购销毁。"
        ),
    }
    return build_cards(rows, narratives)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="战壕悬浮窗（常显，Alt+Q 切换显示/隐藏，左键直接拖动移动）"
    )
    parser.add_argument("--port", type=int, default=int(os.getenv("XYS_ALERT_PORT") or DEFAULT_PORT))
    parser.add_argument("--hotkey", default=os.getenv("XYS_TRENCH_OVERLAY_HOTKEY") or DEFAULT_HOTKEY)
    parser.add_argument("--alpha", type=float, default=float(os.getenv("XYS_TRENCH_OVERLAY_ALPHA") or DEFAULT_ALPHA))
    parser.add_argument("--status", action="store_true", help="只输出一次数据报告后退出")
    parser.add_argument("--selfcheck", action="store_true", help="打印环境/热键/端口自检报告后退出")
    parser.add_argument("--logtail", nargs="?", type=int, const=20, default=0,
                        help="打印日志最后 N 行后退出（默认 20），供启动脚本报错时取证")
    parser.add_argument("--preview", nargs="?", type=float, const=8.0, default=0.0,
                        help="用样例数据渲染面板若干秒（默认 8 秒），用于检查版式")
    parser.add_argument("--reset-position", action="store_true",
                        help="忘掉拖动过的位置，下次启动回到左上角")
    parser.add_argument("--click-through", action="store_true",
                        help="切成点击穿透（纯装饰：鼠标穿到下面窗口，左键拖不动，要按 Alt 拖）")
    args = parser.parse_args()

    # 穿透模式：命令行开关或环境变量（server.py 拉起时只能走环境变量）。
    click_through = bool(args.click_through or click_through_requested())

    # 忘掉拖动位置：纯粹的文件操作，不需要单实例互斥体，也不该产生启动日志。
    if args.reset_position:
        removed = clear_saved_position()
        print("已重置面板位置，下次启动回到左上角。" if removed else "面板位置本来就是默认的，无需重置。")
        return 0

    # 取日志必须在写日志之前，否则 --logtail 会把自己那行也打出来。
    if args.logtail:
        print(read_log_tail(args.logtail) or "(日志为空)")
        return 0

    log_line(
        f"启动：port={args.port} hotkey={args.hotkey!r} alpha={args.alpha} "
        f"clickThrough={click_through} "
        f"status={args.status} selfcheck={args.selfcheck} preview={args.preview} "
        f"来源={os.environ.get('XYS_TRENCH_OVERLAY_SOURCE') or '手动'} python={sys.executable}"
    )

    if args.selfcheck:
        code = selfcheck(args.port, args.hotkey, click_through)
        log_line(f"自检结束：exit={code}")
        return code

    if args.status:
        return print_status(args.port)

    if args.preview and args.preview > 0:
        feed = TrenchFeed(args.port)
        with feed._lock:
            feed._cards = preview_cards()
            feed._fetched_at = time.time()
            feed._error = ""
        overlay = TrenchOverlay(
            feed, hotkey=args.hotkey, alpha=args.alpha, click_through=click_through
        )
        return overlay.run(preview_seconds=args.preview)

    if not acquire_single_instance():
        log_line("已有实例在运行（互斥体被占用），本次退出。")
        print("战壕悬浮窗已在运行。", file=sys.stderr)
        return 3

    try:
        parse_hotkey(args.hotkey)
    except HotkeyError as exc:
        log_line(f"热键配置无效：{exc}")
        print(f"热键配置无效：{exc}", file=sys.stderr)
        return 4

    write_pid_file()
    atexit.register(clear_pid_file)
    log_line(f"pid {os.getpid()} 已写入 {PID_FILE}")
    feed = TrenchFeed(args.port)
    overlay = TrenchOverlay(
        feed, hotkey=args.hotkey, alpha=args.alpha, click_through=click_through
    )
    drag_hint = "按住 Alt 拖动左键移动" if click_through else "左键直接拖动移动"
    print(
        f"战壕悬浮窗已启动：默认常显，{overlay.hotkey_label} 切换显示/隐藏，{drag_hint}。",
        file=sys.stderr,
    )
    return overlay.run()


def guarded_main() -> int:
    """最外层兜底：pythonw 下没有控制台，未捕获异常必须落盘才有迹可循。"""
    try:
        return main()
    except SystemExit:
        raise
    except BaseException:
        import traceback

        log_line("致命错误：" + traceback.format_exc().strip())
        return 70


if __name__ == "__main__":
    raise SystemExit(guarded_main())
