"""投稿历史视图：读取本地历史记录。

历史文件是 JSON 追加写，条目多了会很长，所以只展示最近若干条并倒序排列。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from datetime import datetime

from ...scheduler import DEFAULT_HISTORY_FILE, read_history_diagnose
from .. import theme
from ..widgets import Card, LogConsole, Placeholder, SecondaryButton, SectionTitle

__all__ = ["HistoryView"]

COLUMNS = ("time", "bvid", "name")
_HEADINGS = {"time": "时间", "bvid": "BV 号", "name": "任务"}
_WIDTHS = {"time": 140, "bvid": 130, "name": 240}

#: 只展示最近这么多条，避免长列表拖慢渲染
VISIBLE_LIMIT = 200


class HistoryView(ttk.Frame):
    """投稿历史页。"""

    def __init__(self, master: tk.Misc, app: "object") -> None:
        super().__init__(master, style="TFrame")
        self.app = app
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._build()

    def _build(self) -> None:
        SectionTitle(
            self, "投稿历史", f"记录文件：{DEFAULT_HISTORY_FILE}"
        ).grid(row=0, column=0, sticky="ew", pady=(0, theme.PAD_MD))

        card = Card(self)
        card.grid(row=1, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)

        actions = ttk.Frame(card, style="Card.TFrame")
        actions.grid(row=0, column=0, sticky="w", pady=(0, theme.PAD_SM))
        SecondaryButton(actions, "刷新", self.refresh).pack(side="left")

        holder = ttk.Frame(card, style="Card.TFrame")
        holder.grid(row=1, column=0, sticky="nsew")
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)

        self._tree = ttk.Treeview(
            holder, columns=COLUMNS, show="headings", height=12
        )
        for column in COLUMNS:
            self._tree.heading(column, text=_HEADINGS[column])
            self._tree.column(column, width=_WIDTHS[column], stretch=(column == "name"))
        self._tree.grid(row=0, column=0, sticky="nsew")

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

    def refresh(self) -> None:
        """重新读取历史文件并填充列表。"""
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


def _format_time(stamp: "object") -> str:
    """把 Unix 时间戳转成可读时间，无效值显示 ?。"""
    try:
        value = float(stamp)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?"
    if value <= 0:
        return "?"
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
