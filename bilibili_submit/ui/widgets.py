"""可复用的界面组件。

这一层只管「长什么样」和「怎么交互」，不含任何业务逻辑——
组件不知道 B 站、投稿、cookie 的存在，只接收回调和数据。

所有颜色/字体/间距从 :mod:`bilibili_submit.ui.theme` 取，不写死字面量。
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk
from typing import Callable, Iterable, Sequence

from . import theme

__all__ = [
    "Card",
    "ScrollArea",
    "SectionTitle",
    "ExpansionPanel",
    "OptionSwitches",
    "OPTION_LABELS",
    "FormRow",
    "MaterialButton",
    "FilledButton",
    "OutlinedButton",
    "TextButton",
    "BrandMark",
    "AssistChip",
    "ActionBar",
    "ProgressBar",
    "LogConsole",
    "Placeholder",
    "KeyValueList",
    "NavRailItem",
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
            self, highlightthickness=0, borderwidth=0, background=theme.PAPER
        )
        self._scrollbar = ttk.Scrollbar(
            self, orient="vertical", command=self._canvas.yview
        )
        self._canvas.configure(yscrollcommand=self._sync_scrollbar)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        self._scrollbar.grid(row=0, column=1, sticky="ns")
        #: 上一次量到的画布宽度，见 :meth:`_on_canvas_configure`
        self._last_canvas_width = 0

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
        if self._nested_can_scroll(event.widget, -1 if event.delta > 0 else 1):
            return ""  # 内嵌控件自己还能滚，交给它
        self._scroll_units(-1 if event.delta > 0 else 1)
        return "break"

    def bind_nested_scroll(self, widget: tk.Misc) -> None:
        """给**内嵌的可滚动控件**装「滚到边界交给外层」的转发。

        Tk 的事件沿 ``bindtags`` 传播，不沿父容器链——鼠标停在
        Treeview 上滚滚轮时，绑在 ``self._canvas`` 上的处理器
        **根本收不到**（Treeview 的 bindtags 里没有 canvas）。

        批量任务页的任务列表就装在滚动区里面，不装这个转发的后果是：
        整页内容溢出时用户滚鼠标只能滚列表，永远滚不到下面的日志区，
        而滚动条又细得很难拖。

        用法：建完列表/日志后调一次::

            area.bind_nested_scroll(self._tree)
        """
        if sys.platform == "win32":
            widget.bind("<MouseWheel>", self._on_wheel)
        else:
            widget.bind("<Button-4>", lambda e: self._scroll_from_nested(-1, widget))
            widget.bind("<Button-5>", lambda e: self._scroll_from_nested(1, widget))

    def _scroll_from_nested(self, direction: int, widget: tk.Misc) -> str:
        if self._nested_can_scroll(widget, direction):
            return ""
        self._scroll_units(direction)
        return "break"

    @staticmethod
    def _nested_can_scroll(widget: tk.Misc, direction: int) -> bool:
        """内嵌控件在这个方向上还有余量吗？有就放行给它自己滚。

        认不出的控件（没有 ``yview``）一律当作「不能滚」——
        那是普通 Label，事件本来也不该被它截住。
        """
        view = getattr(widget, "yview", None)
        if view is None:
            return False
        try:
            first, last = view()
        except Exception:  # noqa: BLE001 - 控件已销毁/不是滚动控件
            return False
        return first > 0.0 if direction < 0 else last < 1.0

    def refresh(self) -> None:
        """外部改了 ``body`` 内部几何后，手动重算滚动区。

        档位切换会改列表的 ``minsize``，但 canvas 的 ``scrollregion``
        不会因为孙子控件变了就自己更新——不调这一下，滚到底的位置
        停留在旧值上，底部一截滚不到。
        """
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

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
        # 内容跟着容器宽度走，否则窄窗口下会横向溢出。
        # 宽度没变就别动：Configure 在高度变化、子控件几何变化时也会
        # 派发，无脑 itemconfigure 会让画布重排一遍内嵌窗口。
        if event.width <= 1 or event.width == self._last_canvas_width:
            return
        self._last_canvas_width = event.width
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


class ExpansionPanel(ttk.Frame):
    """展开面板（Expansion panel）：点标题栏展开或收起里面的表单。

    Material 3 的组件名，按钮三档之外最常用的那个。页面上方「每次都要
    填的」和「偶尔动一次的」挤在一起时，任务列表就被压得只剩三行。
    折叠把选择权还给用户。

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
            fg=theme.INK_MUTED,
            font=theme.font("caption"),
            width=2,
            anchor="w",
        )
        self._arrow.grid(row=0, column=0)

        tk.Label(
            self._header,
            text=title,
            bg=theme.COLLAPSE_BG,
            # 折叠标题是「可点的东西」，用深梅而不是纯墨色——
            # 一点点色差就在说「这里能展开」，比加下划线克制
            fg=theme.PINK_DEEP,
            font=theme.font("body-strong"),
        ).grid(row=0, column=1, sticky="w")

        self._hint = tk.Label(
            self._header,
            text="",
            bg=theme.COLLAPSE_BG,
            fg=theme.INK_MUTED,
            font=theme.font("caption"),
        )
        self._hint.grid(row=0, column=2, sticky="e", padx=(theme.PAD_SM, 0))

        self._body = ttk.Frame(self, style="Card.TFrame")
        self._body.columnconfigure(0, weight=1)

        if subtitle:
            tk.Label(
                self._header,
                text=subtitle,
                bg=theme.COLLAPSE_BG,
                fg=theme.INK_MUTED,
                font=theme.font("caption"),
            ).grid(row=1, column=1, columnspan=2, sticky="w")

        # Tk 的事件不冒泡，标题栏里每个子控件都得单独绑一次，
        # 否则点在文字上没反应——只有点到 padding 才展开，很难用。
        # 悬停同理：绑在 header 上的话，鼠标移到标题文字上时底色就掉了。
        for widget in (self._header, self._arrow, self._hint, *self._header.winfo_children()):
            widget.bind("<Button-1>", self._on_click)
            widget.bind("<Enter>", self._on_hover)
            widget.bind("<Leave>", self._on_unhover)

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

    def _on_click(self, _event: tk.Event) -> str:
        self.set_opened(not self._opened)
        return "break"

    def _on_hover(self, _event: tk.Event) -> None:
        """悬停时整条标题栏换底色。

        「这一行能点」本来只靠手型光标传达，而光标在快速划过时很
        容易错过——底色是最不容易被漏掉的那个信号。

        用 ``COLLAPSE_HOVER`` 而不是通用的 ``HOVER``：通用的 ``HOVER``
        是给卡片上的控件选的，刷到折叠标题栏上会和它原本的底色撞成
        同一个值（浅色下就是如此），等于刷了个寂寞。
        """
        self._paint_header(theme.COLLAPSE_HOVER)

    def _on_unhover(self, _event: tk.Event) -> None:
        self._paint_header(theme.COLLAPSE_BG)

    def _paint_header(self, background: str) -> None:
        """把标题栏（含里面所有 Label）统一刷成某个底色。

        子控件要一个个刷：``tk.Frame`` 的底色不会被子 Label 继承，
        Label 默认是父容器底色但**只在创建时取一次**，之后改父的不
        会跟着变。
        """
        self._header.configure(bg=background)
        for child in self._header.winfo_children():
            try:
                child.configure(bg=background)
            except tk.TclError:  # 已被销毁，跳过
                continue

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
        required: bool = False,
    ) -> None:
        super().__init__(master, style="Card.TFrame")
        self.columnconfigure(1, weight=1)

        # 必填项在标签上打星号。这是「填之前就知道」比「提交后报缺」
        # 好得多的例子：星号不需要解释，而「缺少必填项」要用户回去
        # 自己找哪个空着。用深梅而不是红——红在这个界面里是「失败」，
        # 必填只是「注意」，不该占用那么重的颜色。
        text = f"{label} *" if required else f"{label}："
        ttk.Label(
            self, text=text, style="Field.TLabel", width=label_width, anchor="e"
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
    而不是切掉它——每个角给三个点（入角、角、出角）刚好得到圆角。

    半径会被 clamp 到「边长的一半」：

    - 传 :data:`~.theme.RADIUS_CONTROL`（7）得到设计稿那种小圆角矩形，
      按钮、导航高亮、卡片序号徽标都走这一档；
    - 传 :data:`~.theme.RADIUS_PILL`（999）则被 clamp 成胶囊，
      只有状态点、导航角标这类真要画成胶囊的地方才用。
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


def _state_wants_disabled(name: object) -> bool | None:
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


class MaterialButton(tk.Canvas):
    """按钮基类：Canvas 自绘圆角矩形 + 文字，三档形态的共同实现。

    为什么不直接用 ``ttk.Button``：clam 主题（这里统一用的跨平台上观感
    一致的那个）画的是**直角**，而设计稿的按钮是 **7px 小圆角**。ttk
    没有「圆角」这个选项，边框宽度再怎么调也只能是方的。自绘才能把
    圆角、36/40px 高度、五态配色都精确控住。

    五态（对应 Material 的 Rest / Hover / Pressed / Disabled / Focus）：
    默认、悬停、按下、禁用、聚焦（聚焦画主色环）。

    对外保持 ``ttk.Button`` 的常用接口——``state()`` 传 ``["disabled"]`` /
    ``["!disabled"]``、``configure(text=...)``、``cget("text")``、
    ``invoke()``——所以调用方一行都不用改。

    .. note::
       画布比可见按钮上下左右各多 :data:`_RING` 像素，留给聚焦环；
       ``winfo_reqheight()`` 因此是 ``height + 4``，可见按钮本身仍是
       精确的 36 / 40px。
    """

    #: 强调型（实心主色），一个视图里最多一个
    ACCENT = "accent"
    #: 标准型（描边），其余按钮都用这个
    STANDARD = "standard"
    #: 文字型（无边框），只给「低频但必须存在」的入口
    TEXT = "text"

    #: 聚焦环预留的边距（上下左右各这么多）
    _RING = 2
    #: 按钮最小宽度。中文两字按钮（"取消"）按文字算只有 44px，
    #: 太窄会显得局促，Material 3 同样有最小宽度约束。
    _MIN_WIDTH = 64
    #: 文字型的最小宽度。它内边距更小，同样的下限会显得两头空荡荡。
    _MIN_WIDTH_TEXT = 48

    def __init__(
        self,
        master: tk.Misc,
        text: str = "",
        command: Callable[[], None] | None = None,
        *,
        variant: str = STANDARD,
        height: int | None = None,
        width: int | None = None,
        background: str | None = None,
    ) -> None:
        self._text = text
        self._command = command
        self._variant = variant if variant in (
            self.ACCENT, self.STANDARD, self.TEXT
        ) else self.STANDARD
        self._height = int(height or theme.CONTROL_HEIGHT)
        self._disabled = False
        self._hovered = False
        self._pressed = False
        self._focused = False
        self._explicit_width = width
        #: 文字型按钮平时不画底色，用父容器的底色——记下它是 `background`
        #: 传进来的那个值，不能一律取 ``theme.PAPER``（导航区里就会错）。
        self._base_background = background or theme.PAPER

        # 测量字体和实际绘制字体必须是同一个，否则中文按钮宽度会算错。
        self._measure = theme.measure_font(
            "body-strong" if self._variant == self.ACCENT else "body"
        )
        wanted = self._preferred_width()
        super().__init__(
            master,
            width=wanted + self._RING * 2,
            height=self._height + self._RING * 2,
            highlightthickness=0,
            borderwidth=0,
            background=self._base_background,
            takefocus=1,
            cursor="hand2",
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

    def configure(self, cnf: object = None, **kw: object) -> object:
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

    def cget(self, key: str) -> object:
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
        if self._variant == self.TEXT:
            return max(self._MIN_WIDTH_TEXT, text_width + theme.PAD_SM * 2)
        return max(self._MIN_WIDTH, text_width + theme.CONTROL_PAD_X * 2)

    def _set_disabled(self, disabled: bool) -> None:
        if disabled:
            # 禁用时清掉悬停/按下：否则恢复后背景会卡在悬停色上
            self._hovered = False
            self._pressed = False
        self._disabled = bool(disabled)
        # 禁用时不给手型光标：手型在说「这里能点」，而它点不动——
        # 给一个点不动的控件手型，等于骗用户再点一次
        self.configure(cursor="" if disabled else "hand2")

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

    def _on_press(self, _event: tk.Event) -> str:
        if self._disabled:
            return "break"
        self._pressed = True
        self._paint()
        return "break"

    def _on_release(self, _event: tk.Event) -> str:
        if self._disabled:
            return "break"
        was_pressed = self._pressed
        self._pressed = False
        self._paint()
        # 只在「按下也发生在本按钮上」时才触发，避免拖出去松手误触
        if was_pressed:
            self.invoke()
        return "break"

    def _on_enter(self, _event: tk.Event) -> None:
        if self._disabled:
            return
        self._hovered = True
        self._paint()

    def _on_leave(self, _event: tk.Event) -> None:
        self._hovered = False
        self._pressed = False
        self._paint()

    def _on_focus_in(self, _event: tk.Event) -> None:
        self._focused = True
        self._paint()

    def _on_focus_out(self, _event: tk.Event) -> None:
        self._focused = False
        self._pressed = False
        self._paint()

    def _on_key(self, _event: tk.Event) -> str:
        self.invoke()
        return "break"

    def _fill_and_text(self) -> tuple[str, str]:
        """当前状态对应的 ``(底色, 文字色)``。"""
        if self._disabled:
            return theme.DISABLED_BG, theme.DISABLED_FG
        if self._variant == self.ACCENT:
            # 粉底一律配墨字：粉+白只有 2.64:1，AA 不达标
            if self._pressed:
                return theme.PINK_PRESSED, theme.INK_ON_PINK
            if self._hovered:
                return theme.PINK_HOVER, theme.INK_ON_PINK
            return theme.PINK, theme.INK_ON_PINK
        if self._variant == self.TEXT:
            # 文字型平时不画底色（融进父容器），只有悬停/按下才浮出一层。
            # 文字用深梅：它是全按钮唯一的"可点"信号，得站得住。
            if self._pressed:
                return theme.PRESSED, theme.PINK_DEEP
            if self._hovered:
                return theme.HOVER, theme.PINK_DEEP
            return self._base_background, theme.PINK_DEEP
        # 标准型：底色极浅，靠边框表达「这是个按钮」
        if self._pressed:
            return theme.PRESSED, theme.INK
        if self._hovered:
            return theme.HOVER, theme.INK
        return theme.PAPER, theme.INK

    def _font_name(self) -> str:
        """按钮文字用 body/ body-strong，深度只差一档字重。

        主操作加粗是为了让它在视觉上先被看到；但两档都只比正文大0px，
        靠「位置 + 底色」而不是靠字号把主次拉开——放大了会在中文短
        按钮里撑出空荡感。
        """
        return theme.font("body-strong" if self._variant == self.ACCENT else "body")

    def _outline_for(self, fill: str) -> str:
        """当前状态的描边色。

        文字型把描边涂成**和底色一样**——Canvas 画不出「无边框」，
        只能让它隐形。这样文字型就只剩文字和悬停底色两个信号，
        正好是它该有的分量。
        """
        if self._disabled:
            return theme.LINE
        if self._variant == self.TEXT:
            return fill
        if self._variant == self.ACCENT:
            return theme.PINK
        return theme.LINE_STRONG if self._hovered else theme.LINE

    def _paint(self) -> None:
        width = int(float(self["width"]))
        height = int(float(self["height"]))
        self.delete("all")

        ring = self._RING
        fill, text_color = self._fill_and_text()

        if self._focused and not self._disabled:
            # 聚焦环用深梅不用粉：粉在纸白上只有 2.64:1，等于没有
            self.create_polygon(
                _round_rect_points(0, 0, width - 1, height - 1, theme.RADIUS_CONTROL),
                smooth=True,
                fill=theme.FOCUS_RING,
                outline=theme.FOCUS_RING,
            )

        outline = self._outline_for(fill)
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
            font=self._font_name(),
        )


class FilledButton(MaterialButton):
    """实心按钮（Filled）：主色填充，最高强调层级。一个视图里最多放一个。"""

    def __init__(
        self, master: tk.Misc, text: str, command: Callable[[], None]
    ) -> None:
        super().__init__(
            master,
            text=text,
            command=command,
            variant=MaterialButton.ACCENT,
            height=theme.PRIMARY_BUTTON_HEIGHT,
            width=96,
        )


class OutlinedButton(MaterialButton):
    """描边按钮（Outlined）：1px 边框 + 透明底，中等强调层级。"""

    def __init__(
        self, master: tk.Misc, text: str, command: Callable[[], None]
    ) -> None:
        super().__init__(
            master, text=text, command=command, variant=MaterialButton.STANDARD
        )


class TextButton(MaterialButton):
    """文字按钮（Text）：没有边框，只有一行字。

    给「低频但必须存在」的入口——批量页的「从 YAML 加载…」就是典型：
    它在注释里明写着「多数用户不该看见它」，可又不能删（yaml 能表达
    界面表达不了的东西）。这类入口做成标准描边按钮，就会和主操作
    争夺注意力：一排七个长得一样的按钮，用户扫一遍也分不出该点哪个。

    没有边框不等于看不出能点——三个信号都还在：文字用深梅（强调色）、
    悬停浮出一层浅底、手型光标。

    .. note::
       一个视图里**也不要放太多**。它比标准按钮弱，但放五个就等于
       又造出了一排同级的按钮，问题原样回来。
    """

    def __init__(
        self, master: tk.Misc, text: str, command: Callable[[], None]
    ) -> None:
        super().__init__(
            master, text=text, command=command, variant=MaterialButton.TEXT
        )


class BrandMark(tk.Canvas):
    """品牌徽标：圆角方块 + 一个字。

    应用标识一律带圆角（沿用控件圆角体系），而 ``tk.Frame`` 只能是
    直角——和旁边的圆角按钮摆在一起时很扎眼，所以这里也自绘。

    26px 是设计稿给的尺寸：侧栏里它和 13.5px 的品牌名并排，32px 会把
    那一行撑得比导航项还高。
    """

    SIZE = 26

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
            _round_rect_points(
                0, 0, self.SIZE - 1, self.SIZE - 1, theme.RADIUS_CONTROL
            ),
            smooth=True,
            fill=theme.PINK,
            outline=theme.PINK,
        )
        self.create_text(
            self.SIZE / 2,
            self.SIZE / 2,
            text=text,
            fill=theme.INK_ON_PINK,   # 粉底配墨字
            font=theme.font("subtitle"),
        )


