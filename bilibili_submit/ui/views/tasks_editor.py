"""批量任务页的标题编辑协作者。

从 ``views/tasks.py`` 里分出来的三件事：

- :func:`render_title_template` —— 把 ``{name}`` / ``{n}`` 换成实际值
- :class:`PartTitlesDialog` —— 改标题的模态对话框
- :class:`TaskEditor` —— 把上面两件串起来的行为：套模板、改单个稿件

刻意**不持有** ``TasksView``：它需要的东西只有「任务列表、选中行、表格
写回、日志、存盘、弹窗」这几样，全部从构造函数注入。这样它可以在没有
整个批量任务页的情况下单独测——建一个 Tk 根窗口和一个 Treeview 就够了。

.. note::
   注入的是 ``lambda`` 而不是 ``self._xxx`` 的 bound method：bound method
   在构造那一刻就被求值并固定下来了，测试再往 view 上打桩也换不掉它。
   用 lambda 包一层，每次调用才去查 ``self`` 上的名字，打桩才生效——
   ``view._tasks`` 被整体替换、``view._ask_titles`` 被换成假对话框，
   靠的都是这个延迟查找。
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import replace
from tkinter import ttk
from typing import Any, Callable

from ...config import TaskConfig, task_files, task_part_titles
from ...multipart import strip_part_marker
from .. import theme
from ..widgets import FilledButton, OutlinedButton


def render_title_template(template: str, name: str, number: int) -> str:
    """把投稿标题模板里的占位符换成实际值。

    支持 ``{name}``（文件名/文件夹名）与 ``{n}``（勾选顺序，从 1 开始）。

    模板里一个占位符都没写时，每一行会得到**同一个**标题——那通常是
    用户有意填的固定标题（比如给一套视频统一加前缀），所以不去猜、
    也不报错。
    """
    return template.replace("{name}", name).replace("{n}", str(number))


class PartTitlesDialog:
    """改一个稿件的**标题**（多 P 时连同各分 P 标题一起改）。

    稿件标题放在最上面单独一行：它是播放器里显示的那一行字，也是用户
    最想改的东西。分 P 标题每行一个输入框，右边标出对应的文件名——
    只给 P1/P2 序号的话，用户看不出自己在改哪一个，而分 P 顺序错了
    是很难补救的。

    单文件稿件不显示分 P 区：它只有一个分 P，标题就是稿件标题，
    再列一行「P1」纯属重复。
    """

    def __init__(
        self,
        master: tk.Misc,
        archive: str,
        files: list,
        titles: list[str],
        archive_title: str = "",
    ) -> None:
        self._files = list(files)
        self._vars: list[tk.StringVar] = []
        self._result: tuple[str, list[str]] | None = None
        self._archive_var = tk.StringVar(value=archive_title)

        self._top = tk.Toplevel(master)
        self._top.title(f"标题 · {archive}")
        self._top.transient(master)
        self._top.minsize(440, 220)
        self._top.columnconfigure(0, weight=1)
        self._top.rowconfigure(0, weight=1)

        frame = ttk.Frame(self._top, padding=theme.PAD_MD)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(1, weight=1)

        row = 0

        # 稿件标题：单独一区，视觉上和下面的分 P 分开
        ttk.Label(frame, text="稿件标题").grid(
            row=row, column=0, sticky="w", padx=(0, theme.PAD_SM)
        )
        ttk.Entry(frame, textvariable=self._archive_var).grid(
            row=row, column=1, columnspan=2, sticky="ew", pady=theme.PAD_XS
        )
        row += 1

        if len(self._files) > 1:
            ttk.Label(
                frame,
                text=f"共 {len(self._files)} 个分P，按此顺序发布；留空则用文件名",
                style="Card.Secondary.TLabel",
            ).grid(
                row=row,
                column=0,
                columnspan=3,
                sticky="w",
                pady=(theme.PAD_SM, theme.PAD_XS),
            )
            row += 1

            for index, path in enumerate(self._files):
                var = tk.StringVar(
                    value=titles[index] if index < len(titles) else ""
                )
                self._vars.append(var)
                ttk.Label(frame, text=f"P{index + 1}").grid(
                    row=row, column=0, sticky="w", padx=(0, theme.PAD_SM)
                )
                ttk.Entry(frame, textvariable=var).grid(
                    row=row, column=1, sticky="ew", pady=theme.PAD_XS
                )
                ttk.Label(frame, text=path.name, style="Card.Secondary.TLabel").grid(
                    row=row, column=2, sticky="w", padx=(theme.PAD_SM, 0)
                )
                row += 1

        buttons = ttk.Frame(frame)
        buttons.grid(
            row=row, column=0, columnspan=3, sticky="e", pady=(theme.PAD_MD, 0)
        )
        OutlinedButton(buttons, "用文件名", self._reset).pack(
            side="left", padx=(0, theme.PAD_SM)
        )
        OutlinedButton(buttons, "取消", self._cancel).pack(
            side="left", padx=(0, theme.PAD_SM)
        )
        FilledButton(buttons, "确定", self._ok).pack(side="left")

    def _reset(self) -> None:
        """「用文件名」：把每行填回文件名，用户能直接看到将要用的值。

        稿件标题回落到第一个文件的文件名——单文件稿件就是它自己的名字，
        多文件稿件则去掉尾部序号（``旅行_01`` → ``旅行``）更合适。
        """
        for var, path in zip(self._vars, self._files):
            var.set(path.stem)
        if self._files:
            base, _ = strip_part_marker(self._files[0].stem)
            self._archive_var.set(base or self._files[0].stem)

    def _ok(self) -> None:
        self._result = (
            self._archive_var.get().strip(),
            [var.get().strip() for var in self._vars],
        )
        self._top.destroy()

    def _cancel(self) -> None:
        self._result = None
        self._top.destroy()

    def show(self) -> tuple[str, list[str]] | None:
        """模态显示，返回 ``(稿件标题, 各分P标题)``；取消返回 ``None``。"""
        self._top.grab_set()
        self._top.wait_window()
        return self._result


class TaskEditor:
    """批量任务页的标题编辑。

    只管标题这一件事：把模板套到勾选行、改单个稿件的标题（含分 P）。
    任务列表本身的所有权仍在 ``TasksView``——这里只通过注入的回调读写。
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        tree: Any,
        log: Any,
        read_tasks: Callable[[], list[TaskConfig]],
        write_task: Callable[[int, TaskConfig], None],
        selected_indexes: Callable[[], list[int]],
        read_template: Callable[[], str],
        save_state: Callable[[], None],
        asker: Callable[..., tuple[str, list[str]] | None],
    ) -> None:
        self._master = master
        self._tree = tree
        self._log = log
        self._read_tasks = read_tasks
        self._write_task = write_task
        self._selected_indexes = selected_indexes
        self._read_template = read_template
        self._save_state = save_state
        self._asker = asker

    def ask_titles(
        self,
        archive: str,
        files: list,
        current: list[str],
        archive_title: str,
    ) -> tuple[str, list[str]] | None:
        """弹对话框要标题，取消返回 None。

        和 :meth:`edit` 分开是因为对话框要跑模态事件循环，替换掉这一个
        方法就能验证 :meth:`edit` 的写入逻辑本身。
        """
        return PartTitlesDialog(self._master, archive, files, current, archive_title).show()

    def apply_template(self) -> None:
        """把「投稿标题」模板套到勾选的行上。

        只动勾选的行：列表里可能有一半已经手动改过标题，全量套一遍
        等于把那些手工活全冲掉。
        """
        template = self._read_template().strip()
        if not template:
            self._log.append("投稿标题是空的——填个模板再套用，比如「{name} 第{n}集」")
            return
        indexes = self._selected_indexes()
        if not indexes:
            self._log.append("没勾任何任务：先在左边勾上要改标题的行")
            return

        tasks = self._read_tasks()
        for number, index in enumerate(indexes, start=1):
            task = tasks[index]
            # 已改过标题的任务用改后的名字当 {name}，
            # 否则拿任务名（文件夹/文件名）
            title = render_title_template(template, task.title or task.name, number)
            title = title.strip()
            if not title:
                continue
            self._write_task(index, replace(task, title=title))
            self._tree.set(str(index), "title", title)

        self._log.append(f"已套用投稿标题到 {len(indexes)} 行：{template}")
        self._save_state()

    def edit(self, iid: str) -> None:
        """改某个稿件的标题（多 P 时连同各分 P 标题一起改）。

        自动分组给出的标题就是文件名/文件夹名，多数时候够用；但
        ``旅行_01`` 这种机器味的名字在播放器里不好看，所以留个口子让人改。
        单 P 任务也弹——它同样需要一个像样的标题，之前双击它没反应，
        想改标题只能去改文件名，那个绕路太远了。
        """
        index = int(iid)
        tasks = self._read_tasks()
        if index >= len(tasks):
            return
        task = tasks[index]
        files = task_files(task)
        if not files:
            return

        answer = self._asker(
            task.name or files[0].stem,
            files,
            task_part_titles(task, files),
            task.title or task.name or "",
        )
        if answer is None:
            return

        archive_title, part_titles = answer
        updated = replace(task, title=archive_title)
        if len(files) > 1:
            # 单 P 任务写 part_titles 没有意义：它只有一个分 P，
            # 标题就是稿件标题，写进去只是留下一份用不上的脏数据
            updated = replace(updated, part_titles=part_titles)

        self._write_task(index, updated)
        self._tree.set(iid, "title", archive_title or task.name)
        if len(files) > 1:
            self._log.append(
                f"已更新「{task.name}」的标题与 {len(files)} 个分P标题"
            )
        else:
            self._log.append(f"已更新「{task.name}」的标题")
