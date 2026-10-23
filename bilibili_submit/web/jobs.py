"""后台任务：一个线程跑投稿，浏览器通过队列拿事件。

核心层是**全同步**的：``scheduler.run_all`` 里 601 频控退避会
``time.sleep`` 最长 3600 秒，批量间隔也是 sleep。所以 Web 端不可能
在请求线程里等它——必须丢到后台线程，请求立刻返回 ``job_id``。

进度接口唯一：``RunOptions.on_progress``，只给文本。所以这里把文本
事件与结构化事件混在同一条队列里，浏览器按 ``type`` 区分。

取消是**协作式**的：只置一个 ``threading.Event``，由调用方周期性检查。
``run_all`` 内部的 sleep 不可打断（那是核心层为规避频控写死的），
所以「取消」的真实语义是**不再开始下一个任务**，已经在跑的那个会跑完。
这一点要在界面上说清楚，否则用户点了取消却看到任务还在动，会以为坏了。
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from .events import Event

__all__ = ["Job", "JobManager", "JobBusy"]

#: 一条事件在队列里等浏览器来取的最长时间。
#: 超过就丢——浏览器早就关了，攒着只会让内存涨。
_MAX_QUEUED = 2000


class JobBusy(RuntimeError):
    """已经有一个任务在跑。

    同一时刻只允许一个投稿 job：cookie 文件、历史文件都是单份的，
    两个投稿同时跑会互相覆盖。
    """


@dataclass
class Job:
    """一次投稿执行。

    Attributes:
        id: 浏览器用来轮询 / 订阅的标识。
        total: 本次要跑几个任务（决定进度分母）。
        state: ``running`` / ``done`` / ``cancelled`` / ``error``。
        started_at / finished_at: Unix 秒，给前端算耗时。
    """

    id: str
    total: int = 0
    state: str = "running"
    error: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    events: "queue.Queue[dict[str, Any]]" = field(
        default_factory=lambda: queue.Queue(maxsize=_MAX_QUEUED)
    )
    _cancel: threading.Event = field(default_factory=threading.Event)

    #: 任务名 → 结果。前端拿它把列表行标成成功/失败。
    results: dict[str, dict[str, Any]] = field(default_factory=dict)

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def emit(self, payload: dict[str, Any]) -> None:
        """往队列里放一条事件。满了就丢最旧的，不阻塞工作线程。"""
        try:
            self.events.put_nowait(payload)
        except queue.Full:  # pragma: no cover - 需要攒到 2000 条才触发
            try:
                self.events.get_nowait()
            except queue.Empty:
                return
            self.emit(payload)

    def emit_text(self, message: str) -> None:
        self.emit({"type": "log", "message": message, "at": time.time()})

    def emit_event(self, event: Event) -> None:
        payload = event.to_dict()
        payload["type"] = "event"
        payload["at"] = time.time()
        self.emit(payload)

    def finish(self, state: str, error: str = "") -> None:
        self.state = state
        self.error = error
        self.finished_at = time.time()
        # 放一个哨兵，让等待中的 SSE generator 立刻退出，
        # 不必等到下一次超时轮询。
        self.emit({"type": "end", "state": state, "error": error})

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "total": self.total,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "results": list(self.results.values()),
        }


class JobManager:
    """管一个（且只有一个）在跑的 job。

    Methods:
        start: 开一个 job 并在线程里跑 ``runner``。已有 job 在跑时抛
            :class:`JobBusy`。
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._current: Job | None = None
        self._lock = threading.Lock()

    @property
    def current(self) -> Job | None:
        return self._current

    def start(
        self,
        runner: Callable[[Job], None],
        total: int = 0,
    ) -> Job:
        """开一个 job。

        Args:
            runner: 真正干活的 callable，接收 :class:`Job`——它要自己
                调 ``job.emit_*`` 上报、检查 ``job.cancelled``、
                并在结束时调 ``job.finish``。
            total: 任务总数。
        """
        with self._lock:
            if self._current is not None and self._current.state == "running":
                raise JobBusy("已有一个投稿任务在跑")
            job = Job(id=uuid.uuid4().hex[:12], total=total)
            self._jobs[job.id] = job
            self._current = job

        def wrapped() -> None:
            try:
                runner(job)
            except BaseException as exc:  # noqa: BLE001
                # 兜住一切：线程里抛出去了浏览器那边永远等不到 end，
                # 界面会一直转圈。
                job.finish("error", f"{type(exc).__name__}: {exc}")

        threading.Thread(target=wrapped, daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.state != "running":
            return False
        job.cancel()
        return True