def _tone_colors(tone: str) -> tuple[str, str]:
    """取语义色的 ``(前景, 浅底)``。"""
    return theme.TONES.get(tone, theme.TONES["idle"])


class AssistChip(tk.Label):
    """辅助标签（Assist chip）：形状记号 + 文字，表达「就绪/未就绪/进行中」。

    用 ``tk.Label`` 而非 ``ttk``：ttk 的 Label 改前景色要绕 style，
    而状态色是动态变化的，直接 tk.Label 更省事。

    文字前带一个 glyph（``✓✕!◐`` 等）。**这不是装饰**——状态如果只靠
    颜色传达，色盲用户看到的是一排不同底色的色块，读不出哪个是失败。
    颜色和形状双通道编码，才真的可访问。记号见
    :data:`~.theme.TONE_GLYPHS`。
    """

    def __init__(self, master: tk.Misc, text: str = "", tone: str = "idle") -> None:
        super().__init__(
            master,
            text="",
            font=theme.font("caption"),
            background=theme.PAPER_ALT,
            foreground=theme.INK,
            padx=theme.PAD_SM,
            # 半格的内边距：胶囊要贴着文字，用整格会显得像个按钮。
            # 这里**不给手型光标**——状态标签是读的，不是点的，
            # 给手型等于暗示「点它有反应」。
            pady=theme.GRID // 4,
        )
        self.set(text, tone)

    def set(self, text: str, tone: str = "idle") -> None:
        """更新文字与配色。``tone`` 见 :data:`~.theme.TONES`。"""
        fg, bg = _tone_colors(tone)
        glyph = theme.tone_glyph(tone)
        # glyph 始终占位，空文案时也不会让文字左右跳
        self.configure(
            text=f"{glyph} {text}" if text else glyph,
            foreground=fg,
            background=bg,
        )


