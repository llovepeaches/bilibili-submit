"""``BatchRunController`` 的直连单测。

这一组**不需要显示器**：控制器只通过注入的回调跟外界打交道，连表格都
可以换成替身。原先这些逻辑长在 ``TasksView`` 上，要验证「执行途中换掉
任务列表，本轮仍跑启动时那份快照」得先建 App、选目录、等扫描线程。

快照那条尤其值得单独测——它是「工作线程不许读页面上的可变属性」这条
纪律的唯一执行点，而且在界面上坏了是看不出来的：投错了分区，用户只会
觉得 B 站的分区推荐有点怪。
"""

from __future__ import annotations

from dataclasses import replace

from bilibili_submit.config import AppConfig, TaskConfig
from bilibili_submit.exceptions import BiliError, NotLoggedInError
from bilibili_submit.ui.views.tasks import SharedSubmitValues
from bilibili_submit.ui.views.tasks_run import BatchRunController
from bilibili_submit.ui.workers import Cancelled, Event

TID_DAILY = 21


class _FakeTree:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def get_children(self):
        return list(self._rows)


class _FakeTable:
    """只实现控制器用到的那几个方法。"""

    def __init__(self, rows=(), picked=None, tones=None):
        self.tree = _FakeTree(rows)
        self.picked = picked if picked is not None else {iid: True for iid in rows}
        self.missing: set[str] = set()
        self.errors: dict[str, str] = {}
        self.tones = tones if tones is not None else {}
        self.marked: list[tuple] = []

    def mark(self, index, status, tone="idle"):
        self.marked.append((index, status, tone))
        self.tones[str(index)] = tone

    def selected_indexes(self):
        return [
            int(iid)
            for iid in self.tree.get_children()
            if self.picked.get(iid) and iid not in self.missing
        ]


class _FakeLog:
    def __init__(self):
        self.lines: list[str] = []
        self.cleared = 0

    def append(self, text):
        self.lines.append(text)

    def clear(self):
        self.cleared += 1
        self.lines.clear()


class _FakeProgress:
    def __init__(self):
        self.value = None
        self.text = None
        self.reset_count = 0

    def set_value(self, value):
        self.value = value

    def set_text(self, text):
        self.text = text

    def reset(self):
        self.reset_count += 1
        self.value = None


class _FakeWorker:
    def __init__(self, fail_on_run=False):
        self.started = 0
        self.cancelled = 0
        self.fail_on_run = fail_on_run
        self.handlers: dict = {}

    def run(self, body, *, on_progress, on_done, on_error):
        self.started += 1
        self.handlers = {
            "on_progress": on_progress,
            "on_done": on_done,
            "on_error": on_error,
        }
        if self.fail_on_run:
            raise RuntimeError("线程起不来")

    def cancel(self):
        self.cancelled += 1


class _FakeCtx:
    def __init__(self, logged_in=True):
        self.logged_in = logged_in

    def client(self, need_login=True):
        return object()


class _FakeApp:
    def __init__(self, logged_in=True):
        self.ctx = _FakeCtx(logged_in)
        self.progress_texts: list[str] = []
        self.status_refreshed = 0

    def set_task_progress(self, text):
        self.progress_texts.append(text)

    def refresh_status(self):
        self.status_refreshed += 1


def _shared(**overrides):
    base = dict(
        tid=TID_DAILY,
        copyright=1,
        source="",
        tag="",
        desc="",
        dtime_offset_hours=None,
    )
    base.update(overrides)
    return SharedSubmitValues(**base)


