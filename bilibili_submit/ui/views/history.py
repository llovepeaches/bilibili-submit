"""投稿历史视图：读取本地历史记录。

历史文件是 JSON 追加写，条目多了会很长，所以只展示最近若干条并倒序排列。
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk
from datetime import datetime
from typing import TYPE_CHECKING

from ...scheduler import DEFAULT_HISTORY_FILE, read_history_diagnose
from .. import layout, theme
from ..widgets import Card, LogConsole, PageHeader, Placeholder, OutlinedButton

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查
    from ._host import AppHost

__all__ = ["HistoryView"]

COLUMNS = ("time", "bvid", "name")
_HEADINGS = {"time": "时间", "bvid": "BV 号", "name": "任务"}
#: 列宽权重。时间和 BV 号都是**定长内容**（``2026-05-01 12:34:56`` /
#: ``BV1xx411c7mD``），跟着窗口变宽只会多出一片空白，所以权重给 0；
#: 剩下的全给任务名——它才是会被截断的那个。
_COLUMN_WEIGHTS = {"time": 0, "bvid": 0, "name": 1}
#: 每列下限。BV 号低于 130 会被截成 ``BV1xx411c…``，那就失去意义了。
_COLUMN_MIN_WIDTHS = {"time": 140, "bvid": 130, "name": 240}

#: 只展示最近这么多条，避免长列表拖慢渲染
VISIBLE_LIMIT = 200


#: 还没渲染过。要和「文件不存在」区分开——否则第一次切到本页会被
#: 指纹判成「没变过」，直接空着什么都不显示。
_UNSET: object = object()


def _file_stamp(path: str) -> tuple[int, int] | None:
    """历史文件的指纹：``(mtime_ns, 字节数)``，读不到就 None。

    只用来判断「文件变没变」，不用来判断内容——历史是追加写，
    内容一改 mtime 和大小必然至少变一个，够用了。
    """
    try:
        stat = os.stat(os.path.expanduser(path))
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


class HistoryView(ttk.Frame):
    """投稿历史页。"""

    def __init__(self, master: tk.Misc, app: "AppHost") -> None:
        super().__init__(master, style="TFrame")
        self.app = app
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        #: 上一次量到的表格总宽，见 :meth:`_resize_columns`
        self._last_width = 0
        #: 已渲染内容对应的文件指纹，见 :meth:`refresh`
        self._stamp: object = _UNSET
        self._build()

    def _build(self) -> None:
        PageHeader(
            self, "投稿历史", f"记录文件：{DEFAULT_HISTORY_FILE}"
        ).grid(row=0, column=0, sticky="ew", pady=(0, theme.PAD_LG))

        card = Card(self)
        card.grid(row=1, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)

        actions = ttk.Frame(card, style="Card.TFrame")
        actions.grid(row=0, column=0, sticky="w", pady=(0, theme.PAD_SM))
        OutlinedButton(actions, "刷新", lambda: self.refresh(force=True)).pack(
            side="left"
        )

        holder = ttk.Frame(card, style="Card.TFrame")
        holder.grid(row=1, column=0, sticky="nsew")
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)

        self._tree = ttk.Treeview(
            holder, columns=COLUMNS, show="headings", height=12
        )
        for column in COLUMNS:
            self._tree.heading(column, text=_HEADINGS[column])
            self._tree.column(column, stretch=(column == "name"))
        self._tree.grid(row=0, column=0, sticky="nsew")
        self._tree.bind("<Configure>", self._resize_columns)
        self._resize_columns()

        scroll = ttk.Scrollbar(holder, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")

        self._placeholder = Placeholder(
            card,
            "还没有投稿记录",
            "每次投稿成功或失败都会写进这里：稿件号、标题、投稿时间、"
            "失败原因。可以直接复制链接发到动态里。",
        )
        self._placeholder.grid(row=1, column=0, sticky="nsew")

        self._log = LogConsole(card, height=4)
        self._log.grid(row=2, column=0, sticky="ew", pady=(theme.PAD_SM, 0))

    def _resize_columns(self, _event: tk.Event | None = None) -> None:
        """按权重把可用宽度分给各列。

        和批量任务页走同一个 :func:`layout.column_widths`。之前这里写死
        140/130/240，宽屏上右侧空一大片、窄屏上任务名反而被截——同一个
        窗口里两个表格的拉伸手感不一样，看着像两个应用。

        宽度没量出来时不动手：``<Configure>`` 在布局的每一步都会触发，
        拿 1px 去算比例会把列压成下限，再也没恢复回来。

        宽度没变时也不动手——列宽只由总宽决定，缓存总宽就够了。
        这里原本只挡了「没量出来」，于是每一次 Configure 都要写三列，
        拖窗口时和任务页的表格一起抖。
        """
        total = self._tree.winfo_width()
        if total < 100 or total == self._last_width:
            return
        self._last_width = total
        widths = layout.column_widths(
            COLUMNS, _COLUMN_WEIGHTS, _COLUMN_MIN_WIDTHS, total
        )
        for column, width in widths.items():
            self._tree.column(column, width=width)

    def refresh(self, force: bool = False) -> None:
        """重新读取历史文件并填充列表。

        文件没变就不重建：主窗口每次切到本页都会调一次 ``refresh()``，
        而历史文件是**追加写**的——投得越多越大，切一次页就要把整个
        JSON 读进来 parse 一遍，再删掉、插回 200 行 Treeview。条目到
        几百条时点导航能感觉到一下顿，而这下顿里绝大部分工作是在重画
        一模一样的内容。

        指纹变了才动手。``force=True`` 留给「刷新」按钮：用户点了就是
        想重读，不看指纹。
        """
        stamp = _file_stamp(str(DEFAULT_HISTORY_FILE))
        if not force and self._stamp is not _UNSET and stamp == self._stamp:
            return
        self._stamp = stamp

        self._tree.delete(*self._tree.get_children())
        # 每次刷新重置日志：refresh 会在每次切到本页时调用，
        # 不清空的话同一句「共 N 条」会重复叠很多行。
        self._log.clear()
        entries, problem = read_history_diagnose(DEFAULT_HISTORY_FILE)

        if problem:
            # 损坏和「没投过稿」必须区分：都显示空列表的话，
            # 用户会以为程序把记录弄丢了，而不是文件本身坏了。
            self._show_problem(problem)
            return

        if not entries:
            self._set_placeholder("暂无投稿历史")
            self._log.append("暂无投稿历史")
            return

        self._placeholder.grid_remove()
        self._tree.grid()

        # 倒序：最近的在最上面
        for entry in list(entries)[-VISIBLE_LIMIT:][::-1]:
            self._tree.insert(
                "",
                "end",
                values=(
                    _format_time(entry.get("time", 0)),
                    entry.get("bvid", ""),
                    entry.get("name", ""),
                ),
            )
        shown = min(len(entries), VISIBLE_LIMIT)
        suffix = f"（只显示最近 {shown} 条）" if shown < len(entries) else ""
        self._log.append(f"共 {len(entries)} 条记录{suffix}")

    def _show_problem(self, problem: str) -> None:
        """历史文件读不了时给出明确提示，而不是干巴巴一个空列表。"""
        self._set_placeholder("历史文件读取失败，详见下方说明")
        self._log.clear()
        self._log.append(f"⚠ {problem}")

    def _set_placeholder(self, text: str) -> None:
        """显示空状态占位并设置文案。"""
        self._tree.grid_remove()
        self._placeholder.grid()
        # 复用同一个 Label 实例改文案，不重建——
        # 重建会丢掉内部状态，而且两种文案要能来回切。
        for child in self._placeholder.winfo_children():
            if isinstance(child, ttk.Label):
                child.configure(text=text)


def _format_time(stamp: object) -> str:
    """把 Unix 时间戳转成可读时间，无效值显示 ?。"""
    try:
        value = float(stamp)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?"
    if value <= 0:
        return "?"
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
