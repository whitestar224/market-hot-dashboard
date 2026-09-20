from __future__ import annotations

import argparse
import base64
import json
import queue
import os
import re
import subprocess
import sys
import tempfile
import threading
import textwrap
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from alert_delivery import AlertDeliveryStore, VisibleTimer
from alert_audio import speech_script


def popup_is_uncovered(root) -> bool:
    """Mapped is insufficient: another topmost popup can cover the entire window."""
    if not root.winfo_viewable():
        return False
    if os.name != 'nt':
        return True
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.WindowFromPoint.argtypes = [wintypes.POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        own = user32.GetAncestor(root.winfo_id(), 2)
        # Require both the title and body to be exposed, not merely the window edge.
        for y_fraction in (0.22, 0.55):
            point = wintypes.POINT(root.winfo_rootx() + root.winfo_width() // 2,
                                  root.winfo_rooty() + int(root.winfo_height() * y_fraction))
            top = user32.GetAncestor(user32.WindowFromPoint(point), 2)
            if top != own:
                return False
        return True
    except Exception:
        return False


class PopupReceipts:
    """Database IO never blocks Tk. A terminal receipt drains before process exit."""
    def __init__(self, metadata):
        self.metadata = metadata if isinstance(metadata, dict) else {}
        self.pending = queue.Queue(maxsize=8)
        self.worker = None
        if self.metadata.get('db') and self.metadata.get('token'):
            self.worker = threading.Thread(target=self.run, daemon=True)
            self.worker.start()

    def send(self, stage, visible_ms):
        if not self.worker:
            return
        try:
            self.pending.put_nowait((stage, visible_ms))
        except queue.Full:
            # Drop an old heartbeat, never block the close button.
            try:
                self.pending.get_nowait()
            except queue.Empty:
                pass
            self.pending.put_nowait((stage, visible_ms))

    def run(self):
        store = AlertDeliveryStore(self.metadata['db'])
        while True:
            stage, visible_ms = self.pending.get()
            for attempt in range(3):
                try:
                    store.receipt(int(self.metadata['id']), self.metadata['token'], stage, visible_ms)
                    break
                except Exception:
                    if attempt < 2:
                        time.sleep(0.15)
            if stage in {'closed', 'read'}:
                return

    def finish(self):
        if self.worker:
            self.worker.join(timeout=3)


ROOT = Path(__file__).resolve().parent
LOGO_PATH = ROOT / "assets" / "xingyunshe-logo-transparent.png"
AUTO_CLOSE_MS = max(
    60 * 1000,
    int(float(os.getenv("XINGYUN_DESKTOP_ALERT_AUTO_CLOSE_SECONDS", "120") or "120") * 1000),
)
MIN_AUTO_CLOSE_MS = 60 * 1000
MAX_AUTO_CLOSE_MS = 2 * 60 * 60 * 1000


def clamp_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def load_payload(encoded: str) -> dict:
    try:
        raw = base64.b64decode(encoded.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def time_label(value: object) -> str:
    try:
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric = numeric / 1000
        if numeric > 0:
            return datetime.fromtimestamp(numeric).strftime("%H:%M")
    except Exception:
        pass
    return datetime.now().strftime("%H:%M")


def copy_text_to_clipboard(root, value: object) -> bool:
    """Copy synchronously so a following navigation/close cannot lose the CA."""
    text = str(value or "").strip()
    if not text:
        return False
    for attempt in range(4):
        try:
            root.clipboard_clear()
            root.clipboard_append(text)
            root.update()
            return True
        except Exception:
            if attempt < 3:
                time.sleep(0.025 * (attempt + 1))
    return False


def play_sound(enabled: bool) -> None:
    speak_text("", enabled)


def speak_text(value: object, enabled: bool) -> None:
    text = clamp_text(value, 160)
    if not enabled or os.name != "nt":
        return

    def worker() -> None:
        # Use the Windows speech engine so desktop alerts do not require an
        # additional Python package or an online text-to-speech service.
        script = speech_script(os.getpid())
        try:
            time.sleep(0.35)
            subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                input=text,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=155,  # Up to 120s queue wait + bounded 160-character speech.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True, name="xingyun-alert-speech").start()


def wrapped(value: str, width: int) -> str:
    return "\n".join(
        textwrap.wrap(
            value,
            width=width,
            break_long_words=True,
            break_on_hyphens=True,
            replace_whitespace=False,
        )
    )


def soft_wrap_text(value: str, ascii_width: int = 22) -> str:
    text = str(value or "")

    def split_ascii(match: re.Match[str]) -> str:
        token = match.group(0)
        return "\n".join(
            textwrap.wrap(
                token,
                width=ascii_width,
                break_long_words=True,
                break_on_hyphens=True,
                replace_whitespace=False,
            )
        )

    return re.sub(r"[A-Za-z0-9_./:@#-]{42,}", split_ascii, text)


def limited_lines(value: str, max_lines: int) -> str:
    lines = [line for line in str(value or "").splitlines() if line.strip()]
    if len(lines) <= max_lines:
        return "\n".join(lines)
    clipped = lines[:max_lines]
    clipped[-1] = clipped[-1].rstrip(" .。；;，,") + "..."
    return "\n".join(clipped)


def looks_english(value: object) -> bool:
    text = " ".join(str(value or "").split())
    if len(text) < 12:
        return False
    latin = len(re.findall(r"[A-Za-z]", text))
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    words = len(re.findall(r"[A-Za-z]{2,}", text))
    return latin >= 18 and words >= 5 and latin >= max(18, cjk * 4)


def fetch_translation(endpoint: str, text: str) -> str:
    data = json.dumps({"text": text, "clientMode": "desktop-alert"}).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "XingyunSociety/desktop-alert"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.loads(response.read(200_000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read(200_000).decode("utf-8"))
            message = str(payload.get("error") or payload.get("message") or exc)
        except Exception:
            message = str(exc)
        raise RuntimeError(message) from exc
    if not payload.get("ok"):
        raise RuntimeError(str(payload.get("error") or "translation failed"))
    return clamp_text(payload.get("translation") or "", 360)


def post_price_watch_confirmation(endpoint: str, symbol: str, episode: int) -> dict:
    data = json.dumps(
        {"action": "confirm", "symbol": symbol, "episode": episode},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "XingyunSociety/desktop-alert"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read(8_000_000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read(8_000_000).decode("utf-8"))
            message = str(payload.get("error") or payload.get("message") or exc)
        except Exception:
            message = str(exc)
        raise RuntimeError(message) from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise RuntimeError(str(payload.get("error") if isinstance(payload, dict) else "确认失败"))
    return payload


def post_newsflash_explanation_open(endpoint: str, explanation_key: str) -> dict:
    data = json.dumps({"id": explanation_key}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "XingyunSociety/desktop-alert"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read(200_000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read(200_000).decode("utf-8"))
            message = str(payload.get("error") or payload.get("message") or exc)
        except Exception:
            message = str(exc)
        raise RuntimeError(message) from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise RuntimeError(str(payload.get("error") if isinstance(payload, dict) else "解释窗口未能打开"))
    return payload


def post_price_watch_exclusion(
    endpoint: str,
    symbol: str,
    action: str = "exclude_prior_high",
) -> dict:
    safe_action = str(action or "").strip().lower()
    if safe_action not in {"exclude_prior_high", "exclude_structure", "temporary_exclude"}:
        safe_action = "exclude_prior_high"
    data = json.dumps(
        {"action": safe_action, "symbol": symbol},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "XingyunSociety/desktop-alert"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read(8_000_000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read(8_000_000).decode("utf-8"))
            message = str(payload.get("error") or payload.get("message") or exc)
        except Exception:
            message = str(exc)
        raise RuntimeError(message) from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        raise RuntimeError(str(payload.get("error") if isinstance(payload, dict) else "剔除失败"))
    return payload


def payload_image_path(payload: dict) -> tuple[Path | None, Path | None]:
    image_path = str(payload.get("imagePath") or "").strip()
    if image_path:
        path = Path(image_path)
        if path.exists():
            return path, None

    image_url = str(payload.get("imageUrl") or "").strip()
    if image_url.lower().startswith("data:image/"):
        try:
            header, encoded = image_url.split(",", 1)
            suffix = ".png"
            if "jpeg" in header.lower() or "jpg" in header.lower():
                suffix = ".jpg"
            elif "gif" in header.lower():
                suffix = ".gif"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            with tmp:
                tmp.write(base64.b64decode(encoded))
            path = Path(tmp.name)
            return path, path
        except Exception:
            return None, None
    if not image_url.lower().startswith(("http://", "https://")):
        return None, None
    try:
        request = urllib.request.Request(
            image_url,
            headers={"User-Agent": "Mozilla/5.0 XingyunSociety/1.0"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            data = response.read(900_000)
        suffix = ".png"
        content_type = response.headers.get("content-type", "").lower()
        if "gif" in content_type:
            suffix = ".gif"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        with tmp:
            tmp.write(data)
        path = Path(tmp.name)
        return path, path
    except Exception:
        return None, None


def load_tk_image(image_path: Path, max_size: int):
    try:
        from PIL import Image, ImageTk

        image = Image.open(image_path)
        image.thumbnail((max_size, max_size))
        if image.mode not in {"RGB", "RGBA"}:
            image = image.convert("RGBA")
        return ImageTk.PhotoImage(image)
    except Exception:
        pass

    try:
        import tkinter as tk

        image = tk.PhotoImage(file=str(image_path))
        factor = max(
            1,
            max(
                (image.width() + max_size - 1) // max_size,
                (image.height() + max_size - 1) // max_size,
            ),
        )
        return image.subsample(factor, factor)
    except Exception:
        exc = sys.exc_info()[1]
        print(f"alert image unavailable: {exc}", file=sys.stderr)
        return None


def work_area(root):
    if os.name != "nt":
        return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()
    try:
        import ctypes

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        rect = RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def popup_position(
    bounds: tuple[int, int, int, int],
    width: int,
    height: int,
    slot: int,
) -> tuple[int, int]:
    """Tile concurrent popups from bottom-left so every fresh alert stays visible."""
    left, top_edge, right, bottom = bounds
    edge_gap = 18
    tile_gap = 10
    usable_height = max(height, bottom - top_edge - edge_gap * 2)
    rows_per_column = max(1, usable_height // max(1, height + tile_gap))
    row = max(0, int(slot)) % rows_per_column
    column = max(0, int(slot)) // rows_per_column
    x = left + edge_gap + column * (width + tile_gap)
    x = min(max(left + 8, x), max(left + 8, right - width - 8))
    y = bottom - height - edge_gap - row * (height + tile_gap)
    y = max(top_edge + 8, y)
    return x, y


def popup_auto_close_ms(payload: dict) -> int:
    try:
        requested = int(float(payload.get("autoCloseMs") or 0))
    except (TypeError, ValueError):
        requested = 0
    if requested <= 0:
        return AUTO_CLOSE_MS
    return min(MAX_AUTO_CLOSE_MS, max(MIN_AUTO_CLOSE_MS, requested))


def popup_pointer_inside(root) -> bool:
    """Keep an alert open while the user is reading or operating it."""
    try:
        pointer_x = int(root.winfo_pointerx())
        pointer_y = int(root.winfo_pointery())
        left = int(root.winfo_rootx())
        top = int(root.winfo_rooty())
        return (
            left <= pointer_x < left + int(root.winfo_width())
            and top <= pointer_y < top + int(root.winfo_height())
        )
    except Exception:
        return False


def show_popup(payload: dict, slot: int) -> int:
    try:
        import tkinter as tk
    except Exception as exc:
        print(f"tkinter unavailable: {exc}", file=sys.stderr)
        return 2

    title = clamp_text(payload.get("title") or "市场信息", 58)
    body = clamp_text(payload.get("body") or "", 260)
    kind = clamp_text(payload.get("kind") or "市场信息", 18)
    source = clamp_text(payload.get("source") or "星云社", 30)
    source_label = clamp_text(payload.get("sourceLabel") or "NX", 5)
    priority = clamp_text(payload.get("priority") or "实时", 8)
    url = str(payload.get("url") or "").strip()
    contract_address = str(payload.get("contractAddress") or payload.get("contract") or "").strip()
    translation_text = clamp_text(payload.get("translationText") or "", 1800)
    translate_endpoint = str(payload.get("translateEndpoint") or "").strip()
    can_translate = bool(translation_text and translate_endpoint and looks_english(translation_text))
    confirm_endpoint = str(payload.get("confirmEndpoint") or "").strip()
    confirm_symbol = clamp_text(payload.get("confirmSymbol") or "", 30)
    try:
        confirm_episode = int(payload.get("confirmEpisode") or 0)
    except (TypeError, ValueError):
        confirm_episode = 0
    confirm_label = clamp_text(payload.get("confirmLabel") or "确认首次", 8)
    can_confirm = bool(confirm_endpoint and confirm_symbol and confirm_episode > 0)
    exclude_endpoint = str(payload.get("excludeEndpoint") or "").strip()
    exclude_symbol = clamp_text(payload.get("excludeSymbol") or "", 30)
    exclude_label = clamp_text(payload.get("excludeLabel") or "剔除前高", 8)
    exclude_action = str(payload.get("excludeAction") or "exclude_prior_high").strip().lower()
    if exclude_action not in {"exclude_prior_high", "exclude_structure"}:
        exclude_action = "exclude_prior_high"
    can_exclude = bool(exclude_endpoint and exclude_symbol)
    alert_time = time_label(payload.get("time"))
    sound = payload.get("sound") is not False
    speech = payload.get("speech") or ""
    is_red = str(payload.get("alertTone") or "").strip().casefold() == "red"
    is_hot = is_red or "高热" in priority or "重点" in priority or "高热" in title
    border_bg = "#df3f35" if is_red else "#9dcfe8"
    top_bg = "#ffd9d5" if is_red else "#c9edff"
    action_bg = "#ffe9e6" if is_red else "#d7f0fb"
    action_active_bg = "#fff4f2" if is_red else "#eef9ff"
    content_bg = "#fff8f7" if is_red else "#f8fcff"
    explanation_key = str(payload.get("explanationKey") or "")
    can_explain = bool(re.fullmatch(r"[a-f0-9]{40}", explanation_key))
    explanation_open_endpoint = str(payload.get("explanationOpenEndpoint") or "").strip()
    image_path, temp_image_path = payload_image_path(payload)
    title_text = limited_lines(soft_wrap_text(title, 24), 2)
    body_text = soft_wrap_text(body, 42)

    root = tk.Tk()
    root.title("星云社快讯")
    root.overrideredirect(True)
    root.configure(bg=border_bg)
    root.attributes("-topmost", True)
    try:
      root.attributes("-toolwindow", True)
    except Exception:
      pass
    try:
        root.attributes("-alpha", 0.0)
    except Exception:
        pass

    width = 374
    height = 420 if image_path else 212
    x, y = popup_position(work_area(root), width, height, slot)
    root.geometry(f"{width}x{height}+{x}+{y}")

    outer = tk.Frame(root, bg=border_bg, bd=2 if is_red else 1, relief="solid")
    outer.pack(fill="both", expand=True)

    top = tk.Frame(outer, bg=top_bg)
    top.pack(fill="x")

    logo_img = None
    if LOGO_PATH.exists():
        try:
            logo_img = tk.PhotoImage(file=str(LOGO_PATH))
            factor = max(1, int(max(logo_img.width() / 24, logo_img.height() / 24)))
            logo_img = logo_img.subsample(factor, factor)
            logo = tk.Label(top, image=logo_img, bg=top_bg, bd=0)
        except Exception:
            logo = tk.Label(top, text="NX", bg=top_bg, fg="#b5221c" if is_red else "#0b76ff", font=("Microsoft YaHei UI", 9, "bold"))
    else:
        logo = tk.Label(top, text="NX", bg=top_bg, fg="#b5221c" if is_red else "#0b76ff", font=("Microsoft YaHei UI", 9, "bold"))
    logo.pack(side="left", padx=(16, 7), pady=(13, 7))

    brand = tk.Label(
        top,
        text=f"星云社快讯  {alert_time}",
        bg=top_bg,
        fg="#9f1d18" if is_red else "#344047",
        font=("Microsoft YaHei UI", 11, "bold"),
    )
    brand.pack(side="left", pady=(13, 7))

    receipts = PopupReceipts(payload.get('_delivery'))
    visible_timer = VisibleTimer()
    closing = False
    exclusion_state = {"running": False, "mode": ""}

    def close(read: bool = True, force: bool = False) -> None:
        nonlocal closing
        if closing or (exclusion_state["running"] and not force):
            return
        closing = True
        receipts.send('read' if read else 'closed', visible_timer.milliseconds)
        root.destroy()

    copy_btn = None

    def copy_contract() -> bool:
        copied = copy_text_to_clipboard(root, contract_address)
        if copy_btn is not None:
            copy_btn.configure(
                text="已复制" if copied else "复制失败",
                fg="#075669" if copied else "#a43a32",
            )
            root.after(
                1400,
                lambda: copy_btn.configure(text="复制CA", fg="#9f1d18" if is_red else "#075669"),
            )
        return copied

    def open_url() -> None:
        if contract_address:
            copy_contract()
        if url:
            webbrowser.open(url)
        close()

    close_btn = tk.Button(
        top,
        text="x",
        command=close,
        bg=action_bg,
        fg="#455660",
        activebackground=action_active_bg,
        relief="flat",
        width=3,
        height=1,
        font=("Microsoft YaHei UI", 9, "bold"),
    )
    close_btn.pack(side="right", padx=(4, 14), pady=(12, 7))

    if url:
        view_btn = tk.Button(
            top,
            text="查看",
            command=open_url,
            bg=action_bg,
            fg="#455660",
            activebackground=action_active_bg,
            relief="flat",
            width=5,
            height=1,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        view_btn.pack(side="right", padx=(4, 0), pady=(12, 7))

    if contract_address:
        copy_btn = tk.Button(
            top,
            text="复制CA",
            command=copy_contract,
            bg=action_bg,
            fg="#9f1d18" if is_red else "#075669",
            activebackground=action_active_bg,
            relief="flat",
            width=7,
            height=1,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        copy_btn.pack(side="right", padx=(4, 0), pady=(12, 7))

    if can_explain:
        def open_explanations():
            try:
                if explanation_open_endpoint:
                    explanation_button.configure(text="正在打开", state="disabled")

                    def open_worker():
                        try:
                            post_newsflash_explanation_open(explanation_open_endpoint, explanation_key)
                        except Exception:
                            root.after(0, lambda: explanation_button.configure(text="重试打开", state="normal"))
                            return
                        root.after(0, close)

                    threading.Thread(target=open_worker, daemon=True).start()
                    return
                port = int(payload.get("explanationPort") or 8765)
                if not 1 <= port <= 65535:
                    raise ValueError("invalid port")
                explanation_title = str((payload.get('explanationContext') or {}).get('symbol') or payload.get('title') or '')[:120]
                from news_trade_reader import reader_log
                reader_log(explanation_key, 'explanation_clicked', title=explanation_title, port=port)
                subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--explanation-title", explanation_title, "--explanations", explanation_key,
                                  "--port", str(port)], cwd=str(ROOT), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), close_fds=True)
                close()
            except Exception:
                explanation_button.configure(text="重试打开", state="normal")
        explanation_button = tk.Button(top, text="解释推文", command=open_explanations,
            bg=action_bg, fg="#9f1d18" if is_red else "#075669", activebackground=action_active_bg, relief="flat", cursor="hand2",
            font=("Microsoft YaHei UI", 9, "bold"), width=8, height=1)
        explanation_button.pack(side="right", padx=(4, 0), pady=(12, 7))

    translate_btn = None
    if can_translate:
        translate_btn = tk.Button(
            top,
            text="翻译",
            command=lambda: request_translation(),
            bg=action_bg,
            fg="#455660",
            activebackground=action_active_bg,
            relief="flat",
            width=5,
            height=1,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        translate_btn.pack(side="right", padx=(4, 0), pady=(12, 7))

    confirm_btn = None
    if can_confirm:
        confirm_btn = tk.Button(
            top,
            text=confirm_label,
            command=lambda: request_confirmation(),
            bg="#f6bb48",
            fg="#20272b",
            activebackground="#ffd06a",
            relief="flat",
            width=7,
            height=1,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        confirm_btn.pack(side="right", padx=(4, 0), pady=(12, 7))

    content_height = 420 - 58 - 48 if image_path else 212 - 58 - 42
    content = tk.Frame(outer, bg=content_bg, height=max(96, content_height))
    content.pack(fill="x")
    content.pack_propagate(False)

    text = tk.Text(
        content,
        bg=content_bg,
        fg="#61707a",
        bd=0,
        highlightthickness=0,
        relief="flat",
        wrap="word",
        padx=22,
        pady=10,
        cursor="arrow",
        takefocus=0,
    )
    if image_path:
        text.configure(height=6)
        text.pack(fill="x", expand=False)
    else:
        text.pack(fill="both", expand=True)
    text.tag_configure("kind", foreground="#5a6870", font=("Microsoft YaHei UI", 9, "bold"), spacing3=6)
    text.tag_configure(
        "title",
        foreground="#c91e1e" if is_hot else "#20272b",
        font=("Microsoft YaHei UI", 12, "bold"),
        spacing3=5,
    )
    text.tag_configure("body", foreground="#61707a", font=("Microsoft YaHei UI", 8), spacing1=1)
    text.insert("end", f"{kind}\n", "kind")
    text.insert("end", f"{title_text}\n", "title")
    if body:
        text.insert("end", body_text, "body")
    text.configure(state="disabled")

    translation_inserted = {"value": False}

    def show_translation(value: str, error: str = "") -> None:
        if not translate_btn:
            return
        has_value = bool(value)
        translate_btn.configure(text="已翻译" if has_value else "翻译失败", state="normal")
        translation_inserted["value"] = has_value
        result = value or error or "翻译失败"
        if "402" in result or "Payment Required" in result:
            result = "翻译失败：当前大模型额度不足，请在个人资料/设置里换一个可用模型或更新 API Key。"
        elif "missing API_KEY" in result:
            result = "翻译失败：还没有配置可用的大模型 API Key。"
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("end", f"{kind}\n", "kind")
        text.insert("end", f"{'译文' if has_value else '翻译失败'}\n", "title")
        text.insert("end", limited_lines(soft_wrap_text(result, 38), 5), "body")
        text.configure(state="disabled")

    def request_translation() -> None:
        if not translate_btn:
            return
        translate_btn.configure(text="翻译中", state="disabled")

        def worker() -> None:
            try:
                result = fetch_translation(translate_endpoint, translation_text)
                root.after(0, lambda: show_translation(result))
            except Exception as exc:
                root.after(0, lambda: show_translation("", f"翻译失败：{exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def show_confirmation(success: bool, message: str = "") -> None:
        if not confirm_btn:
            return
        success_label = "已开始优化" if "优化" in confirm_label else "已确认首次"
        confirm_btn.configure(
            text=success_label if success else "确认失败",
            state="disabled" if success else "normal",
            bg="#23c99a" if success else "#f6bb48",
        )
        if not success and message:
            print(f"desktop alert confirmation failed: {message}", file=sys.stderr)

    def request_confirmation() -> None:
        if not confirm_btn:
            return
        confirm_btn.configure(text="确认中", state="disabled")

        def worker() -> None:
            try:
                post_price_watch_confirmation(confirm_endpoint, confirm_symbol, confirm_episode)
                root.after(0, lambda: show_confirmation(True))
            except Exception as exc:
                root.after(0, lambda: show_confirmation(False, str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    exclude_btn = None

    def show_exclusion(success: bool, message: str = "") -> None:
        if not exclude_btn:
            return
        if success:
            exclusion_state["running"] = False
            label = "已暂时剔除" if exclusion_state["mode"] == "temporary" else "已彻底剔除"
            exclude_btn.configure(text=label, state="disabled", bg="#dcefe8", fg="#18745c")
            root.after(450, lambda: close(force=True))

    def request_exclusion(selected_action: str) -> None:
        if not exclude_btn:
            return
        exclusion_state["running"] = True
        exclusion_state["mode"] = "temporary" if selected_action == "temporary_exclude" else "permanent"
        exclude_btn.configure(text="剔除中", state="disabled")
        close_btn.configure(state="disabled")

        def worker() -> None:
            attempt = 0
            while exclusion_state["running"] and not closing:
                try:
                    post_price_watch_exclusion(exclude_endpoint, exclude_symbol, selected_action)
                    root.after(0, lambda: show_exclusion(True))
                    return
                except Exception as exc:
                    attempt += 1
                    if attempt == 1 or attempt % 6 == 0:
                        print(f"price watch exclusion is retrying automatically: {exc}", file=sys.stderr)
                    time.sleep(min(5, 1 + attempt))

        threading.Thread(target=worker, daemon=True).start()

    def choose_exclusion() -> None:
        if not exclude_btn or exclusion_state["running"]:
            return
        menu = tk.Menu(root, tearoff=0, font=("Microsoft YaHei UI", 9))
        menu.add_command(
            label="暂时剔除（重新上榜后恢复）",
            command=lambda: request_exclusion("temporary_exclude"),
        )
        menu.add_command(
            label="彻底剔除（仅手动添加恢复）",
            command=lambda: request_exclusion(exclude_action),
        )
        try:
            menu.tk_popup(
                exclude_btn.winfo_rootx(),
                exclude_btn.winfo_rooty() + exclude_btn.winfo_height(),
            )
        finally:
            menu.grab_release()

    alert_image = None
    if image_path:
        try:
            max_image_size = 150
            alert_image = load_tk_image(image_path, max_image_size)
            if alert_image:
                image_wrap = tk.Frame(content, bg="#f8fcff")
                image_wrap.pack(fill="both", expand=True, padx=22, pady=(6, 4))
                image_wrap.pack_propagate(False)
                tk.Label(
                    image_wrap,
                    image=alert_image,
                    bg="#ffffff",
                    bd=0,
                    padx=8,
                    pady=8,
                ).pack()
                root._xingyun_alert_image = alert_image
        except Exception:
            alert_image = None

    footer = tk.Frame(outer, bg="#ffffff")
    footer.pack(fill="x")
    tk.Label(
        footer,
        text=source_label,
        bg="#f7f2df",
        fg="#1c2529",
        font=("Microsoft YaHei UI", 9, "bold"),
        width=4,
    ).pack(side="left", padx=(18, 10), pady=12)
    tk.Label(
        footer,
        text=source,
        bg="#ffffff",
        fg="#6b7880",
        font=("Microsoft YaHei UI", 10),
    ).pack(side="left", pady=12)
    if can_exclude:
        exclude_btn = tk.Button(
            footer,
            text=exclude_label,
            command=choose_exclusion,
            bg="#ffffff",
            fg="#a43a32",
            activebackground="#fff0ed",
            activeforeground="#8e2d27",
            relief="solid",
            bd=1,
            width=7,
            height=1,
            font=("Microsoft YaHei UI", 8, "bold"),
        )
        exclude_btn.pack(side="right", padx=(6, 14), pady=8)
    inbox_url = (payload.get('_delivery') or {}).get('inboxUrl')
    if inbox_url:
        tk.Button(footer, text='未读播报 ›', command=lambda: webbrowser.open(inbox_url),
                  bg='#e3f3fc', fg='#194f72', relief='flat', cursor='hand2',
                  font=('Microsoft YaHei UI', 10, 'bold')).pack(side='right', padx=10, pady=8)
    else:
        tk.Label(footer, text=priority, bg='#ffffff', fg='#c91e1e' if is_hot else '#7b8790',
                 font=('Microsoft YaHei UI', 9)).pack(side='right', padx=18, pady=12)

    if can_explain or contract_address:
        # Keep the compact header action beside View, including under DPI
        # scaling, without clipping the brand, action or footer.
        root.update_idletasks()
        width = max(width, top.winfo_reqwidth()+2)
        height = max(height, outer.winfo_reqheight())
        x, y = popup_position(work_area(root), width, height, slot)
        root.geometry(f"{width}x{height}+{x}+{y}")

    def fade(step: int = 0) -> None:
        try:
            root.attributes("-alpha", min(0.98, step / 10))
        except Exception:
            return
        if step < 10:
            root.after(20, lambda: fade(step + 1))

    root.after(10, fade)
    last_receipt_at = 0.0
    def check_visibility():
        nonlocal last_receipt_at
        if closing:
            return
        mapped = bool(root.winfo_viewable())
        uncovered = popup_is_uncovered(root) if mapped else False
        now = time.monotonic()
        # Visible dwell is counted only while the pointer is outside the
        # popup. Hovering buttons or reading the card pauses auto-close.
        elapsed = visible_timer.tick(uncovered and not popup_pointer_inside(root), now)
        if now - last_receipt_at >= 1:
            # The delivery receipt acknowledges that Tk mapped the window.
            # Whether another topmost window covers it only affects dwell time;
            # it must never cause the same popup to be killed and relaunched.
            receipts.send('visible' if mapped else 'covered', elapsed)
            last_receipt_at = now
        if elapsed >= popup_auto_close_ms(payload):
            close(read=False)
            return
        root.after(250, check_visibility)
    root.after(50, check_visibility)
    # Chime and speech share the same cross-process lane; no separate async beep.
    speak_text(speech, sound)
    root.mainloop()
    receipts.finish()
    if temp_image_path:
        try:
            temp_image_path.unlink(missing_ok=True)
        except Exception:
            pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload")
    parser.add_argument("--explanations")
    parser.add_argument("--explanation-title", default='')
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--slot", type=int, default=int(os.getenv("XYS_ALERT_SLOT") or 0))
    args = parser.parse_args()
    if args.explanations:
        from news_trade_reader import show_reader
        return show_reader(args.explanations, args.port, args.explanation_title)
    if not args.payload:
        parser.error("--payload or --explanations is required")
    return show_popup(load_payload(args.payload), args.slot)


if __name__ == "__main__":
    raise SystemExit(main())