def _controller(
    *,
    rows=("0", "1"),
    tasks=None,
    cfg=None,
    logged_in=True,
    picked=None,
    source_mode="folder",
    shared=None,
    table=None,
    worker=None,
    run_task=None,
    editable=True,
    tones=None,
):
    log = _FakeLog()
    progress = _FakeProgress()
    app = _FakeApp(logged_in)
    if table is None:
        table = _FakeTable(rows, picked if picked is not None else None, tones)
    worker = worker if worker is not None else _FakeWorker()
    tasks_holder = {"value": tasks if tasks is not None else _default_tasks()}
    busy: list[bool] = []
    summaries: list[int] = []
    selected_failed: list[int] = []
    active: dict = {"worker": None}

    controller = BatchRunController(
        app=app,
        worker=worker,
        table=table,
        log=log,
        progress=progress,
        read_tasks=lambda: tasks_holder["value"],
        read_cfg=lambda: cfg if cfg is not None else AppConfig(),
        read_source_mode=lambda: source_mode,
        collect_shared=lambda: shared if shared is not None else _shared(),
        set_busy=lambda flag, *a, **k: busy.append(flag),
        is_editable=lambda: editable,
        update_summary=lambda: summaries.append(1),
        select_failed=lambda: selected_failed.append(1),
        read_active_worker=lambda: active["worker"],
        write_active_worker=lambda w: active.__setitem__("worker", w),
        run_task=run_task or (lambda *a, **k: None),
        get_backend=lambda *a, **k: object(),
    )
    return controller, types(log, progress, app, table, worker, tasks_holder, busy,
                             summaries, selected_failed, active)


class types:  # noqa: N801 - 只是个装替身的小袋子
    def __init__(self, log, progress, app, table, worker, tasks_holder, busy,
                 summaries, selected_failed, active):
        self.log = log
        self.progress = progress
        self.app = app
        self.table = table
        self.worker = worker
        self.tasks_holder = tasks_holder
        self.busy = busy
        self.summaries = summaries
        self.selected_failed = selected_failed
        self.active = active


def _default_tasks():
    return [
        TaskConfig(name="a", type="single", file="/x/a.mp4", title=""),
        TaskConfig(name="b", type="single", file="/x/b.mp4", title=""),
    ]


# ---------- 启动前的校验 ----------


def test_run_refuses_to_start_when_not_logged_in():
    controller, t = _controller(logged_in=False)
    controller.run()

    assert t.worker.started == 0
    assert any("尚未登录" in line for line in t.log.lines)


def test_run_refuses_to_start_with_nothing_picked():
    controller, t = _controller(rows=(), picked={})
    controller.run()

    assert t.worker.started == 0
    assert any("没有勾选任何任务" in line for line in t.log.lines)


def test_run_refuses_to_start_while_locked():
    """界面还锁着说明上一轮没收尾——再起一个 Worker 会和收尾回调打架。"""
    controller, t = _controller(editable=False)
    controller.run()

    assert t.worker.started == 0


def test_run_reports_a_config_error_without_starting():
    """转载缺来源时必须在开始就拦住，而不是让 N 个稿件逐个失败。"""
    controller, t = _controller(shared=_shared(copyright=2, source=""))
    controller.run()

    assert t.worker.started == 0
    assert any("参数有误" in line for line in t.log.lines)


# ---------- 启动 ----------


def test_run_locks_the_ui_and_hands_over_the_worker():
    controller, t = _controller()
    controller.run()

    assert t.busy == [True]
    assert t.worker.started == 1
    assert t.active["worker"] is t.worker
    assert t.log.cleared == 1  # 上一轮的日志清掉，本轮从头记
    assert t.progress.reset_count == 1


def test_run_unlocks_the_ui_when_the_thread_fails_to_start():
    """线程没起来就不会有回调来收尾——不解锁的话界面会永久卡住。"""
    controller, t = _controller(worker=_FakeWorker(fail_on_run=True))
    controller.run()

    assert t.busy == [True, False]
    assert t.active["worker"] is None
    assert any("无法启动任务" in line for line in t.log.lines)


# ---------- 快照 ----------


def test_snapshot_applies_the_shared_values_in_folder_mode():
    controller, t = _controller(shared=_shared(tid=171, tag="游戏"))
    _cfg, selected = controller.snapshot_run([0])

    assert selected[0][1].tid == 171
    assert "游戏" in selected[0][1].tag


def test_snapshot_copies_tasks_so_later_changes_do_not_leak_in():
    """快照必须是副本——执行线程正逐项遍历时列表可能被整体换掉。"""
    controller, t = _controller()
    _cfg, selected = controller.snapshot_run([0, 1])

    t.tasks_holder["value"] = [TaskConfig(name="别的", type="single", file="/x/z.mp4")]

    assert [task.name for _i, task in selected] == ["a", "b"]


