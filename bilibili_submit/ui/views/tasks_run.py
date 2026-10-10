"""批量任务页的执行协作者。

从 ``views/tasks.py`` 里分出来的一整块：启动一轮投稿、做快照、驱动工作
线程、处理进度/完成/失败三种回调，以及取消与「只重试失败项」。

分出来的主要理由不是行数，而是**这一块有独立的生命周期**：它跨越了
「主线程启动 → 工作线程执行 → 回调回到主线程」三个阶段，跨阶段的约定
（什么能读、什么必须先快照）只在这一块内部才说得清。混在视图里时，
这些约定散落在 400 行中间，改的人很容易漏掉其中一条。

**不持有** ``TasksView``：它需要的外部信息全部从构造函数注入。

.. note::
   注入的回调一律用 ``lambda`` 包一层而不是直接传 bound method：bound
   method 在构造那一刻就固定了，测试往视图上 monkeypatch 换不掉它。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from ...config import AppConfig, TaskConfig
from ...exceptions import BiliError, NotLoggedInError
from ...scheduler import RunOptions, TaskOutcome, run_task
from ...submit import get_backend
from ..workers import Cancelled, Event
from .tasks_table import _truncate


class BatchRunController:
    """一轮「开始投稿」的完整生命周期。

    对外只暴露动作（``run`` / ``retry_failed`` / ``cancel``）和给 Worker
    用的回调（``do_run`` / ``on_progress`` / ``on_done`` / ``on_error``）。
    """

    def __init__(
        self,
        *,
        app: Any,
        worker: Any,
        table: Any,
        log: Any,
        progress: Any,
        read_tasks: Any,
        read_cfg: Any,
        read_source_mode: Any,
        collect_shared: Any,
        set_busy: Any,
        is_editable: Any,
        update_summary: Any,
        select_failed: Any,
        read_active_worker: Any,
        write_active_worker: Any,
        run_task: Any = None,
        get_backend: Any = None,
    ) -> None:
        self._app = app
        self._worker = worker
        self._table = table
        self._log = log
        self._progress = progress
        self._read_tasks = read_tasks
        self._read_cfg = read_cfg
        self._read_source_mode = read_source_mode
        self._collect_shared = collect_shared
        self._set_busy = set_busy
        self._is_editable = is_editable
        self._update_summary = update_summary
        self._select_failed = select_failed
        self._read_active_worker = read_active_worker
        self._write_active_worker = write_active_worker
        #: 业务入口。默认是本模块 import 进来的真函数，但可以在构造时
        #: 整个换掉——注入优先、模块级垫底，两边都不是在构造那一刻求值
        #: 的函数对象：``_invoke_*`` 每次调用才决定用谁，所以测试无论
        #: 是打本模块的名字还是传替身进来，都能生效。
        self._run_task = run_task
        self._get_backend = get_backend

        # 注意 ``active_worker`` 不在本对象上：它同时服务于「投稿执行」和
        # 「目录加载」两条流程，取消按钮要作用于当前那一个。放在视图上
        # 才是单一事实来源，这里通过回调读写。

    def _invoke_run_task(self, *args, **kwargs):
        """真正发起一次投稿：注入优先，没注入就用本模块 import 的真函数。

        **到调用这一刻才决定用谁**。构造时就把函数对象取出来存着的话，
        对象是死的，测试 monkeypatch 本模块的 ``run_task`` 换不掉它——
        那会让「已经挡掉网络调用」的测试静默走真实路径还报绿。
        """
        fn = self._run_task if self._run_task is not None else run_task
        return fn(*args, **kwargs)

    def _invoke_backend(self, *args, **kwargs):
        """同上，取后端实例。"""
        fn = self._get_backend if self._get_backend is not None else get_backend
        return fn(*args, **kwargs)

    # ---------- 启动 ----------

    def run(self) -> None:
        # 用 _is_editable 而不是 worker.running：界面还锁着就说明这一轮
        # 还没收尾，此时再启动一个 Worker 会和上一轮的收尾回调打架
        if not self._is_editable():
            return
        indexes = self._table.selected_indexes()
        if not indexes:
            self._log.append("没有勾选任何任务")
            return
        if not self._app.ctx.logged_in:
            self._log.append("尚未登录，请先到「登录」页扫码")
            return
        try:
            cfg, selected = self.snapshot_run(indexes)
        except BiliError as exc:
            self._log.append(f"参数有误：{exc}")
            return

        self._set_busy(True)
        try:
            self._log.clear()
            self._progress.reset()
            self._write_active_worker(self._worker)
            self._worker.run(
                lambda report, is_cancelled: self.do_run(
                    report, is_cancelled, selected, cfg
                ),
                on_progress=self.on_progress,
                on_done=self.on_done,
                on_error=self.on_error,
            )
        except Exception as exc:  # noqa: BLE001 - 启动失败必须把界面解锁
            # 线程没起来就不会有 on_done/on_error 来收尾，busy 会永久
            # 卡在 True——「开始投稿」灰着、取消点不动，只能重启进程。
            # 这里与 on_error 做同样的归位。
            self._set_busy(False)
            self._write_active_worker(None)
            self._log.append(f"无法启动任务：{exc}")

    def retry_failed(self) -> None:
        """只重试上一轮失败的项。

        顺序很重要：**先校验再改选区**。反过来写的话，未登录时
        ``run`` 会直接 return，用户原来那一堆勾选已经被「只选失败项」
        覆盖掉了，白丢一次选择。
        """
        if not self.failed_indexes():
            return
        if not self._app.ctx.logged_in:
            self._log.append("尚未登录，请先到「登录」页扫码")
            return
        self._select_failed()
        self.run()

    def failed_indexes(self) -> list[int]:
        """上一轮失败项的下标（不含文件缺失的）。"""
        return [
            int(iid)
            for iid in self._table.tree.get_children()
            if self._table.tones.get(iid) == "error"
        ]

    def cancel(self) -> None:
        worker = self._read_active_worker()
        if worker:
            worker.cancel()
        self._log.append("已请求取消…")

    def snapshot_run(
        self, indexes: list[int]
    ) -> tuple[AppConfig, list[tuple[int, TaskConfig]]]:
        """为本轮执行做完整快照。

        **工作线程不许读页面上的可变属性**。``self._tasks`` / ``self._cfg``
        随时可能被下一次「重新扫描」整体换掉，而执行线程正逐项遍历它——
        换掉一半的话，投出去的是新任务里的一半、参数却还是旧的。所以这里
        在主线程把要用的东西全拷出来再传进去。
        """
        source_mode = self._read_source_mode()
        cfg = replace(self._read_cfg()) if source_mode == "yaml" else AppConfig()
        if source_mode == "folder":
            shared = self._collect_shared()
            # 转载缺来源时，服务端对每个稿件都回一句 21004——与其让整批
            # 逐个失败、列表一片红，不如在开始前就拦住
            if shared.copyright == 2 and not shared.source.strip():
                raise BiliError("选了「转载」就必须填转载来源（原视频链接或出处）")
            selected = [
                (index, shared.apply(self._read_tasks()[index])) for index in indexes
            ]
        else:
            selected = [
                (index, replace(self._read_tasks()[index])) for index in indexes
            ]
        return cfg, selected

    # ---------- 工作线程 ----------

    def do_run(
        self,
        report,
        is_cancelled,
        selected: list[tuple[int, TaskConfig]],
        cfg: AppConfig,
    ) -> list[TaskOutcome]:
        """依次执行勾选的任务（工作线程）。

        UI 更新一律走 ``report``：文字进度直接是字符串，
        行状态与进度条用 :class:`~..workers.Event`。
        """
        client = self._app.ctx.client()
        backend = self._invoke_backend(cfg.submit.backend, cfg.submit.app)
        outcomes: list[TaskOutcome] = []

        total = len(selected)
        for position, (index, task) in enumerate(selected, start=1):
            if is_cancelled():
                raise Cancelled()

            report(Event("start", index=index, position=position, total=total))
            report(f"[{position}/{total}] 开始：{task.name}")

            outcome = self._invoke_run_task(
                client,
                task,
                cfg,
                backend=backend,
                options=RunOptions(on_progress=report),
            )
            outcomes.append(outcome)

            if outcome.success:
                status, error = "成功", ""
            else:
                status, error = f"失败：{outcome.error}", outcome.error

            report(
                Event(
                    "done",
                    index=index,
                    position=position,
                    total=total,
                    status=status,
                    error=error,
                )
            )
            report(f"[{position}/{total}] {task.name} → {status}")

        return outcomes

    # ---------- 主线程回调 ----------

    def on_progress(self, message: Any) -> None:
        """分发工作线程的上报：Event 更新界面，字符串进日志。"""
        if isinstance(message, Event):
            # 注意：算进度只能用 position（本次执行里的第几个），
            # 不能用 index（下标）——见 Event 的文档。
            if not message.done:
                self._table.mark(message.index, "进行中", "busy")
                self._set_task_status(
                    f"进行中 {message.position}/{message.total}"
                )
            else:
                tone = "ok" if message.succeeded else "error"
                self._table.mark(message.index, _truncate(message.status), tone)
                self._table.errors[str(message.index)] = message.error
                self._progress.set_value(message.percent)
                self._set_task_status(f"{message.position}/{message.total}")
            # ⚠️ 这里**不要**再调一次 _update_summary。
            # mark() 内部已经通过 on_changed 通知过一次（TaskTable 把它
            # 和行状态绑在一起，就是怕「列表里有两个红的、汇总说 0 失败」）。
            # 每个任务来 2 个 Event，重复一次就等于把全表多扫一遍——
            # 跑几百个任务时这段是最密的循环。
            return
        self._log.append(message)

    def _set_task_status(self, text: str) -> None:
        """把进度同步到窗口底部状态栏。

        用户可以切到「历史」页去看之前的记录，状态栏是唯一一直可见的
        地方，在那里报进度就不用切回来盯。
        """
        setter = getattr(self._app, "set_task_progress", None)
        if setter:
            setter(text)

    def on_done(self, outcomes: list[TaskOutcome]) -> None:
        self._set_busy(False)
        self._write_active_worker(None)
        ok = sum(1 for o in outcomes if o.success)
        self._progress.set_value(100)
        self._progress.set_text(f"完成 {ok}/{len(outcomes)}")
        self._set_task_status(f"完成 {ok}/{len(outcomes)}")
        # 一次性写完：跑完几百个任务时这里是最后一批日志，逐条 append
        # 会让每条都各自排一次滚动定位。
        lines = [f"全部结束：成功 {ok}/{len(outcomes)}"]
        lines += [
            f"  {outcome.bvid}  {outcome.url}"
            for outcome in outcomes
            if outcome.success
        ]
        self._log.extend(lines)
        self._update_summary()

    def on_error(self, exc: BaseException) -> None:
        self._set_busy(False)
        self._write_active_worker(None)
        if isinstance(exc, Cancelled):
            self.revert_busy_rows("已取消")
            self._log.append("已取消")
            return
        # 非取消的异常（登录态失效、网络断了、ffmpeg 缺失抛的错）同样要收尾：
        # 不收的话「进行中」的行会永远停在那里、进度条也不复位，
        # 界面看着像还在跑，用户既不知道发生了什么也不知道怎么恢复。
        self.revert_busy_rows("已中断")
        if isinstance(exc, NotLoggedInError):
            self._log.append("登录态已失效，请到「登录」页重新扫码")
            # 登录态变了，状态栏那个「● 已登录」也得跟着改
            self._refresh_app_status()
        else:
            self._log.append(f"错误：{exc}")

    def _refresh_app_status(self) -> None:
        """让主窗口刷新登录态显示（没有这个方法时静默跳过）。"""
        refresh = getattr(self._app, "refresh_status", None)
        if refresh:
            refresh()

    def revert_busy_rows(self, reason: str = "已取消") -> None:
        """把「进行中」的行恢复成待投稿，并复位进度条。

        不处理的话，那一批会永远停在「进行中」，进度条也不复位——
        看着像还在跑。取消和异常都要走这里，所以文案由调用方给。
        """
        pending = 0
        for iid in self._table.tree.get_children():
            if self._table.tones.get(iid) == "busy":
                self._table.mark(int(iid), "待投稿", "idle")
                pending += 1
        self._progress.reset()
        if pending:
            text = f"{reason}，{pending} 项未执行"
        else:
            text = reason
        self._progress.set_text(text)
        self._set_task_status(text)
        self._update_summary()
