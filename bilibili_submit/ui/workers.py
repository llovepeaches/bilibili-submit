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
from typing import Any, Callable, Generic, TypeVar

T = TypeVar("T")

__all__ = ["Worker", "Cancelled"]


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
        task: Callable[[Callable[[str], None], Callable[[], bool]], T],
        on_progress: Callable[[str], None] | None = None,
        on_done: Callable[[T], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        """启动任务。

        Args:
            task: 真正干活的 callable。接收两个参数：
                ``report(message)`` 上报进度，``is_cancelled()`` 查询是否已取消。
            on_progress: 进度回调（主线程执行）。
            on_done: 成功回调（主线程执行）。
            on_error: 失败回调（主线程执行），含 :class:`Cancelled`。
        """
        if self.running:
            raise RuntimeError("已有任务在运行")

        self._cancel.clear()

        def report(message: str) -> None:
            # 工作线程 → 主线程：用 after 排队，不直接调 UI
            if on_progress:
                self._root.after(0, lambda: on_progress(message))

        def wrapped() -> None:
            try:
                value = task(report, self._cancel.is_set)
            # 注意：except 块结束时 Python 会 del 掉 exc（避免循环引用），
            # 而 after 是延迟执行的，直接闭包引用 exc 会拿到未定义名。
            # 用默认参数把值绑进 lambda，和后面的 i=index 是同一个道理。
            except BaseException as exc:  # noqa: BLE001 - 要兜住所有异常送回主线程
                if on_error:
                    self._root.after(0, lambda e=exc: on_error(e))
                return
            if on_done:
                self._root.after(0, lambda: on_done(value))

        self._thread = threading.Thread(target=wrapped, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """请求取消。只是置标志位，任务要自己周期性检查。"""
        self._cancel.set()

    def wait(self, timeout: float = 2.0) -> None:
        """等待线程结束，超时就放弃（守护线程不会挡住进程退出）。"""
        if self._thread is not None:
            self._thread.join(timeout)


def safe_after(root: tk.Misc, delay_ms: int, fn: Callable[..., Any], *args: Any) -> str:
    """注册一个延时回调，窗口已销毁时静默跳过。

    tkinter 在窗口销毁后仍执行排队的 ``after`` 会抛
    ``TclError: invalid command name``，关闭窗口时很容易踩到。
    """
    def wrapped() -> None:
        try:
            fn(*args)
        except tk.TclError:
            pass  # 窗口已关闭，回调没有意义了

    return root.after(delay_ms, wrapped)
