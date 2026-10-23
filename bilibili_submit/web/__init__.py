"""Web 界面。

与 :mod:`bilibili_submit.ui`（桌面端）平行的一层：同样是「最上面的一层，
调用下层、不被下层依赖」，只是把 Tk 换成了浏览器。

    from bilibili_submit.web import launch
    launch()

细分：

- :mod:`~bilibili_submit.web.app` —— Flask 应用与路由
- :mod:`~bilibili_submit.web.jobs` —— 后台线程与事件队列
- :mod:`~bilibili_submit.web.events` —— 进度事件（与桌面端同构）
- :mod:`~bilibili_submit.web.login` —— 扫码登录会话
- :mod:`~bilibili_submit.web.browse` —— 浏览服务器目录
- :mod:`~bilibili_submit.web.submit` —— 预览与执行投稿

Flask 是**软依赖**：命令行与桌面版都不需要它。用 :func:`web_available`
先问一句，而不是直接 import 炸掉。
"""

from __future__ import annotations

__all__ = ["web_available", "launch"]


def web_available() -> tuple[bool, str]:
    """Web 界面能不能用，返回 ``(是否可用, 不可用时原因)``。"""
    from .app import web_available as _probe

    return _probe()


def launch(
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    cookie_file: str | None = None,
    proxy: str | None = None,
) -> int:
    """启动 Web 界面，返回退出码。"""
    from .app import launch as _launch

    return _launch(
        host=host,
        port=port,
        open_browser=open_browser,
        cookie_file=cookie_file,
        proxy=proxy,
    )
