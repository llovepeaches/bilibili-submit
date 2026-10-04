"""可复用的界面组件。

这一层只管「长什么样」和「怎么交互」，不含任何业务逻辑——
组件不知道 B 站、投稿、cookie 的存在，只接收回调和数据。

所有颜色/字体/间距从 :mod:`bilibili_submit.ui.theme` 取，不写死字面量。
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk
from typing import Callable, Iterable

from . import theme

__all__ = [
    "Card",
    "ScrollArea",
    "SectionTitle",
    "FormRow",
    "PrimaryButton",
    "SecondaryButton",
    "StatusPill",
    "ProgressBar",
    "LogConsole",
    "Placeholder",
    "KeyValueList",
    "NavItem",
    "SummaryBar",
]


class Card(ttk.Frame):
    """白底卡片容器。

    页面内容都装在卡片里，靠留白和边框把信息分组，
    比直接铺控件在灰底上更容易扫读。
    """

    def __init__(self, master: tk.Misc, padding: int = theme.PAD_LG) -> None:
        super().__init__(master, style="Card.TFrame", padding=padding)


class ScrollArea(ttk.Frame):
    """可纵向滚动的容器，窗口不够高时用滚轮/滚动条兜住。

    投稿表单在最小窗口（880x600）下装不下：按钮和日志区会被挤出
    可视范围，用户看不到「开始投稿」。这里把内容包进 Canvas，
    内容超出时自动显示滚动条，鼠标滚轮也能滚。

    用法::

        area = ScrollArea(parent)
        area.grid(row=0, column=0, sticky="nsew")
        area.body.grid(row=0, column=0, sticky="ew")   # 内容加在 body 上
    """

    def __init__(self, master: tk.Misc, padding: int = theme.PAD_LG) -> None:
        super().__init__(master, style="Card.TFrame")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self._canvas = tk.Canvas(
            self, highlightthickness=0, borderwidth=0, background=theme.SURFACE
        )
        self._scrollbar = ttk.Scrollbar(
            self, orient="vertical", command=self._canvas.yview
        )
        self._canvas.configure(yscrollcommand=self._sync_scrollbar)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        self._scrollbar.grid(row=0, column=1, sticky="ns")

        #: 内容都加在这个 frame 上
        self.body = ttk.Frame(self._canvas, style="Card.TFrame", padding=padding)
        self.body.columnconfigure(0, weight=1)
        self._window = self._canvas.create_window(
            (0, 0), window=self.body, anchor="nw"
        )

        self.body.bind("<Configure>", self._on_body_configure)
        self._canvas.bind("<Configure>", self._on_canvas_configure)
        # 滚轮只在鼠标悬停本区域时接管，免得抢走别处的滚动
        self._bind_wheel(self._canvas)
        self._bind_wheel(self.body)

    def _bind_wheel(self, widget: tk.Misc) -> None:
        if sys.platform == "win32":
            widget.bind("<MouseWheel>", self._on_wheel)
        else:
            # X11 上滚轮是 Button-4/5 两个事件
            widget.bind("<Button-4>", lambda e: self._scroll_units(-1))
            widget.bind("<Button-5>", lambda e: self._scroll_units(1))

    def _on_wheel(self, event: tk.Event) -> str:
        self._scroll_units(-1 if event.delta > 0 else 1)
        return "break"

    def _scroll_units(self, direction: int) -> None:
        # 内容装得下时别滚，否则会把外层容器滚乱
        if self._overflowing():
            self._canvas.yview_scroll(direction, "units")

    def _overflowing(self) -> bool:
        first, last = self._canvas.yview()
        return not (first <= 0.0 and last >= 1.0)

    def _sync_scrollbar(self, first: str, last: str) -> None:
        """内容装得下时把滚动条收起来，不留一条没用的灰条。"""
        if float(first) <= 0.0 and float(last) >= 1.0:
            self._scrollbar.grid_remove()
        else:
            self._scrollbar.grid()

    def _on_body_configure(self, _event: tk.Event) -> None:
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        # 内容跟着容器宽度走，否则窄窗口下会横向溢出
        self._canvas.itemconfigure(self._window, width=event.width)


class SectionTitle(ttk.Frame):
    """分区标题：主标题 + 可选的说明文字。"""

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        subtitle: str = "",
    ) -> None:
        super().__init__(master, style="Card.TFrame")
        ttk.Label(self, text=text, style="Heading.TLabel").pack(anchor="w")
        if subtitle:
            ttk.Label(
                self,
                text=subtitle,
                style="Card.Secondary.TLabel",
                wraplength=560,
                justify="left",
            ).pack(anchor="w", pady=(theme.PAD_XS, 0))


class FormRow(ttk.Frame):
    """一行表单：左侧固定宽标签，右侧控件自适应。

    标签固定宽度保证多行控件左边缘对齐；控件区 ``weight=1``
    让窗口拉宽时输入框跟着变宽，而不是留一片空白。

    .. important::
       控件必须以本行（或 :attr:`body`）为父容器创建。
       Tk 的 ``grid()`` 始终作用于 ``widget.master``，
       如果调用方拿外层 frame 当父容器建控件、再塞进来，
       控件会被排到外层 frame 的同一个单元格里去叠在一起——
       界面看上去就是「所有输入框糊在第一行」。

       所以这里不直接接收成品控件，而是接收**控件类**，
       由 :meth:`add` 用 :attr:`body` 实例化。
    """

    def __init__(
        self,
        master: tk.Misc,
        label: str,
        hint: str = "",
        label_width: int = 12,
    ) -> None:
        super().__init__(master, style="Card.TFrame")
        self.columnconfigure(1, weight=1)

        text = f"{label}：" if label else ""
        ttk.Label(
            self, text=text, style="Card.TLabel", width=label_width, anchor="e"
        ).grid(row=0, column=0, sticky="e", padx=(0, theme.PAD_SM))

        #: 控件容器。复合控件（输入框 + 按钮）往这里放。
        self.body = ttk.Frame(self, style="Card.TFrame")
        self.body.grid(row=0, column=1, sticky="ew")
        self.body.columnconfigure(0, weight=1)

        if hint:
            ttk.Label(
                self,
                text=hint,
                style="Card.Secondary.TLabel",
                wraplength=420,
                justify="left",
            ).grid(row=1, column=1, sticky="w", pady=(theme.PAD_XS, 0))

    def add(
        self,
        widget: "type[tk.Misc] | tk.Misc",
        *,
        column: int = 0,
        sticky: str = "ew",
        padx: "int | tuple[int, int]" = 0,
        **options: object,
    ) -> tk.Misc:
        """往控件区放一个控件，返回实例化后的控件。

        Args:
            widget: 控件**类**（会用 :attr:`body` 当父容器实例化），
                或已建好的控件（此时自行保证父容器正确）。
            column: 放在控件区的第几列。
            sticky: grid 对齐方式，按钮一般传 ``"w"``。
            padx: 左右留白。
            **options: 传给控件构造函数的参数。
        """
        created = widget(self.body, **options) if isinstance(widget, type) else widget
        created.grid(row=0, column=column, sticky=sticky, padx=padx)
        return created


class PrimaryButton(ttk.Button):
    """主操作按钮（品牌粉实心）。一个视图里最多放一个。"""

    def __init__(
        self, master: tk.Misc, text: str, command: Callable[[], None]
    ) -> None:
        super().__init__(
            master, text=text, command=command, style="Primary.TButton"
        )


class SecondaryButton(ttk.Button):
    """次操作按钮（描边）。"""

    def __init__(
        self, master: tk.Misc, text: str, command: Callable[[], None]
    ) -> None:
        super().__init__(
            master, text=text, command=command, style="Secondary.TButton"
        )


def _tone_colors(tone: str) -> tuple[str, str]:
    """取语义色的 ``(前景, 浅底)``。"""
    return theme.TONES.get(tone, theme.TONES["idle"])


class StatusPill(tk.Label):
    """状态标签：一个小圆点 + 文字，用来表达「就绪/未就绪/进行中」。

    用 ``tk.Label`` 而非 ``ttk``：ttk 的 Label 改前景色要绕 style，
    而状态色是动态变化的，直接 tk.Label 更省事。
    """

    def __init__(self, master: tk.Misc, text: str = "", tone: str = "idle") -> None:
        fg, bg = _tone_colors(tone)
        super().__init__(
            master,
            text=text,
            font=theme.FONT_SMALL,
            background=bg,
            foreground=fg,
            padx=theme.PAD_SM,
            pady=2,
        )

    def set(self, text: str, tone: str = "idle") -> None:
        """更新文字与配色。``tone`` 见 :data:`~.theme.TONES`。"""
        fg, bg = _tone_colors(tone)
        self.configure(text=text, foreground=fg, background=bg)


class NavItem(tk.Frame):
    """侧边导航项：左侧竖条 + 图标 + 文字。

    选中态是一条粉色竖条。clam 主题没法给 Button 画「局部」边框，
    所以用 frame 拼：竖条是独立的 3px 宽 frame，未选中时涂成
    **和底色一样**的颜色而不是隐藏它——隐藏会让文字在选中/未选中
    之间左右跳动，很难看。

    .. note::
       用 ``tk.Frame`` 而非 ``ttk.Frame``：整块导航项要随悬停/选中
       改底色，而 ttk 组件只能通过 style 改色（``ttk.Frame`` 甚至
       不接受 ``background`` 选项，会报 ``unknown option``）。
       这里底色是动态变化的，tk.Frame 直接得多。

    对外接口只有 :meth:`set_active`——不要给它补 ``state()`` 之类的
    ``ttk.Button`` 兼容层：导航项从来不参与 ``state`` 机制，
    硬凑一个空实现只会让人误以为它支持禁用。
    """

    BAR_WIDTH = 3

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        icon: str = "",
        command: Callable[[], None] | None = None,
    ) -> None:
        # highlightthickness=0 去掉默认边框，否则拼出来的块有细黑线
        super().__init__(
            master, background=theme.SURFACE, cursor="hand2", highlightthickness=0
        )
        self._command = command
        self._active = False

        self._bar = tk.Frame(
            self, width=self.BAR_WIDTH, background=theme.SURFACE
        )
        self._bar.pack(side="left", fill="y")
        # 竖条宽度不能被布局压缩掉
        self._bar.pack_propagate(False)

        body = tk.Frame(self, background=theme.SURFACE)
        body.pack(side="left", fill="both", expand=True)

        self._icon = tk.Label(
            body, text=icon, font=theme.FONT_MEDIUM,
            background=theme.SURFACE, foreground=theme.TEXT_SECONDARY,
            width=2, anchor="center",
        )
        self._icon.pack(side="left", padx=(theme.PAD_MD, theme.PAD_XS))

        self._text = tk.Label(
            body, text=text, font=theme.FONT_MEDIUM,
            background=theme.SURFACE, foreground=theme.TEXT,
            anchor="w",
        )
        self._text.pack(side="left", fill="x", expand=True)

        self._paintable = (self, body, self._icon, self._text)
        for widget in self._paintable:
            widget.bind("<Button-1>", self._on_click)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

    # ---------- 对外 ----------

    def set_active(self, active: bool) -> None:
        """切换选中态。"""
        self._active = active
        self._render()

    # ---------- 内部 ----------

    def _on_click(self, _event: "object" = None) -> None:
        if self._command:
            self._command()

    def _on_enter(self, _event: "object" = None) -> None:
        if not self._active:
            self._paint(theme.HOVER)

    def _on_leave(self, _event: "object" = None) -> None:
        self._render()

    def _paint(self, background: str) -> None:
        for widget in self._paintable:
            widget.configure(background=background)

    def _render(self) -> None:
        if self._active:
            self._paint(theme.PRIMARY_SOFT)
            self._bar.configure(background=theme.PRIMARY)
            self._icon.configure(foreground=theme.PRIMARY_DARK)
            self._text.configure(foreground=theme.PRIMARY_DARK)
        else:
            self._paint(theme.SURFACE)
            # 未选中也要占位，否则选中时整行会横向跳动
            self._bar.configure(background=theme.SURFACE)
            self._icon.configure(foreground=theme.TEXT_SECONDARY)
            self._text.configure(foreground=theme.TEXT)


class SummaryBar(ttk.Frame):
    """一行汇总信息：左边统计，右边快捷操作按钮。

    批量任务页要同时展示「共 N / 已选 M / 成功 X / 失败 Y」和
    「全选 / 只选失败项」这类按钮，堆成两行太占地方，
    一行左右分开更紧凑。

    用法::

        bar = SummaryBar(parent)
        bar.grid(row=0, column=0, sticky="ew")
        bar.add_action("全选", on_select_all)
        bar.set_stats("共 12 · 已选 8")
    """

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, style="Card.TFrame")
        self.columnconfigure(0, weight=1)

        self._stats = tk.Label(
            self, text="", font=theme.FONT_SMALL, anchor="w",
            background=theme.SURFACE, foreground=theme.TEXT_SECONDARY,
        )
        self._stats.grid(row=0, column=0, sticky="w")

        self._actions = ttk.Frame(self, style="Card.TFrame")
        self._actions.grid(row=0, column=1, sticky="e")
        self._buttons: list[ttk.Button] = []

    def set_stats(self, text: str) -> None:
        self._stats.configure(text=text)

    def add_action(self, text: str, command: Callable[[], None]) -> ttk.Button:
        """往右侧追加一个次按钮，返回该按钮。

        返回出来是为了让调用方在运行态统一开关——这些按钮点下去会
        改变任务选择，而执行期间的选择是不可改的（见
        ``TasksView._editable``）。
        """
        button = SecondaryButton(self._actions, text, command)
        button.grid(row=0, column=len(self._buttons), padx=(theme.PAD_XS, 0))
        self._buttons.append(button)
        return button

    def set_actions_enabled(self, enabled: bool) -> None:
        """统一启用/禁用所有快捷按钮。"""
        state = ["!disabled"] if enabled else ["disabled"]
        for button in self._buttons:
            button.state(state)


class ProgressBar(ttk.Frame):
    """进度条 + 上方文字说明。

    支持确定进度（0-100）与不确定进度（marquee），
    后者用在「连服务器中」这类无法估算耗时的环节。
    """

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, style="Card.TFrame")
        self.columnconfigure(0, weight=1)

        self._label = ttk.Label(self, text="", style="Card.Secondary.TLabel")
        self._label.grid(row=0, column=0, sticky="w")

        self._bar = ttk.Progressbar(self, mode="determinate", maximum=100)
        self._bar.grid(row=1, column=0, sticky="ew", pady=(theme.PAD_XS, 0))

    def set_text(self, text: str) -> None:
        self._label.configure(text=text)

    def set_value(self, value: float) -> None:
        """设为确定进度，``value`` 取 0-100。"""
        self._bar.configure(mode="determinate", maximum=100, value=max(0.0, min(100.0, value)))

    def start_indeterminate(self) -> None:
        """切到滚动条模式（耗时未知时使用）。"""
        self._bar.configure(mode="indeterminate")
        self._bar.start(12)

    def stop_indeterminate(self) -> None:
        self._bar.stop()
        self._bar.configure(mode="determinate", value=0)

    def reset(self) -> None:
        self.stop_indeterminate()
        self._label.configure(text="")


class LogConsole(tk.Frame):
    """深色日志区。

    上传进度、接口返回、报错都往这里追加。用等宽字体对齐，
    深色底和主区形成对比，用户一眼能看出「这是输出不是输入」。
    """

    def __init__(self, master: tk.Misc, height: int = 10) -> None:
        super().__init__(master, background=theme.LOG_BG, relief="flat")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self._text = tk.Text(
            self,
            height=height,
            wrap="word",
            background=theme.LOG_BG,
            foreground=theme.LOG_TEXT,
            insertbackground=theme.LOG_TEXT,
            font=theme.FONT_MONO,
            relief="flat",
            padx=theme.PAD_MD,
            pady=theme.PAD_SM,
            state="disabled",
        )
        scroll = ttk.Scrollbar(self, orient="vertical", command=self._text.yview)
        self._text.configure(yscrollcommand=scroll.set)

        self._text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

    def append(self, message: str) -> None:
        """追加一行并滚到底部。"""
        self._text.configure(state="normal")
        self._text.insert("end", message.rstrip() + "\n")
        self._text.see("end")
        self._text.configure(state="disabled")

    def clear(self) -> None:
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.configure(state="disabled")


class Placeholder(ttk.Frame):
    """空状态占位：列表没数据、未登录时用它，避免界面看起来像坏了。"""

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        action_text: str = "",
        on_action: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master, style="Card.TFrame")
        self.columnconfigure(0, weight=1)

        ttk.Label(
            self, text=text, style="Card.Secondary.TLabel", justify="center"
        ).grid(row=0, column=0, pady=(theme.PAD_XL, theme.PAD_SM))
        if action_text and on_action:
            SecondaryButton(self, action_text, on_action).grid(row=1, column=0)


class KeyValueList(ttk.Frame):
    """简单的键值表（自检结果、环境信息用）。

    比 Treeview 轻量，适合条目少、只读、不需要选择的场景。
    数据变化时整表重建——条目数本来就不多，重建比增量更新可靠。
    """

    def __init__(self, master: tk.Misc, label_width: int = 14) -> None:
        super().__init__(master, style="Card.TFrame")
        self.columnconfigure(1, weight=1)
        self._label_width = label_width
        self._rows: list[tuple[ttk.Label, tk.Label]] = []

    def set_rows(self, rows: Iterable[tuple[str, str, str]]) -> None:
        """重建表格。

        Args:
            rows: ``(键, 值, 色调)`` 三元组序列，色调见 :class:`StatusPill`。
        """
        for key_label, value_label in self._rows:
            key_label.destroy()
            value_label.destroy()
        self._rows.clear()

        for row_index, (key, value, tone) in enumerate(rows):
            key_label = ttk.Label(
                self,
                text=f"{key}：",
                style="Card.Secondary.TLabel",
                width=self._label_width,
                anchor="e",
            )
            key_label.grid(row=row_index, column=0, sticky="e", padx=(0, theme.PAD_SM))

            value_label = tk.Label(
                self,
                text=value,
                font=theme.FONT_SMALL,
                background=theme.SURFACE,
                foreground=_tone_colors(tone)[0],
                anchor="w",
                justify="left",
            )
            value_label.grid(row=row_index, column=1, sticky="w")
            self._rows.append((key_label, value_label))
