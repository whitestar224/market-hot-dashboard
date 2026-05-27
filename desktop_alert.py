from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import tempfile
import textwrap
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LOGO_PATH = ROOT / "assets" / "xingyunshe-logo-transparent.png"
AUTO_CLOSE_MS = 10 * 60 * 1000


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


def play_sound(enabled: bool) -> None:
    if not enabled or os.name != "nt":
        return
    try:
        import winsound

        winsound.PlaySound("SystemNotification", winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass


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


def payload_image_path(payload: dict) -> tuple[Path | None, Path | None]:
    image_path = str(payload.get("imagePath") or "").strip()
    if image_path:
        path = Path(image_path)
        if path.exists():
            return path, None

    image_url = str(payload.get("imageUrl") or "").strip()
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
        pass

    try:
        from PIL import Image, ImageTk

        image = Image.open(image_path)
        image.thumbnail((max_size, max_size))
        return ImageTk.PhotoImage(image)
    except Exception as exc:
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
    alert_time = time_label(payload.get("time"))
    sound = payload.get("sound") is not False
    is_hot = "高热" in priority or "重点" in priority or "高热" in title
    image_path, temp_image_path = payload_image_path(payload)
    title_text = limited_lines(soft_wrap_text(title, 24), 2)
    body_text = soft_wrap_text(body, 42)

    root = tk.Tk()
    root.title("星云社快讯")
    root.overrideredirect(True)
    root.configure(bg="#9dcfe8")
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
    left, top_edge, right, bottom = work_area(root)
    stack_index = min(max(0, slot), 3)
    x = min(max(left + 18, left + 8), max(left + 8, right - width - 8))
    y = max(top_edge + 8, bottom - height - 18 - stack_index * (height + 10))
    root.geometry(f"{width}x{height}+{x}+{y}")

    outer = tk.Frame(root, bg="#9dcfe8", bd=1, relief="solid")
    outer.pack(fill="both", expand=True)

    top = tk.Frame(outer, bg="#c9edff")
    top.pack(fill="x")

    logo_img = None
    if LOGO_PATH.exists():
        try:
            logo_img = tk.PhotoImage(file=str(LOGO_PATH))
            factor = max(1, int(max(logo_img.width() / 24, logo_img.height() / 24)))
            logo_img = logo_img.subsample(factor, factor)
            logo = tk.Label(top, image=logo_img, bg="#c9edff", bd=0)
        except Exception:
            logo = tk.Label(top, text="NX", bg="#c9edff", fg="#0b76ff", font=("Microsoft YaHei UI", 9, "bold"))
    else:
        logo = tk.Label(top, text="NX", bg="#c9edff", fg="#0b76ff", font=("Microsoft YaHei UI", 9, "bold"))
    logo.pack(side="left", padx=(16, 7), pady=(13, 7))

    brand = tk.Label(
        top,
        text=f"星云社快讯  {alert_time}",
        bg="#c9edff",
        fg="#344047",
        font=("Microsoft YaHei UI", 11, "bold"),
    )
    brand.pack(side="left", pady=(13, 7))

    def close() -> None:
        root.destroy()

    def open_url() -> None:
        if url:
            webbrowser.open(url)
        close()

    close_btn = tk.Button(
        top,
        text="x",
        command=close,
        bg="#d7f0fb",
        fg="#455660",
        activebackground="#eef9ff",
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
            bg="#d7f0fb",
            fg="#455660",
            activebackground="#eef9ff",
            relief="flat",
            width=5,
            height=1,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        view_btn.pack(side="right", padx=(4, 0), pady=(12, 7))

    content_height = 420 - 58 - 48 if image_path else 212 - 58 - 42
    content = tk.Frame(outer, bg="#f8fcff", height=max(96, content_height))
    content.pack(fill="x")
    content.pack_propagate(False)

    text = tk.Text(
        content,
        bg="#f8fcff",
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

    alert_image = None
    if image_path:
        try:
            max_image_size = 150
            alert_image = load_tk_image(image_path, max_image_size)
            if alert_image:
                image_wrap = tk.Frame(content, bg="#f8fcff")
                image_wrap.pack(fill="x", padx=22, pady=(10, 4))
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
    tk.Label(
        footer,
        text=priority,
        bg="#ffffff",
        fg="#c91e1e" if is_hot else "#7b8790",
        font=("Microsoft YaHei UI", 9),
    ).pack(side="right", padx=18, pady=12)

    def fade(step: int = 0) -> None:
        try:
            root.attributes("-alpha", min(0.98, step / 10))
        except Exception:
            return
        if step < 10:
            root.after(20, lambda: fade(step + 1))

    root.after(10, fade)
    root.after(AUTO_CLOSE_MS, close)
    play_sound(sound)
    root.mainloop()
    if temp_image_path:
        try:
            temp_image_path.unlink(missing_ok=True)
        except Exception:
            pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True)
    parser.add_argument("--slot", type=int, default=int(os.getenv("XYS_ALERT_SLOT") or 0))
    args = parser.parse_args()
    return show_popup(load_payload(args.payload), args.slot)


if __name__ == "__main__":
    raise SystemExit(main())
