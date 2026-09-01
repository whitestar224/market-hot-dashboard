"""Render a lightweight TradingView-style static K-line snapshot."""

from __future__ import annotations

import argparse
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


SHANGHAI = timezone(timedelta(hours=8))
BINANCE_FUTURES = "https://fapi.binance.com/fapi/v1/klines"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def millis(text: str) -> int:
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        value = value.replace(tzinfo=SHANGHAI)
    return int(value.timestamp() * 1000)


def fetch(symbol: str, interval: str, start: str, end: str) -> list[dict]:
    params = urllib.parse.urlencode(
        {
            "symbol": symbol.upper(),
            "interval": interval,
            "startTime": millis(start),
            "endTime": millis(end),
            "limit": 1500,
        }
    )
    request = urllib.request.Request(
        f"{BINANCE_FUTURES}?{params}",
        headers={"User-Agent": "DragonWaveStaticSnapshot/1.0"},
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        rows = json.load(response)
    return [
        {
            "time": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
        for row in rows
    ]


def ema(values: list[float], period: int) -> list[float]:
    alpha = 2 / (period + 1)
    output: list[float] = []
    current = values[0]
    for value in values:
        current = value * alpha + current * (1 - alpha)
        output.append(current)
    return output


def format_price(value: float) -> str:
    if value >= 100:
        return f"{value:.2f}"
    if value >= 1:
        return f"{value:.4f}"
    return f"{value:.6f}"


def render(args: argparse.Namespace) -> Path:
    candles = fetch(args.symbol, args.interval, args.start, args.end)
    focus_ms = millis(args.focus)
    focus_index = min(range(len(candles)), key=lambda index: abs(candles[index]["time"] - focus_ms))
    left = max(0, focus_index - args.before)
    right = min(len(candles), focus_index + args.after + 1)
    visible = candles[left:right]
    focus_index -= left

    width, height = 1800, 980
    image = Image.new("RGB", (width, height), "#07110f")
    draw = ImageDraw.Draw(image)
    plot_left, plot_top, plot_right, plot_bottom = 58, 118, 1690, 785
    volume_top, volume_bottom = 815, 925
    plot_width = plot_right - plot_left
    plot_height = plot_bottom - plot_top

    highs = [row["high"] for row in visible]
    lows = [row["low"] for row in visible]
    price_low, price_high = min(lows), max(highs)
    padding = (price_high - price_low) * 0.07 or max(price_high * 0.01, 1e-9)
    price_low -= padding
    price_high += padding

    def x(index: int) -> float:
        return plot_left + (index + 0.5) * plot_width / len(visible)

    def y(value: float) -> float:
        return plot_bottom - (value - price_low) / (price_high - price_low) * plot_height

    draw.text((58, 26), f"{args.symbol.upper()} · {args.interval} · Binance Futures", fill="#e9fff8", font=font(31, True))
    focus_time = datetime.fromtimestamp(visible[focus_index]["time"] / 1000, SHANGHAI)
    draw.text(
        (58, 70),
        f"静态复盘图 · 北京时间 {focus_time:%Y-%m-%d %H:%M} · EMA90 · 主升浪默认环境",
        fill="#7faaa0",
        font=font(19),
    )

    grid_font = font(16)
    for step in range(6):
        yy = plot_top + step * plot_height / 5
        price = price_high - step * (price_high - price_low) / 5
        draw.line((plot_left, yy, plot_right, yy), fill="#172522", width=1)
        draw.text((plot_right + 12, yy - 9), format_price(price), fill="#668078", font=grid_font)

    tick_count = 7
    for step in range(tick_count):
        index = round(step * (len(visible) - 1) / (tick_count - 1))
        xx = x(index)
        draw.line((xx, plot_top, xx, volume_bottom), fill="#10201d", width=1)
        value = datetime.fromtimestamp(visible[index]["time"] / 1000, SHANGHAI)
        draw.text((xx - 44, 940), value.strftime("%m-%d %H:%M"), fill="#607a73", font=font(14))

    closes = [row["close"] for row in candles]
    ema90 = ema(closes, 90)[left:right]
    ema_points = [(x(index), y(value)) for index, value in enumerate(ema90)]
    draw.line(ema_points, fill="#f4ad22", width=3, joint="curve")

    body_width = max(2, min(9, int(plot_width / len(visible) * 0.68)))
    max_volume = max(row["volume"] for row in visible) or 1
    for index, row in enumerate(visible):
        xx = x(index)
        color = "#2ee6b5" if row["close"] >= row["open"] else "#ff5367"
        draw.line((xx, y(row["high"]), xx, y(row["low"])), fill=color, width=1)
        top = y(max(row["open"], row["close"]))
        bottom = y(min(row["open"], row["close"]))
        if bottom - top < 2:
            bottom = top + 2
        draw.rectangle((xx - body_width / 2, top, xx + body_width / 2, bottom), fill=color)
        volume_height = row["volume"] / max_volume * (volume_bottom - volume_top)
        draw.rectangle((xx - body_width / 2, volume_bottom - volume_height, xx + body_width / 2, volume_bottom), fill=color)

    focus = visible[focus_index]
    fx = x(focus_index)
    draw.line((fx, plot_top, fx, volume_bottom), fill="#39f6c7", width=1)
    prior_start = max(0, focus_index - 60)
    prior_high = max(row["high"] for row in visible[prior_start:focus_index])
    draw.line((x(prior_start), y(prior_high), plot_right, y(prior_high)), fill="#d6e4df", width=2)
    draw.text((plot_right - 210, y(prior_high) - 28), f"盘整前高 {format_price(prior_high)}", fill="#d6e4df", font=font(16, True))

    marker_y = min(plot_bottom - 40, y(focus["low"]) + 36)
    draw.polygon([(fx, marker_y - 22), (fx - 15, marker_y + 4), (fx + 15, marker_y + 4)], fill="#39f6c7")
    draw.text((fx - 8, marker_y + 6), "B", fill="#39f6c7", font=font(20, True))
    draw.rounded_rectangle((fx + 24, marker_y - 42, fx + 360, marker_y + 30), radius=8, outline="#39f6c7", width=2, fill="#0b1a17")
    draw.text((fx + 40, marker_y - 33), args.label, fill="#39f6c7", font=font(20, True))
    draw.text((fx + 40, marker_y - 4), "突破K · 高确定性买点", fill="#d5eee7", font=font(16))

    draw.text((58, 948), "绿色B：策略买点    橙线：EMA90    白线：盘整前高", fill="#88a89f", font=font(16))
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, "PNG", optimize=True)
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--focus", required=True)
    parser.add_argument("--before", type=int, default=150)
    parser.add_argument("--after", type=int, default=72)
    parser.add_argument("--label", default="盘整突破 + 横盘起飞")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(render(args))


if __name__ == "__main__":
    main()
