"""登录二维码绘制。

刻意**不依赖 Pillow**：打包配置里 PIL 是被排除的，为一张二维码把
Pillow 拖进来会让 exe 变大。做法是从 ``qrcode`` 拿布尔矩阵，
再用 Canvas 逐格画方块——矩阵是纯数据，画起来也就几行。
"""

from __future__ import annotations

import tkinter as tk

from . import theme

__all__ = ["qr_matrix", "draw_qr", "qr_modules"]


def qr_matrix(url: str) -> list[list[bool]]:
    """把登录链接编成二维码矩阵。

    Returns:
        二维布尔列表，``True`` 表示该模块是黑色。

    Raises:
        RuntimeError: 未安装 ``qrcode``（它是软依赖，源码运行时可能没装）。
    """
    try:
        import qrcode  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise RuntimeError(
            "缺少 qrcode 库，无法绘制二维码。安装：pip install qrcode"
        ) from exc

    qr = qrcode.QRCode(border=2, box_size=1)
    qr.add_data(url)
    qr.make(fit=True)
    return [[bool(cell) for cell in row] for row in qr.get_matrix()]


def qr_modules(url: str) -> int:
    """二维码一边的模块数（含 quiet zone），用于算格子像素。"""
    return len(qr_matrix(url))


def draw_qr(
    canvas: tk.Canvas,
    url: str,
    size: int = theme.QR_SIZE,
    dark: str = theme.TEXT,
    light: str = theme.SURFACE,
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
    background: str = theme.SURFACE_ALT,
    foreground: str = theme.TEXT_MUTED,
) -> None:
    """二维码还没拿到时的占位画面（不是空白，用户才知道这里会有东西）。"""
    canvas.delete("all")
    canvas.create_rectangle(0, 0, size, size, fill=background, outline=background)
    canvas.create_text(
        size // 2,
        size // 2,
        text=text,
        fill=foreground,
        font=theme.FONT_SMALL,
        justify="center",
    )
