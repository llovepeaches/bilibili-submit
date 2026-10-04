"""批量任务视图：加载配置文件，挑出要投稿的任务并依次执行。

这个页要解决的核心问题是**可控**：

- 配置里可能有几十个任务，但这一轮往往只想跑其中几个
  （比如上次失败的那几个），所以列表可勾选。
- 跑之前就知道哪些文件根本不存在，而不是跑到一半才炸。
- 跑的过程中每一行实时变色，扫一眼就知道成败。
- 失败了能一键只重试失败项，不用手动挑。

线程纪律：执行在工作线程，UI 更新一律经 :class:`~..workers.Event`
由 ``report`` 送回主线程——**不在工作线程里直接调 Tk**。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from typing import Any

from ...config import expand_tasks, load_config
from ...exceptions import BiliError, ConfigError, NotLoggedInError
from ...scheduler import RunOptions, TaskOutcome, run_task
from ...submit import get_backend
from .. import theme
from ..widgets import (
    Card,
    LogConsole,
    Placeholder,
    PrimaryButton,
    ProgressBar,
    SecondaryButton,
    SectionTitle,
    SummaryBar,
)
from ..workers import Cancelled, Event, Worker

__all__ = ["TasksView"]

#: 勾选列 + 原来的四列
COLUMNS = ("pick", "name", "file", "tid", "status")
_HEADINGS = {
    "pick": "✓",
    "name": "任务",
    "file": "文件",
    "tid": "分区",
    "status": "状态",
}
_WIDTHS = {"pick": 34, "name": 150, "file": 240, "tid": 56, "status": 130}

PICKED = "✓"
UNPICKED = ""

#: 状态列最多显示多少字。超出的部分存起来，双击看全文
STATUS_MAX = 18


def _truncate(text: str, limit: int = STATUS_MAX) -> str:
    """截断长文本，保留尾部信息（错误原因的尾巴通常更有用）。

    不从头截：``601 投稿过于频繁，等待 30 分钟后重试`` 截成
    ``601 投稿过于频繁…`` 比 ``…30 分钟后重试`` 更好懂。
    """
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


class TasksView(ttk.Frame):
    """批量投稿页。"""

    def __init__(self, master: tk.Misc, app: "object") -> None:
        super().__init__(master, style="TFrame")
        self.app = app
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._worker = Worker(self)
        self._tasks: list[Any] = []
        self._cfg: Any = None
        #: 界面是否处于执行态。由 :meth:`_set_busy` 维护，和
        #: ``_worker.running`` 分工：一个管界面，一个管线程。
        self._busy = False
        #: iid -> 是否勾选
        self._picked: dict[str, bool] = {}
        #: 文件不存在的 iid
        self._missing: set[str] = set()
        #: iid -> 完整错误原因（状态列放不下）
        self._errors: dict[str, str] = {}
        #: iid -> 语义色名，见 :data:`~.theme.TONES`
        self._tones: dict[str, str] = {}

        self._build()

    # ---------- 布局 ----------

    def _build(self) -> None:
        SectionTitle(
            self,
            "批量任务",
            "加载配置文件后勾选要投稿的任务。执行前会先检查文件是否存在。",
        ).grid(row=0, column=0, sticky="ew", pady=(0, theme.PAD_MD))

        card = Card(self)
        card.grid(row=1, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(2, weight=1)

        # 配置文件选择
        picker = ttk.Frame(card, style="Card.TFrame")
        picker.grid(row=0, column=0, sticky="ew")
        picker.columnconfigure(0, weight=1)

        self._path_var = tk.StringVar()
        ttk.Entry(picker, textvariable=self._path_var).grid(
            row=0, column=0, sticky="ew", padx=(0, theme.PAD_SM)
        )
        self._pick_button = SecondaryButton(picker, "选择配置…", self._pick_config)
        self._pick_button.grid(row=0, column=1)
        self._load_button = SecondaryButton(picker, "加载", self._load)
        self._load_button.grid(row=0, column=2, padx=(theme.PAD_SM, 0))

        # 汇总条：左边统计，右边快捷选择
        self._summary = SummaryBar(card)
        self._summary.grid(row=1, column=0, sticky="ew", pady=(theme.PAD_MD, theme.PAD_XS))
        self._summary.add_action("全选", self._select_all)
        self._summary.add_action("全不选", self._select_none)
        self._summary.add_action("只选失败项", self._select_failed)

        # 任务列表
        list_holder = ttk.Frame(card, style="Card.TFrame")
        list_holder.grid(
            row=2, column=0, sticky="nsew", pady=(theme.PAD_XS, theme.PAD_SM)
        )
        list_holder.columnconfigure(0, weight=1)
        list_holder.rowconfigure(0, weight=1)

        self._tree = ttk.Treeview(
            list_holder, columns=COLUMNS, show="headings", height=8
        )
        for column in COLUMNS:
            self._tree.heading(column, text=_HEADINGS[column])
            self._tree.column(
                column,
                width=_WIDTHS[column],
                stretch=(column == "file"),
                anchor="center" if column == "pick" else "w",
            )
        # 行着色的唯一来源
        theme.apply_tree_tags(self._tree)
        self._tree.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(list_holder, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")

        # 点首列切换勾选；表头点全选/全不选
        self._tree.bind("<Button-1>", self._on_tree_click)
        self._tree.bind("<space>", self._on_space)
        # 双击看完整错误原因
        self._tree.bind("<Double-1>", self._show_error_detail)

        self._placeholder = Placeholder(
            card, "尚未加载配置文件", "选择配置…", self._pick_config
        )
        self._placeholder.grid(row=2, column=0, sticky="nsew")

        # 操作区
        actions = ttk.Frame(card, style="Card.TFrame")
        actions.grid(row=3, column=0, sticky="ew")

        self._run_button = PrimaryButton(actions, "开始投稿", self._run)
        self._run_button.pack(side="left", padx=(0, theme.PAD_SM))
        self._run_button.state(["disabled"])

        self._retry_button = SecondaryButton(actions, "重试失败项", self._retry_failed)
        self._retry_button.pack(side="left", padx=(0, theme.PAD_SM))
        self._retry_button.state(["disabled"])

        self._cancel_button = SecondaryButton(actions, "取消", self._cancel)
        self._cancel_button.pack(side="left")
        self._cancel_button.state(["disabled"])

        self._progress = ProgressBar(card)
        self._progress.grid(row=4, column=0, sticky="ew", pady=(theme.PAD_SM, 0))

        self._log = LogConsole(card, height=7)
        self._log.grid(row=5, column=0, sticky="nsew", pady=(theme.PAD_SM, 0))
        card.rowconfigure(5, weight=1)

    # ---------- 行为 ----------

    def refresh(self) -> None:
        """切回本页时若已加载过配置，保持列表不变。"""

    def _pick_config(self) -> None:
        if not self._editable():
            self._log.append("正在执行任务，结束后才能切换配置")
            return
        path = filedialog.askopenfilename(
            title="选择配置文件",
            filetypes=[("YAML 配置", "*.yaml *.yml"), ("所有文件", "*.*")],
        )
        if path:
            self._path_var.set(path)
            self._load()

    def _load(self) -> None:
        # 执行途中换配置会把 self._tasks / self._cfg 整体换掉，而工作线程
        # 正逐项读这两个属性——轻则把任务投给了错的分区，重则直接 IndexError。
        # 所以这里必须挡住，「选择配置…」按钮也一并禁用。
        if not self._editable():
            self._log.append("正在执行任务，结束后才能加载配置")
            return
        raw = self._path_var.get().strip()
        if not raw:
            self._log.append("请先选择配置文件")
            return
        try:
            cfg = load_config(Path(raw).expanduser())
            tasks = expand_tasks(cfg)
        except (ConfigError, BiliError) as exc:
            self._log.append(f"配置加载失败：{exc}")
            return

        self.app.ctx.config_path = raw
        self._tasks = tasks
        self._cfg = cfg
        self._reset_state()
        self._fill_tree(tasks)
        self._run_button.state(["!disabled"])
        self._log.append(f"已加载 {len(tasks)} 个任务")
        if self._missing:
            self._log.append(
                f"其中 {len(self._missing)} 个文件不存在，已标为「缺失」且不可勾选"
            )

    def _reset_state(self) -> None:
        """清空上一轮的选择与结果。"""
        self._picked.clear()
        self._missing.clear()
        self._errors.clear()
        self._tones.clear()

    def _fill_tree(self, tasks: list[Any]) -> None:
        self._tree.delete(*self._tree.get_children())
        for index, task in enumerate(tasks):
            iid = str(index)
            file_path = Path(task.file or "")
            exists = bool(task.file) and file_path.is_file()

            if exists:
                file_text = file_path.name
                status = "待投稿"
                tone = "idle"
                # 正常任务默认勾选——大多数情况用户就是要跑全部
                self._picked[iid] = True
            else:
                file_text = f"[缺] {file_path.name or '(未设置)'}"
                status = "文件缺失"
                tone = "missing"
                self._missing.add(iid)
                # 缺失的不预选，也拒绝后续勾选
                self._picked[iid] = False

            self._tones[iid] = tone
            self._tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    PICKED if self._picked[iid] else UNPICKED,
                    task.name,
                    file_text,
                    task.tid or "",
                    status,
                ),
                tags=(tone,),
            )

        if tasks:
            self._placeholder.grid_remove()
            self._tree.grid()
        else:
            self._tree.grid_remove()
            self._placeholder.grid()

        self._update_summary()

    # ---------- 勾选 ----------

    def _on_tree_click(self, event: tk.Event) -> None:
        """点首列切换勾选；点表头首列则全选/全不选。"""
        region = self._tree.identify_region(event.x, event.y)
        if region == "heading":
            if self._tree.identify_column(event.x) == "#1":
                self._select_all() if self._any_unpicked() else self._select_none()
            return
        if region != "cell":
            return
        if self._tree.identify_column(event.x) != "#1":
            return
        iid = self._tree.identify_row(event.y)
        if iid:
            self._toggle(iid)

    def _on_space(self, _event: tk.Event) -> str:
        """空格切换当前行的勾选。"""
        focused = self._tree.focus()
        if focused:
            self._toggle(focused)
        return "break"

    def _toggle(self, iid: str) -> None:
        """切换一行的勾选状态。

        文件缺失的行**拒绝勾选**：勾了却在执行时被跳过最让人困惑，
        不如一开始就勾不上。
        """
        if iid in self._missing:
            self._log.append("该文件不存在，无法勾选")
            return
        if not self._editable():
            return
        self._picked[iid] = not self._picked.get(iid, False)
        self._refresh_pick_cell(iid)
        self._update_summary()

    def _refresh_pick_cell(self, iid: str) -> None:
        values = list(self._tree.item(iid, "values"))
        values[0] = PICKED if self._picked.get(iid) else UNPICKED
        self._tree.item(iid, values=values)

    def _set_pick(self, iid: str, picked: bool) -> None:
        if iid in self._missing:
            picked = False
        self._picked[iid] = picked

    def _editable(self) -> bool:
        """当前是否允许改动勾选状态。

        执行期间一律锁定：``_run`` 在启动时把 ``indexes`` 快照下来，
        之后 ``_do_run`` 跑的是那份快照。此时让用户改勾选，界面上看到的
        选择和实际执行的列表会**静默脱节**——用户以为重跑了勾的那几个，
        其实跑的还是启动时那几个。

        .. important::
           要同时看 :attr:`_busy` 和 :attr:`~..workers.Worker.running`，
           两者不是一回事：工作线程已经退出、但 ``on_done`` 排队的
           ``after`` 还没执行的那一小段时间里，``running`` 是 False 而
           界面还是运行态。只看线程状态的话，这段时间里用户能改勾选，
           还能直接启动第二轮，和刚结束的那次串在一起。
        """
        return not (self._busy or self._worker.running)

    def _apply_selection(self, predicate: "Any") -> None:
        """按谓词批量设置勾选，然后刷新首列与汇总。"""
        if not self._editable():
            return
        for iid in self._tree.get_children():
            self._set_pick(iid, predicate(iid))
            self._refresh_pick_cell(iid)
        self._update_summary()

    def _select_all(self) -> None:
        self._apply_selection(lambda iid: True)

    def _select_none(self) -> None:
        self._apply_selection(lambda iid: False)

    def _select_failed(self) -> None:
        """只勾失败项（不含文件缺失的）。"""
        self._apply_selection(lambda iid: self._tones.get(iid) == "error")

    def _any_unpicked(self) -> bool:
        """是否存在可勾但未勾的项——决定点表头是全选还是全不选。"""
        return any(
            not self._picked.get(iid, False)
            for iid in self._tree.get_children()
            if iid not in self._missing
        )

    # ---------- 执行 ----------

    def _selected_indexes(self) -> list[int]:
        """当前勾选的任务下标，按列表顺序。"""
        return [
            int(iid)
            for iid in self._tree.get_children()
            if self._picked.get(iid) and iid not in self._missing
        ]

    def _run(self) -> None:
        # 用 _editable 而不是 _worker.running：界面还锁着就说明这一轮
        # 还没收尾，此时再启动一个 Worker 会和上一轮的收尾回调打架
        if not self._editable():
            return
        indexes = self._selected_indexes()
        if not indexes:
            self._log.append("没有勾选任何任务")
            return
        if not self.app.ctx.logged_in:
            self._log.append("尚未登录，请先到「登录」页扫码")
            return

        self._set_busy(True)
        self._log.clear()
        self._progress.reset()
        self._worker.run(
            lambda report, is_cancelled: self._do_run(report, is_cancelled, indexes),
            on_progress=self._on_progress,
            on_done=self._on_done,
            on_error=self._on_error,
        )

    def _retry_failed(self) -> None:
        """只重试上一轮失败的项。

        顺序很重要：**先校验再改选区**。反过来写的话，未登录时
        ``_run`` 会直接 return，用户原来那一堆勾选已经被「只选失败项」
        覆盖掉了，白丢一次选择。
        """
        if not self._failed_indexes():
            return
        if not self.app.ctx.logged_in:
            self._log.append("尚未登录，请先到「登录」页扫码")
            return
        self._select_failed()
        self._run()

    def _failed_indexes(self) -> list[int]:
        """上一轮失败项的下标（不含文件缺失的）。"""
        return [
            int(iid)
            for iid in self._tree.get_children()
            if self._tones.get(iid) == "error"
        ]

    def _cancel(self) -> None:
        self._worker.cancel()
        self._log.append("已请求取消…")

    def _do_run(self, report, is_cancelled, indexes: list[int]) -> list[TaskOutcome]:
        """依次执行勾选的任务（工作线程）。

        UI 更新一律走 ``report``：文字进度直接是字符串，
        行状态与进度条用 :class:`~..workers.Event`。
        """
        client = self.app.ctx.client()
        backend = get_backend(self._cfg.submit.backend, self._cfg.submit.app)
        outcomes: list[TaskOutcome] = []

        total = len(indexes)
        for position, index in enumerate(indexes):
            if is_cancelled():
                raise Cancelled()

            task = self._tasks[index]
            report(Event("start", index=index, position=position + 1, total=total))
            report(f"[{position + 1}/{total}] 开始：{task.name}")

            outcome = run_task(
                client,
                task,
                self._cfg,
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
                    position=position + 1,
                    total=total,
                    status=status,
                    error=error,
                )
            )
            report(f"[{position + 1}/{total}] {task.name} → {status}")

        return outcomes

    # ---------- 主线程回调 ----------

    def _on_progress(self, message: "Any") -> None:
        """分发工作线程的上报：Event 更新界面，字符串进日志。"""
        if isinstance(message, Event):
            # 注意：算进度只能用 position（本次执行里的第几个），
            # 不能用 index（下标）——见 Event 的文档。
            if not message.done:
                self._mark(message.index, "进行中", "busy")
                self._set_task_status(
                    f"进行中 {message.position}/{message.total}"
                )
            else:
                tone = "ok" if message.succeeded else "error"
                self._mark(message.index, _truncate(message.status), tone)
                self._errors[str(message.index)] = message.error
                self._progress.set_value(message.percent)
                self._set_task_status(f"{message.position}/{message.total}")
            self._update_summary()
            return
        self._log.append(message)

    def _set_task_status(self, text: str) -> None:
        """把进度同步到窗口底部状态栏。

        用户可以切到「历史」页去看之前的记录，状态栏是唯一一直可见的
        地方，在那里报进度就不用切回来盯。
        """
        setter = getattr(self.app, "set_task_progress", None)
        if setter:
            setter(text)

    def _mark(self, index: int, status: str, tone: str = "idle") -> None:
        """更新某一行的状态文案与配色，并同步汇总条。

        汇总放在这里更新而不是让调用方记得调：行状态一变，汇总里的
        成败计数就必须跟着变，分成两步迟早会忘，界面上就会出现
        「列表里明明有两个红的，汇总却说 0 失败」。
        """
        iid = str(index)
        if not self._tree.exists(iid):
            return
        values = list(self._tree.item(iid, "values"))
        if len(values) != len(COLUMNS):
            return
        values[-1] = status
        self._tones[iid] = tone
        # 每行只打一个 tag——避免多个 tag 的背景色互相打架
        self._tree.item(iid, values=values, tags=(tone,))
        self._update_summary()

    def _on_done(self, outcomes: list[TaskOutcome]) -> None:
        self._set_busy(False)
        ok = sum(1 for o in outcomes if o.success)
        self._progress.set_value(100)
        self._progress.set_text(f"完成 {ok}/{len(outcomes)}")
        self._set_task_status(f"完成 {ok}/{len(outcomes)}")
        self._log.append(f"全部结束：成功 {ok}/{len(outcomes)}")
        for outcome in outcomes:
            if outcome.success:
                self._log.append(f"  {outcome.bvid}  {outcome.url}")
        self._update_summary()

    def _on_error(self, exc: BaseException) -> None:
        self._set_busy(False)
        if isinstance(exc, Cancelled):
            self._revert_busy_rows("已取消")
            self._log.append("已取消")
            return
        # 非取消的异常（登录态失效、网络断了、ffmpeg 缺失抛的错）同样要收尾：
        # 不收的话「进行中」的行会永远停在那里、进度条也不复位，
        # 界面看着像还在跑，用户既不知道发生了什么也不知道怎么恢复。
        self._revert_busy_rows("已中断")
        if isinstance(exc, NotLoggedInError):
            self._log.append("登录态已失效，请到「登录」页重新扫码")
            # 登录态变了，状态栏那个「● 已登录」也得跟着改
            self._refresh_app_status()
        else:
            self._log.append(f"错误：{exc}")

    def _refresh_app_status(self) -> None:
        """让主窗口刷新登录态显示（没有这个方法时静默跳过）。"""
        refresh = getattr(self.app, "refresh_status", None)
        if refresh:
            refresh()

    def _revert_busy_rows(self, reason: str = "已取消") -> None:
        """把「进行中」的行恢复成待投稿，并复位进度条。

        不处理的话，那一批会永远停在「进行中」，进度条也不复位——
        看着像还在跑。取消和异常都要走这里，所以文案由调用方给。
        """
        pending = 0
        for iid in self._tree.get_children():
            if self._tones.get(iid) == "busy":
                self._mark(int(iid), "待投稿", "idle")
                pending += 1
        self._progress.reset()
        if pending:
            text = f"{reason}，{pending} 项未执行"
        else:
            text = reason
        self._progress.set_text(text)
        self._set_task_status(text)
        self._update_summary()

    def _show_error_detail(self, event: tk.Event) -> None:
        """双击某一行，看完整失败原因。"""
        if self._tree.identify_region(event.x, event.y) != "cell":
            return
        iid = self._tree.identify_row(event.y)
        if not iid:
            return
        error = self._errors.get(iid)
        if not error:
            return
        values = self._tree.item(iid, "values")
        name = values[1] if len(values) > 1 else iid
        messagebox.showerror(f"失败原因 · {name}", error)

    def _set_busy(self, busy: bool) -> None:
        """切换运行态下的按钮可用性。

        汇总条里的「全选 / 只选失败项」也要一起锁——它们最终都落到
        :meth:`_apply_selection`，锁按钮和锁逻辑双保险，界面上不会
        出现「按了没反应」的按钮。
        """
        self._busy = busy
        self._run_button.state(["disabled"] if busy else ["!disabled"])
        self._retry_button.state(["disabled"] if busy else ["!disabled"])
        self._cancel_button.state(["!disabled"] if busy else ["disabled"])
        self._pick_button.state(["disabled"] if busy else ["!disabled"])
        self._load_button.state(["disabled"] if busy else ["!disabled"])
        self._summary.set_actions_enabled(not busy)

    # ---------- 汇总 ----------

    def _update_summary(self) -> None:
        """刷新汇总条：共多少、选了多少、成败各多少。"""
        total = len(self._tree.get_children())
        if not total:
            self._summary.set_stats("")
            self._retry_button.state(["disabled"])
            return

        picked = sum(1 for iid in self._tree.get_children() if self._picked.get(iid))
        tones = [self._tones.get(iid, "idle") for iid in self._tree.get_children()]
        parts = [f"共 {total}", f"已选 {picked}"]
        for label, tone_name in (("成功", "ok"), ("失败", "error")):
            count = tones.count(tone_name)
            if count:
                parts.append(f"{label} {count}")
        if self._missing:
            parts.append(f"缺失 {len(self._missing)}")

        self._summary.set_stats("  ·  ".join(parts))
        # 有失败项才让「重试失败项」可点；但运行中一律锁住——
        # 每完成一项 _mark 都会触发本方法，不在这里判 running 的话，
        # 执行途中出现第一个失败项时按钮就会被解开，而 _run 会因为
        # 「已有任务在运行」直接 return，变成按了没反应的假按钮。
        if tones.count("error") and self._editable():
            self._retry_button.state(["!disabled"])
        else:
            self._retry_button.state(["disabled"])
