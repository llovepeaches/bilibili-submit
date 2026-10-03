"""投稿历史视图：读取本地历史记录。

历史文件是 JSON 追加写，条目多了会很长，所以只展示最近若干条并倒序排列。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from datetime import datetime

from ...scheduler import DEFAULT_HISTORY_FILE, read_history
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

        self._placeholder = Placeholder(card, "暂无投稿历史")
        self._placeholder.grid(row=1, column=0, sticky="nsew")

        self._log = LogConsole(card, height=4)
        self._log.grid(row=2, column=0, sticky="ew", pady=(theme.PAD_SM, 0))

    def refresh(self) -> None:
        """重新读取历史文件并填充列表。"""
        self._tree.delete(*self._tree.get_children())
        try:
            entries = read_history(DEFAULT_HISTORY_FILE)
        except Exception as exc:  # noqa: BLE001 - 历史文件损坏不该让界面崩
            self._log.append(f"读取历史失败：{exc}")
            entries = []

        if not entries:
            self._tree.grid_remove()
            self._placeholder.grid()
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
        self._log.append(f"共 {len(entries)} 条记录")


def _format_time(stamp: "object") -> str:
    """把 Unix 时间戳转成可读时间，无效值显示 ?。"""
    try:
        value = float(stamp)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?"
    if value <= 0:
        return "?"
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
