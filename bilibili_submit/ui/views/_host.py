"""视图眼里的「宿主」——主窗口为了让视图读到共享状态而暴露的那一小面。

**为什么这个类型要单独待在一个文件里**：:mod:`..app` 要 import 视图，
视图要标注宿主的类型。视图在运行期 import ``app.py`` 就成了环。这里定义的
是一个**纯类型**的 :class:`~typing.Protocol`——运行期它只往模块的
``__all__`` 里塞一个名字，不执行任何有副作用的导入，所以环在运行期
根本不会形成（:data:`typing.TYPE_CHECKING` 下五个视图引它，也一样）。

**为什么是 Protocol 而不是写死 ``App``**：测试里的宿主是
``type("App", (), {"ctx": None})()`` 这类替身（见 ``test_ui.py`` 里
投稿页那几个用例），写死具体类就没法替换。Protocol 描述的是**能力**：
只要 ``ctx`` 和 ``refresh_status`` 在，就能当宿主用。

**刻意不做的事**：不加 ``@runtime_checkable``。带非方法成员（``ctx`` 是
属性）的 Protocol 在运行期只能检查属性「存在」而检查不了类型，容易给人
虚假的安全感。这里的 ``ctx`` 故意不写返回类型注解——:class:`AppContext`
定义在 ``app.py``，标注它就等于把循环依赖请回来（哪怕只在
``TYPE_CHECKING`` 下），留白 + 下面的 docstring 说明足够。
"""

from __future__ import annotations

from typing import Protocol


class AppHost(Protocol):
    """视图对主窗口的最小要求。

    五个视图都只通过它读共享状态、刷新状态栏。谁来满足都行：真正的
    :class:`~bilibili_submit.ui.app.App`、测试替身，或者以后把窗口换掉
    另起一个壳。
    """

    @property
    def ctx(self):
        """共享状态。

        形如 :class:`~bilibili_submit.ui.app.AppContext`：``cookie_file``
        与 ``proxy`` 两个可读写属性、``logged_in`` 这个只读属性，
        以及 ``client(need_login=True)`` 方法（未登录时抛
        ``NotLoggedInError``）。
        """
        ...

    def refresh_status(self) -> None:
        """重新读一遍状态并刷新底部状态栏。

        实现方可以同步刷新，也可以排程到主线程——对视图来说只需要
        「叫它一下」这个动作。
        """
        ...
