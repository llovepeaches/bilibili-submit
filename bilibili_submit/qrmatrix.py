"""登录链接 → 二维码矩阵。

放在核心层（而不是 ``ui/`` 里）是因为**两种界面都要用它**：桌面端拿
矩阵在 Tk Canvas 上画方块，Web 端拿同一个矩阵拼 SVG。放在 ``ui/qr.py``
的话，Web 想用它就得先 import 到 tkinter——一个只在浏览器里跑的东西
不该有这种依赖。

.. note::
   刻意**不依赖 Pillow**：矩阵是纯数据（二维布尔列表），怎么画由调用方
   决定。为一张二维码把图像库拖进来，打包体积要付几十 MB 的代价。
"""

from __future__ import annotations

__all__ = ["qr_matrix"]


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
