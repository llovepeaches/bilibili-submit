#!/usr/bin/env python3
"""生成打包用的应用图标 assets/bilibili-submit.ico（同时产出一份 PNG）。

只在开发/打包时运行，运行期不依赖 Pillow。

    python tools/make_icon.py

文件名必须与 bili_submit.spec 里的 APP_NAME 保持一致——之前这里写的是
bili-submit.ico，而 spec 找 bilibili-submit.ico，导致 Windows 上打包直接
报 FileNotFoundError。改成从 spec 读，避免两处各写一个名字再次跑偏。

为什么还要一份 PNG：``iconbitmap`` 在 Linux / macOS 上**读不了 .ico**
（Windows 专属格式），而这两个平台的窗口图标要走 ``iconphoto``，
它只认 PNG/GIF。只产 .ico 的话 Linux 上程序能跑但窗口没有图标，
任务栏里是一片空白——不致命，但一眼看上去像没做完。
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent

# B 站主题粉
PINK = (251, 114, 153, 255)
PINK_DARK = (231, 79, 124, 255)
WHITE = (255, 255, 255, 255)

SIZES = [16, 24, 32, 48, 64, 128, 256]


def app_name() -> str:
    """从 bili_submit.spec 读 APP_NAME，保证图标名与打包目标一致。"""
    spec = (ROOT / "bili_submit.spec").read_text(encoding="utf-8")
    m = re.search(r'^APP_NAME\s*=\s*"([^"]+)"', spec, re.MULTILINE)
    if not m:
        raise RuntimeError("未能从 bili_submit.spec 解析 APP_NAME")
    return m.group(1)


OUT = ROOT / "assets" / f"{app_name()}.ico"
#: 同款图标的 PNG 版，给 Linux / macOS 的 ``iconphoto`` 用。
PNG_OUT = ROOT / "assets" / f"{app_name()}.png"


def rounded_mask(size: int, radius_ratio: float = 0.22) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=int(size * radius_ratio), fill=255
    )
    return mask


def draw_play(draw: ImageDraw.ImageDraw, size: int) -> None:
    """在中心画一个播放三角形。"""
    cx, cy = size / 2, size / 2
    r = size * 0.24
    # 略微右移让视觉重心居中
    points = [
        (cx - r * 0.72, cy - r),
        (cx - r * 0.72, cy + r),
        (cx + r * 0.95, cy),
    ]
    draw.polygon(points, fill=WHITE)


def make_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # 竖向渐变底色
    grad = Image.new("RGBA", (1, size))
    gd = ImageDraw.Draw(grad)
    for y in range(size):
        ratio = y / max(1, size - 1)
        color = tuple(
            int(PINK[i] * (1 - ratio) + PINK_DARK[i] * ratio) for i in range(4)
        )
        gd.point((0, y), fill=color)
    grad = grad.resize((size, size))
    img.paste(grad, (0, 0), rounded_mask(size))

    draw = ImageDraw.Draw(img)
    draw_play(draw, size)
    return img


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    base = make_icon(256)
    base.save(
        OUT,
        format="ICO",
        sizes=[(s, s) for s in SIZES],
    )
    # 256 一个尺寸就够：``iconphoto`` 会自己缩放，而 PNG 不进 Windows
    # 的打包产物（那边用 .ico），没必要多占体积。
    base.save(PNG_OUT, format="PNG")
    print(f"图标已生成: {OUT}")
    print(f"同款 PNG:  {PNG_OUT}（Linux / macOS 的 iconphoto 用）")
    print("预览尺寸:", ", ".join(f"{s}x{s}" for s in SIZES))


if __name__ == "__main__":
    main()
