"""给事件流浮窗画「平滑线条」的小工具：抗锯齿圆角矩形。

为什么不用 Tk 自己画：`Frame(bd=1, relief="solid")` 只有 1px 硬边直角，
`Canvas.create_polygon(smooth=True)` 又画不出真正的圆弧（还会圆过头），
两个放在浅色面板上都显得脏。用户 2026-11-26：「审美再做好一点，比如线条啊，
做平滑一点」。

做法是标准的超采样：按 4 倍尺寸画圆角矩形，再 LANCZOS 缩回去 —— 边缘天然带
抗锯齿过渡，1px 描边也能画得干净。生成的图交给 `tk.Canvas.create_image()` 使用。

本模块只依赖 Pillow，不碰 Tk，方便单测。
"""
from __future__ import annotations

from functools import lru_cache

try:
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover - Pillow 缺失时调用方走降级路径
    Image = None
    ImageDraw = None
# 超采样倍数。4 倍是性价比拐点：2 倍边缘还能看到台阶，8 倍收益几乎为零。
SUPERSAMPLE = 4


def pillow_available() -> bool:
    return Image is not None and ImageDraw is not None


def _hex_to_rgba(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    text = str(value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(character * 2 for character in text)
    if len(text) != 6:
        return (0, 0, 0, alpha)
    return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16), alpha)


def _rounded_layer(width: int, height: int, radius: int, color: str,
                   inset: float = 0) -> "Image.Image":
    """一整层圆角矩形（RGBA，非预乘）。inset 是向内收缩的像素数。"""
    scale = SUPERSAMPLE
    big = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    left = inset * scale
    top = inset * scale
    right = width * scale - 1 - inset * scale
    bottom = height * scale - 1 - inset * scale
    if right <= left or bottom <= top:
        return big.resize((width, height), Image.LANCZOS)
    draw.rounded_rectangle(
        [left, top, right, bottom],
        radius=max(0.0, (radius - inset)) * scale,
        fill=_hex_to_rgba(color),
    )
    return big.resize((width, height), Image.LANCZOS)


@lru_cache(maxsize=256)
def rounded_box(width: int, height: int, radius: int, fill: str,
                outline: str = "", outline_width: int = 1,
                band_fill: str = "", band_height: int = 0) -> "Image.Image":
    """圆角面板图：底色 + 1px 描边 +（可选）顶部一条圆角标题带。

    标题带是「上圆角、下平直」：生成一整块圆角图后裁到 ``band_height``，
    裁剪边天然是直的，顶端两角保留圆角 —— 这样标题条和面板外轮廓严丝合缝，
    不会像 `Frame` 那样在角上盖出一个直角缺口。

    返回普通 PIL Image（不是 PhotoImage），调用方自己转、自己持有引用 ——
    Tk 的 PhotoImage 一旦被 GC，图上就白了，这是最常见的坑。
    """
    width = int(width)
    height = int(height)
    if width <= 2 or height <= 2:
        return Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))

    if outline and outline_width > 0:
        # 先铺满描边色，再内缩贴上底色 —— 剩下的那一圈就是描边。
        # 比 ImageDraw 的 outline 更稳（后者在圆角弧上的 1px 经常断）。
        image = _rounded_layer(width, height, radius, outline)
        image.alpha_composite(_rounded_layer(
            width, height, radius, fill, inset=outline_width
        ))
    else:
        image = _rounded_layer(width, height, radius, fill)

    if band_fill and band_height > 0:
        band = _rounded_layer(width, height, radius, band_fill)
        clamped = max(1, min(int(band_height), height - 1))
        band = band.crop((0, 0, width, clamped))
        image.alpha_composite(band, (0, 0))
    return image


def box_to_photo(width: int, height: int, radius: int, fill: str,
                 outline: str = "", outline_width: int = 1,
                 band_fill: str = "", band_height: int = 0):
    """直接产出 Tk 可用的 PhotoImage；Pillow 不可用时返回 None。"""
    if not pillow_available():
        return None
    try:
        from PIL import ImageTk
    except Exception:
        return None
    image = rounded_box(
        int(width), int(height), int(radius), fill, outline, int(outline_width),
        band_fill, int(band_height),
    )
    return ImageTk.PhotoImage(image)
