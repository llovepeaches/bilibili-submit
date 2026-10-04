"""图形界面。

GUI 是依赖方向最上面的一层：它调用 ``auth`` / ``config`` / ``scheduler``
等下层模块，**不被它们依赖**。下层不知道自己会被命令行还是界面调用。

    from bilibili_submit.ui import launch
    launch()

拆成几块：

- :mod:`~bilibili_submit.ui.theme` —— 颜色、字体、间距的唯一来源
- :mod:`~bilibili_submit.ui.widgets` —— 可复用组件，不含业务逻辑
- :mod:`~bilibili_submit.ui.qr` —— 二维码绘制（不依赖 Pillow）
- :mod:`~bilibili_submit.ui.workers` —— 后台线程与取消
- :mod:`~bilibili_submit.ui.app` —— 主窗口与导航
- :mod:`~bilibili_submit.ui.views` —— 各个页面

tkinter 是标准库，但**精简版 Python 可能没带**，所以 import 本包不一定成功；
调用方要用 :func:`gui_available` 先问一句，而不是直接 import 炸掉。
"""

from __future__ import annotations

__all__ = ["gui_available", "launch"]


def gui_available() -> tuple[bool, str]:
    """界面能不能用，返回 ``(是否可用, 不可用时原因)``。

    用元组而不是布尔值：CLI 需要把原因显示给用户，
    一句「无法启动界面」远不如「未附带 tkinter，请装 python3-tk」有用。
    """
    try:
        import tkinter  # noqa: F401  # pylint: disable=unused-import
    except ImportError as exc:
        return False, f"当前 Python 未附带 tkinter（{exc}）"
    return True, ""


def launch() -> int:
    """启动界面，返回退出码。

    Raises:
        RuntimeError: 环境不支持 GUI。调用方应先查 :func:`gui_available`。
    """
    from .app import launch as _launch

    return _launch()
