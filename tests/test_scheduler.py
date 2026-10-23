"""调度层的执行钩子。

``RunOptions`` 上的 ``on_task_start`` / ``on_task_done`` / ``should_stop``
是为 Web 版加的：那边的界面要「第 3 行变成执行中」这种结构化进度，
还要能取消——而这些都不在 ``on_progress`` 那一行文本里。

这三条是**纯增量**的（默认 None，行为与加之前一致），所以这里的用例
同时也钉住「不加它们时老行为不变」。
"""

from __future__ import annotations

import time
from unittest import mock

from bilibili_submit.config import AppConfig, TaskConfig
from bilibili_submit.scheduler import RunOptions, run_all

SENTINEL = object()


def _client() -> object:
    """一个够用的假 client——``run_task`` 只会在失败路径上碰它。"""
    return mock.MagicMock()


def test_task_hooks_receive_the_index_in_the_passed_list():
    """``on_task_start`` 的下标是**传入列表**里的下标。

    调用方可能只传了勾选的几个任务，想指回自己完整列表的行号要自己
    存映射（Web 端就是这么做的）。这里钉的是「不传映射时它就是入参
    下标」——别哪天改成全局计数。
    """
    tasks = [TaskConfig(name=f"t{i}", type="single", file="/x.mp4") for i in range(3)]
    starts: list[int] = []
    dones: list[int] = []

    with mock.patch("bilibili_submit.scheduler.run_task") as run_task:
        run_task.return_value = mock.MagicMock(success=True, bvid="BV1", url="u")
        run_all(
            _client(),
            tasks,
            AppConfig(),
            options=RunOptions(
                dry_run=True,  # 只走调度，不真调 run_task 内部
                on_task_start=lambda i, t: starts.append(i),
                on_task_done=lambda i, t, o: dones.append(i),
                history_file="",  # 别往真实历史文件里写
            ),
        )

    assert starts == [0, 1, 2]
    assert dones == [0, 1, 2]


def test_should_stop_prevents_the_next_task():
    """取消的语义是「不再开始下一个」，已在跑的那个会跑完。

    分片上传没有安全的中断点，硬停会留下半个稿件。这条用例把
    「停在哪里」说清楚，免得后来人以为它该立刻中断。
    """
    tasks = [TaskConfig(name=f"t{i}", type="single", file="/x.mp4") for i in range(3)]
    ran: list[str] = []

    def fake_run_task(client, task, cfg, backend=None, options=None):
        ran.append(task.name)
        return mock.MagicMock(success=True, bvid="BV1", url="u")

    with mock.patch("bilibili_submit.scheduler.run_task", fake_run_task):
        outcomes = run_all(
            _client(),
            tasks,
            AppConfig(),
            options=RunOptions(
                dry_run=True,
                history_file="",
                should_stop=lambda: len(ran) >= 1,
            ),
        )

    assert ran == ["t0"], "已在跑的第一个跑完后就该停，不再开始第二个"
    assert len(outcomes) == 1


def test_interval_sleep_can_be_interrupted():
    """间隔等待必须能被取消打断。

    间隔可能长达几十分钟。一次 ``time.sleep`` 睡到底的话，用户点了取消
    要等这么久才生效——那不叫取消。
    """
    options = RunOptions(should_stop=lambda: True)
    started = time.time()
    stopped = _interruptible(0, options)
    assert stopped is True
    assert time.time() - started < 1.0, "should_stop 为真时应立刻返回，不该真的睡"


def test_interval_sleep_returns_false_when_not_asked_to_stop():
    """没被要求停止时要睡够，不能因为加了取消就顺手把间隔也取消了——
    间隔是规避频控的，少了它批量投稿会被 601 打回。"""
    options = RunOptions(should_stop=lambda: False)
    started = time.time()
    assert _interruptible(0.02, options) is False
    assert time.time() - started >= 0.02


def _interruptible(seconds: float, options: RunOptions) -> bool:
    from bilibili_submit.scheduler import _interruptible_sleep

    return _interruptible_sleep(seconds, options)


def test_stop_on_error_still_stops_the_batch():
    """``stop_on_error`` 是老行为，加钩子不能把它弄丢。"""
    tasks = [
        TaskConfig(name="ok", type="single", file="/x.mp4"),
        TaskConfig(name="bad", type="single", file="/x.mp4"),
        TaskConfig(name="never", type="single", file="/x.mp4"),
    ]

    def fake_run_task(client, task, cfg, backend=None, options=None):
        from bilibili_submit.scheduler import TaskOutcome

        return TaskOutcome(
            name=task.name,
            success=task.name != "bad",
            error="" if task.name != "bad" else "炸了",
            bvid="BV1",
            url="u",
        )

    with mock.patch("bilibili_submit.scheduler.run_task", fake_run_task):
        outcomes = run_all(
            _client(), tasks, AppConfig(),
            options=RunOptions(dry_run=True, history_file=""),
        )
    assert [o.name for o in outcomes] == ["ok", "bad"]