class NavRailItem(tk.Frame):
    """导航栏项：整行小圆角高亮 + 可选的右侧数字角标。

    选中指示是撑满整行的圆角底（7px），不再有左侧竖条。tk.Frame 画不出
    圆角，所以底座换成 Canvas：圆角矩形画在 Canvas 上，图标和文字作为
    window item 嵌进画布，垂直居中。

    未选中/悬停时底色涂成**当刻该有的底色**（隐形 / 悬停色），选中
    才浮出浅粉底——选中与否只是底色差异，图标和文字不会左右跳动。
    这是竖条版导航「占位不隐藏」的同一条纪律。

    角标（``set_badge``）是**装饰性计数**，不是状态：批量任务页用它
    显示队列里还有几条没跑完。不传或传 0 就不显示。

    .. note::
       文字隐藏用 ``itemconfigure(state="hidden")``：window item 和
       它的配置都还在，恢复时原样回来——和 ``grid_remove()`` 一样
       是可逆隐藏（见 .impeccable.md 的反模式清单），不用
       ``grid_forget()``。
    """

    #: 图标/文字距药丸左缘的距离
    _INSET = 8

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
        self._hovered = False
        self._collapsed = False
        self._pill_item: int | None = None  # 药丸的 canvas item id
        self._badge_count = 0

        # 药丸底座。宽度由 pack 拉伸决定，画药丸时才读实际宽度
        self._pill = tk.Canvas(
            self,
            height=theme.NAV_ITEM_HEIGHT,
            background=theme.NAV_BG,
            highlightthickness=0,
            borderwidth=0,
        )
        self._pill.pack(fill="both", expand=True, padx=theme.NAV_ITEM_MARGIN)

        self._icon = tk.Label(
            self._pill, text=icon, font=theme.font("subtitle"),
            background=theme.NAV_BG, foreground=theme.INK_MUTED,
            width=2, anchor="center",
        )
        self._text = tk.Label(
            self._pill, text=text, font=theme.font("body"),
            background=theme.NAV_BG, foreground=theme.NAV_FG,
            anchor="w",
        )
        self._icon_win = self._pill.create_window(
            self._INSET, theme.NAV_ITEM_HEIGHT / 2,
            window=self._icon, anchor="w",
        )
        self._text_win = self._pill.create_window(
            self._INSET + self._icon.winfo_reqwidth() + 2,
            theme.NAV_ITEM_HEIGHT / 2,
            window=self._text, anchor="w",
        )
        # 角标：粉底墨字的胶囊，右端对齐药丸内侧
        self._badge = tk.Canvas(
            self._pill,
            width=1, height=16,
            background=theme.NAV_BG,
            highlightthickness=0,
            borderwidth=0,
        )
        self._badge_win = self._pill.create_window(
            0, theme.NAV_ITEM_HEIGHT / 2, window=self._badge, anchor="e"
        )
        self._pill.bind("<Configure>", self._on_configure)

        self._paintable = (self._pill, self._icon, self._text, self._badge)
        for widget in self._paintable:
            widget.bind("<Button-1>", self._on_click)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

    # ---------- 对外 ----------

    def set_active(self, active: bool) -> None:
        """切换选中态。"""
        self._active = active
        self._render()

    def set_badge(self, count: int | None) -> None:
        """在导航项右端显示一个数字角标，``0`` / ``None`` 表示不显示。

        角标是**装饰性计数**（「批量任务」后面那个「3」），不是状态色：
        它不参与成败语义，所以用品牌粉而不是语义色。底色仍配墨字——
        粉底白字只有 2.64:1。

        折叠成图标栏时角标也藏起来：56px 里图标居中已经很挤，角标
        会压到图标上。计数在页面里本来也看得到（批量任务页有汇总行）。
        """
        self._badge_count = int(count) if count else 0
        self._paint_badge()
        self._relayout()

    @property
    def badge(self) -> int:
        """当前角标数字（0 表示不显示）。"""
        return self._badge_count

    def _paint_badge(self) -> None:
        canvas = self._badge
        canvas.delete("all")
        show = self._badge_count > 0 and not self._collapsed
        if not show:
            canvas.configure(width=1)
            return
        text = str(self._badge_count) if self._badge_count < 100 else "99+"
        measure = theme.measure_font("caption-strong")
        width = max(int(measure.measure(text)) + 12, 18)
        height = 16
        canvas.configure(width=width, height=height)
        canvas.create_polygon(
            _round_rect_points(0, 0, width - 1, height - 1, theme.RADIUS_PILL),
            smooth=True,
            fill=theme.PINK,
            outline=theme.PINK,
        )
        canvas.create_text(
            width / 2, height / 2,
            text=text, fill=theme.INK_ON_PINK, font=theme.font("caption-strong"),
        )

    def set_collapsed(self, collapsed: bool) -> None:
        """收成图标栏：只留汉字图标，隐藏文字。

        窗口窄到一定程度时，导航那 200px 里大半是空白，而内容区正
        挤得看不全文件名——折叠把那 144px 还给内容区。

        隐藏用 ``itemconfigure(state="hidden")``（可逆，item 还在），
        理由见类文档。图标重新居中靠 :meth:`_relayout` 按药丸实际
        宽度现算，比手算 padx 稳。
        """
        self._collapsed = collapsed
        self._pill.itemconfigure(
            self._text_win, state="hidden" if collapsed else "normal"
        )
        self._relayout()

    # ---------- 内部 ----------

    def _on_click(self, _event: object = None) -> None:
        if self._command:
            self._command()

    def _on_enter(self, _event: object = None) -> None:
        if not self._active:
            self._hovered = True
            self._render()

    def _on_leave(self, _event: object = None) -> None:
        self._hovered = False
        self._render()

    def _on_configure(self, _event: object = None) -> None:
        # pack 拉伸 / 窗口折叠都会走到这里：药丸宽度变了，重画
        self._relayout()

    def _pill_color(self) -> str:
        if self._active:
            return theme.NAV_ACTIVE_BG
        if self._hovered:
            return theme.NAV_HOVER
        return theme.NAV_BG

    def _paint_pill(self) -> None:
        """画圆角底：选中浅粉、悬停上色、平时与导航条同色（隐形）。

        只重建这一个 item——``delete("all")`` 会把嵌在画布上的
        图标/文字 window item 一起删掉（真踩过：恢复展开后文字再也不
        出现，``winfo_ismapped()`` 恒为 0）。重画的底用 ``tag_lower``
        压回底层，图标和文字才不会被它盖住。
        """
        canvas = self._pill
        if self._pill_item is not None:
            canvas.delete(self._pill_item)
            self._pill_item = None
        # 底永远是导航底——高亮不满铺（两侧各留 NAV_ITEM_MARGIN），
        # 圆角才读得出来；满铺的选中样式读不出这个「悬空」感。
        canvas.configure(background=theme.NAV_BG)
        color = self._pill_color()
        # 角标画布跟着底色走，否则选中/悬停时它会露出一个方块
        self._badge.configure(background=color)
        width = canvas.winfo_width()
        if width <= 1:
            return  # 还没布局，Configure 马上会再来
        self._pill_item = canvas.create_polygon(
            _round_rect_points(
                0, 0, width - 1, theme.NAV_ITEM_HEIGHT - 1, theme.RADIUS_CONTROL
            ),
            smooth=True,
            fill=color,
            outline=color,
        )
        canvas.tag_lower(self._pill_item)

    def _relayout(self, _event: object = None) -> None:
        """重画圆角底并重排图标/文字/角标。

        折叠态把图标挪到底片正中；展开态图标靠左、文字跟在后面，
        角标贴右缘。坐标现算而不是记死——宽度随折叠/展开变化。
        """
        self._paint_pill()
        icon_w = self._icon.winfo_reqwidth()
        pill_w = self._pill.winfo_width()
        self._pill.coords(
            self._badge_win,
            max(pill_w - self._INSET, self._INSET),
            theme.NAV_ITEM_HEIGHT / 2,
        )
        if self._collapsed:
            x = max((pill_w - icon_w) / 2, 0)
            self._pill.coords(self._icon_win, x, theme.NAV_ITEM_HEIGHT / 2)
        else:
            self._pill.coords(
                self._icon_win, self._INSET, theme.NAV_ITEM_HEIGHT / 2
            )
            self._pill.coords(
                self._text_win,
                self._INSET + icon_w + 2, theme.NAV_ITEM_HEIGHT / 2,
            )

    def _render(self) -> None:
        color = self._pill_color()
        if self._active:
            icon_fg = text_fg = theme.NAV_ACTIVE_FG
        else:
            icon_fg, text_fg = theme.INK_MUTED, theme.NAV_FG
        self._icon.configure(background=color, foreground=icon_fg)
        self._text.configure(background=color, foreground=text_fg)
        self._paint_pill()
        self._relayout()


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
            self, text="", font=theme.font("caption"), anchor="w",
            background=theme.PAPER, foreground=theme.INK_SECOND,
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
        button = OutlinedButton(self._actions, text, command)
        button.grid(row=0, column=len(self._buttons), padx=(theme.PAD_XS, 0))
        self._buttons.append(button)
        return button

    def set_actions_enabled(self, enabled: bool) -> None:
        """统一启用/禁用所有快捷按钮。"""
        state = ["!disabled"] if enabled else ["disabled"]
        for button in self._buttons:
            button.state(state)


