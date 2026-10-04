"""可复用的界面组件。

这一层只管「长什么样」和「怎么交互」，不含任何业务逻辑——
组件不知道 B 站、投稿、cookie 的存在，只接收回调和数据。

所有颜色/字体/间距从 :mod:`bilibili_submit.ui.theme` 取，不写死字面量。
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Callable, Iterable, Sequence

from . import theme

__all__ = [
    "Card",
    "ScrollArea",
    "SectionTitle",
    "Collapsible",
    "OptionSwitches",
    "OPTION_LABELS",
    "FormRow",
    "FluentButton",
    "PrimaryButton",
    "SecondaryButton",
    "BrandMark",
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


class Collapsible(ttk.Frame):
    """可折叠分组：点标题栏展开或收起里面的表单。

    页面上方「每次都要填的」和「偶尔动一次的」挤在一起时，任务列表
    就被压得只剩三行。折叠把选择权还给用户。

    收起时右侧可以显示一段提示（见 :meth:`set_hint`）——**这条是必需
    的**：里面开着杜比、关着评论，收起后界面上什么都没有，用户会以为
    设置丢了。

    .. note::
       展开状态**不记偏好**，只在本次会话内保持；要跨会话记住的由
       调用方自己存（界面偏好那一层的事，组件不该知道）。
    """

    def __init__(
        self,
        master: tk.Misc,
        title: str,
        subtitle: str = "",
        opened: bool = False,
        on_toggle: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__(master, style="Card.TFrame")
        self._opened = bool(opened)
        self._on_toggle = on_toggle
        self.columnconfigure(0, weight=1)

        # tk.Frame 而非 ttk：ttk 的 cursor 在部分平台不生效，
        # 而“这一行能点”全靠手型光标传达
        self._header = tk.Frame(self, bg=theme.COLLAPSE_BG, cursor="hand2")
        self._header.grid(row=0, column=0, sticky="ew")
        self._header.columnconfigure(1, weight=1)

        self._arrow = tk.Label(
            self._header,
            text="",
            bg=theme.COLLAPSE_BG,
            fg=theme.TEXT_SECONDARY,
            font=theme.FONT_SMALL,
            width=2,
            anchor="w",
        )
        self._arrow.grid(row=0, column=0)

        tk.Label(
            self._header,
            text=title,
            bg=theme.COLLAPSE_BG,
            fg=theme.PRIMARY_DARK,
            font=theme.FONT_MEDIUM,
        ).grid(row=0, column=1, sticky="w")

        self._hint = tk.Label(
            self._header,
            text="",
            bg=theme.COLLAPSE_BG,
            fg=theme.TEXT_MUTED,
            font=theme.FONT_SMALL,
        )
        self._hint.grid(row=0, column=2, sticky="e", padx=(theme.PAD_SM, 0))

        self._body = ttk.Frame(self, style="Card.TFrame")
        self._body.columnconfigure(0, weight=1)

        if subtitle:
            tk.Label(
                self._header,
                text=subtitle,
                bg=theme.COLLAPSE_BG,
                fg=theme.TEXT_MUTED,
                font=theme.FONT_SMALL,
            ).grid(row=1, column=1, columnspan=2, sticky="w")

        # Tk 的事件不冒泡，标题栏里每个子控件都得单独绑一次，
        # 否则点在文字上没反应——只有点到 padding 才展开，很难用
        for widget in (self._header, self._arrow, self._hint, *self._header.winfo_children()):
            widget.bind("<Button-1>", self._on_click)

        self._render()

    @property
    def body(self) -> ttk.Frame:
        """内容容器。表单行往这里 ``grid``。"""
        return self._body

    @property
    def opened(self) -> bool:
        return self._opened

    def set_opened(self, opened: bool) -> None:
        """展开或收起。重复设同一个值不触发回调（避免自触发循环）。"""
        opened = bool(opened)
        if opened == self._opened:
            return
        self._opened = opened
        self._render()
        if self._on_toggle:
            self._on_toggle(opened)

    @property
    def hint(self) -> str:
        """当前提示文字。收起时显示在标题栏右侧，测试也靠它断言。"""
        return str(self._hint.cget("text"))

    def set_hint(self, text: str) -> None:
        """设置收起时显示在右侧的提示文字。"""
        self._hint.configure(text=text)

    def _on_click(self, _event: "tk.Event") -> str:
        self.set_opened(not self._opened)
        return "break"

    def _render(self) -> None:
        self._arrow.configure(text="▾" if self._opened else "▸")
        if self._opened:
            self._body.grid(row=1, column=0, sticky="ew")
        else:
            self._body.grid_remove()


#: 投稿选项的标签文字。投稿页与批量页共用同一份，
#: 两处各写一遍的话，改名时漏一处就是「两个页面说法不一样」。
OPTION_LABELS = {
    "close_danmu": "关闭弹幕",
    "close_reply": "关闭评论区",
    "selection_reply": "精选评论",
    "dolby": "杜比音效",
    "hires": "Hi-Res 无损",
}

#: 折叠收起时用的简称。全称放不进标题栏那一行。
OPTION_SHORT = {
    "close_danmu": "关弹幕",
    "close_reply": "关评论",
    "selection_reply": "精选评论",
    "dolby": "杜比",
    "hires": "Hi-Res",
}


class OptionSwitches(ttk.Frame):
    """互动设置与音质增强的五个开关，投稿页与批量页共用。

    两页各写一遍的话，漏掉某一个的表现是「这一页勾了没反应、那一页
    正常」——不报错、不告警，只能等用户撞见。所以连标签文字、
    置灰逻辑、已开启摘要都收在这里。
    """

    def __init__(
        self, master: tk.Misc, on_change: Callable[[], None] | None = None
    ) -> None:
        super().__init__(master, style="Card.TFrame")
        self.columnconfigure(0, weight=1)
        self._on_change = on_change
        self._vars: dict[str, tk.BooleanVar] = {}
        self._boxes: list[ttk.Checkbutton] = []

        row = FormRow(
            self,
            "互动设置",
            hint="关闭后就不再有新的弹幕/评论，已发布的稿件不受影响",
        )
        row.grid(row=0, column=0, sticky="ew", pady=(0, theme.PAD_SM))
        for column, key in enumerate(("close_danmu", "close_reply", "selection_reply")):
            self._add(row, key, column)

        row = FormRow(
            self,
            "音质增强",
            hint="源文件本身得是杜比/无损音轨，否则开了也不会有效果",
        )
        row.grid(row=1, column=0, sticky="ew")
        for column, key in enumerate(("dolby", "hires")):
            self._add(row, key, column)

    def _add(self, row: "FormRow", key: str, column: int) -> None:
        var = tk.BooleanVar()
        self._vars[key] = var
        self._boxes.append(
            row.add(
                ttk.Checkbutton,
                text=OPTION_LABELS[key],
                variable=var,
                command=self._changed,
                column=column,
            )
        )

    def _changed(self) -> None:
        if self._on_change:
            self._on_change()

    @property
    def flags(self) -> dict[str, bool]:
        """五个开关的当前值，键名与配置字段一致。"""
        return {key: var.get() for key, var in self._vars.items()}

    def set_flags(self, flags: "dict[str, bool] | None" = None, **kwargs: bool) -> None:
        """回填开关。认不出的键直接忽略——旧偏好里没有的项保持默认。"""
        merged = dict(flags or {})
        merged.update(kwargs)
        for key, value in merged.items():
            if key in self._vars:
                self._vars[key].set(bool(value))

    def summary(self) -> str:
        """已开启项的简称串，给折叠区收起时显示用。"""
        return "、".join(
            OPTION_SHORT[key] for key, value in self.flags.items() if value
        )

    def set_disabled(self, disabled: bool) -> None:
        """整组置灰。yaml 模式下统一参数不生效，但保留让用户看见自己设了什么。"""
        state = ["disabled"] if disabled else ["!disabled"]
        for box in self._boxes:
            box.state(state)


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


def _round_rect_points(
    x1: float, y1: float, x2: float, y2: float, radius: float
) -> list[float]:
    """圆角矩形的折线顶点。

    Tk 的 Canvas 没有圆角矩形图元，常用做法是给 ``create_polygon`` 传
    ``smooth=True``：把角上的点**重复一遍**，样条就会在那里拐出一个角
    而不是切掉它——每个角给三个点（入角、角、出角）刚好得到 4~8px
    视觉圆角，和 Fluent 的控件圆角一致。
    """
    r = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1,
    ]


def _state_wants_disabled(name: "object") -> "bool | None":
    """把 ``"disabled"`` / ``"!disabled"`` 翻译成布尔。

    ttk 的状态写法是 ``["disabled"]`` 置位、``["!disabled"]`` 清位，
    其它状态名（``"active"`` 之类）这里一律不认，返回 ``None`` 表示
    「这条与本组件无关」——不认的状态就该安静跳过，而不是当成禁用。
    """
    if not isinstance(name, str):
        return None
    if name == "disabled" or name == "disable":
        return True
    if name.startswith("!") and name[1:] in ("disabled", "disable"):
        return False
    return None


class FluentButton(tk.Canvas):
    """Fluent 风格按钮：Canvas 自绘圆角矩形 + 文字。

    为什么不直接用 ``ttk.Button``：clam 主题（这里统一用的跨平台上观感
    一致的那个）画的是**直角**，而 Fluent 的按钮圆角是 4~8px。ttk 没有
    「圆角」这个选项，边框宽度再怎么调也只能是方的。自绘才能把圆角、
    32/40px 高度、五态配色都精确控住。

    五态（对应 Fluent 的 Rest / Hover / Pressed / Disabled / Focus）：
    默认、悬停、按下、禁用、聚焦（聚焦画主色环）。

    对外保持 ``ttk.Button`` 的常用接口——``state()`` 传 ``["disabled"]`` /
    ``["!disabled"]``、``configure(text=...)``、``cget("text")``、
    ``invoke()``——所以调用方一行都不用改。

    .. note::
       画布比可见按钮上下左右各多 :data:`_RING` 像素，留给聚焦环；
       ``winfo_reqheight()`` 因此是 ``height + 4``，可见按钮本身仍是
       精确的 32 / 40px。
    """

    #: 强调型（实心主色），一个视图里最多一个
    ACCENT = "accent"
    #: 标准型（描边），其余按钮都用这个
    STANDARD = "standard"

    #: 聚焦环预留的边距（上下左右各这么多）
    _RING = 2
    #: 按钮最小宽度。中文两字按钮（"取消"）按文字算只有 44px，
    #: 太窄会显得局促，Fluent 也有同样的最小宽度约束。
    _MIN_WIDTH = 64

    def __init__(
        self,
        master: "tk.Misc",
        text: str = "",
        command: "Callable[[], None] | None" = None,
        *,
        variant: str = STANDARD,
        height: int | None = None,
        width: int | None = None,
        background: str | None = None,
    ) -> None:
        self._text = text
        self._command = command
        self._variant = variant if variant in (self.ACCENT, self.STANDARD) else self.STANDARD
        self._height = int(height or theme.CONTROL_HEIGHT)
        self._disabled = False
        self._hovered = False
        self._pressed = False
        self._focused = False
        self._explicit_width = width

        self._measure = tkfont.Font(family=theme.FAMILY, size=theme.FONT_NORMAL[1])
        wanted = self._preferred_width()
        super().__init__(
            master,
            width=wanted + self._RING * 2,
            height=self._height + self._RING * 2,
            highlightthickness=0,
            borderwidth=0,
            background=background or theme.SURFACE,
            takefocus=1,
        )
        self._paint()
        self._bind_events()

    # ---------- 对外（ttk.Button 兼容层） ----------

    def state(self, spec: "Sequence[str] | str | None" = None) -> tuple[str, ...]:
        """读写状态。``state(["disabled"])`` / ``state(["!disabled"])``。

        无参调用返回当前状态元组（与 ``ttk.Widget.state()`` 同形状），
        测试断言 ``"disabled" in button.state()`` 就靠它。
        """
        if spec is None:
            return ("disabled",) if self._disabled else ()
        names = (spec,) if isinstance(spec, str) else tuple(spec)
        for name in names:
            disable = _state_wants_disabled(name)
            if disable is not None:
                self._set_disabled(disable)
        self._paint()
        return self.state()

    def invoke(self) -> None:
        """触发回调。禁用时不触发——和 ``ttk.Button.invoke()`` 一致。"""
        if self._disabled or self._command is None:
            return
        self._command()

    def configure(self, cnf: "object" = None, **kw: "object") -> "object":
        """支持 ``text`` / ``command`` / ``state`` / ``background`` 等。"""
        merged: dict[str, object] = {}
        if isinstance(cnf, dict):
            merged.update(cnf)
        elif cnf is not None:
            raise TypeError("configure() 只接受关键字参数或字典")
        merged.update(kw)

        for key in ("text", "command", "variant"):
            if key in merged:
                setattr(self, f"_{key}", merged.pop(key))
        if "state" in merged:
            self._set_disabled(_state_wants_disabled(merged.pop("state")) is True)

        result = super().configure(merged) if merged else super().configure()
        self._paint()
        return result

    config = configure

    def cget(self, key: str) -> "object":
        if key == "text":
            return self._text
        if key == "state":
            return "disabled" if self._disabled else "normal"
        return super().cget(key)  # type: ignore[arg-type]

    # ---------- 内部 ----------

    def _preferred_width(self) -> int:
        """按文字算宽度，再套最小宽度。"""
        if self._explicit_width:
            return int(self._explicit_width)
        text_width = self._measure.measure(self._text or " ")
        return max(self._MIN_WIDTH, text_width + theme.CONTROL_PAD_X * 2)

    def _set_disabled(self, disabled: bool) -> None:
        if disabled:
            # 禁用时清掉悬停/按下：否则恢复后背景会卡在悬停色上
            self._hovered = False
            self._pressed = False
        self._disabled = bool(disabled)

    def _bind_events(self) -> None:
        # 不要叫 ``_bind``：那是 ``tk.Misc.bind`` 内部调用的方法名，
        # 覆盖它会让 ``bind()`` 直接炸（参数对不上）
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Return>", self._on_key)
        self.bind("<space>", self._on_key)

    def _on_press(self, _event: "tk.Event") -> str:
        if self._disabled:
            return "break"
        self._pressed = True
        self._paint()
        return "break"

    def _on_release(self, _event: "tk.Event") -> str:
        if self._disabled:
            return "break"
        was_pressed = self._pressed
        self._pressed = False
        self._paint()
        # 只在「按下也发生在本按钮上」时才触发，避免拖出去松手误触
        if was_pressed:
            self.invoke()
        return "break"

    def _on_enter(self, _event: "tk.Event") -> None:
        if self._disabled:
            return
        self._hovered = True
        self._paint()

    def _on_leave(self, _event: "tk.Event") -> None:
        self._hovered = False
        self._pressed = False
        self._paint()

    def _on_focus_in(self, _event: "tk.Event") -> None:
        self._focused = True
        self._paint()

    def _on_focus_out(self, _event: "tk.Event") -> None:
        self._focused = False
        self._pressed = False
        self._paint()

    def _on_key(self, _event: "tk.Event") -> str:
        self.invoke()
        return "break"

    def _fill_and_text(self) -> tuple[str, str]:
        """当前状态对应的 ``(底色, 文字色)``。"""
        if self._disabled:
            return theme.DISABLED_BG, theme.DISABLED_FG
        if self._variant == self.ACCENT:
            if self._pressed:
                return theme.PRIMARY_PRESSED, theme.TEXT_ON_PRIMARY
            if self._hovered:
                return theme.PRIMARY_HOVER, theme.TEXT_ON_PRIMARY
            return theme.PRIMARY, theme.TEXT_ON_PRIMARY
        # 标准型：底色极浅，靠边框表达「这是个按钮」
        if self._pressed:
            return theme.PRESSED, theme.TEXT
        if self._hovered:
            return theme.HOVER, theme.TEXT
        return theme.SURFACE, theme.TEXT

    def _paint(self) -> None:
        width = int(float(self["width"]))
        height = int(float(self["height"]))
        self.delete("all")

        ring = self._RING
        fill, text_color = self._fill_and_text()

        if self._focused and not self._disabled:
            self.create_polygon(
                _round_rect_points(0, 0, width - 1, height - 1, theme.RADIUS_CONTROL + ring),
                smooth=True,
                fill=theme.PRIMARY_RING,
                outline=theme.PRIMARY_RING,
            )

        outline = theme.CARD_BORDER if self._disabled else (
            theme.PRIMARY if self._variant == self.ACCENT
            else (theme.BORDER_STRONG if self._hovered else theme.BORDER)
        )
        self.create_polygon(
            _round_rect_points(
                ring, ring, width - 1 - ring, height - 1 - ring, theme.RADIUS_CONTROL
            ),
            smooth=True,
            fill=fill,
            outline=outline,
            width=1,
        )
        self.create_text(
            width / 2,
            height / 2,
            text=self._text,
            fill=text_color,
            font=theme.FONT_NORMAL,
        )


class PrimaryButton(FluentButton):
    """主操作按钮（主色实心，40px）。一个视图里最多放一个。"""

    def __init__(
        self, master: tk.Misc, text: str, command: Callable[[], None]
    ) -> None:
        super().__init__(
            master,
            text=text,
            command=command,
            variant=FluentButton.ACCENT,
            height=theme.PRIMARY_BUTTON_HEIGHT,
            width=96,
        )


class SecondaryButton(FluentButton):
    """次操作按钮（描边，32px）。"""

    def __init__(
        self, master: tk.Misc, text: str, command: Callable[[], None]
    ) -> None:
        super().__init__(
            master, text=text, command=command, variant=FluentButton.STANDARD
        )


class BrandMark(tk.Canvas):
    """品牌徽标：圆角方块 + 一个字。

    Fluent 的应用标识一律带圆角（沿用控件圆角体系），而 ``tk.Frame``
    只能是直角——和旁边的圆角按钮摆在一起时很扎眼，所以这里也自绘。
    """

    SIZE = 32

    def __init__(self, master: tk.Misc, text: str = "B") -> None:
        super().__init__(
            master,
            width=self.SIZE,
            height=self.SIZE,
            highlightthickness=0,
            borderwidth=0,
            background=theme.NAV_BG,
        )
        self.create_polygon(
            _round_rect_points(0, 0, self.SIZE - 1, self.SIZE - 1, theme.RADIUS_CARD),
            smooth=True,
            fill=theme.PRIMARY,
            outline=theme.PRIMARY,
        )
        self.create_text(
            self.SIZE / 2,
            self.SIZE / 2,
            text=text,
            fill=theme.TEXT_ON_PRIMARY,
            font=theme.FONT_MEDIUM,
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
            master, background=theme.NAV_BG, cursor="hand2", highlightthickness=0
        )
        self._command = command
        self._active = False

        # 选中指示条用 Canvas 画：Fluent 的指示条是**圆角**竖条，
        # 而 tk.Frame 只能是直角方块
        self._bar = tk.Canvas(
            self,
            width=theme.NAV_BAR_WIDTH,
            height=theme.NAV_ITEM_HEIGHT,
            background=theme.NAV_BG,
            highlightthickness=0,
            borderwidth=0,
        )
        self._bar.pack(side="left", fill="y")
        self._bar.pack_propagate(False)

        body = tk.Frame(self, background=theme.NAV_BG)
        body.pack(side="left", fill="both", expand=True)

        self._icon = tk.Label(
            body, text=icon, font=theme.FONT_MEDIUM,
            background=theme.NAV_BG, foreground=theme.TEXT_MUTED,
            width=2, anchor="center",
        )
        self._icon.pack(side="left", padx=(theme.PAD_MD, theme.PAD_XS))

        self._text = tk.Label(
            body, text=text, font=theme.FONT_MEDIUM,
            background=theme.NAV_BG, foreground=theme.NAV_FG,
            anchor="w",
        )
        self._text.pack(side="left", fill="x", expand=True)

        self._paintable = (self, body, self._icon, self._text)
        for widget in self._paintable:
            widget.bind("<Button-1>", self._on_click)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
        self._bar.bind("<Button-1>", self._on_click)

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
            self._paint(theme.NAV_HOVER)
            # 指示条区域也要跟着换底，否则悬停时左边留一条原底色的缝
            self._bar.configure(background=theme.NAV_HOVER)

    def _on_leave(self, _event: "object" = None) -> None:
        self._render()

    def _paint(self, background: str) -> None:
        for widget in self._paintable:
            widget.configure(background=background)

    def _render_bar(self) -> None:
        """画选中指示条：选中时主色圆角竖条，否则不画但**占位**。

        占位是必须的——把整条隐藏/显示会让文字左右跳动。
        """
        self._bar.delete("all")
        self._bar.configure(background=(
            theme.NAV_ACTIVE_BG if self._active else theme.NAV_BG
        ))
        if not self._active:
            return
        width = theme.NAV_BAR_WIDTH
        top = (theme.NAV_ITEM_HEIGHT - theme.NAV_BAR_HEIGHT) / 2
        self._bar.create_polygon(
            _round_rect_points(
                0, top, width - 1, top + theme.NAV_BAR_HEIGHT - 1, width / 2
            ),
            smooth=True,
            fill=theme.PRIMARY,
            outline=theme.PRIMARY,
        )

    def _render(self) -> None:
        if self._active:
            self._paint(theme.NAV_ACTIVE_BG)
            self._icon.configure(foreground=theme.NAV_ACTIVE_FG)
            self._text.configure(foreground=theme.NAV_ACTIVE_FG)
        else:
            self._paint(theme.NAV_BG)
            self._icon.configure(foreground=theme.TEXT_MUTED)
            self._text.configure(foreground=theme.NAV_FG)
        self._render_bar()


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
    """日志区。

    上传进度、接口返回、报错都往这里追加。用等宽字体对齐；
    底色与主区刻意拉开（浅色主题下用略深的灰、深色主题下用更深的黑），
    用户一眼能看出「这是输出不是输入」——**不抢眼**，所以不用语义色。
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

    def rows(self) -> list[tuple[str, str]]:
        """读回当前内容，``(键, 值)`` 列表。

        给测试用：环境自检是异步填的，要断言「界面已经显示成结果了」
        就得能读回控件上的文字。顺带对使用者也是个方便的取值口。
        """
        return [
            (key.cget("text"), value.cget("text")) for key, value in self._rows
        ]

    def text(self) -> str:
        """全部内容拼成一段文本，便于断言某个词在不在。"""
        return "\n".join(f"{key}{value}" for key, value in self.rows())
