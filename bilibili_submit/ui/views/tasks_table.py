"""批量任务页的表格协作者。

从 ``views/tasks.py`` 里分出来的一整块：Treeview 本身、勾选项、行着色、
列宽几何，以及「哪些行被选中」这类只跟表格有关的事实。

四个以 iid 为键的容器（``picked`` / ``missing`` / ``errors`` / ``tones``）
全部跟着表格走：它们的键就是 Treeview 的行 id，表格一清空它们就应该
一起空，分开放在视图上迟早会出现「列表换了一批，旧行的勾选还留着」。

**不持有** ``TasksView``：它需要的外部信息只有「能不能改、改完通知谁、
分区显示什么、任务来自哪种来源」四样，全部从构造函数注入。

.. note::
   注入的回调一律用 ``lambda`` 包一层而不是直接传 bound method：bound
   method 在构造那一刻就固定了，测试往视图上 monkeypatch 换不掉它。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ...config import TaskConfig, task_files
from .. import layout, theme

COLUMNS = ("pick", "name", "title", "file", "parts", "tid", "status")
_HEADINGS = {
    "pick": "✓",
    "name": "任务",
    "title": "标题",
    "file": "文件",
    "parts": "分P",
    "tid": "分区",
    "status": "状态",
}
#: 列宽权重。窗口宽度变化时按这个比例分配——固定列宽在宽屏上右侧
#: 留一大片空白，在窄屏上文件名被截得认不出是哪个，两个毛病一起治。
_COLUMN_WEIGHTS = {
    "pick": 0,
    "name": 3,
    "title": 5,
    "file": 6,
    "parts": 1,
    "tid": 2,
    "status": 3,
}
#: 每列的下限。权重再小也不能窄到看不清内容。
_COLUMN_MIN_WIDTHS = {
    "pick": 34,
    "name": 70,
    "title": 90,
    "file": 110,
    "parts": 42,
    "tid": 48,
    "status": 90,
}
#: 短内容居中更整齐，长文本左对齐更好扫读
_CENTERED_COLUMNS = ("pick", "parts", "tid", "status")

PICKED = "✓"
UNPICKED = ""


def _column_widths(total: int) -> dict[str, int]:
    """按权重把总宽度分给各列，返回每列宽度。

    算法本身在 :func:`layout.column_widths`——历史页用的是同一份。
    两边各写一遍的结果就是同一个窗口里两个表格拉伸手感不一样，
    而且改一处漏一处。
    """
    return layout.column_widths(COLUMNS, _COLUMN_WEIGHTS, _COLUMN_MIN_WIDTHS, total)


class TaskTable:
    """任务列表：Treeview + 勾选状态 + 行着色 + 列宽。

    对外只暴露数据和动作，不暴露 Tk 细节（``tree`` 除外——滚动条和
    ``grid`` 还得靠它）。
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        append_log: "object",
        is_editable: "object",
        on_changed: "object",
        on_filled: "object",
        read_tid: "object",
        source_mode: "object",
    ) -> None:
        self._append_log = append_log
        self._is_editable = is_editable
        self._on_changed = on_changed
        self._on_filled = on_filled
        self._read_tid = read_tid
        self._source_mode = source_mode

        #: iid -> 是否勾选
        self.picked: dict[str, bool] = {}
        #: 文件不存在的 iid
        self.missing: set[str] = set()
        #: iid -> 完整错误原因（状态列放不下）
        self.errors: dict[str, str] = {}
        #: iid -> 语义色名，见 :data:`~.theme.TONES`
        self.tones: dict[str, str] = {}

        self.tree = ttk.Treeview(master, columns=COLUMNS, show="headings", height=8)
        for column in COLUMNS:
            self.tree.heading(column, text=_HEADINGS[column])
            self.tree.column(
                column,
                width=_COLUMN_MIN_WIDTHS[column],
                stretch=True,
                anchor="center" if column in _CENTERED_COLUMNS else "w",
            )
        # 行着色的唯一来源
        theme.apply_tree_tags(self.tree)
        # 宽度跟着窗口走，而不是锁死在固定值上
        self.tree.bind("<Configure>", self.resize_columns)
        # 点首列切换勾选；表头点全选/全不选
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", self._on_space)

    # ---------- 状态 ----------

    def reset(self) -> None:
        """清空上一轮的选择与结果。"""
        self.picked.clear()
        self.missing.clear()
        self.errors.clear()
        self.tones.clear()

    def editable(self) -> bool:
        """当前是否允许改动勾选状态——由外部的状态决定。"""
        return bool(self._is_editable())

    # ---------- 渲染 ----------

    def fill(self, tasks: list[TaskConfig]) -> None:
        """按任务列表重建整个表格。"""
        shared_tid = self._read_tid()
        self.tree.delete(*self.tree.get_children())
        for index, task in enumerate(tasks):
            iid = str(index)
            files = task_files(task)
            # 多 P 稿件缺一个文件就投不完整，所以整行都算缺失
            gone = [path for path in files if not path.is_file()]
            exists = bool(files) and not gone

            if exists:
                file_text = files[0].name
                if len(files) > 1:
                    file_text = f"{files[0].name} 等 {len(files)} 个"
                status = "待投稿"
                tone = "idle"
                # 正常任务默认勾选——大多数情况用户就是要跑全部
                self.picked[iid] = True
            else:
                if files:
                    detail = f"（缺 {len(gone)}/{len(files)}）" if len(files) > 1 else ""
                    file_text = f"[缺] {files[0].name}{detail}"
                else:
                    file_text = "[缺] (未设置)"
                status = "文件缺失"
                tone = "missing"
                self.missing.add(iid)
                # 缺失的不预选，也拒绝后续勾选
                self.picked[iid] = False

            self.tones[iid] = tone
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    PICKED if self.picked[iid] else UNPICKED,
                    task.name,
                    task.title or task.name,
                    file_text,
                    str(len(files)) if len(files) > 1 else "—",
                    shared_tid if self._source_mode() == "folder" else (task.tid or ""),
                    status,
                ),
                tags=(tone,),
            )

        # 表格本身是否露出来由外部决定（空列表时要换成占位图），
        # 这里只负责通知「填完了、有几行」。
        self._on_filled(bool(tasks))
        self._on_changed()

    def resize_columns(self, _event: "tk.Event | None" = None) -> None:
        """按权重把可用宽度分给各列。

        只在宽度真的变了时动手：``<Configure>`` 在布局的每一步都会触发，
        无脑重算会让 Treeview 反复重排，拖窗口时能看出明显抖动。
        """
        total = self.tree.winfo_width()
        if total < 100:  # 还没布局出来，别拿 1px 去算比例
            return
        for column, width in _column_widths(total).items():
            self.tree.column(column, width=width)

    def mark(self, index: int, status: str, tone: str = "idle") -> None:
        """更新某一行的状态文案与配色，并通知外部刷新汇总。

        汇总放在这里一起通知而不是让调用方记得调：行状态一变，汇总里的
        成败计数就必须跟着变，分成两步迟早会忘，界面上就会出现
        「列表里明明有两个红的，汇总却说 0 失败」。
        """
        iid = str(index)
        if not self.tree.exists(iid):
            return
        values = list(self.tree.item(iid, "values"))
        if len(values) != len(COLUMNS):
            return
        values[-1] = status
        self.tones[iid] = tone
        # 每行只打一个 tag——避免多个 tag 的背景色互相打架
        self.tree.item(iid, values=values, tags=(tone,))
        self._on_changed()

    # ---------- 勾选 ----------

    def _on_click(self, event: tk.Event) -> None:
        """点首列切换勾选；点表头首列则全选/全不选。"""
        region = self.tree.identify_region(event.x, event.y)
        if region == "heading":
            if self.tree.identify_column(event.x) == "#1":
                self.select_all() if self.any_unpicked() else self.select_none()
            return
        if region != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":
            return
        iid = self.tree.identify_row(event.y)
        if iid:
            self.toggle(iid)

    def _on_space(self, _event: tk.Event) -> str:
        """空格切换当前行的勾选。"""
        focused = self.tree.focus()
        if focused:
            self.toggle(focused)
        return "break"

    def toggle(self, iid: str) -> None:
        """切换一行的勾选状态。

        文件缺失的行**拒绝勾选**：勾了却在执行时被跳过最让人困惑，
        不如一开始就勾不上。
        """
        if iid in self.missing:
            self._append_log("该文件不存在，无法勾选")
            return
        if not self.editable():
            return
        self.picked[iid] = not self.picked.get(iid, False)
        self._refresh_pick_cell(iid)
        self._on_changed()

    def set_pick(self, iid: str, picked: bool) -> None:
        if iid in self.missing:
            picked = False
        self.picked[iid] = picked

    def _refresh_pick_cell(self, iid: str) -> None:
        values = list(self.tree.item(iid, "values"))
        values[0] = PICKED if self.picked.get(iid) else UNPICKED
        self.tree.item(iid, values=values)

    def _apply_selection(self, predicate) -> None:
        """按谓词批量设置勾选，然后刷新首列与汇总。"""
        if not self.editable():
            return
        for iid in self.tree.get_children():
            self.set_pick(iid, predicate(iid))
            self._refresh_pick_cell(iid)
        self._on_changed()

    def select_all(self) -> None:
        self._apply_selection(lambda iid: True)

    def select_none(self) -> None:
        self._apply_selection(lambda iid: False)

    def select_failed(self) -> None:
        """只勾失败项（不含文件缺失的）。"""
        self._apply_selection(lambda iid: self.tones.get(iid) == "error")

    def any_unpicked(self) -> bool:
        """是否存在可勾但未勾的项——决定点表头是全选还是全不选。"""
        return any(
            not self.picked.get(iid, False)
            for iid in self.tree.get_children()
            if iid not in self.missing
        )

    def selected_indexes(self) -> list[int]:
        """当前勾选的任务下标，按列表顺序。"""
        return [
            int(iid)
            for iid in self.tree.get_children()
            if self.picked.get(iid) and iid not in self.missing
        ]
