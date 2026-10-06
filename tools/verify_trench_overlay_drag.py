#!/usr/bin/env python3
"""战壕悬浮窗「左键拖动」的真机验收。

单元测试（tests/test_trench_overlay.py）覆盖的是状态机；这里补的是单元测试够不到的
那一段：真实 win32 轮询、真实 overrideredirect 窗口位移、真实注入的左键拖动，以及
两条只有真机才验得出的边界：

  * 默认模式**不按 Alt** 直接左键就能拖（窗口不能带 WS_EX_TRANSPARENT，否则收不到鼠标）；
  * 单击（位移 < 4px 阈值）不能挪面板 —— 光电鼠标单击随手就会抖 1~3px；
  * 穿透模式（``--click-through``）下不按 Alt 拖不动，按 Alt 才拖得动。

用法：
  python tools/verify_trench_overlay_drag.py           # 自建面板跑 17 项检查
  python tools/verify_trench_overlay_drag.py --live    # 对正在运行的生产面板拖走再拖回

-------------------------------------------------------------------------------
两个「异步输入」陷阱（改这个脚本时务必保留写法，别再踩）
-------------------------------------------------------------------------------
1. ``mouse_event`` 是异步投递的。发完立刻 ``GetAsyncKeyState`` 可能还没反映，
   于是「左键已按下」偶发判定失败、拖动根本没起手。必须轮询等状态生效。
2. ``SetCursorPos`` 同样是异步的，紧接着 ``GetCursorPos`` 会读到**移动途中**的
   坐标。拖动位移 = 当前光标 − 起手光标，起手点读偏 31px，整块面板就偏 31px。
   必须等光标真的到位再开始拖。

这两点会让验收结果随机浮动 ±1~31px，看起来像「面板逻辑不稳定」，其实是注入时序。
"""
from __future__ import annotations

import argparse
import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import trench_overlay as ov  # noqa: E402

USER32 = ctypes.windll.user32
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020

# 拖动位移的断言容差。#1 的残留抖动最多到 3px。
TOLERANCE = 3
# 单击抖动用的位移，必须小于 trench_overlay.DRAG_THRESHOLD_PX。
JITTER_PX = 2

_RESULTS: list[tuple[str, bool]] = []


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


def check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(ok)))
    print(("PASS  " if ok else "FAIL  ") + name + (("  |  " + detail) if detail else ""))


def wait_until(predicate, timeout: float = 1.0, interval: float = 0.02) -> bool:
    """等实时按键状态生效。见文件头陷阱 #1。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def wait_cursor(point: tuple[int, int], tol: int = 2, timeout: float = 1.5) -> bool:
    """等光标真的到达目标点。见文件头陷阱 #2。"""
    def arrived() -> bool:
        current = ov.cursor_position()
        return bool(current) and abs(current[0] - point[0]) <= tol and abs(current[1] - point[1]) <= tol

    return wait_until(arrived, timeout=timeout, interval=0.01)


def move_cursor(point: tuple[int, int]) -> None:
    USER32.SetCursorPos(*point)
    wait_cursor(point)


def press_left_until_seen(attempts: int = 4) -> bool:
    for _ in range(attempts):
        USER32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        if wait_until(ov.left_button_down, 0.3):
            return True
    return False


def release_all() -> None:
    USER32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    USER32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)


def hold_alt() -> None:
    USER32.keybd_event(VK_MENU, 0, 0, 0)
    time.sleep(0.08)


def panel_hwnd(root) -> int:
    """面板真正的窗口句柄：Tk 给的是子窗口，扩展样式挂在它的父窗口上。"""
    hwnd = int(root.winfo_id())
    parent = USER32.GetParent(hwnd)
    return int(parent) if parent else hwnd


def ex_style_of(root) -> int:
    return int(USER32.GetWindowLongW(panel_hwnd(root), GWL_EXSTYLE))


def find_panel_rect() -> tuple[int, int, int, int] | None:
    hwnd = USER32.FindWindowW(None, "战壕悬浮窗")
    if not hwnd:
        return None
    rect = RECT()
    USER32.GetWindowRect(hwnd, ctypes.byref(rect))
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def build_preview_overlay(*, click_through: bool):
    feed = ov.TrenchFeed(8765)
    with feed._lock:
        feed._cards = ov.preview_cards()
        feed._fetched_at = time.time()
        feed._error = ""
    inst = ov.TrenchOverlay(feed, hotkey="alt+q", click_through=click_through)
    inst._build()
    inst.show()
    inst.root.update()
    return inst


