"""二维码 → SVG 字符串。

浏览器里画二维码有两种做法：引一个前端库，或者服务端直接给图。
这里选后者——``qrcode`` 已经是项目的软依赖（命令行 ``login`` 打终端
二维码用的就是它），再往前端塞一个库就得考虑打包、缓存、版本，
而矩阵总共也就几十行方块。

返回 SVG 而不是 PNG：不用 Pillow（打包体积），缩放不糊，
内联进页面也不用多一次请求。
"""

from __future__ import annotations

from ..qrmatrix import qr_matrix

__all__ = ["qr_svg"]


def qr_svg(url: str, dark: str = "#111111", light: str = "#ffffff") -> str:
    """把登录链接编成一个自包含的 SVG 文档。

    Args:
        dark / light: 前景/背景色。默认黑白，深色模式下由 CSS 也改不了
            SVG 内部，所以让调用方传进来。

    Returns:
        一段 ``<svg ...>...</svg>``，可直接内联进 HTML。

    Raises:
        RuntimeError: 未安装 ``qrcode``。
    """
    matrix = qr_matrix(url)
    count = len(matrix)

    # 每个模块画成 1 个单位，靠 viewBox 缩放到任意显示尺寸——
    # 不乘 box_size 是因为那会让 SVG 的坐标随内容长度变化，
    # CSS 里就不好控制了。
    rows: list[str] = []
    for row_index, row in enumerate(matrix):
        row_parts = [
            f"M{col_index} {row_index}h1v1h-1z"
            for col_index, filled in enumerate(row)
            if filled
        ]
        if row_parts:
            rows.append("".join(row_parts))

    path = "".join(rows)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {count} {count}" '
        f'shape-rendering="crispEdges" role="img" aria-label="登录二维码">'
        f'<rect width="{count}" height="{count}" fill="{light}"/>'
        f'<path d="{path}" fill="{dark}"/>'
        f"</svg>"
    )
