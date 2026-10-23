"""Web 端的工作线程 → 浏览器事件。

这里只有一个 :class:`Event`，它是 :class:`bilibili_submit.ui.workers.Event`
的**逐字段复制**。为什么不直接 import 那个：

``ui/workers.py`` 顶部就 ``import tkinter``。Web 版要能在没装 tkinter
的机器上运行（那正是它的卖点之一），一 import 就全盘皆输。

复制的代价是**漂移**：哪天给桌面端加个字段，Web 端会静默缺一半状态。
这个代价用测试来付：``tests/test_web.py`` 里用 ``dataclasses.fields()``
逐个字段比对两个类（名字、顺序、默认值），改一边忘另一边就会红。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Event"]


@dataclass(frozen=True)
class Event:
    """一个任务项的状态变化。字段语义与 ``ui.workers.Event`` 完全一致。

    Attributes:
        kind: ``"start"`` 开始执行某项；``"done"`` 某项结束。
        index: 当前项在**完整列表**里的下标（从 0 开始），用于定位行。
        position: 当前项在**本次执行**里是第几个（从 1 开始），用于算进度。
        total: 本次执行的总项数。
        status: ``"done"`` 时该项的结果文案（成功 / 失败原因）。
        error: 失败原因全文。

    .. important::
       ``index`` 和 ``position`` **不能混用**。用户可能只勾选下标 3 和 5
       的两项，此时 ``total=2`` 而 ``index`` 是 3 和 5——拿
       ``index + 1`` 除以 ``total`` 会算出 200%。
    """

    kind: str
    index: int = 0
    position: int = 0
    total: int = 0
    status: str = ""
    error: str = ""

    @property
    def done(self) -> bool:
        return self.kind == "done"

    @property
    def succeeded(self) -> bool:
        return self.kind == "done" and not self.error

    @property
    def percent(self) -> float:
        """完成百分比（0-100）。

        ``position`` 为 0 时退回 0，而不是拿 ``index`` 顶替。
        """
        if self.position <= 0 or self.total <= 0:
            return 0.0
        return min(100.0, self.position / self.total * 100)

    def to_dict(self) -> dict[str, object]:
        """给 JSON / SSE 用的形态。

        ``percent`` 是算出来的，不进序列化——浏览器自己除也行，但那要
        把 ``position`` 的语义再实现一遍，不如服务端算好。
        """
        return {
            "kind": self.kind,
            "index": self.index,
            "position": self.position,
            "total": self.total,
            "status": self.status,
            "error": self.error,
            "done": self.done,
            "succeeded": self.succeeded,
            "percent": round(self.percent, 1),
        }
