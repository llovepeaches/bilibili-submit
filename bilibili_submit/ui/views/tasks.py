"""批量任务视图：加载配置文件，列出待投稿任务并执行。

列表用 Treeview 展示，执行时逐条更新状态列——
比「跑完再一次性显示结果」更让人安心，长任务尤其明显。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk
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
)
from ..workers import Cancelled, Worker

__all__ = ["TasksView"]

COLUMNS = ("name", "file", "tid", "status")
_HEADINGS = {"name": "任务", "file": "文件", "tid": "分区", "status": "状态"}
_WIDTHS = {"name": 150, "file": 260, "tid": 60, "status": 120}


class TasksView(ttk.Frame):
    """批量投稿页。"""

    def __init__(self, master: tk.Misc, app: "object") -> None:
        super().__init__(master, style="TFrame")
        self.app = app
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._worker = Worker(self)
        self._tasks: list[Any] = []
        self._build()

    # ---------- 布局 ----------

    def _build(self) -> None:
        SectionTitle(
            self,
            "批量任务",
            "加载配置文件（config.yaml）后按其中的任务列表依次投稿。",
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
        SecondaryButton(picker, "选择配置…", self._pick_config).grid(row=0, column=1)
        SecondaryButton(picker, "加载", self._load).grid(row=0, column=2, padx=(theme.PAD_SM, 0))

        # 任务列表
        list_holder = ttk.Frame(card, style="Card.TFrame")
        list_holder.grid(row=1, column=0, sticky="nsew", pady=(theme.PAD_MD, theme.PAD_SM))
        list_holder.columnconfigure(0, weight=1)
        list_holder.rowconfigure(0, weight=1)

        self._tree = ttk.Treeview(
            list_holder, columns=COLUMNS, show="headings", height=8
        )
        for column in COLUMNS:
            self._tree.heading(column, text=_HEADINGS[column])
            self._tree.column(column, width=_WIDTHS[column], stretch=(column == "file"))
        self._tree.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(list_holder, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")

        self._placeholder = Placeholder(
            card, "尚未加载配置文件", "选择配置…", self._pick_config
        )
        self._placeholder.grid(row=1, column=0, sticky="nsew")

        # 操作区
        actions = ttk.Frame(card, style="Card.TFrame")
        actions.grid(row=3, column=0, sticky="ew")

        self._run_button = PrimaryButton(actions, "开始投稿", self._run)
        self._run_button.pack(side="left", padx=(0, theme.PAD_SM))
        self._run_button.state(["disabled"])

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
        path = filedialog.askopenfilename(
            title="选择配置文件",
            filetypes=[("YAML 配置", "*.yaml *.yml"), ("所有文件", "*.*")],
        )
        if path:
            self._path_var.set(path)
            self._load()

    def _load(self) -> None:
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
        self._fill_tree(tasks)
        self._run_button.state(["!disabled"])
        self._log.append(f"已加载 {len(tasks)} 个任务")

    def _fill_tree(self, tasks: list[Any]) -> None:
        self._tree.delete(*self._tree.get_children())
        for index, task in enumerate(tasks):
            self._tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    task.name,
                    Path(task.file or "").name,
                    task.tid or "",
                    "待投稿",
                ),
            )
        if tasks:
            self._placeholder.grid_remove()
            self._tree.grid()
        else:
            self._tree.grid_remove()
            self._placeholder.grid()

    def _run(self) -> None:
        if self._worker.running:
            return
        if not self._tasks:
            self._log.append("没有可执行的任务")
            return
        if not self.app.ctx.logged_in:
            self._log.append("尚未登录，请先到「登录」页扫码")
            return

        self._set_busy(True)
        self._log.clear()
        self._progress.reset()
        self._worker.run(
            lambda report, is_cancelled: self._do_run(report, is_cancelled),
            on_progress=self._log.append,
            on_done=self._on_done,
            on_error=self._on_error,
        )

    def _cancel(self) -> None:
        self._worker.cancel()
        self._log.append("已请求取消…")

    def _do_run(self, report, is_cancelled) -> list[TaskOutcome]:
        """依次执行所有任务（工作线程）。"""
        client = self.app.ctx.client()
        backend = get_backend(self._cfg.submit.backend, self._cfg.submit.app)
        outcomes: list[TaskOutcome] = []

        total = len(self._tasks)
        for index, task in enumerate(self._tasks):
            if is_cancelled():
                raise Cancelled()

            self.after(0, lambda i=index: self._mark(i, "进行中"))
            report(f"[{index + 1}/{total}] 开始：{task.name}")

            outcome = run_task(
                client,
                task,
                self._cfg,
                backend=backend,
                options=RunOptions(on_progress=report),
            )
            outcomes.append(outcome)

            mark = "成功" if outcome.success else f"失败：{outcome.error}"
            self.after(0, lambda i=index, m=mark: self._mark(i, m))
            self.after(
                0,
                lambda i=index: self._progress.set_value((i + 1) / total * 100),
            )
            report(f"[{index + 1}/{total}] {task.name} → {mark}")

        return outcomes

    def _mark(self, index: int, status: str) -> None:
        """更新某一行的状态列。"""
        item = str(index)
        if not self._tree.exists(item):
            return
        values = list(self._tree.item(item, "values"))
        if len(values) == len(COLUMNS):
            values[-1] = status
            self._tree.item(item, values=values)

    def _on_done(self, outcomes: list[TaskOutcome]) -> None:
        self._set_busy(False)
        ok = sum(1 for o in outcomes if o.success)
        self._progress.set_text(f"完成 {ok}/{len(outcomes)}")
        self._log.append(f"全部结束：成功 {ok}/{len(outcomes)}")
        for outcome in outcomes:
            if outcome.success:
                self._log.append(f"  {outcome.bvid}  {outcome.url}")

    def _on_error(self, exc: BaseException) -> None:
        self._set_busy(False)
        if isinstance(exc, Cancelled):
            self._log.append("已取消")
            return
        if isinstance(exc, NotLoggedInError):
            self._log.append("登录态已失效，请到「登录」页重新扫码")
            return
        self._log.append(f"错误：{exc}")

    def _set_busy(self, busy: bool) -> None:
        self._run_button.state(["disabled"] if busy else ["!disabled"])
        self._cancel_button.state(["!disabled"] if busy else ["disabled"])
