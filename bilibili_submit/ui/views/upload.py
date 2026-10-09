"""投稿视图：选文件、填元数据、看进度。

这是 GUI 的主功能页。表单字段与 CLI 的 ``upload`` 命令一一对应，
提交时构造 :class:`~bilibili_submit.config.TaskConfig` 走同一条
``run_task`` 链路——GUI 不自己实现投稿，避免两套逻辑走偏。

2026-10 按设计稿重排：页面拆成**两张编号卡片**——①视频源、②投稿信息。
编号不是装饰，它把「先选文件、再填信息」的顺序直接画了出来；五格元
信息条让用户选完文件立刻确认「选对了吗」，不用再打开资源管理器核对。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk
from pathlib import Path
from typing import TYPE_CHECKING

from ...config import AppConfig, TaskConfig
from ...exceptions import BiliError, NotLoggedInError
from ...metadata import COMMON_TIDS
from ...scheduler import RunOptions, run_task
from ...submit import get_backend
from .. import theme
from ..widgets import (
    ActionBar,
    ExpansionPanel,
    MetaGrid,
    OptionSwitches,
    LogConsole,
    PageHeader,
    ProgressBar,
    ScrollArea,
    SegmentedControl,
    StepCard,
    OutlinedButton,
    TagChipField,
)
from ..workers import Cancelled, Worker

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查
    from ._host import AppHost

__all__ = [
    "UploadView",
    "parse_tid",
    "TID_OPTIONS",
    "parse_copyright",
    "copyright_option",
    "COPYRIGHT_OPTIONS",
]

#: 标签上限，B 站硬性限制
MAX_TAGS = 10
#: 标题字数上限，B 站硬性限制
MAX_TITLE = 80
#: 简介字数上限
MAX_DESC = 2000
#: 建议标签数下限。低于它预检行会提示——标签太少影响推荐，但不算错误
SUGGESTED_TAGS = 3

#: 分区下拉的选项，格式 ``"21 - 日常"``。投稿页和批量任务页共用同一份，
#: 免得两个页面的分区列表哪天不一样，用户要重新适应。
TID_OPTIONS = [f"{tid} - {name}" for tid, name in sorted(COMMON_TIDS.items())]

#: 投稿类型的下拉选项。中文给用户看，值是 B 站 ``copyright`` 字段
#: （1=自制 2=转载）——两页共用这一份，术语不会各写各的。
COPYRIGHT_OPTIONS = ["自制", "转载"]

#: ``copyright`` 的默认值。自制不要求填来源，作为回落方向不会让投稿
#: 直接失败；反之若认不出就当转载，用户会撞上「缺 source」被服务端
#: 打回（21004），却看不出是自己没选还是程序弄错了。
COPYRIGHT_SELF_MADE = 1

#: 常用标签快捷项。给的是 B 站覆盖面最广的泛用标签——这里的目的是
#: 「点一下就补够推荐数」，具体选题标签该由用户自己想。
QUICK_TAGS = ["日常", "生活", "原创"]


def parse_tid(text: str, default: int = 21) -> int:
    """从「21 - 日常」这样的下拉项里取出分区号。"""
    head = (text or "").split("-")[0].strip()
    try:
        return int(head)
    except (ValueError, IndexError):
        return default


def parse_copyright(text: str) -> int:
    """界面文案 → ``copyright`` 值：「转载」是 2，其余一律自制（1）。"""
    return 2 if (text or "").strip() == COPYRIGHT_OPTIONS[1] else COPYRIGHT_SELF_MADE


def copyright_option(value: int) -> str:
    """``copyright`` 值 → 下拉文案。只有 2 是转载，其余都按自制显示。"""
    return COPYRIGHT_OPTIONS[1] if int(value) == 2 else COPYRIGHT_OPTIONS[0]


class _ThumbPlaceholder(tk.Canvas):
    """视频缩略图占位：粉渐变 + 播放钮 + 右下角时长。

    真正的视频封面要等平台转码后才有，投稿阶段谁都画不出缩略图——
    设计稿在这里放的是一张粉渐变占位图，本组件照做。渐变用 40 条
    竖条插值模拟（tkinter 的 Canvas 没有渐变 API）。

    时长角标的内容随元信息探测更新（``set_duration``）；没探测到就
    保持 ``—``，不要空着——空角标看起来像渲染漏了一块。
    """

    #: 渐变条数。40 条在 200px 宽度上肉眼已是连续过渡，再多只是白算
    _STEPS = 40

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(
            master,
            width=theme.THUMB_W, height=theme.THUMB_H,
            highlightthickness=0, borderwidth=0,
        )
        self._paint()

    def _paint(self) -> None:
        w, h = theme.THUMB_W, theme.THUMB_H
        # 三段渐变（0%~40%~100%），逐条线性插值
        stops = (theme.THUMB_FROM, theme.THUMB_MID, theme.THUMB_TO)
        for i in range(self._STEPS):
            t = i / (self._STEPS - 1)
            if t < 0.4:
                color = _mix(stops[0], stops[1], t / 0.4)
            else:
                color = _mix(stops[1], stops[2], (t - 0.4) / 0.6)
            x0 = int(w * i / self._STEPS)
            x1 = int(w * (i + 1) / self._STEPS)
            self.create_rectangle(x0, 0, x1 + 1, h, outline="", fill=color)

        # 播放钮：墨色圆 + 白三角。tkinter 画不出半透明，用实色近似；
        # 设计稿里它是 rgba(0,0,0,.32)，在粉底上观感就是「深一档」。
        cx, cy, r = w / 2, h / 2, 19
        self.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill=theme.INK, outline="", width=0,
        )
        # 三角形往右偏 2px 才视觉居中（几何中心≠视觉中心）
        s = 12
        self.create_polygon(
            cx - s / 2 + 2, cy - s / 2,
            cx - s / 2 + 2, cy + s / 2,
            cx + s / 2 + 2, cy,
            fill=theme.FIELD_BG, outline="", width=0,
        )

        # 角标底：tkinter 文字没有背景色，先垫一个墨色矩形——
        # 后创建的图元盖住先创建的，所以矩形必须在文字**之前**
        self.create_rectangle(
            w - 62, h - 26, w, h,
            fill=theme.INK, outline="", width=0,
        )
        self._dur = self.create_text(
            w - 8, h - 8, text="—", anchor="se",
            font=theme.font("caption"), fill=theme.FIELD_BG,
        )

    def set_duration(self, text: str) -> None:
        """更新右下角时长角标。"""
        self.itemconfigure(self._dur, text=text)


def _mix(a: str, b: str, t: float) -> str:
    """两个 ``#RRGGBB`` 之间线性插值，``t=0`` 返回 a、``t=1`` 返回 b。"""
    ar, ag, ab = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
    br, bg, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
    return "#{:02X}{:02X}{:02X}".format(
        round(ar + (br - ar) * t),
        round(ag + (bg - ag) * t),
        round(ab + (bb - ab) * t),
    )