def test_snapshot_keeps_the_yaml_config_in_yaml_mode():
    """yaml 模式下逐任务元数据不能被统一参数覆盖掉。"""
    cfg = AppConfig(submit=replace(AppConfig().submit, backend="app"))
    controller, t = _controller(source_mode="yaml", cfg=cfg)
    _snapshot_cfg, _selected = controller.snapshot_run([0])

    assert _snapshot_cfg is not None
    assert _snapshot_cfg.submit.backend == "app"


def test_snapshot_rejects_reprint_without_a_source():
    controller, _t = _controller(shared=_shared(copyright=2, source=""))
    try:
        controller.snapshot_run([0])
    except BiliError as exc:
        assert "转载来源" in str(exc)
    else:
        raise AssertionError("转载缺来源时必须抛错")


# ---------- 工作线程 ----------


def test_do_run_uses_the_snapshot_not_the_live_list():
    """执行途中换掉任务列表，本轮投的仍该是启动时那份快照。

    没有快照的话，用户中途点「重新扫描」就会把任务投给错的分区。
    """
    seen = []

    def fake_run_task(client, task, cfg, **kwargs):
        seen.append(task.name)
        from bilibili_submit.scheduler import TaskOutcome

        return TaskOutcome(name=task.name, success=True, bvid="BV1", url="u")

    controller, t = _controller(run_task=fake_run_task)
    _cfg, selected = controller.snapshot_run([0, 1])

    # 用户在主线程把任务列表整个换掉
    t.tasks_holder["value"] = [TaskConfig(name="别的", type="single", file="/x/z.mp4")]

    controller.do_run(lambda _m: None, lambda: False, selected, _cfg)

    assert seen == ["a", "b"], f"本轮该跑快照里的 a、b，实际 {seen}"


def test_do_run_reports_position_not_index():
    """非连续勾选时 position 是「第几个」，不能填成下标。

    下标 3、5 被勾选（total=2）时若填成 index，进度会算出 200%/300%。
    """
    events = []

    def fake_run_task(client, task, cfg, **kwargs):
        from bilibili_submit.scheduler import TaskOutcome

        return TaskOutcome(name=task.name, success=True, bvid="BV1", url="u")

    tasks = [TaskConfig(name=f"n{i}", type="single", file=f"/x/{i}.mp4") for i in range(6)]
    table = _FakeTable(rows=[str(i) for i in range(6)])
    controller, _t = _controller(
        tasks=tasks, table=table, run_task=fake_run_task
    )
    _cfg, selected = controller.snapshot_run([3, 5])

    controller.do_run(events.append, lambda: False, selected, _cfg)

    # start 和 done 两条上报都要盯：「第几个」算错了的话，进度条会在
    # 第一条就跳到 150%，只看其中一条会漏
    starts = [e for e in events if isinstance(e, Event) and not e.done]
    dones = [e for e in events if isinstance(e, Event) and e.done]
    assert [(e.index, e.position, e.total) for e in starts] == [(3, 1, 2), (5, 2, 2)]
    assert [(e.index, e.position, e.total) for e in dones] == [(3, 1, 2), (5, 2, 2)]


def test_do_run_stops_immediately_when_cancelled():
    controller, _t = _controller(run_task=lambda *a, **k: None)
    _cfg, selected = controller.snapshot_run([0, 1])

    try:
        controller.do_run(lambda _m: None, lambda: True, selected, _cfg)
    except Cancelled:
        pass
    else:
        raise AssertionError("已取消时必须抛 Cancelled")


# ---------- 主线程回调 ----------


def test_on_progress_routes_a_string_to_the_log():
    controller, t = _controller()
    controller.on_progress("[1/2] 开始：a")

    assert t.log.lines == ["[1/2] 开始：a"]


def test_on_progress_marks_a_starting_row_as_busy():
    controller, t = _controller()
    controller.on_progress(Event("start", index=0, position=1, total=2))

    assert (0, "进行中", "busy") in t.table.marked
    assert t.app.progress_texts == ["进行中 1/2"]