class _PreflightItem(tk.Frame):
    """操作条里的一个预检项：圆点记号 + 一行短文案。

    设计稿的预检是「14px 圆点 + 12px 文字」一串横排。圆点里放的是
    :data:`~.theme.TONE_GLYPHS` 的形状记号（``✓`` / ``!`` / ``·``），
    **不是纯色块**——和 :class:`AssistChip` 同样的理由：状态只靠颜色
    传达的话，色盲用户看到的是一排颜色不同的圆点，读不出哪个是问题。

    圆点的底是语义色的前景（深绿 / 深橙 / 中灰），记号用 :data:`~.theme.FIELD_BG`
    （浅色下就是纯白）。这样圆点自身也有 5:1 以上的对比度，不是「能看见就行」。
    """

    #: 圆点直径
    DOT = 14

    def __init__(
        self, master: tk.Misc, tone: str = "idle", text: str = "",
        background: str | None = None,
    ) -> None:
        bg = background or theme.PAPER
        super().__init__(master, background=bg)
        fg, _chip_bg = _tone_colors(tone)

        dot = tk.Canvas(
            self,
            width=self.DOT, height=self.DOT,
            highlightthickness=0, borderwidth=0, background=bg,
        )
        dot.create_oval(0, 0, self.DOT - 1, self.DOT - 1, fill=fg, outline=fg)
        dot.create_text(
            self.DOT / 2, self.DOT / 2,
            text=theme.tone_glyph(tone),
            fill=theme.FIELD_BG,
            font=theme.font("caption"),
        )
        dot.pack(side="left")

        tk.Label(
            self, text=text, font=theme.font("caption"),
            background=bg, foreground=theme.INK_SECOND, anchor="w",
        ).pack(side="left", padx=(theme.PAD_XS, 0))