class _FieldBody(ttk.Frame):
    """字段格的控件槽：``add`` 语义与 :meth:`FormRow.add` 一致。

    控件必须以本槽为父容器创建——Tk 的 ``grid()`` 始终作用于
    ``widget.master``，父容器拿错了控件会被排到格子的外层去叠在一起。
    """

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master, style="Card.TFrame")
        # 唯一列吃满宽度：窗口拉宽时输入框跟着变宽，而不是在槽里
        # 缩成一小截、右侧留一块裸底色
        self.columnconfigure(0, weight=1)

    def add(
        self,
        widget: "type[tk.Misc] | tk.Misc",
        *,
        column: int = 0,
        sticky: str = "ew",
        padx: "int | tuple[int, int]" = 0,
        **options: object,
    ) -> tk.Misc:
        created = widget(self, **options) if isinstance(widget, type) else widget
        created.grid(row=0, column=column, sticky=sticky, padx=padx)
        return created


class UploadView(ttk.Frame):
    """单文件投稿页。"""

    def __init__(self, master: tk.Misc, app: "AppHost") -> None:
        super().__init__(master, style="TFrame")
        self.app = app
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._worker = Worker(self)
        self._build()

    # ---------- 布局 ----------

    def _build(self) -> None:
        PageHeader(
            self, "投稿", "填写信息后可直接投稿，或加入批量队列定时发布"
        ).grid(row=0, column=0, sticky="ew", pady=(0, theme.PAD_LG))

        # 窗口压到最小尺寸时表单装不下，用滚动区兜住，
        # 否则日志区会被挤出可视范围。
        area = ScrollArea(self)
        area.grid(row=1, column=0, sticky="nsew")
        body = area.body
        body.columnconfigure(0, weight=1)

        self._meta_generation = 0
        self._build_source_card(body)
        self._build_info_card(body)

        self._progress = ProgressBar(body)
        self._progress.grid(row=2, column=0, sticky="ew", pady=(theme.PAD_SM, 0))

        # 滚动容器里不能用 weight=1 让日志区「吃掉剩余空间」——
        # 父容器高度就是内容高度，权重不会带来额外空间，反而会压缩内容。
        # 固定高度 + 内容超出时日志自己滚。
        self._log = LogConsole(body, height=theme.LOG_HEIGHT)
        self._log.grid(row=3, column=0, sticky="ew", pady=(theme.PAD_SM, 0))

        # 操作条**在滚动区之外**，钉在页面底部。这是本次流程优化的核心：
        # 「开始投稿」不再混在表单中间——表单一长它就被推出屏幕，
        # 折叠区一开它就跳位置。用户不必每次都扫一遍界面找按钮。
        self._action_bar = ActionBar(self)
        self._action_bar.grid(row=2, column=0, sticky="ew", pady=(theme.PAD_MD, 0))
        self._action_bar.set_primary(
            "开始投稿", self._submit,
            secondary_text="仅预览", secondary_command=self._dry_run,
        )
        self._preview_button = self._action_bar.secondary

        # 表单一改就刷新摘要与预检：用户边填边看到「将要发生什么」，
        # 不需要提交前再回头核对一遍。
        for variable in (
            self._file_var, self._title_var, self._tid_var,
            self._copyright_var, self._dtime_var,
        ):
            variable.trace_add("write", lambda *_: self._refresh_action_bar())
        self._refresh_action_bar()

    # ---------- 卡片一：视频源 ----------

    def _build_source_card(self, body: tk.Misc) -> None:
        """① 视频源：缩略图 + 路径行 + 五格元信息。

        缩略图占位为什么值得画：它把「这是一个视频」的语义直接给了出来
        （播放钮人人认识），比一行灰路径好认得多——用户扫一眼就知道
        这张卡片在等什么。
        """
        card = StepCard(
            body, step=1, title="视频源",
            hint="支持 mp4 / flv / mkv，单文件最大 8 GB",
        )
        card.grid(row=0, column=0, sticky="ew", pady=(0, theme.PAD_MD))

        src = tk.Frame(card.body, background=theme.PAPER)
        src.pack(fill="x")
        src.columnconfigure(1, weight=1)

        self._thumb = _ThumbPlaceholder(src)
        self._thumb.grid(row=0, column=0, padx=(0, theme.PAD_MD), sticky="nw")

        info = tk.Frame(src, background=theme.PAPER)
        info.grid(row=0, column=1, sticky="ew")
        info.columnconfigure(0, weight=1)

        # 路径行：只读展示框（路径只能来自文件对话框，手改框里的文字
        # 既无意义也改不了任何状态，所以不做成输入框）+ 选择按钮
        self._file_var = tk.StringVar()
        pathbox = tk.Frame(
            info, background=theme.LINE, height=theme.CONTROL_HEIGHT
        )
        pathbox.grid(row=0, column=0, sticky="ew", pady=(theme.PAD_XS, 0))
        pathbox.grid_propagate(False)
        pathbox.columnconfigure(1, weight=1)
        tk.Label(
            pathbox, text="视频文件", font=theme.font("caption"),
            background=theme.FILL, foreground=theme.INK_MUTED,
        ).grid(row=0, column=0, sticky="nsew", padx=(theme.PAD_SM, 0), pady=(1, 1))
        tk.Label(
            pathbox, textvariable=self._file_var, anchor="w",
            font=theme.font("subtitle"), background=theme.FILL,
            foreground=theme.INK_SECOND,
        ).grid(row=0, column=1, sticky="ew", padx=(theme.PAD_SM, theme.PAD_SM), pady=(1, 1))

        OutlinedButton(info, "选择文件…", self._pick_file).grid(
            row=1, column=0, sticky="w", pady=(theme.PAD_SM, 0)
        )

        # 五格元信息：选完文件异步探测填充，探测前全部显示占位符
        self._meta = MetaGrid(info)
        self._meta.grid(row=2, column=0, sticky="ew", pady=(theme.PAD_MD, 0))
        self._meta.set_rows([
            ("分辨率", "—"), ("时长", "—"), ("大小", "—"),
            ("编码", "—"), ("码率", "—"),
        ])

    # ---------- 卡片二：投稿信息 ----------

    def _build_info_card(self, body: tk.Misc) -> None:
        """② 投稿信息：标题 / 三列参数 / 标签 / 简介。"""
        card = StepCard(
            body, step=2, title="投稿信息", hint="标题与标签直接影响推荐量"
        )
        card.grid(row=1, column=0, sticky="ew")
        form = card.body

        # 标题：小标题 + 必填星 + 右侧字数计数
        self._title_var = tk.StringVar()
        head, self._title_count = self._field_head(form, "标题", required=True)
        self._title_count.configure(text=f"0 / {MAX_TITLE}")
        head.pack(fill="x", pady=(0, theme.PAD_XS))
        ttk.Entry(form, textvariable=self._title_var).pack(
            fill="x", pady=(0, theme.PAD_MD)
        )
        self._title_var.trace_add("write", lambda *_: self._sync_title_count())

        # 三列：分区 / 类型 / 延时发布
        row3 = tk.Frame(form, background=theme.PAPER)
        row3.pack(fill="x", pady=(0, theme.PAD_MD))
        for col in range(3):
            row3.columnconfigure(col, weight=1, uniform="params")

        self._tid_var = tk.StringVar(value="21 - 日常")
        tid_cell = self._field(row3, 0, "分区", required=True)
        tid_cell.body.add(
            ttk.Combobox, textvariable=self._tid_var,
            values=TID_OPTIONS, state="readonly",
        )

        # 类型是两个互斥且穷尽的选项，分段控件比下拉少两次点击。
        # StringVar 仍是**状态源**（测试和 _collect 都读它），
        # 分段控件只是它的另一种画法，两边互相同步。
        self._copyright_var = tk.StringVar(
            value=copyright_option(COPYRIGHT_SELF_MADE)
        )
        seg_cell = self._field(row3, 1, "类型")
        self._copyright_seg = SegmentedControl(
            seg_cell.body, options=COPYRIGHT_OPTIONS,
            on_change=self._on_copyright_changed,
        )
        self._copyright_seg.pack(fill="x")
        self._copyright_var.trace_add("write", lambda *_: self._sync_copyright_seg())

        self._dtime_var = tk.StringVar()
        dtime_cell = self._field(row3, 2, "延时发布")
        dtime_cell.body.add(ttk.Entry, textvariable=self._dtime_var)
        tk.Label(
            dtime_cell.body, text="小时后发布，需大于 4；留空为立即发布",
            font=theme.font("caption"), background=theme.PAPER,
            foreground=theme.INK_MUTED, anchor="w", wraplength=200,
            justify="left",
        ).grid(row=1, column=0, sticky="ew", pady=(theme.PAD_XS, 0))

        # 转载来源跟着类型显隐：自制状态下摆一个灰着的「转载来源」
        # 只会让人困惑。
        self._source_var = tk.StringVar()
        self._source_row = self._make_field(form, "转载来源", required=True)
        # 行本身走 pack，显隐就得用 pack_forget——grid_remove 对
        # pack 管的窗口是**静默无效**的，来源行会一直挂在页面上。
        # pack_forget 会丢参数，所以把参数存下来，显示时原样还回去。
        self._source_pack_kw = {"fill": "x", "pady": (0, theme.PAD_MD)}
        self._source_row.pack(**self._source_pack_kw)
        self._source_row.body.add(ttk.Entry, textvariable=self._source_var)
        # 建完立刻同步一次：默认自制，来源行应当是收起的
        self._sync_source_row()

        # 标签：chips + 输入框 + 常用标签
        tag_head, self._tag_count = self._field_head(form, "标签")
        self._tag_count.configure(text="已选 0 个")
        tag_head.pack(fill="x", pady=(0, theme.PAD_XS))
        self._tag_field = TagChipField(
            form, max_tags=MAX_TAGS, on_change=self._on_tags_changed,
        )
        self._tag_field.pack(fill="x", pady=(0, theme.PAD_XS))
        self._tag_field.set_quick(QUICK_TAGS)

        # 简介：多行文本（设计稿 92px textarea）。tk.Text 没有
        # textvariable，用 KeyRelease 同步——中文输入法在组合期间
        # 不触发 KeyRelease，松键（选字）那一下会触发，够用。
        desc_head, self._desc_count = self._field_head(form, "简介")
        self._desc_count.configure(text=f"0 / {MAX_DESC}")
        desc_head.pack(fill="x", pady=(0, theme.PAD_XS))
        desc_box = tk.Frame(form, background=theme.LINE)
        desc_box.pack(fill="x", pady=(0, theme.PAD_MD))
        self._desc_text = tk.Text(
            desc_box, height=3, relief="flat", wrap="word",
            font=theme.font("body"), background=theme.FIELD_BG,
            foreground=theme.INK, padx=theme.PAD_SM, pady=theme.PAD_SM,
            insertbackground=theme.INK,
        )
        self._desc_text.pack(fill="x", padx=(1, 1), pady=(1, 1))
        self._desc_text.bind("<KeyRelease>", lambda _e: self._sync_desc_count())

        # 更多设置：与批量任务页共用同一组开关，术语和默认值都一致，
        # 免得用户在一个页里设过、換个页又要重新找一遍。
        self._more = ExpansionPanel(
            form, "更多设置", "互动设置、音质增强", opened=False,
            on_toggle=lambda _opened: self._update_more_hint(),
        )
        self._more.pack(fill="x", pady=(theme.PAD_SM, 0))
        self._option_switches = OptionSwitches(
            self._more.body, on_change=self._update_more_hint
        )
        self._option_switches.grid(row=0, column=0, sticky="ew")

    # ---------- 字段格 ----------

    @staticmethod
    def _field_head(
        parent: tk.Misc, label: str, required: bool = False
    ) -> tuple[tk.Frame, tk.Label]:
        """字段的小标题行：标签 + 可选必填星 + 右侧计数。

        返回 ``(标题行, 计数 Label)``。计数由这里创建是因为 **pack 不能
        跨父容器**——外部造好的 Label 以 ``parent`` 为父，pack 进标题行
        只会「显示成标题行的兄弟」而不是「贴在标题行右端」。
        """
        head = tk.Frame(parent, background=theme.PAPER)
        tk.Label(
            head, text=label, font=theme.font("subtitle"),
            background=theme.PAPER, foreground=theme.INK_SECOND,
        ).pack(side="left")
        if required:
            tk.Label(
                head, text="*", font=theme.font("subtitle"),
                background=theme.PAPER, foreground=theme.PINK_DEEP,
            ).pack(side="left", padx=(2, 0))
        counter = tk.Label(
            head, font=theme.font("caption"),
            background=theme.PAPER, foreground=theme.INK_MUTED,
        )
        counter.pack(side="right")
        return head, counter

    def _make_field(
        self, parent: tk.Misc, label: str, required: bool = False
    ) -> "ttk.Frame":
        """造一个纵排字段格：小标题在上、控件槽在下。

        返回**外层 cell 本身**（一个真 Frame）：显隐、查询几何都用它，
        ``grid_remove()`` 丢不了配置；控件槽挂在 ``cell.body`` 上，
        ``add`` 的语义与 :meth:`FormRow.add` 相同。
        """
        cell = ttk.Frame(parent, style="Card.TFrame")
        head, _unused = self._field_head(cell, label, required=required)
        head.pack(fill="x", pady=(0, theme.PAD_XS))
        cell.body = _FieldBody(cell)
        cell.body.pack(fill="x")
        return cell

    def _field(self, parent: tk.Misc, column: int, label: str, required: bool = False):
        """三列参数行里的一格（等宽，列间留沟）。"""
        cell = self._make_field(parent, label, required)
        cell.grid(
            row=0, column=column, sticky="ew",
            padx=(0 if column == 0 else theme.PAD_MD, 0),
        )
        return cell

    # ---------- 计数与同步 ----------

    def _sync_title_count(self) -> None:
        self._title_count.configure(
            text=f"{len(self._title_var.get())} / {MAX_TITLE}"
        )

    def _sync_desc_count(self) -> None:
        text = self._desc_text.get("1.0", "end-1c")
        self._desc_count.configure(text=f"{len(text)} / {MAX_DESC}")
        self._refresh_action_bar()

    def _on_tags_changed(self, tags: list) -> None:
        self._tag_count.configure(
            text=f"已选 {len(tags)} / 建议 {SUGGESTED_TAGS}-{MAX_TAGS} 个"
        )
        self._refresh_action_bar()

    def _on_copyright_changed(self, value: str) -> None:
        """分段控件 → StringVar（StringVar 是唯一状态源）。"""
        if self._copyright_var.get() != value:
            self._copyright_var.set(value)

    def _sync_copyright_seg(self) -> None:
        """StringVar → 分段控件。判断过值相等才 set，不会成环。"""
        value = self._copyright_var.get()
        if self._copyright_seg.value != value:
            self._copyright_seg.set(value)

    def _probe_meta(self, path: str) -> None:
        """异步探测视频元信息，填五格与时长角标。

        探测有代次：连续换两个文件时，慢的那次晚回来**不许**覆盖新
        文件的结果——和 :meth:`App.refresh_status` 同一个坑，同一个解法。
        """
        self._meta_generation += 1
        generation = self._meta_generation

        def work(_report, _cancelled):
            from ...ffmpeg import video_meta

            return video_meta(path)

        worker = Worker(self)
        self._meta_worker = worker
        worker.run(
            work,
            on_done=lambda meta: self._apply_meta(generation, meta),
            on_error=lambda _exc: self._apply_meta(generation, None),
        )

    def _apply_meta(self, generation: int, meta) -> None:
        # 过期的探测结果丢弃：它属于上一个文件
        if generation != self._meta_generation or meta is None:
            return
        self._meta.set_rows([
            ("分辨率", meta["resolution"]), ("时长", meta["duration"]),
            ("大小", meta["size"]), ("编码", meta["codec"]),
            ("码率", meta["bitrate"]),
        ])
        self._thumb.set_duration(meta["duration"])

    # ---------- 行为 ----------

    def refresh(self) -> None:
        """切到本页时不需要额外加载，但登录态可能变了，要重算按钮可用性。"""
        self._refresh_action_bar()

    def _describe_pending(self) -> str:
        """一句话说清「点下去会发生什么」。

        提交前用户最想确认的不是「我填对了吗」（那要逐项核对），
        而是「这一下会发出什么」。所以这里给结论而不是清单：
        文件名 + 分区 + 类型 + 是否延时，四个信息点，多了没人读。
        """
        raw = self._file_var.get().strip()
        if not raw:
            return ""
        name = Path(raw).name
        tid = self._tid_var.get().strip() or "21 - 日常"
        kind = self._copyright_var.get().strip() or COPYRIGHT_OPTIONS[0]
        parts = [f"投稿「{name}」", f"分区 {tid}", kind]
        offset = self._dtime_var.get().strip()
        if offset:
            parts.append(f"延时 {offset} 小时发布")
        else:
            parts.append("立即发布")
        return " · ".join(parts)

    def _logged_in(self) -> bool:
        """当前是否已登录。

        对缺 ``ctx`` 的宿主对象返回 True：视图不该假设 app 一定带登录
        上下文（测试里的假 app就没有），把「查不到」当成「已登录」是
        更安全的默认——顶多让投稿在服务端失败，而不是让按钮永远点不动。
        """
        ctx = getattr(self.app, "ctx", None)
        return bool(getattr(ctx, "logged_in", True))

    def _refresh_action_bar(self) -> None:
        """刷新底部摘要与按钮可用性。

        未登录时**就地禁用并说明原因**，而不是让人点下去、再在日志里
        看到「尚未登录，请先到登录页扫码」——那是事后通知，用户已经
        白点了一次，还可能以为自己哪里填错了。

        两个按钮的门槛**不一样**：预览不需要登录（``_run(dry_run=True)``
        拿的是 ``need_login=False`` 的 client），但同样得有文件。所以登录
        状态只挡主按钮，文件缺失两个都挡——一个亮着的「仅预览」在空表单
        状态下点下去只会报错，是最容易误导人的那种亮。
        """
        if self._worker.running:
            return  # 运行中由 _set_busy 接管，别抢状态
        summary = self._describe_pending()
        self._action_bar.set_summary(
            "将要投稿" if summary else "填完上面几项就能投稿", summary or "先选一个视频文件"
        )
        has_file = bool(self._file_var.get().strip())
        preview = self._action_bar.secondary
        if preview is not None:
            preview.state(["!disabled"] if has_file else ["disabled"])
        self._refresh_checks()
        if not self._logged_in():
            self._action_bar.block("尚未登录 · 请先到「登录」页扫码")
        elif not has_file:
            self._action_bar.block("还差一个视频文件")
        else:
            self._action_bar.unblock()

    def _refresh_checks(self) -> None:
        """刷新操作条上的预检行。

        预检回答的是「点了之后顺不顺」：哪些条件已满足、哪些还差着。
        它**不挡按钮**——标签 3 个、简介空着都不该拦着投稿，B 站也收；
        拦人的事（没登录、没文件）由 block 的原因文案负责。两者分工
        不同，混在一起就会变成「什么都不满足却只红一项」。
        """
        raw = self._file_var.get().strip()
        if not raw:
            file_pair = ("dim", "未选择文件")
        elif Path(raw).expanduser().is_file():
            file_pair = ("ok", "文件可读取")
        else:
            file_pair = ("error", "文件不存在")

        title = self._title_var.get().strip()
        if not title:
            title_pair = ("dim", "标题为空（将取文件名）")
        elif len(title) > MAX_TITLE:
            title_pair = ("error", f"标题超 {MAX_TITLE} 字")
        else:
            title_pair = ("ok", "标题合规")

        n_tags = len(self._tag_field.tags)
        if n_tags == 0:
            tag_pair = ("dim", "无标签")
        elif n_tags < SUGGESTED_TAGS:
            tag_pair = ("warn", f"标签仅 {n_tags} 个")
        else:
            tag_pair = ("ok", f"标签 {n_tags} 个")

        desc = self._desc_text.get("1.0", "end-1c").strip()
        desc_pair = (
            ("dim", "简介为空") if not desc
            else ("warn" if len(desc) > MAX_DESC else "ok", f"简介 {len(desc)} 字")
        )
        self._action_bar.set_checks([file_pair, title_pair, tag_pair, desc_pair])

    def _update_more_hint(self) -> None:
        """收起时右侧列出已开启的项——不然设了什么全看不见。"""
        self._more.set_hint(self._option_switches.summary())

    def _sync_source_row(self) -> None:
        """「转载来源」只在选了转载时出现。

        用 ``pack_forget`` 收起、带原参数 ``pack`` 恢复——布局参数存在
        :attr:`_source_pack_kw` 里，收起再展开不会跑到别的位置去。
        """
        if parse_copyright(self._copyright_var.get()) == 2:
            self._source_row.pack(**self._source_pack_kw)
        else:
            self._source_row.pack_forget()
        # 切换类型会改变「将发生什么」（自制/转载），但操作条可能还没建
        if hasattr(self, "_action_bar"):
            self._refresh_action_bar()

    def _pick_file(self) -> None:
        path = filedialog.askopenfilename(
            title="选择视频文件",
            filetypes=[
                ("视频文件", "*.mp4 *.flv *.mov *.mkv *.avi *.wmv"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        self._file_var.set(path)
        # 标题空着的话顺手填上文件名，省一次输入
        if not self._title_var.get().strip():
            self._title_var.set(Path(path).stem)
        # 元信息探测：分辨率/时长/大小/编码/码率，让用户当场确认选对了文件
        self._probe_meta(path)

    def _collect(self) -> TaskConfig:
        """从表单收集并校验，返回任务配置。"""
        raw = self._file_var.get().strip()
        if not raw:
            raise BiliError("请先选择视频文件")
        file = Path(raw).expanduser()
        if not file.is_file():
            raise BiliError(f"视频文件不存在: {file}")

        tid = parse_tid(self._tid_var.get(), default=21)
        flags = self._option_switches.flags

        offset_text = self._dtime_var.get().strip()
        offset = None
        if offset_text:
            try:
                offset = float(offset_text)
            except ValueError as exc:
                raise BiliError(f"延时发布小时数不是数字: {offset_text}") from exc

        # 转载缺来源，服务端只回一个干巴巴的 21004，不如当场拦住
        copyright = parse_copyright(self._copyright_var.get())
        source = self._source_var.get().strip()
        if copyright == 2 and not source:
            raise BiliError("选了「转载」就必须填转载来源（原视频链接或出处）")

        title = self._title_var.get().strip()
        if len(title) > MAX_TITLE:
            raise BiliError(f"标题超过 {MAX_TITLE} 字（当前 {len(title)} 字）")
        desc = self._desc_text.get("1.0", "end-1c").strip()
        if len(desc) > MAX_DESC:
            raise BiliError(f"简介超过 {MAX_DESC} 字（当前 {len(desc)} 字）")
        tags = self._tag_field.text

        return TaskConfig(
            name=file.stem,
            type="single",
            file=str(file),
            title=title or None,
            tid=tid,
            tag=tags or None,
            desc=desc or None,
            copyright=copyright,
            source=source or None,
            dtime_offset_hours=offset,
            # flags 用的是界面上的名字（close_reply），配置字段是
            # 接口名（up_close_reply）——这一处翻译别漏
            up_close_reply=flags["close_reply"],
            up_close_danmu=flags["close_danmu"],
            up_selection_reply=flags["selection_reply"],
            dolby=int(flags["dolby"]),
            hires=int(flags["hires"]),
        )

    def _run(self, dry_run: bool) -> None:
        if self._worker.running:
            return
        try:
            task = self._collect()
        except BiliError as exc:
            self._log.append(f"输入有误：{exc}")
            return

        self._set_busy(True)
        self._log.clear()
        self._progress.reset()
        self._progress.start_indeterminate()
        self._progress.set_text("准备中…")

        self._worker.run(
            lambda report, is_cancelled: self._do_upload(task, dry_run, report, is_cancelled),
            on_progress=self._on_progress,
            on_done=self._on_done,
            on_error=self._on_error,
        )

    def _submit(self) -> None:
        # 未登录时按钮已经是禁用态，正常点不到。这里仍然兜一道：
        # 快捷键 Ctrl+Enter 走的是同一条路径，禁用判断在 ActionBar 里，
        # 但登录态可能在按下和执行之间过期。
        if not self._logged_in():
            self._log.append("尚未登录，请先到「登录」页扫码")
            return
        self._run(dry_run=False)

    def _dry_run(self) -> None:
        self._run(dry_run=True)

    def _cancel(self) -> None:
        self._worker.cancel()
        self._log.append("已请求取消…")

    def _do_upload(self, task: TaskConfig, dry_run: bool, report, is_cancelled):
        """投稿主流程（工作线程执行）。"""
        if is_cancelled():
            raise Cancelled()

        cfg = AppConfig()
        cfg.account.cookie_file = self.app.ctx.cookie_file
        cfg.account.proxy = self.app.ctx.proxy

        client = self.app.ctx.client(need_login=not dry_run)
        report(f"{'[预览] ' if dry_run else ''}开始处理：{task.file}")

        outcome = run_task(
            client,
            task,
            cfg,
            backend=get_backend(cfg.submit.backend, cfg.submit.app),
            options=RunOptions(dry_run=dry_run, on_progress=report),
        )
        return outcome

    # ---------- 回调（主线程） ----------

    def _on_progress(self, message: str) -> None:
        self._log.append(message)
        self._progress.set_text(message)

    def _on_done(self, outcome) -> None:
        self._set_busy(False)
        self._progress.stop_indeterminate()
        if outcome.success:
            self._progress.set_value(100)
            self._progress.set_text("完成")
            self._log.append(f"投稿成功：{outcome.bvid}\n{outcome.url}")
        else:
            self._progress.set_text("失败")
            self._log.append(f"投稿失败：{outcome.error}")

    def _on_error(self, exc: BaseException) -> None:
        self._set_busy(False)
        self._progress.stop_indeterminate()
        self._progress.set_text("出错")
        if isinstance(exc, Cancelled):
            self._log.append("已取消")
            return
        if isinstance(exc, NotLoggedInError):
            self._log.append("登录态已失效，请到「登录」页重新扫码")
            return
        self._log.append(f"错误：{exc}")

    def _set_busy(self, busy: bool) -> None:
        """运行态切换。

        运行中主按钮变成「取消」而不是灰掉的「开始投稿」——用户此刻
        唯一想做的就是停下来，禁用按钮不给任何出路。
        """
        if busy:
            self._action_bar.set_busy(True, cancel=self._cancel)
            self._preview_button.state(["disabled"])
        else:
            self._action_bar.set_busy(False)
            self._preview_button.state(["!disabled"])
            self._refresh_action_bar()