def drag_by(inst, frm: tuple[int, int], delta: tuple[int, int], *, steps=((60, 30), (120, 60), (180, 120))):
    """从 frm 起手，按住左键把光标分步推过去。返回 (起手已记录, 拖动中)。"""
    move_cursor(frm)
    pressed = press_left_until_seen()
    inst._poll_pointer()                 # 起手：只记下起手点
    inst.root.update()
    armed = inst._press_origin is not None
    for dx, dy in steps:
        move_cursor((frm[0] + dx, frm[1] + dy))
        inst._poll_pointer()
        inst.root.update()
    dragging = inst._dragging
    release_all()
    time.sleep(0.05)
    inst._poll_pointer()
    inst.root.update()
    return pressed, armed, dragging


# --------------------------------------------------------------------------- #
# 模式一：自建两个面板（默认 / 穿透），17 项全查
# --------------------------------------------------------------------------- #
def verify_preview() -> None:
    backup = ov.POSITION_FILE.read_text(encoding="utf-8") if ov.POSITION_FILE.exists() else None
    ov.clear_saved_position()
    origin_cursor = ov.cursor_position()

    inst = build_preview_overlay(click_through=False)
    through = None
    try:
        root = inst.root
        check("cursor_position 可用", ov.cursor_position() is not None, str(ov.cursor_position()))
        check("virtual_screen 可用", ov.virtual_screen() is not None, str(ov.virtual_screen()))
        check("work_area 可用", ov.work_area() is not None, str(ov.work_area()))
        default = inst._default_position()
        check(
            "首次落位=工作区左上+边距（不是先闪在 0,0）",
            (root.winfo_x(), root.winfo_y()) == default,
            f"实际 {root.winfo_x()},{root.winfo_y()} 期望 {default[0]},{default[1]}",
        )
        check(
            "默认模式窗口不带 WS_EX_TRANSPARENT（否则收不到鼠标，左键拖不动）",
            not ex_style_of(root) & WS_EX_TRANSPARENT,
            f"exStyle=0x{ex_style_of(root):08X}",
        )

        width, height = inst._panel_size
        inst._move_panel_to(500, 120)
        root.update()
        x0, y0 = root.winfo_x(), root.winfo_y()
        check(
            "_move_panel_to 真的移动了窗口",
            (x0, y0) == ov.clamp_position(500, 120, width, height),
            f"窗口 {x0},{y0} / 面板 {width}x{height}",
        )
        check("拖动前没有位置文件", not ov.POSITION_FILE.exists())

        # --- 单击（位移 < 阈值）不能挪面板 ---
        before_click = (x0, y0)
        grab = (x0 + 40, y0 + 20)
        move_cursor(grab)
        check("左键已按下（left_button_down 读到）", press_left_until_seen())
        inst._poll_pointer()                       # 记下起手点
        root.update()
        move_cursor((grab[0] + JITTER_PX, grab[1] + 1))   # 只抖 2px
        inst._poll_pointer()
        release_all()
        time.sleep(0.05)
        inst._poll_pointer()
        root.update()
        check(
            f"单击（位移 {JITTER_PX}px < 阈值）不会挪动面板",
            (root.winfo_x(), root.winfo_y()) == before_click,
            f"实际 {root.winfo_x()},{root.winfo_y()} 期望 {before_click}",
        )
        check("单击不会写位置文件", not ov.POSITION_FILE.exists())

        # --- 不按 Alt，直接左键拖动 ---
        check("此刻 Alt 确实没按下", not ov.modifiers_down(inst.modifier_vks))
        start = (x0 + 40, y0 + 20)
        pressed, armed, entered = drag_by(
            inst, start, (180, 120),
            steps=((60, 30), (120, 60), (180, 120)),
        )
        check("按下后已记下起手点", pressed and armed)
        check("位移超过阈值后进入拖动（不按 Alt）", entered)

        x1, y1 = root.winfo_x(), root.winfo_y()
        expected = ov.clamp_position(x0 + 180, y0 + 120, width, height)
        check(
            "窗口跟随光标位移 180,120",
            abs(x1 - expected[0]) <= TOLERANCE and abs(y1 - expected[1]) <= TOLERANCE,
            f"实际 {x1},{y1} 期望 {expected}",
        )
        check("松手后结束拖动", not inst._dragging)
        check("位置已落盘", ov.read_saved_position() == (x1, y1), str(ov.read_saved_position()))

        # --- 穿透模式：左键必须穿给下面的窗口，拖动退回 Alt+左键 ---
        ov.clear_saved_position()
        through = build_preview_overlay(click_through=True)
        troot = through.root
        check(
            "穿透模式窗口带 WS_EX_TRANSPARENT",
            bool(ex_style_of(troot) & WS_EX_TRANSPARENT),
            f"exStyle=0x{ex_style_of(troot):08X}",
        )
        through._move_panel_to(500, 120)
        troot.update()
        tx, ty = troot.winfo_x(), troot.winfo_y()
        tgrab = (tx + 40, ty + 20)

        # 不按 Alt：拖了也不该动
        drag_by(through, tgrab, (120, 80), steps=((60, 40), (120, 80)))
        check(
            "穿透模式下不按 Alt 拖不动",
            (troot.winfo_x(), troot.winfo_y()) == (tx, ty),
            f"实际 {troot.winfo_x()},{troot.winfo_y()} 期望 {tx},{ty}",
        )

        # 按住 Alt：应该能拖
        hold_alt()
        check("Alt 已按下（modifiers_down 读到）", ov.modifiers_down(through.modifier_vks))
        drag_by(through, tgrab, (120, 80), steps=((60, 40), (120, 80)))
        moved = (troot.winfo_x(), troot.winfo_y())
        wanted = ov.clamp_position(tx + 120, ty + 80, *through._panel_size)
        check(
            "穿透模式下按住 Alt 能拖动 +120,+80",
            abs(moved[0] - wanted[0]) <= TOLERANCE and abs(moved[1] - wanted[1]) <= TOLERANCE,
            f"实际 {moved} 期望 {wanted}",
        )
    finally:
        for instance in (inst, through):
            if instance is None:
                continue
            try:
                instance.root.destroy()
            except Exception:
                pass
        release_all()
        if origin_cursor:
            USER32.SetCursorPos(*origin_cursor)
        if backup is None:
            ov.clear_saved_position()
        else:
            ov.POSITION_FILE.write_text(backup, encoding="utf-8")