def test_on_progress_marks_a_finished_row_and_keeps_the_full_error():
    """状态列放不下的完整原因要存起来——双击要看全文。"""
    controller, t = _controller()
    controller.on_progress(
        Event("done", index=1, position=2, total=2, status="失败：网络断了", error="网络断了")
    )

    assert (1, "失败：网络断了", "error") in t.table.marked
    assert t.table.errors["1"] == "网络断了"
    assert t.progress.value == 100


def test_on_done_unlocks_and_reports_the_tally():
    from bilibili_submit.scheduler import TaskOutcome

    controller, t = _controller()
    # 先摆成「正在跑」的样子：active_worker 初始就是 None，不预设的话
    # 「收尾时有没有把它清掉」这条断言永远成立，等于没测
    t.active["worker"] = t.worker
    outcomes = [
        TaskOutcome(name="a", success=True, bvid="BV1", url="u"),
        TaskOutcome(name="b", success=False, error="x"),
    ]
    controller.on_done(outcomes)

    assert t.busy[-1] is False
    assert t.active["worker"] is None, "收尾必须把当前 worker 清掉，否则取消会打到上一轮"
    assert t.progress.text == "完成 1/2"
    assert any("成功 1/2" in line for line in t.log.lines)


def test_on_error_reverts_the_busy_rows_when_cancelled():
    controller, t = _controller(rows=("0",), tones={"0": "busy"})
    controller.on_error(Cancelled())

    assert (0, "待投稿", "idle") in t.table.marked
    assert any("已取消" in line for line in t.log.lines)


def test_on_error_refreshes_the_login_status_when_the_cookie_dies():
    """登录态失效时状态栏那个「● 已登录」也得跟着改。"""
    controller, t = _controller()
    controller.on_error(NotLoggedInError("cookie 失效"))

    assert t.app.status_refreshed == 1
    assert any("登录态已失效" in line for line in t.log.lines)


def test_on_error_unlocks_even_for_an_unexpected_failure():
    """不收尾的话「进行中」的行会永远停在那里，界面看着像还在跑。"""
    controller, t = _controller()
    t.active["worker"] = t.worker
    controller.on_error(RuntimeError("boom"))

    assert t.busy[-1] is False
    assert t.active["worker"] is None


# ---------- 取消与重试 ----------


def test_cancel_stops_the_running_worker():
    controller, t = _controller()
    controller.run()
    controller.cancel()

    assert t.worker.cancelled == 1


def test_retry_failed_validates_before_changing_the_selection():
    """先校验再改选区——反过来写的话，未登录时用户那堆勾选就白丢了。"""
    controller, t = _controller(logged_in=False, tones={"0": "error"})
    controller.retry_failed()

    assert t.selected_failed == [], "未登录时不该动选区"


def test_retry_failed_picks_the_failed_rows_then_runs():
    controller, t = _controller(tones={"0": "error", "1": "ok"})
    controller.retry_failed()

    assert t.selected_failed == [1]
    assert t.worker.started == 1


def test_retry_failed_does_nothing_when_nothing_failed():
    controller, t = _controller(tones={"0": "ok"})
    controller.retry_failed()

    assert t.worker.started == 0


def test_failed_indexes_lists_only_the_error_rows():
    controller, _t = _controller(
        rows=("0", "1", "2"), tones={"0": "error", "1": "ok", "2": "error"}
    )
    assert controller.failed_indexes() == [0, 2]


# ---------- 收尾 ----------


def test_revert_busy_rows_reports_how_many_never_ran():
    controller, t = _controller(tones={"0": "busy", "1": "ok"})
    controller.revert_busy_rows("已取消")

    assert (0, "待投稿", "idle") in t.table.marked
    assert t.progress.text == "已取消，1 项未执行"


def test_revert_busy_rows_without_any_busy_row_keeps_the_text_plain():
    controller, t = _controller(tones={"0": "ok"})
    controller.revert_busy_rows("已取消")

    assert t.progress.text == "已取消"
