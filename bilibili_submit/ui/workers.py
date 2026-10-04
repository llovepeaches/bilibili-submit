"""后台任务执行。

tkinter **不是线程安全的**：工作线程里直接改控件，轻则界面卡住，
重则整个进程崩掉（Tcl 解释器不是可重入的）。

所以所有耗时操作（网络请求、上传、投稿）都在工作线程跑，
结果统一通过 ``after`` 排到主线程再落地。这个模块把这套管道封起来，
业务代码只管传函数和回调，不用自己操心线程。

另一个要点是**取消**：登录轮询和上传都可能跑很久，用户关窗口或点取消时
要保证线程能停下来，不能让进程卡在 join 上退不掉。
"""

from __future__ import annotations

import threading
import tkinter as tk
from dataclasses import dataclass
from typing import Any, Callable, Generic, TypeVar, Union

T = TypeVar("T")

__all__ = ["Worker", "Cancelled", "Event"]


@dataclass(frozen=True)
class Event:
    """工作线程上报的**结构化**状态，区别于纯文字进度。

    ``report`` 原本只接受 ``str``（一行日志）。但像「第 3 个任务跑完了、
    状态是成功」这种信息需要更新列表行和进度条，硬塞进字符串再解析太脆。

    调用方通过 ``isinstance`` 区分两种上报::

        def _on_progress(self, message: str | Event) -> None:
            if isinstance(message, Event):
                ...        # 更新行状态 / 进度条
            else:
                self._log.append(message)

    Attributes:
        kind: ``"start"`` 开始执行某项；``"done"`` 某项结束。
        index: 当前项在**完整列表**里的下标（从 0 开始），用于定位行。
        position: 当前项在**本次执行**里是第几个（从 1 开始），用于算进度。
        total: 本次执行的总项数，用于算进度百分比。
        status: ``"done"`` 时该项的结果文案（成功 / 失败原因）。
        error: 失败原因全文。状态列放不下，双击时弹窗看它。

    .. important::
       ``index`` 和 ``position`` **不能混用**。用户可能只勾选了下标
       3 和 5 的两项，此时 ``total=2`` 而 ``index`` 是 3 和 5——
       拿 ``index + 1`` 除以 ``total`` 会算出 200%，进度条直接顶满，
       状态栏还会显示「6/2」。凡是算进度的地方一律用 :attr:`position`。
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
        """该项是否成功。失败时 ``status`` 是错误文案。"""
        return self.kind == "done" and not self.error

    @property
    def percent(self) -> float:
        """完成百分比（0-100），供进度条直接用。

        ``position`` 为 0（调用方没给）时退回 0，而不是拿 ``index`` 顶替——
        宁可进度条不动，也不要给一个错误的百分比。
        """
        if self.position <= 0 or self.total <= 0:
            return 0.0
        return min(100.0, self.position / self.total * 100)


#: ``report`` 接受的参数：一行日志，或一个结构化事件
Report = Union[str, Event]


class Cancelled(Exception):
    """任务被用户取消。

    与业务异常区分开：取消不该弹错误提示，界面安静地回到空闲态即可。
    """


class Worker(Generic[T]):
    """在工作线程执行一个函数，把结果/进度/错误送回主线程。

    用法::

        worker = Worker(root)
        worker.run(
            lambda report, is_cancelled: do_something(),
            on_progress=lambda msg: log.append(msg),
            on_done=lambda value: show(value),
            on_error=lambda exc: show_error(exc),
        )

    同一时刻只允许一个任务在跑——本程序没有并发投稿的场景，
    串行反而避免了「两个任务同时写 cookie 文件」这类竞态。
    """

    def __init__(self, root: tk.Misc) -> None:
        self._root = root
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run(
        self,
        task: Callable[[Callable[[Report], None], Callable[[], bool]], T],
        on_progress: Callable[[Report], None] | None = None,
        on_done: Callable[[T], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        """启动任务。

        Args:
            task: 真正干活的 callable。接收两个参数：
                ``report(message)`` 上报进度或 :class:`Event`，
                ``is_cancelled()`` 查询是否已取消。
            on_progress: 进度回调（主线程执行）。参数是 ``str`` 或
                :class:`Event`，回调方自己 ``isinstance`` 区分。
            on_done: 成功回调（主线程执行）。
            on_error: 失败回调（主线程执行），含 :class:`Cancelled`。
        """
        if self.running:
            raise RuntimeError("已有任务在运行")

        self._cancel.clear()

        def report(message: Report) -> None:
            # 工作线程 → 主线程：用 after 排队，不直接调 UI
            if on_progress:
                safe_after(self._root, 0, on_progress, message)

        def wrapped() -> None:
            try:
                value = task(report, self._cancel.is_set)
            # 注意：except 块结束时 Python 会 del 掉 exc（避免循环引用），
            # 而 after 是延迟执行的，直接闭包引用 exc 会拿到未定义名。
            # safe_after 用参数传值（而不是闭包），顺带解决了这个问题。
            except BaseException as exc:  # noqa: BLE001 - 要兜住所有异常送回主线程
                if on_error:
                    safe_after(self._root, 0, on_error, exc)
                return
            if on_done:
                safe_after(self._root, 0, on_done, value)

        self._thread = threading.Thread(target=wrapped, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """请求取消。只是置标志位，任务要自己周期性检查。"""
        self._cancel.set()


def safe_after(root: tk.Misc, delay_ms: int, fn: Callable[..., Any], *args: Any) -> str:
    """注册一个延时回调，**注册和执行**都不认在工作线程上抛异常。

    需要挡的两种情况：

    - ``tk.TclError``：窗口销毁后排队的 ``after`` 已被 Tcl 清掉，
      再注册或再执行都会炸。关窗口时很容易踩到。
    - ``RuntimeError: main thread is not in main loop``：主线程还没进
      事件循环（或已经退出）时，``after`` 注册不到 Tcl 的命令表上。

    .. important::
       ``try`` 必须包住 :meth:`after` **注册本身**，而不只是回调。
       只包回调的话异常从注册那一行就漏出去了——而它几乎总是发生在
       工作线程里，一抛就是整条线程挂掉：既收不到 ``on_done`` 也收不到
       ``on_error``，界面会永远停在「执行中」，用户既不能重试也不能取消。
    """
    def wrapped() -> None:
        try:
            fn(*args)
        except (tk.TclError, RuntimeError):
            pass  # 窗口已关闭或事件循环已停，回调没有意义了

    try:
        return root.after(delay_ms, wrapped)
    except (tk.TclError, RuntimeError):
        return ""