# --------------------------------------------------------------------------- #
# 模式二：对正在运行的生产面板拖走再拖回（面板自己的轮询循环负责位移）
# --------------------------------------------------------------------------- #
def drag_live(frm: tuple[int, int], to: tuple[int, int], steps: int = 4) -> None:
    """默认模式：不按 Alt，直接左键拖。"""
    move_cursor(frm)
    time.sleep(0.15)
    USER32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.35)                      # 等面板 40ms 轮询发现起手
    for index in range(1, steps + 1):
        move_cursor((frm[0] + (to[0] - frm[0]) * index // steps,
                     frm[1] + (to[1] - frm[1]) * index // steps))
        time.sleep(0.12)
    release_all()
    time.sleep(0.35)


def wait_panel_at(expect: tuple[int, int], timeout: float = 2.0) -> tuple[int, int, int, int] | None:
    deadline = time.monotonic() + timeout
    rect = find_panel_rect()
    while time.monotonic() < deadline:
        rect = find_panel_rect()
        if rect and abs(rect[0] - expect[0]) <= TOLERANCE and abs(rect[1] - expect[1]) <= TOLERANCE:
            return rect
        time.sleep(0.03)
    return rect


def verify_live() -> None:
    rect = find_panel_rect()
    if rect is None:
        check("找到正在运行的面板窗口", False, "没有标题为「战壕悬浮窗」的窗口，面板没在跑")
        return
    check("找到正在运行的面板窗口", True, str(rect))
    sx, sy = rect[0], rect[1]
    origin_cursor = ov.cursor_position()
    try:
        grab = (sx + 60, sy + 24)
        drag_live(grab, (grab[0] + 120, grab[1] + 80))
        moved = wait_panel_at((sx + 120, sy + 80))
        check(
            "生产面板跟随拖动 +120,+80",
            bool(moved) and abs(moved[0] - (sx + 120)) <= TOLERANCE and abs(moved[1] - (sy + 80)) <= TOLERANCE,
            str(moved),
        )
        if not moved:
            return
        grab2 = (moved[0] + 60, moved[1] + 24)
        drag_live(grab2, (grab2[0] - 120, grab2[1] - 80))
        back = wait_panel_at((sx, sy))
        check(
            "生产面板可拖回原位",
            bool(back) and abs(back[0] - sx) <= TOLERANCE and abs(back[1] - sy) <= TOLERANCE,
            str(back),
        )
    finally:
        release_all()
        if origin_cursor:
            USER32.SetCursorPos(*origin_cursor)
        ov.clear_saved_position()


def main() -> int:
    parser = argparse.ArgumentParser(description="战壕悬浮窗拖动功能的真机验收")
    parser.add_argument("--live", action="store_true",
                        help="对正在运行的生产面板做验证（拖走再拖回），不新建面板")
    args = parser.parse_args()

    if args.live:
        verify_live()
    else:
        verify_preview()

    failed = [name for name, ok in _RESULTS if not ok]
    print(f"SUMMARY total={len(_RESULTS)} failed={len(failed)} {failed if failed else ''}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