class ActionBar(tk.Frame):
    """底部固定操作条：左边说「将要发生什么」和预检结果，右边放主操作。

    这个组件解决的是**「主操作不该需要找」**。之前「开始投稿」按钮
    混在表单中间：表单一长它就被推到屏幕外，折叠区一展开它就换位置，
    窗口一缩它就消失。用户的真实体验是「每次都要先扫一遍界面找按钮」。

    有了它：

    - 主按钮**永远在同一个位置**，不随内容滚动、不随折叠开合移动；
    - 左边实时显示**将要发生什么**（几个稿件 / 几个分P / 自制还是转载），
      提交前不用回头核对表单；
    - 条件不满足时**就地禁用并说明原因**（「先在「投稿」页登录」），
      而不是让人点下去、再在日志里报错——那是「死后告知」。

    设计稿的底部操作条是**白底 + 顶部 1px 分隔线**（和卡片同一张纸，
    靠分隔线而不是靠深浅「切」出来），左边放**提交前预检**，右边放操作
    按钮。预检项比「将要发生什么」更贴近用户此刻的问题——他想知道的是
    「能不能投」，不是「会投什么」——所以两者都留：上面一行是计划，
    下面一行是体检结果。

    用法::

        bar = ActionBar(parent)
        bar.set_summary("将投稿 3 个稿件 · 5 个分P组 · 自制")
        bar.set_checks([("ok", "文件可读取"), ("warn", "标签仅 3 个")])
        bar.set_primary("开始投稿", on_submit)
        bar.block("先在「投稿」页扫码登录")
        bar.pack(fill="x", side="bottom")
    """

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, background=theme.PAPER)
        self.columnconfigure(0, weight=1)

        # 顶部 1px 分隔线：操作条和上方内容区同色，不画线就分不出界
        self._rule = tk.Frame(self, background=theme.LINE, height=1)
        self._rule.grid(row=0, column=0, columnspan=4, sticky="ew")

        # 快捷键绑在 **master**（整个视图）而不是 self：Tk 的事件不冒泡，
        # 焦点在标题输入框里时，绑在操作条上的 Ctrl+Enter 根本收不到。
        # 而「填完标题直接回车投稿」恰恰是最常见的用法。
        self._on_submit = lambda: None
        self._on_cancel = lambda: None
        #: 空闲时主按钮的文案。运行中会变成「取消」，收尾时靠它改回来
        self._idle_text = "开始"
        master.bind("<Control-Return>", self._fire_submit, add="+")
        master.bind("<Escape>", self._fire_cancel, add="+")

        # 左侧：标题 + 实时摘要 + 提交前预检。标题固定不变，摘要和预检
        # 随表单变——固定的部分提供锚点，变化的部分提供反馈。
        text_box = tk.Frame(self, background=theme.PAPER)
        text_box.grid(
            row=1, column=0, sticky="w",
            padx=(theme.PAD_XL, theme.PAD_SM), pady=theme.PAD_MD,
        )
        self._title = tk.Label(
            text_box, text="", font=theme.font("caption"),
            background=theme.PAPER, foreground=theme.INK_MUTED,
            anchor="w",
        )
        self._title.pack(anchor="w")

        self._summary = tk.Label(
            text_box, text="", font=theme.font("body-strong"),
            background=theme.PAPER, foreground=theme.INK,
            anchor="w",
        )
        self._summary.pack(anchor="w")

        #: 预检行。没调过 :meth:`set_checks` 就整行不占位——批量任务页
        #: 没有「体检」的概念，不该白占一行高度。
        self._checks = tk.Frame(text_box, background=theme.PAPER)
        self._check_items: list[_PreflightItem] = []

        # 右侧：原因提示 + 次按钮 + 主按钮。三者**各占一列**。
        #
        # 曾经把次按钮也grid 到主按钮那一列（靠 sticky 区分），结果两者
        # 完全重叠——粉底主按钮被压在下面看不见。所以列分配必须是
        # 独占的，调用方不许自己往里塞控件。
        self._reason = tk.Label(
            self, text="", font=theme.font("caption"),
            background=theme.PAPER, foreground=theme.INK_MUTED,
            anchor="e", justify="right",
        )
        self._reason.grid(row=1, column=1, sticky="e", padx=(0, theme.PAD_MD))

        self._secondary: OutlinedButton | None = None
        self._primary: FilledButton | None = None

    def set_primary(
        self,
        text: str,
        command: Callable[[], None],
        secondary_text: str = "",
        secondary_command: Callable[[], None] | None = None,
    ) -> None:
        """装上主操作按钮（以及可选的次操作）。重复调用会替换。

        「取消」是**特殊**的次操作：它只在运行中出现，且会顶掉普通次
        操作（比如「仅预览」）——运行中用户只可能想停止，不该还能预览。
        """
        for button in (self._primary, self._secondary):
            if button is not None:
                button.destroy()
        self._primary, self._secondary = None, None
        self._reason.grid_remove()

        self._on_submit = command
        self._idle_text = text
        self._primary = FilledButton(self, text, command)
        self._primary.grid(
            row=1, column=3, padx=(theme.PAD_XS, theme.PAD_XL), pady=theme.PAD_MD
        )

        if secondary_text and secondary_command:
            self._on_cancel = secondary_command
            self._secondary = OutlinedButton(self, secondary_text, secondary_command)
            self._secondary.grid(row=1, column=2, padx=(theme.PAD_XS, theme.PAD_XS))

    @property
    def secondary(self) -> "OutlinedButton | None":
        """次操作按钮。调用方要控制它的启用状态时用这个拿。"""
        return self._secondary

    def _fire_submit(self, _event: object = None) -> str:
        """Ctrl+Enter。禁用时不响应——快捷键不能绕过禁用状态。"""
        if self._primary is not None and "disabled" not in self._primary.state():
            self._on_submit()
        return "break"

    def _fire_cancel(self, _event: object = None) -> str:
        self._on_cancel()
        return "break"

    def set_summary(self, title: str, summary: str = "") -> None:
        """更新说明。``summary`` 是「将要发生什么」那句话。"""
        self._title.configure(text=title)
        self._summary.configure(text=summary)

    def set_checks(self, items: "Iterable[tuple[str, str]]") -> None:
        """更新提交前预检行，每项 ``(语义色名, 文案)``。

        预检是**提交前的体检报告**：哪些条件已经满足、哪些还差着。
        它和操作条右侧那句「还差一个视频文件」是两件事——后者是
        「能不能点」，前者是「点了之后顺不顺」。两者都在用户按下之前
        说出来，才不用等日志里报错。

        传空序列就把整行收起来（``pack_forget`` 而不是 ``destroy``：
        下次还能再挂回来）。批量任务页没有「体检」的概念，不调即可。

        语义色名见 :data:`~.theme.TONES`；文案要短——这一行和按钮
        共享横向空间，写长了会把按钮挤出可视区。
        """
        for child in self._checks.winfo_children():
            child.destroy()
        self._check_items = []

        pairs = list(items)
        if not pairs:
            self._checks.pack_forget()
            return
        for tone, text in pairs:
            item = _PreflightItem(self._checks, tone, text, background=theme.PAPER)
            item.pack(side="left", padx=(0, theme.PAD_MD))
            self._check_items.append(item)
        self._checks.pack(anchor="w", pady=(theme.PAD_XS, 0))

    @property
    def checks(self) -> "list[_PreflightItem]":
        """当前预检项，主要给测试看（组件不指望调用方读它）。"""
        return list(self._check_items)

    def block(self, reason: str = "") -> None:
        """禁用主操作并就地说明原因。

        ``reason`` 为空时只禁用不解释——**只应该在调用方已经用别的方式
        说明了原因时用**，否则用户面对一个灰按钮却不知道为什么。

        无论有没有 ``reason``，都会先把上一条原因擦掉：控件状态变了、
        文案还停在「尚未登录」是最糟的一种错，比什么都不说更误导。
        """
        if self._primary is not None:
            self._primary.state(["disabled"])
        if reason:
            self._reason.configure(text=self._ellipsize(reason))
            self._reason.grid()
        else:
            self._reason.configure(text="")
            self._reason.grid_remove()

    def _ellipsize(self, text: str, limit: int = 24) -> str:
        """原因太长时截断加省略号。

        操作条是**固定高度**的一行，写长了会把按钮挤出可视区——而按钮
        比原因重要。所以宁可少说几个字，也不能让「开始投稿」消失。
        真要解释清楚，位置应该是日志区，不是这一行。
        """
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def unblock(self) -> None:
        """解除禁用，隐藏原因。"""
        if self._primary is not None:
            self._primary.state(["!disabled"])
        self._reason.grid_remove()

    def set_busy(
        self,
        busy: bool,
        cancel: Callable[[], None] | None = None,
        cancel_text: str = "",
        busy_title: str = "",
    ) -> None:
        """切到「运行中」形态：主按钮变成取消。

        「运行中」时用户唯一想做的事就是**停下来**，所以这个位置上
        出现的应该是取消而不是一个禁用的「开始投稿」——禁用按钮不给
        用户任何出路。

        ``cancel_text`` / ``busy_title`` 用来把「正在做什么」说准：
        投稿时叫「取消 / 正在投稿」就够，但扫描目录时也叫「取消」
        会让人以为要放弃整批投稿。调用方按实际在做的事传。

        ``Ctrl+Enter`` 跟着一起变：空闲时是「开始」，运行中是「取消」，
        和按钮保持一致，不给用户两个含义不同的入口。

        次操作**不动**（不隐藏、不禁用）：它多半是「仅预览」这类
        无害操作，运行中依然有意义。要不要锁由调用方决定——组件
        不知道那个按钮是干什么的。
        """
        if self._primary is None:
            return
        if busy:
            self._title.configure(text=busy_title or "正在投稿")
            if cancel is not None:
                self._on_cancel = cancel
                self._primary.configure(command=cancel)
        else:
            self._primary.configure(command=self._on_submit)
        # 文案记在 _idle_text 上，不硬编码「开始投稿」：批量页的主按钮
        # 可能叫别的，硬编码回去就把它改名了
        self._primary.configure(
            text=(cancel_text or "取消") if busy else self._idle_text
        )
        self._primary.state(["!disabled"])


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

    上传进度、接口返回、报错都往这里追加。用等宽字体对齐——
    等宽在这里是**有职责的**：时间戳和进度百分比要能上下对齐成列，
    扫一眼就知道卡在哪一步。
    底色用次级面而不是纯色块，用户一眼能看出「这是输出不是输入」。
    """

    #: 最多保留多少行。日志是**追加**的，长批次能堆到几万行，
    #: 而 Text 每多一万行就多一份内存与重排成本，用户却只会看最后
    #: 几十行。超出的从顶部丢弃——留着头部的旧行没有意义。
    MAX_LINES = 3000

    def __init__(self, master: tk.Misc, height: int = 10) -> None:
        super().__init__(master, background=theme.LOG_BG, relief="flat")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        theme.register_fonts()
        self._text = tk.Text(
            self,
            height=height,
            wrap="word",
            background=theme.LOG_BG,
            foreground=theme.LOG_TEXT,
            insertbackground=theme.LOG_TEXT,
            font=theme.font("mono"),
            relief="flat",
            padx=theme.PAD_MD,
            pady=theme.PAD_SM,
            state="disabled",
        )
        scroll = ttk.Scrollbar(self, orient="vertical", command=self._text.yview)
        self._text.configure(yscrollcommand=scroll.set)

        self._text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        # 滚动与「恢复只读」都攒到事件循环空闲时做一次，见 _write。
        self._scroll_pending = False
        self._writable = False
        self._lines = 0

    def append(self, message: str) -> None:
        """追加一行。

        内容**同步**落盘（调用返回后就能从 Text 里读到），但滚动与
        状态恢复挪到空闲时——见 :meth:`_write` 里那段的实测数据。
        """
        self._write((message,))

    def extend(self, messages) -> None:
        """一次追加多行。批量到达（一批日志、一次扫描结果）走这条路。

        和循环调 :meth:`append` 的区别不只是少几次函数调用：多行合并
        成一次 ``insert``，Tk 只重排一次。
        """
        self._write(tuple(messages))

    def _write(self, messages: tuple[str, ...]) -> None:
        """落盘多行，并把滚动/只读恢复合并到空闲时做一次。

        ⚠️ 这里的形状是**实测**定下来的，别改回「每写一行滚一次」：
        Xvfb 上 2000 行实测——

        - 每行的 insert + see：175.7 ms
        - 同样多的内容，一次 insert + 一次 see：0.8 ms

        差了 220 倍，而且大头不是 insert（2000 次只要 1.8 ms），
        是 **see**：它每次都要让 Text 重排一遍再算滚动位置，写一行
        触发一次，行数越多越贵。上传/批量投稿时日志是成批到的，
        逐条 see 就等于把界面按在地上摩擦。

        所以：insert 保持同步（测试和用户都要立刻看到内容），
        see 用 after_idle 合并——一帧里来多少行都只滚一次。
        """
        if not messages:
            return
        if not self._writable:
            self._text.configure(state="normal")
            self._writable = True
        self._text.insert("end", "".join(m.rstrip() + "\n" for m in messages))
        self._lines += len(messages)
        self._trim()
        self._schedule_scroll()

    def _trim(self) -> None:
        over = self._lines - self.MAX_LINES
        if over > 0:
            self._text.delete("1.0", f"{over + 1}.0")
            self._lines -= over

    def _schedule_scroll(self) -> None:
        if self._scroll_pending:
            return
        self._scroll_pending = True
        try:
            self.after_idle(self._flush_scroll)
        except tk.TclError:  # 窗口已销毁，滚动没有意义了
            self._scroll_pending = False

    def _flush_scroll(self) -> None:
        self._scroll_pending = False
        try:
            self._text.see("end")
            if self._writable:
                self._text.configure(state="disabled")
                self._writable = False
        except tk.TclError:
            pass  # 窗口已销毁

    def clear(self) -> None:
        self._text.configure(state="normal")
        self._text.delete("1.0", "end")
        self._text.configure(state="disabled")
        self._lines = 0
        self._writable = False

    def forward_wheel_to(self, area: "ScrollArea") -> None:
        """把「滚到边界」的滚轮事件交给外层滚动区。

        日志自带滚动条，装在外层滚动区里时会把滚轮事件吃掉——用户
        滚到底想看下面的内容却滚不动。交给 :meth:`ScrollArea.
        bind_nested_scroll` 判断边界后再决定是否转给外层。

        抽成方法而不是让调用方读 ``self._text``，是因为那个 Text 是
        本组件的内部实现，调用方不该知道它是 ``tk.Text`` 还是别的。
        """
        area.bind_nested_scroll(self._text)


class Placeholder(ttk.Frame):
    """空状态：说明**这里将来会出现什么**，而不是「暂无数据」。

    「暂无数据」只陈述了现在的空，没告诉用户下一步做什么。而空状态是
    用户第一次看到这块区域时唯一的引导，浪费它很可惜。所以结构固定为：

    1. **标题**——这块区域是干什么的（永远有）
    2. **说明**——具体会出现什么、怎么用（永远有）
    3. **操作**——一个能立刻填上内容的按钮（可选）

    没有第2 步的「空状态」等于没说：用户看到「暂无数据」既不知道该做什么，
    也不知道填了之后会得到什么。
    """

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        hint: str = "",
        action_text: str = "",
        on_action: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master, style="Card.TFrame")
        self.columnconfigure(0, weight=1)

        ttk.Label(
            self, text=text, style="Heading.TLabel", justify="center"
        ).grid(row=0, column=0, pady=(theme.PAD_XL, theme.PAD_XS))
        if hint:
            ttk.Label(
                self, text=hint, style="Card.Secondary.TLabel", justify="center",
                wraplength=360,
            ).grid(row=1, column=0, pady=(0, theme.PAD_SM))
        if action_text and on_action:
            OutlinedButton(self, action_text, on_action).grid(
                row=2, column=0, pady=(theme.PAD_SM, theme.PAD_XL)
            )


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
            rows: ``(键, 值, 色调)`` 三元组序列，色调见 :class:`AssistChip`。
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
                font=theme.font("mono"),
                background=theme.PAPER,
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


class PageHeader(ttk.Frame):
    """页面头：大标题 + 右侧一行弱化副标题。

    设计稿的页面头是「21px 标题 + 12.5px 副标题」贴在同一行基线上：
    标题告诉用户「我在哪」，副标题告诉用户「这一页能干什么」。
    两者**不换行**——换行的话副标题会被当成另一段正文，而它是标题的
    补充说明，不是内容。
    """

    def __init__(self, master: tk.Misc, title: str, subtitle: str = "") -> None:
        super().__init__(master, style="TFrame")
        self.columnconfigure(1, weight=1)

        self._title = tk.Label(
            self, text=title, font=theme.font("display"),
            background=theme.SHELL, foreground=theme.INK, anchor="w",
        )
        self._title.grid(row=0, column=0, sticky="sw")

        self._subtitle = tk.Label(
            self, text=subtitle, font=theme.font("caption"),
            background=theme.SHELL, foreground=theme.INK_MUTED,
            anchor="sw", justify="left",
        )
        self._subtitle.grid(
            row=0, column=1, sticky="sw", padx=(theme.PAD_MD, 0), pady=(0, 3)
        )

    def set_subtitle(self, text: str) -> None:
        self._subtitle.configure(text=text)


class StepCard(Card):
    """带序号的卡片：圆形序号徽标 + 卡名 + 右侧提示语。

    设计稿把投稿流程拆成「① 视频源 / ② 投稿信息」两张编号卡片。编号
    不是装饰——它把「先选文件、再填信息」这个**顺序**直接画了出来，
    用户不用读完整个页面才知道该从哪开始。

    徽标是 Canvas 自绘的：``tk.Frame`` 画不出圆角，而 19px 的方块在
    一堆 7px 圆角控件旁边会很扎眼。
    """

    #: 序号徽标边长
    BADGE = 19

    def __init__(
        self,
        master: tk.Misc,
        step: int = 1,
        title: str = "",
        hint: str = "",
        padding: int = theme.PAD_LG,
    ) -> None:
        super().__init__(master, padding=padding)
        self.columnconfigure(0, weight=1)

        head = ttk.Frame(self, style="Card.TFrame")
        head.grid(row=0, column=0, sticky="ew", pady=(0, theme.PAD_MD))
        head.columnconfigure(1, weight=0)
        head.columnconfigure(2, weight=1)

        badge = tk.Canvas(
            head,
            width=self.BADGE, height=self.BADGE,
            highlightthickness=0, borderwidth=0, background=theme.PAPER,
        )
        size = self.BADGE
        badge.create_polygon(
            _round_rect_points(0, 0, size - 1, size - 1, theme.RADIUS_BADGE),
            smooth=True, fill=theme.PINK_TINT, outline=theme.PINK_TINT,
        )
        badge.create_text(
            size / 2, size / 2, text=str(step),
            fill=theme.PINK_DEEP, font=theme.font("caption-strong"),
        )
        badge.grid(row=0, column=0, padx=(0, theme.PAD_SM))

        tk.Label(
            head, text=title, font=theme.font("title"),
            background=theme.PAPER, foreground=theme.INK, anchor="w",
        ).grid(row=0, column=1, sticky="w")

        self._hint = tk.Label(
            head, text=hint, font=theme.font("caption"),
            background=theme.PAPER, foreground=theme.INK_MUTED,
            anchor="e", justify="right",
        )
        self._hint.grid(row=0, column=2, sticky="e", padx=(theme.PAD_MD, 0))

        #: 卡片内容挂这里
        self.body = ttk.Frame(self, style="Card.TFrame")
        self.body.grid(row=1, column=0, sticky="ew")
        self.body.columnconfigure(0, weight=1)

    @property
    def hint(self) -> str:
        return str(self._hint.cget("text"))

    def set_hint(self, text: str) -> None:
        """更新右侧提示语（比如「已选 3 / 建议 5-10 个」这种动态提示）。"""
        self._hint.configure(text=text)


class MetaGrid(tk.Frame):
    """横向元信息网格：一格一个「小标签 + 值」。

    设计稿用它展示视频的分辨率 / 时长 / 大小 / 编码 / 码率——五个数横着
    排成一带，比竖着的键值表省一半高度，而且「这几个是同一类东西」
    的关系一眼就看出来了（竖排要读五个键才知道）。

    分隔线靠**容器底色透出来**：容器涂成描边色，格子涂成卡片色，中间
    留 1px 缝——比给每个格子加边框省事，也不会出现相邻两格边框叠成
    2px 的那种粗线。
    """

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, background=theme.LINE)
        self._cells: list[tk.Frame] = []

    def set_rows(self, rows: "Iterable[tuple[str, str]]") -> None:
        """重建网格。

        Args:
            rows: ``(小标签, 值)`` 序列。值拿不到时传 ``"—"``，
                别留空字符串——空格子会让整排看起来像没加载完。
        """
        for child in self.winfo_children():
            child.destroy()
        self._cells.clear()

        pairs = list(rows)
        for index, (label, value) in enumerate(pairs):
            # 偶数列是格子，奇数列是 1px 分隔线；首尾各留一条当外框
            if index == 0:
                self._separator(column=0)
            cell = tk.Frame(self, background=theme.PAPER)
            cell.grid(row=0, column=index * 2 + 1, sticky="nsew", pady=1)
            self.columnconfigure(index * 2 + 1, weight=1)
            tk.Label(
                cell, text=label, font=theme.font("caption"),
                background=theme.PAPER, foreground=theme.INK_MUTED, anchor="w",
            ).pack(anchor="w", padx=theme.PAD_MD, pady=(theme.PAD_SM, 0))
            tk.Label(
                cell, text=value, font=theme.font("body-strong"),
                background=theme.PAPER, foreground=theme.INK, anchor="w",
            ).pack(anchor="w", padx=theme.PAD_MD, pady=(0, theme.PAD_SM))
            self._cells.append(cell)
            self._separator(column=index * 2 + 2)

    def _separator(self, column: int) -> None:
        line = tk.Frame(self, background=theme.LINE, width=1)
        line.grid(row=0, column=column, sticky="ns")

    def values(self) -> list[tuple[str, str]]:
        """读回当前内容，``(标签, 值)`` 列表——给测试用。"""
        result = []
        for cell in self._cells:
            labels = cell.winfo_children()
            if len(labels) == 2:
                result.append(
                    (str(labels[0].cget("text")), str(labels[1].cget("text")))
                )
        return result


class TagChipField(ttk.Frame):
    """标签输入：已选标签是能点掉的 chips，末尾跟着一个无边框输入框。

    设计稿的标签区是三个可删除的 chips + 一个「输入后回车添加」的输入
    框，下面再挂一排虚线的常用标签。相比之前「逗号分隔的一行文本」：

    - **标签是**一个一个的对象，不是一串字符——点 ``×`` 就能删掉一个，
      不用在文本框里做字符串手术；
    - 已选数量、上限直接显示出来，不用自己数逗号；
    - 常用标签点一下就加上，省一次输入。

    输入框**无边框**（融进标签盒）：它和 chips 共处一个带边框的容器里，
    容器已经是那个「框」了，再给输入框加一个框就成了框套框。
    """

    #: 单个 chip 的高度
    CHIP_H = 24

    def __init__(
        self,
        master: tk.Misc,
        max_tags: int = 10,
        on_change: Callable[[list[str]], None] | None = None,
    ) -> None:
        super().__init__(master, style="Card.TFrame")
        self.columnconfigure(0, weight=1)
        self._max = max(1, int(max_tags))
        self._on_change = on_change
        self._tags: list[str] = []

        # 标签盒：1px 描边，chips 和输入框都装在里面
        self._box = tk.Frame(
            self,
            background=theme.PAPER,
            highlightthickness=1,
            highlightbackground=theme.LINE_STRONG,
        )
        self._box.grid(row=0, column=0, sticky="ew")
        self._box.columnconfigure(0, weight=1)

        self._chips = tk.Frame(self._box, background=theme.PAPER)
        self._chips.grid(row=0, column=0, sticky="w")

        self._entry = tk.Entry(
            self._box,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            background=theme.PAPER,
            foreground=theme.INK,
            insertbackground=theme.INK,
            font=theme.font("body"),
        )
        self._entry.grid(row=0, column=1, sticky="ew", padx=(theme.PAD_XS, 0))
        self._entry.bind("<Return>", self._commit)
        self._entry.bind("<BackSpace>", self._maybe_pop)

        self._quick = ttk.Frame(self, style="Card.TFrame")
        self._quick.grid(row=1, column=0, sticky="w", pady=(theme.PAD_XS, 0))

    # ---------- 对外 ----------

    @property
    def tags(self) -> list[str]:
        """当前已选标签。"""
        return list(self._tags)

    @property
    def text(self) -> str:
        """逗号分隔的标签串——提交时用的格式（和 B 站接口一致）。"""
        return ",".join(self._tags)

    def set_tags(self, tags: "Iterable[str]") -> None:
        """整体替换已选标签。返回时不会触发 ``on_change``。"""
        self._tags = []
        for raw in tags:
            self._add_tag(raw, notify=False)
        self._render()

    def add(self, text: str) -> bool:
        """加一个标签，返回是否真的加进去了。

        被拒的三种情况：空、重复、超上限。**去重要悄悄做**——用户手滑
        回车了两次，不该弹一个「标签重复」的错误框。
        """
        added = self._add_tag(text, notify=True)
        if added:
            self._render()
        return added

    def set_quick(self, tags: "Iterable[str]") -> None:
        """挂一排常用标签，点一下就加。

        用虚线描边的弱化 chip 表示「这是建议，不是已选」——和已选的
        实色 chip 在形状上就要能分开。
        """
        for child in self._quick.winfo_children():
            child.destroy()
        if not tags:
            self._quick.grid_remove()
            return
        self._quick.grid()
        tk.Label(
            self._quick, text="常用：", font=theme.font("caption"),
            background=theme.PAPER, foreground=theme.INK_MUTED,
        ).pack(side="left", padx=(0, theme.PAD_XS))
        for tag in tags:
            chip = tk.Label(
                self._quick, text=tag, font=theme.font("caption"),
                background=theme.PAPER, foreground=theme.INK_MUTED,
                padx=theme.PAD_SM, pady=1, cursor="hand2",
                highlightthickness=1, highlightbackground=theme.LINE_STRONG,
            )
            chip.pack(side="left", padx=(0, theme.PAD_XS))
            chip.bind("<Button-1>", lambda _e, value=tag: self._pick_quick(value))

    def set_disabled(self, disabled: bool) -> None:
        state = "disabled" if disabled else "normal"
        self._entry.configure(state=state)

    # ---------- 内部 ----------

    def _pick_quick(self, value: str) -> None:
        self.add(value)

    def _commit(self, _event: object = None) -> str:
        raw = self._entry.get().strip()
        self._entry.delete(0, "end")
        if raw:
            # 允许一次输入多个：「校园, 日常」和逐个回车一样
            for piece in raw.replace("，", ",").split(","):
                self._add_tag(piece, notify=False)
            self._render()
            self._notify()
        return "break"

    def _maybe_pop(self, _event: object = None) -> None:
        """输入框空着时按退格，删掉最后一个标签。

        这是「chips 输入框」的通用约定：光标在最前面再按退格，用户想
        的是「删掉上一个」，不是「什么都没发生」。
        """
        if self._entry.get() or not self._tags:
            return
        self._tags.pop()
        self._render()
        self._notify()

    def _add_tag(self, raw: str, notify: bool) -> bool:
        text = (raw or "").strip()
        if not text or text in self._tags or len(self._tags) >= self._max:
            return False
        self._tags.append(text)
        if notify:
            self._notify()
        return True

    def _remove(self, tag: str) -> None:
        if tag in self._tags:
            self._tags.remove(tag)
            self._render()
            self._notify()

    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change(self.tags)

    def _render(self) -> None:
        for child in self._chips.winfo_children():
            child.destroy()
        for tag in self._tags:
            chip = tk.Frame(self._chips, background=theme.PINK_TINT)
            chip.pack(side="left", padx=(0, theme.PAD_XS), pady=theme.PAD_XS)
            tk.Label(
                chip, text=tag, font=theme.font("caption"),
                background=theme.PINK_TINT, foreground=theme.PINK_DEEP,
            ).pack(side="left", padx=(theme.PAD_XS, 0))
            closer = tk.Label(
                chip, text="×", font=theme.font("caption"),
                background=theme.PINK_TINT, foreground=theme.PINK_DEEP,
                cursor="hand2",
            )
            closer.pack(side="left", padx=(theme.PAD_XS, theme.PAD_XS))
            closer.bind("<Button-1>", lambda _e, value=tag: self._remove(value))
        # 空了就把输入框挪到最左边，否则它会孤零零挂在中间
        self._chips.grid_configure(padx=0)


class SegmentedControl(tk.Canvas):
    """分段控件：一组互斥的选项横着排，选中的那一段反白浮起。

    设计稿用它替代「自制 / 转载」的下拉框。为什么值得换：这两个值是
    **互斥且穷尽**的，总共就两个——下拉框要点开、要看、要选三步，
    分段控件一眼看到全部选项、一步点完。

    自绘的理由和 :class:`MaterialButton` 一样：clam 画不出圆角，而
    「槽 + 浮起的选中段」这个形态全靠圆角和底色差。

    键盘可用：左右方向键在选项间移动，和 radiobutton 的预期一致。
    """

    #: 槽的内边距（设计稿 ``padding: 3px``）
    _PAD = 3
    #: 两段之间的缝
    _GAP = 2

    def __init__(
        self,
        master: tk.Misc,
        options: "Sequence[str]",
        on_change: Callable[[str], None] | None = None,
    ) -> None:
        self._choices = [str(option) for option in options] or [""]
        self._index = 0
        self._on_change = on_change
        self._hovered: int | None = None

        measure = theme.measure_font("body")
        # 每段宽度 = 文字宽 + 两侧各 16px，够放下四个汉字
        seg = max(int(measure.measure(text)) + 32 for text in self._choices)
        width = seg * len(self._choices) + self._GAP * (len(self._choices) - 1) + 6
        height = theme.CONTROL_HEIGHT

        super().__init__(
            master,
            width=width, height=height,
            highlightthickness=0, borderwidth=0,
            background=theme.PAPER,
            takefocus=1,
            cursor="hand2",
        )
        self._bind_events()
        self._paint()

    # ---------- 对外 ----------

    @property
    def value(self) -> str:
        return self._choices[self._index]

    @property
    def index(self) -> int:
        return self._index

    def set(self, value: str) -> bool:
        """按文字选中，认不出就不动（返回 ``False``）。

        不抛异常是因为调用方常拿它回显外部数据（配置里的 ``copyright``），
        认不出时**保持现状**比崩掉好——界面还在，用户能自己改。
        """
        if value in self._choices:
            self._index = self._choices.index(value)
            self._paint()
            return True
        return False

    def set_disabled(self, disabled: bool) -> None:
        self._disabled = bool(disabled)
        self._paint()

    # ---------- 内部 ----------

    def _bind_events(self) -> None:
        self.bind("<Button-1>", self._on_click)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _e: self._set_hovered(None))
        self.bind("<Left>", self._on_key)
        self.bind("<Right>", self._on_key)

    def _set_hovered(self, index: "int | None") -> None:
        if self._hovered != index:
            self._hovered = index
            self._paint()

    def _on_motion(self, event: tk.Event) -> None:
        self._set_hovered(self._hit(event.x))

    def _on_click(self, event: tk.Event) -> None:
        index = self._hit(event.x)
        if index is None or index == self._index:
            return
        self._index = index
        self._paint()
        if self._on_change is not None:
            self._on_change(self.value)

    def _on_key(self, event: tk.Event) -> str:
        step = -1 if event.keysym == "Left" else 1
        index = (self._index + step) % len(self._choices)
        if index != self._index:
            self._index = index
            self._paint()
            if self._on_change is not None:
                self._on_change(self.value)
        return "break"

    def _hit(self, x: float) -> "int | None":
        """横坐标 → 落在第几段（落在缝里返回 ``None``）。"""
        seg = self._segment_geometry()
        for index, (left, right) in enumerate(seg):
            if left <= x <= right:
                return index
        return None

    def _segment_geometry(self) -> list[tuple[float, float]]:
        count = len(self._choices)
        width = int(float(self["width"]))
        inner = width - 2 * self._PAD - self._GAP * (count - 1)
        seg = inner / count
        return [
            (
                self._PAD + i * (seg + self._GAP),
                self._PAD + i * (seg + self._GAP) + seg,
            )
            for i in range(count)
        ]

    def _paint(self) -> None:
        width = int(float(self["width"]))
        height = int(float(self["height"]))
        self.delete("all")
        disabled = getattr(self, "_disabled", False)

        self.create_polygon(
            _round_rect_points(0, 0, width - 1, height - 1, theme.RADIUS_CONTROL),
            smooth=True,
            fill=theme.FILL,
            outline=theme.FILL,
        )
        for index, (left, right) in enumerate(self._segment_geometry()):
            if index == self._index:
                # 选中的一段反白浮起：设计稿给它一层浅阴影，tkinter 画不出，
                # 用「比槽更亮的底 + 描边」表达同样的层次
                self.create_polygon(
                    _round_rect_points(
                        left, self._PAD, right - 1, height - 1 - self._PAD,
                        theme.RADIUS_CONTROL,
                    ),
                    smooth=True,
                    fill=theme.PAPER if not disabled else theme.DISABLED_BG,
                    outline=theme.LINE,
                )
            self.create_text(
                (left + right) / 2, height / 2,
                text=self._choices[index],
                fill=(
                    theme.DISABLED_FG if disabled
                    else theme.INK if index == self._index
                    else theme.INK_MUTED
                ),
                font=theme.font("body-strong" if index == self._index else "body"),
            )


class StatusBar(tk.Frame):
    """顶部状态条：左边一串状态，右边一句补充。

    设计稿把它放在标题栏下方、侧栏和内容区**上方**，通栏。位置是这一版
    的关键改动：状态（登录态 / ffmpeg）从底部挪到了顶部——它们回答的是
    「这台机器现在能不能干活」，属于**开工前的检查**，该在眼睛第一落点
    就看到，而不是干完活低头才发现。

    每一项都是「形状记号 + 文字」双通道（记号见
    :data:`~.theme.TONE_GLYPHS`），不靠颜色单独传达——色盲用户看不出
    「绿点 = 正常」。
    """

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(
            master, background=theme.PAPER, height=theme.STATUS_BAR_H
        )
        # 高度是设计稿定死的 30px，别让内容把它顶高
        self.grid_propagate(False)
        self.columnconfigure(1, weight=1)

        self._groups = tk.Frame(self, background=theme.PAPER)
        self._groups.grid(row=0, column=0, sticky="w", padx=(theme.PAD_XL, 0))

        self._note = tk.Label(
            self, text="", font=theme.font("caption"),
            background=theme.PAPER, foreground=theme.INK_MUTED, anchor="e",
        )
        self._note.grid(row=0, column=2, sticky="e", padx=(0, theme.PAD_XL))

        # 底边 1px：状态条和下方内容区要分开，否则它只是「一条灰」
        self._rule = tk.Frame(self, background=theme.LINE, height=1)
        self._rule.grid(row=1, column=0, columnspan=3, sticky="sew")

        self._items: list[tk.Label] = []

    def set_groups(self, groups: "Iterable[tuple[str, str]]") -> None:
        """更新左侧状态组，每项 ``(语义色名, 文案)``。

        语义色名见 :data:`~.theme.TONES`；``busy`` 用来表示「正在检测」。
        """
        for child in self._groups.winfo_children():
            child.destroy()
        self._items.clear()

        for tone, text in groups:
            fg, _bg = _tone_colors(tone)
            label = tk.Label(
                self._groups,
                text=f"{theme.tone_glyph(tone)} {text}",
                font=theme.font("caption"),
                background=theme.PAPER, foreground=fg, anchor="w",
            )
            label.pack(side="left", padx=(0, theme.PAD_LG))
            self._items.append(label)

    def set_note(self, text: str) -> None:
        """更新右侧那句补充（「上次同步 2 分钟前」这类）。"""
        self._note.configure(text=text)

    def texts(self) -> list[str]:
        """读回左侧各项文字——给测试用（探测是异步的，断言要读控件）。"""
        return [str(item.cget("text")) for item in self._items]
