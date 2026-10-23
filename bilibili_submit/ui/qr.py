"""登录二维码绘制。

刻意**不依赖 Pillow**：打包配置里 PIL 是被排除的，为一张二维码把
Pillow 拖进来会让 exe 变大。做法是从 ``qrcode`` 拿布尔矩阵，
再用 Canvas 逐格画方块——矩阵是纯数据，画起来也就几行。

矩阵本身在 :mod:`bilibili_submit.qrmatrix`（核心层）。那里不能有
tkinter 依赖，因为 Web 版要用同一个矩阵拼 SVG——两边各编一次矩阵
的话，哪天调了容错级别，桌面端和 Web 端就会悄悄不一样。
"""

from __future__ import annotations

import tkinter as tk

from ..qrmatrix import qr_matrix
from . import theme

__all__ = ["qr_matrix", "draw_qr"]


def draw_qr(
    canvas: tk.Canvas,
    url: str,
    size: int = theme.QR_SIZE,
    dark: str = theme.INK,
    light: str = theme.PAPER,
) -> None:
    """把登录链接画到 Canvas 上（居中，带 quiet zone）。

    Args:
        canvas: 目标画布，调用前会清空。
        url: 二维码内容（B 站登录链接）。
        size: 画布边长像素。
        dark: 前景色（二维码黑块）。
        light: 背景色。
    """
    canvas.delete("all")
    matrix = qr_matrix(url)
    count = len(matrix)

    # 先取整格像素再居中，避免整除误差导致的边缘白边不对称
    cell = max(1, int(size / count))
    side = cell * count
    offset = (size - side) // 2

    canvas.create_rectangle(0, 0, size, size, fill=light, outline=light)
    for row_index, row in enumerate(matrix):
        y0 = offset + row_index * cell
        for col_index, filled in enumerate(row):
            if not filled:
                continue
            x0 = offset + col_index * cell
            canvas.create_rectangle(
                x0, y0, x0 + cell, y0 + cell, fill=dark, outline=dark
            )


def draw_placeholder(
    canvas: tk.Canvas,
    text: str,
    size: int = theme.QR_SIZE,
    background: str = theme.PAPER_ALT,
    foreground: str = theme.INK_MUTED,
) -> None:
    """二维码还没拿到时的占位画面（不是空白，用户才知道这里会有东西）。"""
    canvas.delete("all")
    canvas.create_rectangle(0, 0, size, size, fill=background, outline=background)
    canvas.create_text(
        size // 2,
        size // 2,
        text=text,
        fill=foreground,
        font=theme.font("caption"),
        justify="center",
    )
