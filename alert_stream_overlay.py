#!/usr/bin/env python3
"""事件流浮窗 —— 常驻屏幕左下角的固定提醒流窗口。

用户 2026-11-26：「能不能把弹窗换成左下角固定的事件流窗口，而不是这样一直弹来弹去」，
随后补「但是浮窗里要保留原先弹窗的一些功能，比如语音，查看，叙事结构，以及相应的
弹窗标题和内容」。再往后是三条观感要求：「ui风格还是保留原先的风格」「审美再做好一点，
比如线条啊，做平滑一点」「太粗了 / 很多黑边」→ 所以这里**一个 `relief` 边框都不用**，
底板与卡片全部用 `stream_ui` 生成的抗锯齿圆角图铺在 Canvas 上。
最后「默认只展示一条，可以自己滚动下滑查看」→ `PANEL_HEIGHT` 就按一张卡片给。

它不是一个「又一个弹窗」：**窗口只有一个，位置固定，消息往下滚**。
投递侧由 ``DESKTOP_ALERT_STREAM_ONLY`` 关掉系统通知（见 server.py 的
``launch_desktop_alert`` / ``desktop_alert_delivery_tick``），本窗口只读事件流接口：

    GET /api/event-flow?after=<cursor>&limit=20

每张卡片保留原弹窗的三件事：

  * **语音** —— 直接复用 ``desktop_alert.speak_text()``（Windows SAPI，走同一个
    ``XingYunShe.PopupAudio.v1`` 互斥量，不会和别处的语音撞车）；
  * **查看** —— 复用 ``desktop_alert.open_external_url()``（优先拉起用户的 Chrome，
    带登录态；找不到才回落系统默认浏览器）；
  * **叙事** —— 入库时带了 ``explanation``（币安/GIGA X 中文叙事）。这份快照可能为空
    （弹窗路径上只做 prefetch，不同步等 AI），点开时再按合约问一次
    ``/api/exchange-ai-narratives``，与弹窗里的 ✦ 按钮同一条链路。

标题与正文用的就是入库的 ``title`` / ``thesis``，不再是拼接出来的长串。

**基线**：窗口以「启动那一刻」为基线，启动前的老事件一概不显示（用户 2026-11-26
「每次事件窗口启动它都在里面……事件流应该是以启动那一刻为基线」）。首轮只取游标、
不取历史；增量轮询再按 ``activityAt >= baseline`` 过一道闸 —— 因为 ``flow_events`` 里
有些被反复合并的老事件 revision 一直在涨，会被当成新事件推回来。

用法：
  python alert_stream_overlay.py                 # 常驻，Alt+Q 显隐，左键直接拖动
  python alert_stream_overlay.py --reset-position
  python alert_stream_overlay.py --selfcheck     # 端口 / 热键 / 事件流自检
  python alert_stream_overlay.py --dump          # 只打最近 10 条事件流后退出
  python alert_stream_overlay.py --preview 20    # 样例数据渲染 20 秒，看版式用
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

# Windows 控制台是 GBK，币名/叙事里什么字符都有。这一点项目已经踩过（GMT 分析器被
# 韩文币名打死 18462 次），所以本模块的 stdout/stderr 一律 errors="replace"。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

import stream_ui

DEFAULT_PORT = 8765
DEFAULT_HOTKEY = "alt+q"

# --------------------------------------------------------------------------- #
# 尺寸
# --------------------------------------------------------------------------- #
# 宽度对齐原弹窗（374），再窄就压不住长标题、再宽又显笨（用户 2026-11-26「有点太宽了」）。
PANEL_WIDTH = 372
# **默认只露一张卡片**（「默认只展示一条，可以自己滚动下滑查看」）。
# 38 是标题条，上下各留 9，剩下约 150 正好一张卡片。
PANEL_HEIGHT = 224
HEADER_H = 38
COLLAPSED_HEIGHT = HEADER_H + 2
# 内容区四周的留白：卡片要「整体居中」，所以上、下、左、右一起给，
# 而不是像第一版那样顶死在标题条下沿。
BODY_PAD_TOP = 9
BODY_PAD_BOTTOM = 9
CARD_RADIUS = 12
CARD_MARGIN_X = 9
CARD_GAP = 9
CARD_PAD_X = 14
CARD_PAD_Y = 10

# 事件流轮询间隔。2 秒：弹窗本来就是秒级到达，再快也只是空轮询。
POLL_SECONDS = 2.0
POLL_BACKOFF_SECONDS = 8.0
HTTP_TIMEOUT = 15
#: 基线容差：允许比窗口启动早这么一点点的消息进来（同一台机器，但事件时间来自
#: 上游，偶尔会早上几秒）。超过这个量级的老事件一律不显示。
BASELINE_TOLERANCE_MS = 5000
CARDS_TO_FETCH = 20
# 卡片上限。够了就砍最老的 —— 这是「流」，不是历史台账；翻历史请开 /event-flow 页面。
MAX_CARDS = 30

DRAG_POLL_MS = 16
DRAG_THRESHOLD_PX = 4
HOTKEY_POLL_MS = 60

MUTEX_NAME = "Local\\xys-alert-stream-overlay"
PID_FILE = Path(tempfile.gettempdir()) / "xys-alert-stream.pid"
LOG_FILE = Path(tempfile.gettempdir()) / "xys-alert-stream.log"
POSITION_FILE = Path(tempfile.gettempdir()) / "xys-alert-stream.pos"
LOG_MAX_BYTES = 256 * 1024

# --------------------------------------------------------------------------- #
# 配色：**原样沿用 desktop_alert.show_popup()**（用户「ui风格还是保留原先的风格」）
# --------------------------------------------------------------------------- #
# 改动这里之前先去那边对一眼：底 / 标题条 / 按钮 / 正文字色 都按 alertTone 分两套，
# 两处必须一致，否则同一个提醒在两个界面上是两种长相。
BORDER_NORMAL = "#cfe6f5"   # 面板 1px 描边（比原来的 #9dcfe8 淡，避免"太粗"）
BORDER_HOT = "#e8695f"      # 红卡描边（比纯红 #df3f35 柔一档）
BG_HEADER = "#c9edff"
BG_PANEL = "#f8fcff"
BG_CARD = "#ffffff"
BG_CARD_HOT = "#fffdfd"
BG_BUTTON = "#eef7fd"       # 卡片按钮底色，比原来的 #d7f0fb 更淡
BG_BUTTON_ACTIVE = "#dceffa"
BG_BUTTON_HOT = "#fdeeec"
BG_BUTTON_HOT_ACTIVE = "#fbe0dc"
BG_SOURCE_CHIP = "#f7f2df"
FG_HEADER = "#344047"
FG_ACTION = "#455660"
FG_KIND = "#8a97a3"          # 时间/类型这类次要信息，弱化一档更清爽
FG_TITLE = "#1f2a30"
FG_TITLE_HOT = "#c0392b"
FG_TEXT = "#5f6e78"
FG_MUTED = "#7c8a94"
FG_CHAIN = "#0e7490"
FG_CHAIN_HOT = "#b42318"
FG_CHIP = "#1c2529"
FG_ACCENT = "#b5861a"

# 只对 loopback 生效的直连 opener：本机服务绝不能走系统代理。
_LOOPBACK_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_LOG_LOCK = threading.Lock()


def tone_colors(is_hot: bool) -> dict[str, str]:
    """红 / 普通两套颜色。键名对齐 desktop_alert 里的局部变量名，方便互相对照。"""
    return {
        "border": BORDER_HOT if is_hot else BORDER_NORMAL,
        "card": BG_CARD_HOT if is_hot else BG_CARD,
        "action": BG_BUTTON_HOT if is_hot else BG_BUTTON,
        "action_active": BG_BUTTON_HOT_ACTIVE if is_hot else BG_BUTTON_ACTIVE,
        "title": FG_TITLE_HOT if is_hot else FG_TITLE,
        "chain": FG_CHAIN_HOT if is_hot else FG_CHAIN,
    }


def log_line(message: str) -> None:
    """写临时目录的日志。pythonw 没有控制台，不写就永远查不到启动失败的原因。"""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}\n"
    try:
        with _LOG_LOCK:
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX_BYTES:
                LOG_FILE.write_text("", encoding="utf-8")
            with LOG_FILE.open("a", encoding="utf-8") as handle:
                handle.write(line)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 热键与指针：GetAsyncKeyState 轮询，绝不 RegisterHotKey（后者会把按键从系统里吃掉）
# --------------------------------------------------------------------------- #
_ALPHABET_VK = {chr(code): code for code in range(ord("A"), ord("Z") + 1)}
_DIGIT_VK = {str(digit): ord(str(digit)) for digit in range(10)}
KEY_VK = {**_ALPHABET_VK, **_DIGIT_VK, "space": 0x20}
MODIFIER_VK = {
    "alt": ((0x12,), "Alt"),
    "ctrl": ((0x11,), "Ctrl"),
    "control": ((0x11,), "Ctrl"),
    "shift": ((0x10,), "Shift"),
    "win": ((0x5B, 0x5C), "Win"),
}
VK_LBUTTON = 0x01


class HotkeyError(ValueError):
    pass


def parse_hotkey(value: str) -> tuple[tuple[tuple[int, ...], ...], int, str]:
    """``"alt+q"`` -> ``(修饰键码组, 主键码, 显示名)``。"""
    parts = [part.strip().casefold() for part in str(value or "").split("+") if part.strip()]
    if not parts:
        raise HotkeyError("热键不能为空")
    main_token = parts[-1].upper()
    if main_token not in KEY_VK:
        raise HotkeyError(f"不支持的按键：{main_token}（只支持 A-Z、0-9、Space）")
    if not parts[:-1]:
        raise HotkeyError("请至少加一个修饰键（Alt/Ctrl/Shift/Win），避免误触")
    modifier_vks: list[tuple[int, ...]] = []
    labels: list[str] = []
    for token in parts[:-1]:
        if token not in MODIFIER_VK:
            raise HotkeyError(f"不支持的修饰键：{token}")
        vks, label = MODIFIER_VK[token]
        if label in labels:
            continue
        modifier_vks.append(vks)
        labels.append(label)
    return tuple(modifier_vks), KEY_VK[main_token], "+".join([*labels, main_token])


def _user32():
    if os.name != "nt":
        return None
    try:
        return ctypes.windll.user32
    except Exception:
        return None


def hotkey_is_down(modifier_vks: tuple[tuple[int, ...], ...], main_vk: int) -> bool:
    api = _user32()
    if api is None:
        return False
    for group in modifier_vks:
        if not any(api.GetAsyncKeyState(int(vk)) & 0x8000 for vk in group):
            return False
    return bool(api.GetAsyncKeyState(int(main_vk)) & 0x8000)


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong), ("rcMonitor", _Rect),
        ("rcWork", _Rect), ("dwFlags", ctypes.c_ulong),
    ]


# EnumDisplayMonitors 的回调类型。**必须留模块级引用**：用局部函数包成回调后
# 这个 CFUNCTYPE 一被回收，还没走完的枚举就是野指针（会直接崩进程）。
_MONITOR_ENUM_PROC = getattr(ctypes, "WINFUNCTYPE", None)
if _MONITOR_ENUM_PROC is not None:
    _MONITOR_ENUM_PROC = _MONITOR_ENUM_PROC(
        ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(_Rect), ctypes.c_ssize_t,
    )


def work_area() -> tuple[int, int, int, int] | None:
    """**主显示器**工作区 ``(left, top, right, bottom)``，已排除任务栏。

    只用来决定「第一次出现在哪」。判边界不要用它 —— 见 ``screen_work_areas()``。
    """
    api = _user32()
    if api is None:
        return None
    try:
        rect = _Rect()
        # SPI_GETWORKAREA = 0x0030（它只认主显示器）
        if not api.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return None
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    except Exception:
        return None


def screen_work_areas() -> list[tuple[int, int, int, int]]:
    """**所有**显示器的工作区（各自排除自己的任务栏）。

    用户 2026-11-26：「我有副屏，它没法被拖拽到我的副屏上」。

    根因是旧代码拿 ``SPI_GETWORKAREA`` 做边界，而它只返回主显示器 —— 副屏如果挂在
    主屏的左边或上边（坐标是负的），夹取里的 ``max(top, y)`` 就把窗口永久钉在
    主屏边缘，怎么拖都进不去。所以边界必须把每块屏都算进来。
    """
    areas: list[tuple[int, int, int, int]] = []
    api = _user32()
    if api is not None and _MONITOR_ENUM_PROC is not None:
        try:
            def _collect(hmon, _hdc, _rect, _data):
                info = _MonitorInfo()
                info.cbSize = ctypes.sizeof(_MonitorInfo)
                if api.GetMonitorInfoW(hmon, ctypes.byref(info)):
                    work = info.rcWork
                    areas.append((
                        int(work.left), int(work.top),
                        int(work.right), int(work.bottom),
                    ))
                return 1

            api.EnumDisplayMonitors(None, None, _MONITOR_ENUM_PROC(_collect), 0)
        except Exception:
            areas = []
    if areas:
        return areas

    # 退路：枚举失败就退回主屏，至少不会把窗口夹到屏幕外面去。
    primary = work_area()
    return [primary] if primary else []


# 「还露得出来」的下限：横向留够标题字数、纵向留够整条标题条（标题条就是拖拽把手）。
GRAB_SLIVER_WIDTH = 120
GRAB_SLIVER_HEIGHT = HEADER_H + 6


def clamp_to_monitors(x: int, y: int, width: int, height: int) -> tuple[int, int]:
    """把窗口位置约束在「所有显示器」的范围内，并允许它跨屏滑动。

    两步走，刻意不做「硬夹进单块屏」：

    1. 只要窗口还有 ``GRAB_SLIVER_WIDTH x GRAB_SLIVER_HEIGHT`` 露在某块屏上就原样放行。
       否则跨越两屏交界的那一瞬，整窗会「跳」一下（从主屏一边瞬间弹到副屏另一边）。
    2. 完全露不出来（被拖进 L 形拼接的死角，或拖出所有屏）才就近吸附到最近的那块屏。
    """
    areas = screen_work_areas()
    x, y, width, height = int(x), int(y), int(width), int(height)
    if not areas:
        return x, y

    need_w = min(GRAB_SLIVER_WIDTH, width)
    need_h = min(GRAB_SLIVER_HEIGHT, height)
    for left, top, right, bottom in areas:
        overlap_x = min(x + width, right) - max(x, left)
        overlap_y = min(y + height, bottom) - max(y, top)
        if overlap_x >= need_w and overlap_y >= need_h:
            return x, y

    def _distance(area: tuple[int, int, int, int]) -> float:
        a_left, a_top, a_right, a_bottom = area
        center_x = x + width / 2
        center_y = y + height / 2
        near_x = min(max(center_x, a_left), a_right)
        near_y = min(max(center_y, a_top), a_bottom)
        return (center_x - near_x) ** 2 + (center_y - near_y) ** 2

    left, top, right, bottom = min(areas, key=_distance)
    x = min(max(left, x), max(left, right - width))
    y = min(max(top, y), max(top, bottom - height))
    return int(x), int(y)


def confine_to_monitor(
    x: int, y: int, width: int, height: int,
    anchor_x: int, anchor_y: int,
) -> tuple[int, int]:
    """把子窗口**整块**夹进「锚点所在的那块屏」，用于从浮窗弹出的叙事小窗。

    小窗宽 520，副屏只有 1280 宽；浮窗贴着右边时它会被推出屏幕。这种从属窗口不能
    用 ``clamp_to_monitors`` 的「露一条就放行」—— 半个窗口在屏外等于点不到。
    """
    areas = screen_work_areas()
    if not areas:
        return int(x), int(y)

    anchor_area = None
    for area in areas:
        left, top, right, bottom = area
        if left <= anchor_x < right and top <= anchor_y < bottom:
            anchor_area = area
            break
    if anchor_area is None:
        anchor_area = min(
            areas,
            key=lambda a: (anchor_x - min(max(anchor_x, a[0]), a[2])) ** 2
            + (anchor_y - min(max(anchor_y, a[1]), a[3])) ** 2,
        )

    left, top, right, bottom = anchor_area
    x = min(max(left, int(x)), max(left, right - width))
    y = min(max(top, int(y)), max(top, bottom - height))
    return x, y


def load_position() -> tuple[int, int] | None:
    try:
        text = POSITION_FILE.read_text(encoding="utf-8").strip()
        x_text, y_text = text.split(",")
        return int(float(x_text)), int(float(y_text))
    except Exception:
        return None


def save_position(x: int, y: int) -> None:
    try:
        POSITION_FILE.write_text(f"{int(x)},{int(y)}", encoding="utf-8")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 单实例：两个窗口同时轮询会把同一条消息显示两遍
# --------------------------------------------------------------------------- #
_MUTEX_HANDLE = None


def acquire_single_instance() -> bool:
    global _MUTEX_HANDLE
    if os.name != "nt":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            return True
        # ERROR_ALREADY_EXISTS = 183
        if kernel32.GetLastError() == 183:
            return False
        _MUTEX_HANDLE = handle
        return True
    except Exception:
        return True


# --------------------------------------------------------------------------- #
# 事件流读取
# --------------------------------------------------------------------------- #
def service_port() -> int:
    for name in ("XYS_ALERT_STREAM_PORT", "XINGYUN_PORT", "PORT", "XYS_ALERT_PORT"):
        value = str(os.getenv(name) or "").strip()
        if value.isdigit():
            return int(value)
    return DEFAULT_PORT


def fetch_event_flow(port: int, *, after: int | None = None, limit: int = CARDS_TO_FETCH) -> dict:
    params = [f"limit={max(1, int(limit))}"]
    if after is None:
        params.append("category=popup")
        # 首屏要**关掉 live 过滤**：live 的条件里只有 news/signal，没有 popup 分支，
        # `live=1&category=popup` 天然恒空 —— 表现就是每次重启后窗口空着，
        # 非要等一条新提醒才冒出来。`after` 增量那条路径本来就不带 live 条件。
        params.append("live=0")
    else:
        params.append(f"after={max(0, int(after))}")
    url = f"http://127.0.0.1:{port}/api/event-flow?{'&'.join(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": "XingyunSociety/event-stream"})
    with _LOOPBACK_OPENER.open(request, timeout=HTTP_TIMEOUT) as response:
        payload = json.loads(response.read(4_000_000).decode("utf-8"))
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise RuntimeError("event-flow returned an invalid payload")
    return payload


def popup_items(payload: dict) -> list[dict]:
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and isinstance(item.get("popup"), dict)]


def preview_items() -> list[dict]:
    """`--preview` 用的假数据：一条红卡、两条普通卡，专门看两套配色对不对。"""
    now = int(time.time() * 1000)
    samples = [
        {
            "title": "聪明钱买入 CICI · $12,480",
            "thesis": "0x7b3d…8d61 以 3 笔成交买入 12,480 美元，KOL 同车 4 人。",
            "source": "聪明钱监控", "sourceLabel": "NX", "kind": "聪明钱买入",
            "priority": "实时", "alertTone": "red", "chainLabel": "BSC",
            "url": "https://gmgn.ai/bsc/token/0x0", "contractAddress": "0x0", "chain": "bsc",
            "voiceText": "聪明钱买入 CICI，一万两千四百八十美元",
        },
        {
            "title": "新创建 · TrumpTrumpcoin",
            "thesis": "战壕「新创建」段，创建 3 分钟内，已打满 41%。",
            "source": "GMGN 战壕", "sourceLabel": "GM", "kind": "战壕新币",
            "priority": "关注", "alertTone": "normal", "chainLabel": "BSC",
            "url": "https://gmgn.ai/bsc/token/0x1", "contractAddress": "0x1", "chain": "bsc",
            "voiceText": "战壕新创建 TrumpTrumpcoin",
        },
        {
            "title": "币安将上线 AVAAI 永续合约",
            "thesis": "2026-11-26 16:30 上线 U 本位永续，最高 50 倍杠杆。",
            "source": "币安公告", "sourceLabel": "BN", "kind": "上币公告",
            "priority": "重点", "alertTone": "normal", "chainLabel": "中心化",
            "url": "https://www.binance.com/zh-CN/support/announcement",
            "voiceText": "币安将上线 AVAAI 永续合约",
        },
    ]
    return [
        {"id": index + 1, "activityAt": now - (len(samples) - index) * 90_000, "popup": sample}
        for index, sample in enumerate(samples)
    ]


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #
class AlertStreamWindow:
    def __init__(
        self, *, port: int, hotkey: str = DEFAULT_HOTKEY,
        position: tuple[int, int] | None = None, preview: bool = False,
    ):
        import tkinter as tk

        self.tk = tk
        self.port = port
        self.preview = preview
        self.modifier_vks, self.main_vk, self.hotkey_label = parse_hotkey(hotkey)
        self.cards: list[dict] = []
        self.cursor: int | None = None
        # 事件流以「窗口启动那一刻」为基线：启动前的老事件一律不显示。
        # 用户 2026-11-26：「每次事件窗口启动它都在里面，很奇怪，事件流应该是以启动
        # 那一刻为基线」。那条反复出现的是 `flow_events` 里一个被上千条新闻合并的老
        # 事件，`revision` 一直在涨，增量轮询每次都把它当新事件推回来，卡片上却挂着
        # 几个小时前的时间 —— 光靠「首轮不取历史」不够，还得按基线过滤推送。
        self.baseline_ms = int(time.time() * 1000)
        self.inbox: "list[dict]" = []
        self.inbox_lock = threading.Lock()
        self.stopping = False
        self.visible = True
        self.collapsed = False
        self.auto_speak = False
        self.unread = 0
        self.drag_state: dict | None = None
        self.hotkey_down = False
        self.body_item = None
        self.header_item = None
        self.edge_item = None

        root = tk.Tk()
        self.root = root
        root.title("星云社 · 事件流")
        root.overrideredirect(True)
        root.configure(bg=BG_PANEL)
        root.attributes("-topmost", True)
        # 不设 -alpha：Tk 在 Windows 上把带 alpha 的 PhotoImage 画进 Canvas 时，
        # 半透明窗口会让整块内容跟着发虚（实测卡片文字像蒙了一层白），
        # 而这个窗口是纯浅色面板，透明本来也换不来什么观感收益。
        self._place(root, position if position else self._default_position())
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.quit)

        if preview:
            with self.inbox_lock:
                self.inbox.extend(preview_items())
        else:
            self.worker = threading.Thread(
                target=self._poll_loop, daemon=True, name="xys-alert-stream-poll"
            )
            self.worker.start()

        root.after(300, self._drain)
        root.after(HOTKEY_POLL_MS, self._poll_hotkey)
        log_line(f"window ready port={port} hotkey={self.hotkey_label} preview={preview}")
        # 多屏问题别靠猜：把每块屏的工作区打出来，下次「拖不过去」直接看日志。
        log_line(
            "monitors: "
            + " | ".join(
                f"({l},{t})-({r},{b})" for l, t, r, b in screen_work_areas()
            )
            + f" | panel at {self._current_position()}"
        )

    # -- 位置 ------------------------------------------------------------- #
    def _panel_height(self) -> int:
        return COLLAPSED_HEIGHT if self.collapsed else PANEL_HEIGHT

    def _default_position(self) -> tuple[int, int]:
        area = work_area()
        if area:
            left, top, _right, bottom = area
            return left + 24, max(top, bottom - PANEL_HEIGHT - 24)
        return 24, 200

    def _place(self, root, position: tuple[int, int]) -> None:
        height = self._panel_height()
        # 走多屏边界，不走主屏工作区：上次关在副屏（负坐标）时，用主屏夹取会把
        # 窗口强行拽回主屏，用户会觉得「位置没记住」。
        x, y = clamp_to_monitors(position[0], position[1], PANEL_WIDTH, height)
        root.geometry(f"{PANEL_WIDTH}x{height}+{int(x)}+{int(y)}")

    def _current_position(self) -> tuple[int, int]:
        return int(self.root.winfo_x()), int(self.root.winfo_y())

    # -- UI --------------------------------------------------------------- #
    def _build(self) -> None:
        tk = self.tk

        # 窗口分三块：顶部标题条（普通 Frame + pack）、正文滚动区（Frame + Canvas）、
        # 外圈 1px 淡描边由 shell 这条 Canvas 画。
        #
        # 标题条**不用 Canvas 手工排文字**：`Canvas.bbox()` 在窗口还没映射时给的是
        # 没排版的结果，拿它算右侧按钮位置会越算越偏，最后整排按钮被推出窗口外
        # （第一版实测就是这样）。Frame + pack(side="right") 由 Tk 自己排，不会错。
        self.shell = tk.Canvas(
            self.root, bg=BG_PANEL, highlightthickness=0, bd=0, takefocus=0
        )
        self.shell.pack(fill="both", expand=True)
        self.edge_item = None
        self.header_item = None

        self.header = tk.Frame(self.shell, bg=BG_HEADER)
        self.title_label = tk.Label(
            self.header, text="星云社 · 事件流", bg=BG_HEADER, fg=FG_HEADER,
            font=self._title_font(),
        )
        self.title_label.pack(side="left", padx=(15, 7))
        self.count_label = tk.Label(
            self.header, text="0 条", bg=BG_HEADER, fg=FG_KIND,
            font=self._count_font(),
        )
        self.count_label.pack(side="left")

        # 纯文字按钮：没有底框、没有边框，悬停只换颜色 —— 这是最平滑的做法。
        self.header_buttons: dict[str, tk.Label] = {}
        for key, text, command in (
            ("close", "×", self.quit),
            ("collapse", "—", self.toggle_collapse),
            ("speak", "语音", self.toggle_auto_speak),
            ("clear", "清空", self.clear_cards),
        ):
            button = tk.Label(
                self.header, text=text, bg=BG_HEADER,
                fg=FG_ACCENT if (key == "speak" and self.auto_speak) else FG_ACTION,
                font=self._header_button_font(), cursor="hand2", padx=6,
            )
            button.pack(side="right", padx=(0, 5))
            button.bind("<Button-1>", lambda _event, fn=command: fn())
            button.bind(
                "<Enter>",
                lambda _event, widget=button, k=key: widget.configure(
                    fg=FG_ACCENT if (k == "speak" and self.auto_speak) else FG_HEADER
                ),
            )
            button.bind(
                "<Leave>",
                lambda _event, widget=button, k=key: widget.configure(
                    fg=FG_ACCENT if (k == "speak" and self.auto_speak) else FG_ACTION
                ),
            )
            self.header_buttons[key] = button

        for widget in (self.header, self.title_label, self.count_label):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-1>", self._drag_end)

        self.body = tk.Frame(self.shell, bg=BG_PANEL)
        self.canvas = tk.Canvas(self.body, bg=BG_PANEL, highlightthickness=0, bd=0, takefocus=0)
        # 上下留白给在滚动区**外面**：这样留白固定不跟着内容滚走，
        # 视觉上卡片永远是「整体居中」的（顶死标题条会显得憋）。
        self.canvas.pack(fill="both", expand=True,
                         pady=(BODY_PAD_TOP, BODY_PAD_BOTTOM))
        # 不挂可见滚动条：浅色面板上那条灰色直角滚动条是最破坏观感的东西，
        # 滚轮已经绑好了（见 _on_wheel），浏览器式的自动跟随也做了。
        self.list_frame = tk.Frame(self.canvas, bg=BG_PANEL)
        self.list_window = self.canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.list_frame.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        # 上下两块"弹性留白"。窗口只有一张卡片高，想让最后一张卡垂直居中，
        # 光靠滚动位置是不够的（视口上方露出来的仍是上一条卡片的底边，看着像切坏了）。
        # 真正撑出空白的是这两块 spacer：它们算在内容里，滚到卡片顶部时上下就是纯背景。
        self.top_spacer = tk.Frame(self.list_frame, bg=BG_PANEL, height=0)
        self.top_spacer.pack(fill="x")
        self.bottom_spacer = tk.Frame(self.list_frame, bg=BG_PANEL, height=0)
        self.bottom_spacer.pack(fill="x")
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(self.list_window, width=event.width),
        )
        for widget in (self.canvas, self.list_frame):
            widget.bind("<MouseWheel>", self._on_wheel)

        self.empty_label = None
        self._show_empty_hint()

        self._paint_shell()

    def _show_empty_hint(self) -> None:
        """空态提示。第一次进窗口要用，「清空」之后也要用 ——
        旧版 clear_cards 只销毁卡片不补提示，清完就是一块纯白（像卡死了）。"""
        if self.empty_label is not None:
            return
        self.empty_label = self.tk.Label(
            self.list_frame, text="等待新提醒…", bg=BG_PANEL, fg=FG_MUTED,
            font=("Microsoft YaHei UI", 9), pady=28,
        )
        self.empty_label.pack(fill="x", before=self.bottom_spacer)

    def _font(self, key: str, *, size: int, weight: str = "normal"):
        """按用途缓存 Font 对象：`measure()` 每次都要用，反复构造白费 CPU。"""
        cache = getattr(self, "_font_cache", None)
        if cache is None:
            cache = self._font_cache = {}
        if key not in cache:
            from tkinter import font as tkfont
            cache[key] = tkfont.Font(
                family="Microsoft YaHei UI", size=size, weight=weight
            )
        return cache[key]

    def _title_font(self):
        return self._font("title", size=10, weight="bold")

    def _count_font(self):
        return self._font("count", size=9)

    def _header_button_font(self):
        return self._font("header_button", size=9, weight="bold")

    def _header_button_font(self):
        return self._font("header_button", size=9, weight="bold")

    def _paint_shell(self) -> None:
        """摆好标题条与正文区，并画外圈 1px 淡描边。

        窗口本身**不做圆角**（也不画圆角图）：圆角图四角是透明的，Tk 的
        `overrideredirect` 窗口又不支持真透明，那四个小三角只会露出 root 底色，
        在深色桌面上反而更脏 —— 用户要的是「没有粗边和黑边」，不是「窗口必须是圆的」。
        圆角留给卡片（卡片是浮在浅底上的，圆角有意义）。
        """
        height = self._panel_height()
        self.shell.configure(width=PANEL_WIDTH, height=height)
        # 描边永远在最外层：`create_window` 放的 Frame 会盖住它，所以要重画一次。
        if self.edge_item is not None:
            self.shell.delete(self.edge_item)
        self.edge_item = self.shell.create_rectangle(
            0, 0, PANEL_WIDTH - 1, height - 1,
            outline=BORDER_NORMAL, width=1,
        )
        self.shell.tag_raise(self.edge_item)

        if self.header_item is None:
            self.header_item = self.shell.create_window(
                1, 1, anchor="nw", window=self.header,
                width=PANEL_WIDTH - 2, height=HEADER_H,
            )
        else:
            self.shell.itemconfigure(
                self.header_item, width=PANEL_WIDTH - 2, height=HEADER_H
            )

        body_height = height - HEADER_H - 2
        if self.collapsed:
            if self.body_item is not None:
                self.shell.itemconfigure(self.body_item, state="hidden")
            return
        if self.body_item is None:
            self.body_item = self.shell.create_window(
                1, HEADER_H + 1, anchor="nw", window=self.body,
                width=PANEL_WIDTH - 2, height=body_height,
            )
        else:
            self.shell.itemconfigure(
                self.body_item, state="normal",
                width=PANEL_WIDTH - 2, height=body_height,
            )

    def _on_wheel(self, event) -> None:
        try:
            # 一格 3 行：默认只露一条时，滚一格就能翻过一整张卡片更跟手。
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)) * 2, "units")
        except Exception:
            pass

    def _at_top(self) -> bool:
        """是否停在最上面（= 最新那条）。

        用户 2026-11-26：「最新的事件应该在最上面啊，按照时间倒排」—— 流的方向
        从「往下长」改成「往上顶」，所以「跟随最新」的判据也从视图底部换成视图顶部。
        """
        try:
            return self.canvas.yview()[0] <= 0.005
        except Exception:
            return True

    # -- 拖动 ------------------------------------------------------------- #
    def _drag_start(self, event) -> None:
        self.drag_state = {
            "x": event.x_root, "y": event.y_root,
            "win_x": self.root.winfo_x(), "win_y": self.root.winfo_y(),
        }

    def _drag_move(self, event) -> None:
        state = self.drag_state
        if not state:
            return
        dx = event.x_root - state["x"]
        dy = event.y_root - state["y"]
        if abs(dx) < DRAG_THRESHOLD_PX and abs(dy) < DRAG_THRESHOLD_PX:
            return
        # 边界按「所有显示器」算（旧代码按主屏，副屏在负坐标区就永远拖不过去），
        # 且允许跨屏平滑滑行，不硬夹到单块屏。
        new_x, new_y = clamp_to_monitors(
            state["win_x"] + dx, state["win_y"] + dy,
            PANEL_WIDTH, self._panel_height(),
        )
        self.root.geometry(f"+{int(new_x)}+{int(new_y)}")

    def _drag_end(self, _event) -> None:
        if not self.drag_state:
            return
        self.drag_state = None
        save_position(*self._current_position())

    # -- 热键 / 显隐 ------------------------------------------------------- #
    def _poll_hotkey(self) -> None:
        if self.stopping:
            return
        try:
            down = hotkey_is_down(self.modifier_vks, self.main_vk)
            if down and not self.hotkey_down:
                self.hotkey_down = True
                self.toggle_visibility()
            elif not down:
                self.hotkey_down = False
        except Exception:
            pass
        self.root.after(HOTKEY_POLL_MS, self._poll_hotkey)

    def toggle_visibility(self) -> None:
        if self.visible:
            self.root.withdraw()
            self.visible = False
        else:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.lift()
            self.visible = True
            self.unread = 0
            self._refresh_count()

    def toggle_collapse(self) -> None:
        self.collapsed = not self.collapsed
        x, y = self._current_position()
        self._place(self.root, (x, y))
        self._paint_shell()
        if not self.collapsed:
            self._scroll_to_newest()

    def toggle_auto_speak(self) -> None:
        self.auto_speak = not self.auto_speak
        try:
            button = self.header_buttons.get("speak")
            if button is not None:
                button.configure(
                    text="语音✓" if self.auto_speak else "语音",
                    fg=FG_ACCENT if self.auto_speak else FG_ACTION,
                )
        except Exception:
            pass

    # -- 轮询 ------------------------------------------------------------- #
    def _poll_loop(self) -> None:
        backoff = False
        while not self.stopping:
            try:
                if self.cursor is None:
                    # 首轮**只要游标基线，不取历史**（「以启动那一刻为基线」）。
                    # ``after=None`` 时服务端返回的 ``cursor`` 就是当前全局 revision。
                    # 这里仍然带 ``live=0``：`live=1` 的过滤条件里没有 popup 分支，
                    # 拿到的会是个恒空的榜单，白白掩盖「基线到底建没建上」。
                    # `limit=1` 只是为了让接口有个合法参数，返回的那条**直接丢弃**。
                    payload = fetch_event_flow(self.port, after=None, limit=1)
                    items = []
                else:
                    payload = fetch_event_flow(self.port, after=self.cursor, limit=CARDS_TO_FETCH)
                    items = popup_items(payload)
                self.cursor = int(payload.get("cursor") or 0) or self.cursor
                if items:
                    with self.inbox_lock:
                        self.inbox.extend(items)
                backoff = False
            except Exception as exc:
                if not backoff:
                    log_line(f"poll failed: {exc}")
                    backoff = True
                time.sleep(POLL_BACKOFF_SECONDS)
                continue
            time.sleep(POLL_SECONDS)

    def _drain(self) -> None:
        if self.stopping:
            return
        with self.inbox_lock:
            pending, self.inbox = self.inbox, []
        if pending:
            follow = self._at_top()
            # 用户滚下去看历史时，新卡片插在最上面会把整屏内容往下顶。记下当前
            # 绝对偏移，插完再按"多出来的高度"补回去，正在读的那条才不会被推走。
            try:
                before_total = max(1, self.list_frame.winfo_height())
                before_offset = self.canvas.yview()[0] * before_total
            except Exception:
                before_total, before_offset = 1, 0.0
            known = self._known_ids()
            # **必须先按时间正序排**。接口返回的是 `ORDER BY activity_at DESC`（最新
            # 在前），而 `_add_card` 是「每来一条就插到最上面」—— 直接按原顺序灌进去，
            # 一批里最早的那条会顶到最上面（首屏 8 条时表现最明显：最旧的在顶上）。
            pending.sort(key=lambda item: (
                int(item.get("activityAt") or 0), int(item.get("id") or 0)
            ))
            added = 0
            for item in pending:
                if item.get("id") in known:
                    continue
                # 基线闸：早于窗口启动的老事件不进流。增量轮询按 `revision` 走，
                # 而那些被反复合并的老事件 revision 一直在涨，会被当成新事件推回来；
                # 不过这道闸，窗口里就会莫名其妙躺着一条几小时前的卡片。
                # `activityAt` 缺失时不拦（宁可多显示一条，也别吞掉真实提醒）。
                if not self.preview:
                    activity_at = int(item.get("activityAt") or 0)
                    if activity_at and activity_at < self.baseline_ms - BASELINE_TOLERANCE_MS:
                        known.add(item.get("id"))
                        continue
                self._add_card(item)
                known.add(item.get("id"))
                added += 1
            if added:
                if follow:
                    self._scroll_to_newest()
                else:
                    try:
                        after_total = max(1, self.list_frame.winfo_height())
                        grown = max(0, after_total - before_total)
                        self.canvas.yview_moveto(
                            min(1.0, (before_offset + grown) / after_total)
                        )
                    except Exception:
                        pass
        self.root.after(400, self._drain)

    def _known_ids(self) -> set:
        return {card["id"] for card in self.cards}

    def _chip_font(self):
        """来源胶囊 / 动作胶囊共用的字体。缓存一份，别每张卡片都新建 Font 对象。"""
        if getattr(self, "_chip_font_cache", None) is None:
            from tkinter import font as tkfont
            self._chip_font_cache = tkfont.Font(
                family="Microsoft YaHei UI", size=8, weight="bold"
            )
        return self._chip_font_cache

    def _scroll_to_newest(self) -> None:
        """把**最新那条卡片**（现在的 ``self.cards[0]``，在最上面）滚到内容区正中。

        上下留白由 `top_spacer` / `bottom_spacer` 真实撑出来（不是靠滚动位置硬凑）：
        ``top_spacer = (视口高 - 卡片高) / 2``，再 `yview_moveto(0)`，最新那条就自然
        落在正中，上面看到的是纯背景而不是上一条卡片的底边。
        ``bottom_spacer`` 要够到"最老那张也能滚到居中"，否则滚到底会露出半条。
        """
        try:
            self.root.update_idletasks()
            if not self.cards:
                return
            view = self.canvas.winfo_height()
            newest = self.cards[0]["card"]
            height = newest.winfo_height()
            pad_top = max(0, (view - height) // 2)
            try:
                oldest_height = self.cards[-1]["card"].winfo_height()
            except Exception:
                oldest_height = height
            pad_bottom = max(pad_top, view - oldest_height)
            self.top_spacer.configure(height=pad_top)
            self.bottom_spacer.configure(height=pad_bottom)
            self.root.update_idletasks()
            total = self.list_frame.winfo_height()
            if total <= 0:
                return
            self.canvas.configure(
                scrollregion=(0, 0, self.canvas.winfo_width(), total)
            )
            # 最新在最上面 → "跟随最新"就是回到顶。
            self.canvas.yview_moveto(0.0)
        except Exception:
            pass

    def _refresh_count(self) -> None:
        total = len(self.cards)
        extra = f" · {self.unread} 未看" if self.unread else ""
        try:
            self.count_label.configure(text=f"{total} 条{extra}")
            self.title_label.configure(
                text="● 星云社 · 事件流" if self.unread else "星云社 · 事件流",
            )
        except Exception:
            pass

    # -- 卡片 ------------------------------------------------------------- #
    def _add_card(self, item: dict) -> None:
        """一张卡片 = 原来那个弹窗的内容，只是不再单独开窗。

        底板是一张抗锯齿圆角图（1px 描边，红 / 蓝两套）。**绝不用 `relief`**：
        Windows 的 `Frame(relief="solid")` 画出来是黑边，用户 2026-11-26 就是被
        那圈黑边劝退的（「太粗了 / 很多黑边」）。
        """
        tk = self.tk
        popup = item.get("popup") or {}
        title = str(popup.get("title") or "市场信息").strip()
        body = str(popup.get("thesis") or "").strip()
        source = str(popup.get("source") or "").strip()
        source_label = str(popup.get("sourceLabel") or "").strip()
        kind = str(popup.get("kind") or "").strip()
        chain = str(popup.get("chainLabel") or popup.get("chain") or "").strip()
        priority = str(popup.get("priority") or "").strip()
        tone = str(popup.get("alertTone") or "").strip().casefold()
        is_hot = tone == "red" or "热" in priority or "重点" in priority
        colors = tone_colors(is_hot)
        card_bg = colors["card"]
        event_at = int(item.get("activityAt") or popup.get("eventAt") or 0)
        # 老记录里有把「秒」当「毫秒」写进去的（Alpha 那条 1795682674），
        # 渲染侧兜一层：小于 1e11 的一律当秒处理，别显示成 1970。
        if 0 < event_at < 1e11:
            event_at *= 1000
        stamp = time.strftime("%H:%M:%S", time.localtime(event_at / 1000)) if event_at else "--:--:--"

        if self.empty_label is not None:
            try:
                self.empty_label.destroy()
            except Exception:
                pass
            self.empty_label = None

        card_width = PANEL_WIDTH - 2 - CARD_MARGIN_X * 2
        inner_width = card_width - CARD_PAD_X * 2
        wrap = inner_width

        # ⚠️ `inner` 的父**必须**是这张卡片 Canvas 本身。
        # 曾经的写法是先 `tk.Frame(self.list_frame)` 建内容、量高、再 `create_window()`
        # 搬进 Canvas —— 那是一次 reparent，而 Tk 不会重新布局已经算过 requested size
        # 的子控件，结果卡片里一个字的都看不见（探针里 ismapped 还是 1，极具迷惑性）。
        canvas = tk.Canvas(
            self.list_frame, width=card_width, height=1,
            bg=BG_PANEL, highlightthickness=0, bd=0, takefocus=0,
        )
        inner = tk.Frame(canvas, bg=card_bg)

        head = tk.Frame(inner, bg=card_bg)
        head.pack(fill="x")
        # 用 grid 而不是 pack(side="right")：pack 在空间不够时会直接把右侧那块
        # 画出父容器之外（第一版截图里「上市公告」就溢到卡片外面去了）。
        # grid 的列会被压缩，最坏是省略号式截断，但绝不会跑出卡片。
        head.columnconfigure(0, weight=1)
        tk.Label(
            head, text=stamp, bg=card_bg, fg=FG_KIND,
            font=("Microsoft YaHei UI", 9),
        ).grid(row=0, column=0, sticky="w")
        if chain:
            tk.Label(
                head, text=chain, bg=card_bg, fg=colors["chain"],
                font=("Microsoft YaHei UI", 9, "bold"),
            ).grid(row=0, column=1, sticky="w", padx=(7, 0))
        tail = " · ".join(part for part in (kind, priority) if part)
        if tail:
            tk.Label(
                head, text=tail, bg=card_bg, fg=FG_KIND,
                font=("Microsoft YaHei UI", 9), anchor="e",
            ).grid(row=0, column=2, sticky="e", padx=(8, 0))

        tk.Label(
            inner, text=title, bg=card_bg, fg=colors["title"],
            font=("Microsoft YaHei UI", 11, "bold"), anchor="w", justify="left",
            wraplength=wrap,
        ).pack(fill="x", pady=(5, 2))

        if body:
            tk.Label(
                inner, text=body, bg=card_bg, fg=FG_TEXT,
                font=("Microsoft YaHei UI", 9), anchor="w", justify="left",
                wraplength=wrap,
            ).pack(fill="x")

        footer = tk.Frame(inner, bg=card_bg)
        footer.pack(fill="x", pady=(8, 0))

        def pill(parent, text: str, fill: str, fg: str, *, hover_fill: str = "",
                 command=None, font=None):
            """画一个圆角胶囊。**不用 Label**：Label 的底色是矩形，浅色底上一眼就能
            看出那四个直角，正是用户说的「线条不平滑」。胶囊底图走同一套抗锯齿。
            """
            font = font or self._chip_font()
            pill_w = font.measure(text) + 20
            pill_h = 20
            holder = tk.Canvas(
                parent, width=pill_w, height=pill_h, bg=card_bg,
                highlightthickness=0, bd=0, takefocus=0,
            )
            normal = stream_ui.box_to_photo(pill_w, pill_h, pill_h // 2, fill)
            active = (
                stream_ui.box_to_photo(pill_w, pill_h, pill_h // 2, hover_fill)
                if hover_fill else None
            )
            holder.image_refs = (normal, active)  # 两张都要留引用
            image_item = (
                holder.create_image(0, 0, anchor="nw", image=normal) if normal else None
            )
            text_item = holder.create_text(
                pill_w // 2, pill_h // 2 + 1, text=text, fill=fg, font=font,
            )
            if command is not None:
                holder.configure(cursor="hand2")
                holder.bind("<Button-1>", lambda _event: command())

                def enter(_event=None) -> None:
                    if image_item is not None and active is not None:
                        holder.itemconfigure(image_item, image=active)
                    holder.itemconfigure(text_item, fill=FG_HEADER)

                def leave(_event=None) -> None:
                    if image_item is not None and normal is not None:
                        holder.itemconfigure(image_item, image=normal)
                    holder.itemconfigure(text_item, fill=fg)

                holder.bind("<Enter>", enter)
                holder.bind("<Leave>", leave)
            return holder

        if source_label:
            pill(footer, source_label, BG_SOURCE_CHIP, FG_CHIP).pack(
                side="left", padx=(0, 7),
            )
        if source:
            tk.Label(
                footer, text=source, bg=card_bg, fg=FG_MUTED,
                font=("Microsoft YaHei UI", 9),
            ).pack(side="left")

        def add_action(text: str, command) -> None:
            pill(
                footer, text, colors["action"], FG_ACTION,
                hover_fill=colors["action_active"], command=command,
            ).pack(side="right", padx=(6, 0))

        voice_text = str(popup.get("voiceText") or body or title)
        # 从右往左 pack，所以倒着写：叙事 最右，语音 最左（贴着来源）。
        add_action("叙事", lambda: self._open_narrative(popup, title))
        url = str(popup.get("url") or "").strip()
        if url:
            add_action("查看", lambda target=url: self._open_url(target))
        add_action("语音", lambda: self._speak(voice_text))

        # 先量高度，再按实际高度出圆角底图 —— 卡片高度由内容决定，不能写死。
        inner.update_idletasks()
        card_height = inner.winfo_reqheight() + CARD_PAD_Y * 2

        canvas.configure(height=card_height)
        # **新卡片插在最上面**（用户 2026-11-26：「最新的事件应该在最上面啊，按照时间
        # 倒排」）。pack 的顺序就是上下顺序，所以插到"当前最上面那张"之前；
        # 列表为空时退回 bottom_spacer（等价于紧跟在 top_spacer 之后）。
        if self.cards:
            canvas.pack(fill="x", padx=CARD_MARGIN_X, pady=(0, CARD_GAP),
                        before=self.cards[0]["card"])
        else:
            canvas.pack(fill="x", padx=CARD_MARGIN_X, pady=(0, CARD_GAP),
                        before=self.bottom_spacer)
        photo = stream_ui.box_to_photo(
            card_width, card_height, CARD_RADIUS, card_bg,
            outline=colors["border"], outline_width=1,
        )
        canvas.photo_ref = photo  # 持有引用，否则整张图变白
        if photo is not None:
            canvas.create_image(0, 0, anchor="nw", image=photo)
        canvas.create_window(
            CARD_PAD_X, CARD_PAD_Y, anchor="nw", window=inner, width=inner_width,
        )
        canvas.bind("<MouseWheel>", self._on_wheel)


        record = {"id": item.get("id"), "card": canvas, "popup": popup, "photo": photo}
        # `self.cards` 的**顺序 = 从上到下的显示顺序**：0 是最新（最上面），末尾最老。
        self.cards.insert(0, record)
        while len(self.cards) > MAX_CARDS:
            oldest = self.cards.pop()
            try:
                oldest["card"].destroy()
            except Exception:
                pass
        if not self.visible:
            self.unread += 1
        self._refresh_count()
        if self.auto_speak:
            self._speak(voice_text)

    # -- 动作 ------------------------------------------------------------- #
    def _speak(self, text: str) -> None:
        try:
            import desktop_alert
            desktop_alert.speak_text(text, True)
        except Exception as exc:
            log_line(f"speak failed: {exc}")

    def _open_url(self, url: str) -> None:
        try:
            import desktop_alert
            desktop_alert.open_external_url(url)
        except Exception as exc:
            log_line(f"open url failed: {exc}")

    def _open_narrative(self, popup: dict, title: str) -> None:
        text = str(popup.get("explanation") or "").strip()
        quote = str(popup.get("quoteText") or "").strip()
        contract = str(popup.get("contractAddress") or "").strip()
        chain = str(popup.get("chain") or "").strip()
        source_id = str(popup.get("sourceId") or "").strip()

        if not contract:
            self._show_text_window(title, text or quote or "这条消息没有合约地址，取不到叙事。")
            return

        # 先开窗再在后台问 —— 点一下按钮要立刻有反应，不能先卡 20 秒。
        window = self._show_text_window(title, text or "正在获取叙事…")
        body_widget = window["text"]

        def worker() -> None:
            result_text = ""
            try:
                import desktop_alert
                item = desktop_alert.fetch_token_ai_narrative(
                    self.port, source_id, chain, contract,
                    str(popup.get("eventFlowKey") or popup.get("key") or contract),
                )
                # 字段名照 desktop_alert 里那个 ✦ 按钮的口径：先 GIGA X，再币安 AI。
                narrative = str(
                    item.get("exchangeAiNarrative") or item.get("binanceAiNarrative") or ""
                ).strip()
                result_text = desktop_alert.chinese_ai_narrative(narrative, 1600) or narrative
                if not result_text:
                    reason = str(
                        item.get("exchangeAiNarrativeStatus")
                        or item.get("binanceAiNarrativeStatus")
                        or ""
                    ).strip()
                    result_text = "该标的暂无 AI 叙事" + (f"（{reason}）" if reason else "")
            except Exception as exc:
                result_text = f"叙事获取失败：{exc}"
            if quote:
                result_text = f"{result_text}\n\n———\n原文摘录：\n{quote}"
            self.root.after(0, lambda: self._fill_text_window(body_widget, result_text))

        threading.Thread(target=worker, daemon=True, name="xys-alert-stream-narrative").start()

    def _fill_text_window(self, body_widget, text: str) -> None:
        try:
            body_widget.configure(state="normal")
            body_widget.delete("1.0", "end")
            body_widget.insert("1.0", text)
            body_widget.configure(state="disabled")
        except Exception:
            pass

    def _show_text_window(self, title: str, text: str) -> dict:
        """叙事 / 原文的小窗口。配色沿用原弹窗的「叙事浮层」：
        ``#f8fcff`` 底 + 深灰字（见 desktop_alert.make_narrative_tooltip）。
        外壳同样是圆角图，不开 relief。
        """
        tk = self.tk
        x, y = self._current_position()
        width, height = 520, 360
        window = tk.Toplevel(self.root)
        window.title(title)
        window.overrideredirect(True)
        window.configure(bg=BG_PANEL)
        window.attributes("-topmost", True)
        # 默认贴在浮窗右边；右边放不下就翻到左边。副屏只有 1280 宽，浮窗靠右时
        # 520 宽的小窗会被整块推出屏外（点都点不到），所以最后还要按所在屏夹一次。
        gap = 12
        anchor_x, anchor_y = x + PANEL_WIDTH // 2, y + HEADER_H // 2
        open_x, open_y = x + PANEL_WIDTH + gap, y
        for left, top, right, bottom in screen_work_areas():
            if left <= anchor_x < right and top <= anchor_y < bottom:
                if open_x + width > right:
                    open_x = x - width - gap
                break
        open_x, open_y = confine_to_monitor(
            open_x, open_y, width, height, anchor_x, anchor_y
        )
        window.geometry(f"{width}x{height}+{int(open_x)}+{int(open_y)}")
        shell = tk.Canvas(window, bg=BG_PANEL, highlightthickness=0, bd=0)
        shell.pack(fill="both", expand=True)
        shell.create_rectangle(0, 0, width, 34, fill=BG_HEADER, outline="")
        shell.create_rectangle(
            0, 0, width - 1, height - 1, outline=BORDER_NORMAL, width=1,
        )
        shell.create_text(
            14, 17, text=title, anchor="w", fill=FG_HEADER,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        close_item = shell.create_text(
            width - 14, 17, text="×", anchor="e", fill=FG_ACTION,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        shell.tag_bind(close_item, "<Button-1>", lambda _event: window.destroy())

        frame = tk.Frame(shell, bg=BG_PANEL)
        shell.create_window(1, 34, anchor="nw", window=frame, width=width - 2, height=height - 36)
        body = tk.Text(
            frame, bg=BG_PANEL, fg=FG_TEXT, bd=0, wrap="word",
            font=("Microsoft YaHei UI", 9), padx=12, pady=10,
            insertbackground=FG_TEXT, highlightthickness=0,
        )
        body.pack(fill="both", expand=True)
        body.insert("1.0", text)
        body.configure(state="disabled")
        window.bind("<Escape>", lambda _event: window.destroy())
        return {"window": window, "text": body}

    def clear_cards(self) -> None:
        for card in self.cards:
            try:
                card["card"].destroy()
            except Exception:
                pass
        self.cards.clear()
        self.unread = 0
        self._show_empty_hint()
        self._refresh_count()

    # -- 收尾 ------------------------------------------------------------- #
    def quit(self) -> None:
        if self.stopping:
            return
        self.stopping = True
        try:
            save_position(*self._current_position())
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def _selfshot(self, path) -> None:
        """预览用：按窗口句柄自身截图。

        多显示器 + 缩放时 Tk 的 ``winfo_rootx/rooty``（逻辑像素）和
        ``ImageGrab.grab()``（虚拟屏物理像素）不是同一个坐标系，靠手算 bbox
        永远会截偏。``GetWindowRect`` 和 ImageGrab 同属物理像素，直接问它要。
        """
        try:
            from PIL import ImageGrab

            api = ctypes.windll.user32
            hwnd = api.GetAncestor(self.root.winfo_id(), 2)  # GA_ROOT

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long),
                ]

            rect = RECT()
            if not api.GetWindowRect(hwnd, ctypes.byref(rect)):
                raise RuntimeError("GetWindowRect failed")
            ImageGrab.grab(
                bbox=(rect.left, rect.top, rect.right, rect.bottom)
            ).save(str(path))
            log_line(
                f"selfshot {rect.left},{rect.top},{rect.right},{rect.bottom} -> {path}"
            )
        except Exception as exc:
            log_line(f"selfshot failed: {exc}")

    def run(self) -> int:
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            pass
        finally:
            self.stopping = True
        return 0


# --------------------------------------------------------------------------- #
# 拉起 / 自检
# --------------------------------------------------------------------------- #
def retire_stale_overlay(script: Path) -> list[int]:
    """让上一代服务拉起的、代码已经过期的浮窗退休。

    浮窗是独立进程（``server.py`` 退出它照活），并且靠 ``CreateMutexW`` 保证单实例。
    两者一叠加就有个坑：服务重启后新的 spawn 会被那个**老**窗口挡回去，用户看到的
    还是旧界面 —— 2026-11-26 改「最新在最上面」时就是这么白改了一轮（窗口 pid 比
    服务还老，日志里也没有新的 spawned）。

    只掐「启动时间早于本进程」的同脚本进程：刚被别人拉起的新窗口不动。
    位置写在 ``POSITION_FILE`` 里，重开后原地出现，不会跑丢。
    """
    try:
        import psutil
    except Exception:
        return []
    victims = []
    try:
        own_start = psutil.Process().create_time()
        target = script.resolve().as_posix().lower()
        for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
            try:
                cmdline = " ".join(proc.info.get("cmdline") or [])
                if "alert_stream_overlay.py" not in cmdline.lower():
                    continue
                # 同目录下的那份才认 —— 别的 checkout 里也有一份副本时别乱杀。
                if target not in cmdline.replace("\\", "/").lower():
                    continue
                if float(proc.info.get("create_time") or 0) >= own_start:
                    continue
                victims.append(proc)
            except Exception:
                continue
        if not victims:
            return []
        for proc in victims:
            try:
                proc.terminate()
            except Exception:
                pass
        # 必须等它真的退出：单实例互斥量是进程退出时才释放的，
        # 早一步 spawn，新窗口就直接被判「已存在」然后自杀。
        deadline = time.time() + 3.0
        while time.time() < deadline and any(p.is_running() for p in victims):
            time.sleep(0.1)
        for proc in victims:
            if proc.is_running():
                try:
                    proc.kill()
                except Exception:
                    pass
        time.sleep(0.15)
    except Exception as exc:
        # 别静默吞：这里曾因为 `str(script).resolve()` 写错而整个失效，
        # 结果就是「服务重启了、窗口还是老代码」，一点线索都没有。
        log_line(f"retire stale overlay failed: {exc}")
        return []
    pids = [p.pid for p in victims]
    log_line(f"retired {len(pids)} stale overlay(s): {pids}")
    return pids


def spawn_detached(*, root: Path | None = None, source: str = "server", port: int | None = None) -> dict:
    """由 server.py 启动时拉起浮窗（独立进程，`server.py` 退出它也能活）。

    **绝不抛异常**：它只是附加功能，失败只回 reason。
    """
    if os.name != "nt":
        return {"started": False, "reason": "only supported on Windows", "pid": None}
    if str(os.getenv("XINGYUN_ALERT_STREAM_OVERLAY", "1")).strip().lower() in {"0", "false", "off", "no"}:
        return {"started": False, "reason": "disabled by XINGYUN_ALERT_STREAM_OVERLAY", "pid": None}
    script = (root or Path(__file__).resolve().parent) / "alert_stream_overlay.py"
    if not script.is_file():
        return {"started": False, "reason": f"script not found: {script}", "pid": None}
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    launcher = str(pythonw) if pythonw.is_file() else "pythonw.exe"
    # 先让上一代的老窗口退休，否则单实例互斥量会把下面这个新进程挡掉，
    # 于是「改了半天界面却毫无变化」。
    retire_stale_overlay(script)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
    )
    env = dict(os.environ)
    env["XYS_ALERT_STREAM_SOURCE"] = source
    # 端口必须显式传过去：浮窗是独立进程，读不到 server.py 的 --port 参数。
    # 少了这一句，用非默认端口起服务时窗口会一直对着 8765 空转。
    if port:
        env["XYS_ALERT_STREAM_PORT"] = str(int(port))
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
    except Exception as exc:
        return {"started": False, "reason": f"spawn failed: {exc}", "pid": None}
    try:
        PID_FILE.write_text(str(child.pid), encoding="utf-8")
    except Exception:
        pass
    log_line(f"spawned by {source} pid={child.pid}")
    return {"started": True, "reason": "", "pid": child.pid}


def selfcheck(port: int) -> int:
    print(f"port            : {port}")
    print(f"pillow          : {'OK' if stream_ui.pillow_available() else 'MISSING（会退回直角）'}")
    try:
        payload = fetch_event_flow(port, after=None, limit=3)
    except Exception as exc:
        print(f"event-flow      : FAILED ({exc})")
        return 1
    print(f"event-flow      : OK cursor={payload.get('cursor')} total={payload.get('total')}")
    items = popup_items(payload)
    print(f"popup items     : {len(items)}")
    for item in items[:3]:
        popup = item.get("popup") or {}
        print(f"  - {popup.get('title')}  chain={popup.get('chainLabel')} url={bool(popup.get('url'))}")
    print(f"hotkey poll     : {'OK' if _user32() is not None else 'UNAVAILABLE'}")
    print(f"position file   : {POSITION_FILE}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="星云社事件流浮窗")
    parser.add_argument("--port", type=int, default=service_port())
    parser.add_argument("--hotkey", default=os.getenv("XYS_ALERT_STREAM_HOTKEY") or DEFAULT_HOTKEY)
    parser.add_argument("--reset-position", action="store_true")
    parser.add_argument("--selfcheck", action="store_true")
    parser.add_argument("--dump", action="store_true")
    parser.add_argument(
        "--preview", nargs="?", type=int, const=20, default=0, metavar="SECONDS",
        help="用样例数据渲染 N 秒（默认 20），只看版式和配色，不连服务",
    )
    args = parser.parse_args()

    if args.reset_position:
        try:
            POSITION_FILE.unlink()
        except Exception:
            pass
        print("position reset")
        return 0
    if args.selfcheck:
        return selfcheck(args.port)
    if args.dump:
        payload = fetch_event_flow(args.port, after=None, limit=10)
        for item in popup_items(payload):
            popup = item.get("popup") or {}
            print(f"[{popup.get('stage')}] {popup.get('title')} | {popup.get('thesis')}")
        return 0

    if args.preview:
        window = AlertStreamWindow(port=args.port, hotkey=args.hotkey, preview=True)
        shot = Path(tempfile.gettempdir()) / "xys-selfshot.png"
        window.root.after(2600, lambda: window._selfshot(shot))
        window.root.after(max(1, args.preview) * 1000, window.quit)
        return window.run()

    if not acquire_single_instance():
        log_line("another instance is already running")
        return 0
    try:
        window = AlertStreamWindow(port=args.port, hotkey=args.hotkey, position=load_position())
    except HotkeyError as exc:
        log_line(f"bad hotkey: {exc}")
        print(f"hotkey error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        log_line(f"window init failed: {exc}")
        print(f"window init failed: {exc}", file=sys.stderr)
        return 2
    atexit.register(lambda: PID_FILE.unlink(missing_ok=True))
    return window.run()


if __name__ == "__main__":
    raise SystemExit(main())
