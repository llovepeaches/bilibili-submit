"""批量任务视图：选一个视频文件夹，挑出要投稿的任务并依次执行。

这个页要解决的核心问题是**可控**：

- 配置里可能有几十个任务，但这一轮往往只想跑其中几个
  （比如上次失败的那几个），所以列表可勾选。
- 跑之前就知道哪些文件根本不存在，而不是跑到一半才炸。
- 跑的过程中每一行实时变色，扫一眼就知道成败。
- 失败了能一键只重试失败项，不用手动挑。

**不要求用户写配置文件**：选个视频文件夹，页面顶部填一次分区/
标签/简介，下面自动列出全部任务。下次打开自动恢复上次的目录和参数。
熟悉 yaml 的高级用户仍可从次要入口加载 ``config.yaml``，那份配置里
的逐任务元数据和 ``submit.backend`` 依然生效。

线程纪律：执行在工作线程，UI 更新一律经 :class:`~..workers.Event`
由 ``report`` 送回主线程——**不在工作线程里直接调 Tk，也不读页面
上之后可能被改掉的属性**。要用的东西在启动前做成快照传进去。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from ...config import AppConfig, TaskConfig, expand_tasks, load_config, scan_video_files
from ...config import task_files, task_part_titles
from ...exceptions import BiliError, ConfigError, NotLoggedInError
from ...multipart import PartGroup, group_files, strip_part_marker
from ...scheduler import RunOptions, TaskOutcome, run_task
from ...submit import get_backend
from .. import theme
from ..state import BatchUIState, DEFAULT_TID, load_ui_state, save_ui_state
from ..widgets import (
    Card,
    Collapsible,
    FormRow,
    LogConsole,
    OptionSwitches,
    Placeholder,
    PrimaryButton,
    ProgressBar,
    SecondaryButton,
    SectionTitle,
    StatusPill,
    SummaryBar,
)
from ..workers import Cancelled, Event, Worker
from .upload import (
    COPYRIGHT_OPTIONS,
    COPYRIGHT_SELF_MADE,
    TID_OPTIONS,
    copyright_option,
    parse_copyright,
    parse_tid,
)

__all__ = ["TasksView"]

#: 勾选列 + 原来的六列
#:
#: ``name`` 是任务的标识（扫目录时是文件名/文件夹名），``title`` 是真正
#: 投出去的稿件标题。两者默认一致，但改过之后就不一样了——分开两列正是
#: 为了让"我改过哪些"一眼可见，否则改完跟没改看起来一样。
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

#: 分 P 合并方式。``分P`` 列是数字，单 P 稿件显示一条横线更分明
GROUP_OFF = "不合并"
GROUP_PREFIX = "按文件名前缀分组"
GROUP_FOLDER = "按文件夹分组"
GROUP_OPTIONS = (GROUP_OFF, GROUP_PREFIX, GROUP_FOLDER)

#: 下拉框显示值 → 内部模式名。UI 说人话，配置和 :mod:`.multipart` 说机器话，
#: 两边都不委屈，映射只在这一处维护。
GROUP_MODE_BY_LABEL = {GROUP_PREFIX: "prefix", GROUP_FOLDER: "folder"}
GROUP_LABEL_BY_MODE = {mode: label for label, mode in GROUP_MODE_BY_LABEL.items()}

#: 状态列最多显示多少字。超出的部分存起来，双击看全文
STATUS_MAX = 18

#: 任务来源
#:
#: ``folder`` 是默认路径，不碰 yaml；``yaml`` 是高级兼容路径，
#: 此时顶部统一参数**不可编辑**——配置里的逐任务 tid、title_template、
#: submit.backend 会被统一参数覆盖掉，那些能力就等于没了。
SourceMode = Literal["folder", "yaml"]


def _truncate(text: str, limit: int = STATUS_MAX) -> str:
    """截断长文本，保留尾部信息（错误原因的尾巴通常更有用）。

    不从头截：``601 投稿过于频繁，等待 30 分钟后重试`` 截成
    ``601 投稿过于频繁…`` 比 ``…30 分钟后重试`` 更好懂。
    """
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def render_title_template(template: str, name: str, number: int) -> str:
    """把投稿标题模板里的占位符换成实际值。

    支持 ``{name}``（文件名/文件夹名）与 ``{n}``（勾选顺序，从 1 开始）。

    模板里一个占位符都没写时，每一行会得到**同一个**标题——那通常是
    用户有意填的固定标题（比如给一套视频统一加前缀），所以不去猜、
    也不报错。
    """
    return template.replace("{name}", name).replace("{n}", str(number))


def _column_widths(total: int) -> dict[str, int]:
    """按权重把总宽度分给各列，返回每列宽度。

    抽成纯函数是因为「分得对不对」跟窗口无关，绑死在 Tk 上就只能靠
    建窗口 + 拖尺寸来测，那种测试既慢又脆。
    """
    fixed = sum(_COLUMN_MIN_WIDTHS[c] for c in COLUMNS if not _COLUMN_WEIGHTS[c])
    weights = sum(_COLUMN_WEIGHTS.values())
    usable = max(total - fixed, 0)
    return {
        column: (
            _COLUMN_MIN_WIDTHS[column]
            if not _COLUMN_WEIGHTS[column]
            else max(
                _COLUMN_MIN_WIDTHS[column],
                int(usable * _COLUMN_WEIGHTS[column] / weights),
            )
        )
        for column in COLUMNS
    }


@dataclass(frozen=True)
class SharedSubmitValues:
    """顶部统一填的投稿参数，这一批任务共用。

    刻意做成**不可变 + 产出新对象**（见 :meth:`apply`）而不是就地改
    ``TaskConfig``：执行线程可能正拿着那些对象读，就地改会读到
    改了一半的字段——比如 tag 已经换了、tid 还是旧的。
    """

    tid: int
    #: 投稿类型：1=自制 2=转载（B 站 ``copyright`` 字段）
    copyright: int = COPYRIGHT_SELF_MADE
    #: 转载来源。``copyright=2`` 时必填——缺了会被服务端打回（21004）
    source: str = ""
    tag: str = ""
    desc: str = ""
    dtime_offset_hours: float | None = None
    # 互动设置与音质增强。命名跟界面文案走（close_reply 而不是
    # up_close_reply），只有 :meth:`apply` 那一步翻译成配置字段名。
    close_reply: bool = False
    close_danmu: bool = False
    selection_reply: bool = False
    dolby: bool = False
    hires: bool = False

    def apply(self, task: TaskConfig) -> TaskConfig:
        return replace(
            task,
            tid=self.tid,
            copyright=self.copyright,
            # 自制时不带来源：留着上次的转载地址，哪天切回转载就会把
            # 一个早就不相关的出处投上去。空字符串一律归一成 None
            source=self.source.strip() or None,
            tag=self.tag or None,
            desc=self.desc or None,
            dtime_offset_hours=self.dtime_offset_hours,
            # 顶部是「距今几小时」，逐任务的绝对时间要一并清掉，
            # 否则两套定时来源打架，resolve_dtime 拿到的是旧值
            dtime=None,
            up_close_reply=self.close_reply,
            up_close_danmu=self.close_danmu,
            up_selection_reply=self.selection_reply,
            # 界面上是勾选框（bool），配置里是 0/1（int）
            dolby=int(self.dolby),
            hires=int(self.hires),
        )


class TasksView(ttk.Frame):
    """批量投稿页。"""

    def __init__(self, master: tk.Misc, app: "object") -> None:
        super().__init__(master, style="TFrame")
        self.app = app
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        #: 投稿执行用。跑完才结束，所以独占「取消」按钮的时间最长。
        self._worker = Worker(self)
        #: 读目录/读 yaml 用。目录大了（几千个文件）或 yaml 解析慢时
        #: 主线程会僵住，所以同样挪到工作线程。
        self._load_worker = Worker(self)
        #: 界面偏好读写。纯小文件，但失败不该弹窗打断用户。
        self._state_worker = Worker(self)
        #: 当前正在跑的 worker，取消按钮作用于它
        self._active_worker: Worker[Any] | None = None

        self._tasks: list[TaskConfig] = []
        self._cfg: AppConfig = AppConfig()
        self._source_mode: SourceMode = "folder"
        #: 界面是否处于执行态。由 :meth:`_set_busy` 维护，和
        #: ``_worker.running`` 分工：一个管界面，一个管线程。
        self._busy = False
        #: 界面状态只恢复一次，否则每次切页都重扫目录
        self._restored = False
        #: 偏好文件路径。None 表示用默认位置；测试注入临时路径用，
        #: 否则测试会去动用户真实的 ~/.config 下的文件。
        self._state_file: Path | None = None
        #: iid -> 是否勾选
        self._picked: dict[str, bool] = {}
        #: 文件不存在的 iid
        self._missing: set[str] = set()
        #: iid -> 完整错误原因（状态列放不下）
        self._errors: dict[str, str] = {}
        #: iid -> 语义色名，见 :data:`~.theme.TONES`
        self._tones: dict[str, str] = {}

        self._build()

    # ---------- 布局 ----------

    def _build(self) -> None:
        SectionTitle(
            self,
            "批量任务",
            "选一个视频文件夹，顶部参数填一次，下面就是全部待投稿任务。",
        ).grid(row=0, column=0, sticky="ew", pady=(0, theme.PAD_MD))

        card = Card(self)
        card.grid(row=1, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(6, weight=1)

        # ① 顶部统一投稿参数。默认展开：分区/标签每次都要看一眼。
        self._shared_section = Collapsible(
            card, "投稿设置", "这一批任务共用", opened=True
        )
        self._shared_section.grid(row=0, column=0, sticky="ew")
        shared = self._shared_section.body

        self._tid_var = tk.StringVar(value=tid_option(DEFAULT_TID))
        row = FormRow(
            shared,
            "分区",
            hint="这一批视频统一发到哪个分区",
        )
        row.grid(row=0, column=0, sticky="ew", pady=(0, theme.PAD_SM))
        self._tid_combo = row.add(
            ttk.Combobox,
            textvariable=self._tid_var,
            values=TID_OPTIONS,
            state="readonly",
        )

        self._tag_var = tk.StringVar()
        row = FormRow(shared, "标签", hint="逗号分隔，这一批共用")
        row.grid(row=1, column=0, sticky="ew", pady=(0, theme.PAD_SM))
        self._tag_entry = row.add(ttk.Entry, textvariable=self._tag_var)

        self._desc_var = tk.StringVar()
        row = FormRow(shared, "简介", hint="可留空")
        row.grid(row=2, column=0, sticky="ew", pady=(0, theme.PAD_SM))
        self._desc_entry = row.add(ttk.Entry, textvariable=self._desc_var)

        # 类型（自制/转载）。转载要填来源，所以来源行跟着类型显隐——
        # 一直摆着一个用不上的输入框，只会让人以为自己哪里没填。
        self._copyright_var = tk.StringVar(value=copyright_option(COPYRIGHT_SELF_MADE))
        row = FormRow(shared, "类型", hint="这一批共用；转载必须填来源，否则 B 站拒稿")
        row.grid(row=3, column=0, sticky="ew", pady=(0, theme.PAD_SM))
        self._copyright_combo = row.add(
            ttk.Combobox,
            textvariable=self._copyright_var,
            values=COPYRIGHT_OPTIONS,
            state="readonly",
            width=10,
        )
        self._copyright_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self._on_copyright_changed()
        )

        self._source_var = tk.StringVar()
        self._source_row = FormRow(shared, "转载来源", hint="原视频链接或出处")
        self._source_row.grid(row=4, column=0, sticky="ew", pady=(0, theme.PAD_SM))
        self._source_entry = self._source_row.add(
            ttk.Entry, textvariable=self._source_var
        )
        self._sync_source_row()

        self._dtime_var = tk.StringVar()
        row = FormRow(
            shared,
            "延时发布",
            hint="距今多少小时后统一发布，需大于 4；留空为立即发布",
        )
        row.grid(row=5, column=0, sticky="ew", pady=(0, theme.PAD_SM))
        self._dtime_entry = row.add(ttk.Entry, textvariable=self._dtime_var)

        # ② 更多设置。默认收起：这些都是「想起来才动一次」的开关，
        #    铺开会占掉列表三行的高度。收起时标题栏右侧会列出已开启的项，
        #    否则用户会以为自己设的东西丢了。
        self._more_section = Collapsible(
            card,
            "更多设置",
            "投稿标题模板、互动设置、音质增强",
            opened=False,
            on_toggle=lambda _opened: self._save_state(),
        )
        self._more_section.grid(row=1, column=0, sticky="ew", pady=(theme.PAD_XS, 0))
        more = self._more_section.body

        self._title_template_var = tk.StringVar()
        row = FormRow(
            more,
            "投稿标题",
            hint="{name} 文件名或文件夹名，{n} 序号；留空不改标题。"
            "填好后点「套用到选中行」",
        )
        row.grid(row=0, column=0, sticky="ew", pady=(0, theme.PAD_SM))
        self._title_template_entry = row.add(
            ttk.Entry, textvariable=self._title_template_var
        )
        self._apply_title_button = row.add(
            SecondaryButton,
            text="套用到选中行",
            command=self._apply_title_template,
            column=1,
            sticky="w",
            padx=(theme.PAD_SM, 0),
        )

        # 互动设置与音质增强。五个开关在投稿页也用同一份组件，
        # 免得哪天改了批量页忘了投稿页，变成「这一页勾了没反应」。
        #
        # 属性名**不能叫 ``_options``**：Tk 的 ``Misc`` 内部就有这个
        # 方法（``grid()`` 会调 ``self._options(cnf, kw)`` 拼参数），
        # 被实例属性盖掉之后所有 grid 调用都会炸成
        # “'OptionSwitches' object is not callable”。
        self._option_switches = OptionSwitches(more, on_change=self._on_more_changed)
        self._option_switches.grid(row=1, column=0, sticky="ew")

        # ③ 目录来源
        self._dir_var = tk.StringVar()
        picker = ttk.Frame(card, style="Card.TFrame")
        picker.grid(row=2, column=0, sticky="ew")
        picker.columnconfigure(0, weight=1)

        row = FormRow(
            picker,
            "视频文件夹",
            hint="只扫描这一层，不含子目录",
        )
        row.grid(row=0, column=0, sticky="ew")
        row.add(ttk.Entry, textvariable=self._dir_var, padx=(0, theme.PAD_SM))
        self._pick_button = row.add(
            SecondaryButton,
            text="选择文件夹…",
            command=self._pick_dir,
            column=1,
            sticky="w",
        )
        self._load_button = row.add(
            SecondaryButton,
            text="重新扫描",
            command=self._load,
            column=2,
            sticky="w",
            padx=(theme.PAD_SM, 0),
        )

        # 分 P 合并方式。跟着目录走：它决定「目录里这批文件怎么变成稿件」
        self._group_var = tk.StringVar(value=GROUP_OFF)
        row = FormRow(
            picker,
            "分P合并",
            hint="按文件名前缀或文件夹把同一套视频合成一个稿件的 P1/P2/P3"
            "（只钻一层子目录）；双击列表行可改各分P标题",
        )
        row.grid(row=1, column=0, sticky="ew", pady=(theme.PAD_XS, 0))
        self._group_combo = row.add(
            ttk.Combobox,
            textvariable=self._group_var,
            values=GROUP_OPTIONS,
            state="readonly",
            width=18,
        )
        self._group_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_group_changed())

        # ③ yaml 入口放次要位置。多数用户不该看见它，也不该需要它。
        advanced = ttk.Frame(card, style="Card.TFrame")
        advanced.grid(row=3, column=0, sticky="e", pady=(theme.PAD_XS, theme.PAD_SM))
        ttk.Label(
            advanced,
            text="熟悉配置文件？可从 yaml 加载，逐任务参数与投稿后端以配置为准。",
            style="Card.Secondary.TLabel",
        ).pack(side="left", padx=(0, theme.PAD_SM))
        self._config_button = SecondaryButton(
            advanced, "从 YAML 加载…", self._pick_config
        )
        self._config_button.pack(side="left")

        self._source_pill = StatusPill(card, "尚未选择文件夹", "idle")
        self._source_pill.grid(
            row=4, column=0, sticky="w", pady=(0, theme.PAD_SM)
        )

        # ④ 以下与原版一致：汇总条 + 列表 + 操作 + 进度 + 日志
        self._summary = SummaryBar(card)
        self._summary.grid(row=5, column=0, sticky="ew", pady=(0, theme.PAD_XS))
        self._summary.add_action("全选", self._select_all)
        self._summary.add_action("全不选", self._select_none)
        self._summary.add_action("只选失败项", self._select_failed)

        list_holder = ttk.Frame(card, style="Card.TFrame")
        list_holder.grid(
            row=6, column=0, sticky="nsew", pady=(theme.PAD_XS, theme.PAD_SM)
        )
        list_holder.columnconfigure(0, weight=1)
        list_holder.rowconfigure(0, weight=1)

        self._tree = ttk.Treeview(
            list_holder, columns=COLUMNS, show="headings", height=8
        )
        for column in COLUMNS:
            self._tree.heading(column, text=_HEADINGS[column])
            self._tree.column(
                column,
                width=_COLUMN_MIN_WIDTHS[column],
                stretch=True,
                anchor="center" if column in _CENTERED_COLUMNS else "w",
            )
        # 行着色的唯一来源
        theme.apply_tree_tags(self._tree)
        # 宽度跟着窗口走，而不是锁死在固定值上
        self._tree.bind("<Configure>", self._resize_columns)
        self._tree.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(list_holder, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")

        # 点首列切换勾选；表头点全选/全不选
        self._tree.bind("<Button-1>", self._on_tree_click)
        self._tree.bind("<space>", self._on_space)
        # 双击：有失败原因看全文，否则多 P 任务可以改分P标题
        self._tree.bind("<Double-1>", self._on_double_click)

        self._placeholder = Placeholder(
            card, "尚未选择视频文件夹", "选择文件夹…", self._pick_dir
        )
        self._placeholder.grid(row=6, column=0, sticky="nsew")

        actions = ttk.Frame(card, style="Card.TFrame")
        actions.grid(row=7, column=0, sticky="ew")

        self._run_button = PrimaryButton(actions, "开始投稿", self._run)
        self._run_button.pack(side="left", padx=(0, theme.PAD_SM))
        self._run_button.state(["disabled"])

        self._retry_button = SecondaryButton(actions, "重试失败项", self._retry_failed)
        self._retry_button.pack(side="left", padx=(0, theme.PAD_SM))
        self._retry_button.state(["disabled"])

        self._cancel_button = SecondaryButton(actions, "取消", self._cancel)
        self._cancel_button.pack(side="left")
        self._cancel_button.state(["disabled"])

        self._progress = ProgressBar(card)
        self._progress.grid(row=8, column=0, sticky="ew", pady=(theme.PAD_SM, 0))

        self._log = LogConsole(card, height=7)
        self._log.grid(row=9, column=0, sticky="nsew", pady=(theme.PAD_SM, 0))
        card.rowconfigure(9, weight=1)

        self._apply_source_mode()

    # ---------- 行为 ----------

    def refresh(self) -> None:
        """切到本页时：首次恢复上次目录，之后保持列表不变。

        恢复只做一次。没这么做的话每次切页都会重扫目录、重置勾选，
        用户执行到一半切去看了眼历史，回来发现选择被清了。
        """
        if self._restored:
            return
        self._restored = True
        self._restore_state()

    def _restore_state(self) -> None:
        """读界面偏好并回填，目录还在就自动扫描。"""
        state, problem = load_ui_state(self._state_file)
        if problem:
            self._log.append(problem)
        self._apply_state(state)
        if not state.directory:
            return
        if not Path(state.directory).expanduser().is_dir():
            # 目录没了（移动过/U盘拔了）。路径留着让用户看见并改，
            # 静默清掉的话他会以为程序把设置弄丢了
            self._log.append(f"上次的文件夹已不存在：{state.directory}")
            return
        self._log.append(f"正在加载上次的文件夹：{state.directory}")
        self._load()

    def _apply_state(self, state: BatchUIState) -> None:
        """把偏好填进表单（纯内存，主线程安全）。"""
        self._dir_var.set(state.directory)
        self._tag_var.set(state.tag)
        self._desc_var.set(state.desc)
        self._dtime_var.set(
            "" if state.dtime_offset_hours is None else _fmt_offset(state.dtime_offset_hours)
        )
        self._tid_var.set(tid_option(state.tid))
        self._copyright_var.set(copyright_option(state.copyright))
        self._source_var.set(state.source)
        # 显隐要跟着恢复的值走，否则上次选了转载、这次打开来源框是收起的，
        # 用户会以为程序把出处弄丢了——而它其实还在，只是没显示
        self._sync_source_row()
        self._group_var.set(GROUP_LABEL_BY_MODE.get(state.group_mode, GROUP_OFF))
        self._title_template_var.set(state.title_template)
        self._option_switches.set_flags(
            close_reply=state.close_reply,
            close_danmu=state.close_danmu,
            selection_reply=state.selection_reply,
            dolby=state.dolby,
            hires=state.hires,
        )
        self._update_more_hint()
        # 展开状态放在最后设：它会触发一次偏好保存，此时上面那些变量
        # 必须都已经填好，否则存下去的是半份设置
        self._more_section.set_opened(state.advanced_opened)

    def _group_mode(self) -> str:
        """「分P合并」下拉框当前值对应的内部分组模式。

        认不出来时返回 ``"none"``（不合并）而不是抛异常——下拉框可能被
        手改成没见过的字符串，退回最保守的结果好过让整次扫描失败。
        """
        return GROUP_MODE_BY_LABEL.get(self._group_var.get(), "none")

    def _current_state(self) -> BatchUIState:
        """收集当前表单为偏好对象。

        只收**成功解析**的值：延时填到一半（比如还没输完）时不该把
        上次那个能用的值覆盖掉。但解析失败只影响那一个字段——
        分区、标签、简介都照常收集，不能因为延时手滑就把整份偏好丢光。
        """
        try:
            offset = _parse_offset(self._dtime_var.get())
        except BiliError:
            offset = None
        return BatchUIState(
            directory=self._dir_var.get().strip(),
            tid=parse_tid(self._tid_var.get()),
            copyright=parse_copyright(self._copyright_var.get()),
            source=self._source_var.get().strip(),
            tag=self._tag_var.get().strip(),
            desc=self._desc_var.get().strip(),
            dtime_offset_hours=offset,
            group_mode=self._group_mode(),
            title_template=self._title_template_var.get().strip(),
            advanced_opened=self._more_section.opened,
            **self._more_flags(),
        )

    def _save_state(self) -> None:
        """异步保存偏好。失败只在日志里说一次，不弹窗打断用户。"""
        state = self._current_state()
        try:
            worker = self._state_worker
            if worker.running:
                return
            worker.run(
                lambda _report, _cancelled: save_ui_state(state, self._state_file),
                on_error=lambda exc: self._log.append(f"偏好保存失败：{exc}"),
            )
        except RuntimeError:
            pass

    # ---------- 任务来源：文件夹 ----------

    def _pick_dir(self) -> None:
        if not self._editable():
            self._log.append("正在执行任务，结束后才能切换文件夹")
            return
        path = filedialog.askdirectory(title="选择视频文件夹")
        if not path:
            return
        self._dir_var.set(path)
        self._source_mode = "folder"
        self._apply_source_mode()
        self._load()

    def _load(self) -> None:
        """扫描当前目录，生成任务列表。

        执行途中换目录会把 ``self._tasks`` 整体换掉，而工作线程正逐项读
        那个列表——轻则把任务投给了错的分区，重则直接 IndexError。
        所以这里必须挡住，「选择文件夹…」「重新扫描」也一并禁用。
        """
        if not self._editable():
            self._log.append("正在执行任务，结束后才能重新扫描")
            return
        raw = self._dir_var.get().strip()
        if not raw:
            self._log.append("请先选择视频文件夹")
            return
        directory = Path(raw).expanduser()
        self._source_mode = "folder"
        self._apply_source_mode()

        # 分组方式在这里读好再传进去：工作线程不该回头读 Tk 变量
        mode = self._group_mode()
        self._start_load(
            "正在扫描…",
            lambda _report, is_cancelled: _scan_to_tasks(
                directory, is_cancelled, mode=mode
            ),
        )

    def _on_group_changed(self) -> None:
        """切换分 P 合并方式：已选过目录就立刻重扫，并记进偏好。

        不重扫的话用户会觉得开关坏了——列表还是老样子，
        实际得再点一次「重新扫描」才生效，这种隐性依赖最招骂。
        """
        if not self._editable():
            self._log.append("正在执行任务，结束后才能切换分P合并")
            return
        self._save_state()
        if self._dir_var.get().strip():
            self._load()

    # ---------- 任务来源：yaml（高级） ----------

    def _pick_config(self) -> None:
        """高级入口：从 yaml 加载任务。

        保留是为了不丢功能——``submit.backend``、``upload.concurrency``、
        逐任务 ``title_template`` 这些只有 yaml 能表达。
        """
        if not self._editable():
            self._log.append("正在执行任务，结束后才能切换任务来源")
            return
        path = filedialog.askopenfilename(
            title="选择 YAML 配置文件",
            filetypes=[("YAML 配置", "*.yaml *.yml"), ("所有文件", "*.*")],
        )
        if not path:
            return

        def load_yaml(_report: Any, is_cancelled: Any) -> tuple[AppConfig, list[TaskConfig]]:
            cfg = load_config(Path(path).expanduser())
            if is_cancelled():
                raise Cancelled()
            return cfg, expand_tasks(cfg)

        self._start_load("正在读取配置…", load_yaml, mode="yaml", label=Path(path).name)

    def _start_load(
        self,
        pending_text: str,
        loader: Any,
        *,
        mode: SourceMode = "folder",
        label: str = "",
    ) -> None:
        """在后台加载任务来源，完成后替换列表。

        加载期间置 ``_busy``：复用 :meth:`_editable` 那套守卫，
        扫描还没跑完就点「开始投稿」的话，投出去的是**上一批**任务——
        用户看到的是新列表，执行的却是旧内容。
        """
        self._source_pill.set(pending_text, "busy")
        self._set_busy(True)
        try:
            worker = self._load_worker
            self._active_worker = worker
            worker.run(
                loader,
                on_done=lambda result, m=mode, lb=label: self._on_loaded(result, m, lb),
                on_error=self._on_load_error,
            )
        except Exception as exc:  # noqa: BLE001 - 启动失败必须把界面解锁
            self._set_busy(False)
            self._active_worker = None
            self._source_pill.set("加载失败", "error")
            self._log.append(f"无法启动加载：{exc}")

    def _on_loaded(
        self, result: tuple[AppConfig, list[TaskConfig]], mode: SourceMode, label: str
    ) -> None:
        self._set_busy(False)
        self._active_worker = None
        cfg, tasks = result
        if not tasks:
            self._source_pill.set("没有可投稿的任务", "warn")
            self._log.append("没有找到可投稿的任务")
            return

        self._cfg = cfg
        self._tasks = tasks
        self._source_mode = mode
        self._apply_source_mode()
        self._reset_state()
        self._fill_tree(tasks)
        self._run_button.state(["!disabled"])

        source = label if label else self._dir_var.get().strip()
        self._source_pill.set(f"{source} · {len(tasks)} 个任务", "ok")
        videos = sum(len(task_files(task)) for task in tasks)
        multip = sum(1 for task in tasks if len(task_files(task)) > 1)
        if multip:
            self._log.append(
                f"已加载 {len(tasks)} 个任务（{videos} 个视频，"
                f"其中 {multip} 个是多P稿件）"
            )
        else:
            self._log.append(f"已加载 {len(tasks)} 个任务")
        if self._missing:
            self._log.append(
                f"其中 {len(self._missing)} 个文件不存在，已标为「缺失」且不可勾选"
            )
        if mode == "folder":
            self._save_state()

    def _on_load_error(self, exc: BaseException) -> None:
        self._set_busy(False)
        self._active_worker = None
        if isinstance(exc, Cancelled):
            self._source_pill.set("已取消", "idle")
            self._log.append("已取消加载")
            return
        # 加载失败保留上一批任务：用户已经看到列表了，静默清空等于
        # 「我明明选好了怎么又没了」
        self._source_pill.set("加载失败", "error")
        self._log.append(f"加载失败：{exc}")

    def _apply_source_mode(self) -> None:
        """yaml 模式下把顶部参数置灰。

        统一参数只在文件夹模式生效。yaml 模式下一批任务可能各有各的
        分区和标题模板，用一份统一值盖上去等于把这些能力废了。
        """
        yaml_mode = self._source_mode == "yaml"
        # 分区下拉框必须用 ``configure(state=...)`` 而不是
        # ``state(["!readonly"])``：readonly 是 ttk 的**选项**，
        # 不是 state 标志位，走 state() 改它没有效果——
        # 表现为从 yaml 切回文件夹后，下拉框永远灰着。
        self._tid_combo.configure(state="disabled" if yaml_mode else "readonly")
        # 分 P 合并在 yaml 模式下没有意义：配置文件里的任务已经是
        # 明确的 single/multip，不该再由界面重新解释一遍
        self._group_combo.configure(state="disabled" if yaml_mode else "readonly")
        entry_state = ["disabled"] if yaml_mode else ["!disabled"]
        self._tag_entry.state(entry_state)
        self._desc_entry.state(entry_state)
        self._dtime_entry.state(entry_state)
        # 类型同样属于统一参数：yaml 里每个任务的 copyright/source
        # 可能各不相同，界面这一份不该盖上去
        self._copyright_combo.configure(state="disabled" if yaml_mode else "readonly")
        self._source_entry.state(entry_state)
        # 更多设置里的开关同样属于「统一参数」，yaml 模式下不生效
        self._option_switches.set_disabled(yaml_mode)
        if yaml_mode:
            self._source_pill.set("参数由 YAML 提供", "idle")
        # 「套用到选中行」不跟着置灰：它改的是列表里那几行任务本身的
        # 标题，无论任务来自文件夹还是 yaml 都成立，跟「统一参数」不是
        # 一回事——那种才只在文件夹模式有意义。

    def _sync_source_row(self) -> None:
        """「转载来源」只在选了转载时出现。

        ``grid_remove()`` 而不是 ``grid_forget()``：后者会把已有的网格
        配置一起忘掉，再显示时行位置就乱了。
        """
        if parse_copyright(self._copyright_var.get()) == 2:
            self._source_row.grid()
        else:
            self._source_row.grid_remove()

    def _on_copyright_changed(self) -> None:
        """切换类型：同步来源行的显隐，并记进偏好。"""
        self._sync_source_row()
        self._save_state()

    def _more_flags(self) -> dict[str, bool]:
        """更多设置里五个开关的当前值。"""
        return self._option_switches.flags

    def _on_more_changed(self) -> None:
        """开关变动：更新收起时的提示，并记进偏好。"""
        self._update_more_hint()
        self._save_state()

    def _update_more_hint(self) -> None:
        """收起时标题栏右侧列出已开启的项。

        不列出来的话，面板收起后里面开着什么完全看不见，用户会以为
        自己设的东西丢了——而它们其实正在生效。
        """
        self._more_section.set_hint(self._option_switches.summary())

    def _apply_title_template(self) -> None:
        """把「投稿标题」模板套到勾选的行上。

        只动勾选的行：列表里可能有一半已经手动改过标题，全量套一遍
        等于把那些手工活全冲掉。
        """
        template = self._title_template_var.get().strip()
        if not template:
            self._log.append("投稿标题是空的——填个模板再套用，比如「{name} 第{n}集」")
            return
        indexes = self._selected_indexes()
        if not indexes:
            self._log.append("没勾任何任务：先在左边勾上要改标题的行")
            return

        for number, index in enumerate(indexes, start=1):
            task = self._tasks[index]
            # 已改过标题的任务用改后的名字当 {name}，
            # 否则拿任务名（文件夹/文件名）
            title = render_title_template(template, task.title or task.name, number)
            title = title.strip()
            if not title:
                continue
            self._tasks[index] = replace(task, title=title)
            self._tree.set(str(index), "title", title)

        self._log.append(f"已套用投稿标题到 {len(indexes)} 行：{template}")
        self._save_state()

    def _reset_state(self) -> None:
        """清空上一轮的选择与结果。"""
        self._picked.clear()
        self._missing.clear()
        self._errors.clear()
        self._tones.clear()

    def _fill_tree(self, tasks: list[TaskConfig]) -> None:
        shared_tid = parse_tid(self._tid_var.get())
        self._tree.delete(*self._tree.get_children())
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
                self._picked[iid] = True
            else:
                if files:
                    detail = f"（缺 {len(gone)}/{len(files)}）" if len(files) > 1 else ""
                    file_text = f"[缺] {files[0].name}{detail}"
                else:
                    file_text = "[缺] (未设置)"
                status = "文件缺失"
                tone = "missing"
                self._missing.add(iid)
                # 缺失的不预选，也拒绝后续勾选
                self._picked[iid] = False

            self._tones[iid] = tone
            self._tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    PICKED if self._picked[iid] else UNPICKED,
                    task.name,
                    task.title or task.name,
                    file_text,
                    str(len(files)) if len(files) > 1 else "—",
                    shared_tid if self._source_mode == "folder" else (task.tid or ""),
                    status,
                ),
                tags=(tone,),
            )

        if tasks:
            self._placeholder.grid_remove()
            self._tree.grid()
        else:
            self._tree.grid_remove()
            self._placeholder.grid()

        self._update_summary()

    # ---------- 勾选 ----------

    def _resize_columns(self, _event: "tk.Event | None" = None) -> None:
        """按权重把可用宽度分给各列。

        只在宽度真的变了时动手：``<Configure>`` 在布局的每一步都会触发，
        无脑重算会让 Treeview 反复重排，拖窗口时能看出明显抖动。
        """
        total = self._tree.winfo_width()
        if total < 100:  # 还没布局出来，别拿 1px 去算比例
            return
        for column, width in _column_widths(total).items():
            self._tree.column(column, width=width)

    def _on_tree_click(self, event: tk.Event) -> None:
        """点首列切换勾选；点表头首列则全选/全不选。"""
        region = self._tree.identify_region(event.x, event.y)
        if region == "heading":
            if self._tree.identify_column(event.x) == "#1":
                self._select_all() if self._any_unpicked() else self._select_none()
            return
        if region != "cell":
            return
        if self._tree.identify_column(event.x) != "#1":
            return
        iid = self._tree.identify_row(event.y)
        if iid:
            self._toggle(iid)

    def _on_space(self, _event: tk.Event) -> str:
        """空格切换当前行的勾选。"""
        focused = self._tree.focus()
        if focused:
            self._toggle(focused)
        return "break"

    def _toggle(self, iid: str) -> None:
        """切换一行的勾选状态。

        文件缺失的行**拒绝勾选**：勾了却在执行时被跳过最让人困惑，
        不如一开始就勾不上。
        """
        if iid in self._missing:
            self._log.append("该文件不存在，无法勾选")
            return
        if not self._editable():
            return
        self._picked[iid] = not self._picked.get(iid, False)
        self._refresh_pick_cell(iid)
        self._update_summary()

    def _refresh_pick_cell(self, iid: str) -> None:
        values = list(self._tree.item(iid, "values"))
        values[0] = PICKED if self._picked.get(iid) else UNPICKED
        self._tree.item(iid, values=values)

    def _set_pick(self, iid: str, picked: bool) -> None:
        if iid in self._missing:
            picked = False
        self._picked[iid] = picked

    def _editable(self) -> bool:
        """当前是否允许改动勾选状态。

        执行期间一律锁定：``_run`` 在启动时把 ``indexes`` 快照下来，
        之后 ``_do_run`` 跑的是那份快照。此时让用户改勾选，界面上看到的
        选择和实际执行的列表会**静默脱节**——用户以为重跑了勾的那几个，
        其实跑的还是启动时那几个。

        .. important::
           只看 :attr:`_busy`，不要掺 ``_worker.running``。两个信号
           并非等价：``on_done`` / ``on_error`` 是排进 Tk 事件循环执行的，
           那一刻工作线程**还没退出**（它只是排完队就返回了），
           ``running`` 仍为 True。若拿它当条件，收尾时会把「重试失败项」
           重新锁死，而之后没有任何东西再来刷新一次——
           有失败项却点不了重试。

           ``_busy`` 覆盖了「线程还活着」的整个窗口：``_run`` /
           ``_start_load`` 开头置 True，``_on_done`` / ``_on_error`` /
           ``_on_loaded`` / ``_on_load_error`` / 启动失败五处归 False。
           相比线程内部状态，界面态才是这里真正该依据的东西。
        """
        return not self._busy

    def _apply_selection(self, predicate: "Any") -> None:
        """按谓词批量设置勾选，然后刷新首列与汇总。"""
        if not self._editable():
            return
        for iid in self._tree.get_children():
            self._set_pick(iid, predicate(iid))
            self._refresh_pick_cell(iid)
        self._update_summary()

    def _select_all(self) -> None:
        self._apply_selection(lambda iid: True)

    def _select_none(self) -> None:
        self._apply_selection(lambda iid: False)

    def _select_failed(self) -> None:
        """只勾失败项（不含文件缺失的）。"""
        self._apply_selection(lambda iid: self._tones.get(iid) == "error")

    def _any_unpicked(self) -> bool:
        """是否存在可勾但未勾的项——决定点表头是全选还是全不选。"""
        return any(
            not self._picked.get(iid, False)
            for iid in self._tree.get_children()
            if iid not in self._missing
        )

    # ---------- 执行 ----------

    def _selected_indexes(self) -> list[int]:
        """当前勾选的任务下标，按列表顺序。"""
        return [
            int(iid)
            for iid in self._tree.get_children()
            if self._picked.get(iid) and iid not in self._missing
        ]

    def _collect_shared(self) -> SharedSubmitValues:
        """读顶部统一参数。**主线程调用**，只碰内存里的 Tk 变量。"""
        offset = _parse_offset(self._dtime_var.get())
        return SharedSubmitValues(
            tid=parse_tid(self._tid_var.get()),
            copyright=parse_copyright(self._copyright_var.get()),
            source=self._source_var.get().strip(),
            tag=self._tag_var.get().strip(),
            desc=self._desc_var.get().strip(),
            dtime_offset_hours=offset,
            **self._more_flags(),
        )

    def _snapshot_run(
        self, indexes: list[int]
    ) -> tuple[AppConfig, list[tuple[int, TaskConfig]]]:
        """为本轮执行做完整快照。

        **工作线程不许读页面上的可变属性**。``self._tasks`` /
        ``self._cfg`` 随时可能被下一次「重新扫描」整体换掉，而执行线程
        正逐项遍历它——换掉一半的话，投出去的是新任务里的一半、参数却
        还是旧的。所以这里在主线程把要用的东西全拷出来再传进去。
        """
        cfg = replace(self._cfg) if self._source_mode == "yaml" else AppConfig()
        if self._source_mode == "folder":
            shared = self._collect_shared()
            # 转载缺来源时，服务端对每个稿件都回一句 21004——与其让整批
            # 逐个失败、列表一片红，不如在开始前就拦住
            if shared.copyright == 2 and not shared.source.strip():
                raise BiliError("选了「转载」就必须填转载来源（原视频链接或出处）")
            selected = [
                (index, shared.apply(self._tasks[index])) for index in indexes
            ]
        else:
            selected = [
                (index, replace(self._tasks[index])) for index in indexes
            ]
        return cfg, selected

    def _run(self) -> None:
        # 用 _editable 而不是 _worker.running：界面还锁着就说明这一轮
        # 还没收尾，此时再启动一个 Worker 会和上一轮的收尾回调打架
        if not self._editable():
            return
        indexes = self._selected_indexes()
        if not indexes:
            self._log.append("没有勾选任何任务")
            return
        if not self.app.ctx.logged_in:
            self._log.append("尚未登录，请先到「登录」页扫码")
            return
        try:
            cfg, selected = self._snapshot_run(indexes)
        except BiliError as exc:
            self._log.append(f"参数有误：{exc}")
            return

        self._set_busy(True)
        try:
            self._log.clear()
            self._progress.reset()
            self._active_worker = self._worker
            self._worker.run(
                lambda report, is_cancelled: self._do_run(
                    report, is_cancelled, selected, cfg
                ),
                on_progress=self._on_progress,
                on_done=self._on_done,
                on_error=self._on_error,
            )
        except Exception as exc:  # noqa: BLE001 - 启动失败必须把界面解锁
            # 线程没起来就不会有 _on_done/_on_error 来收尾，_busy 会永久
            # 卡在 True——「开始投稿」灰着、取消点不动，只能重启进程。
            # 这里与 _on_error 做同样的归位。
            self._set_busy(False)
            self._active_worker = None
            self._log.append(f"无法启动任务：{exc}")

    def _retry_failed(self) -> None:
        """只重试上一轮失败的项。

        顺序很重要：**先校验再改选区**。反过来写的话，未登录时
        ``_run`` 会直接 return，用户原来那一堆勾选已经被「只选失败项」
        覆盖掉了，白丢一次选择。
        """
        if not self._failed_indexes():
            return
        if not self.app.ctx.logged_in:
            self._log.append("尚未登录，请先到「登录」页扫码")
            return
        self._select_failed()
        self._run()

    def _failed_indexes(self) -> list[int]:
        """上一轮失败项的下标（不含文件缺失的）。"""
        return [
            int(iid)
            for iid in self._tree.get_children()
            if self._tones.get(iid) == "error"
        ]

    def _cancel(self) -> None:
        worker = self._active_worker
        if worker:
            worker.cancel()
        self._log.append("已请求取消…")

    def _do_run(
        self,
        report,
        is_cancelled,
        selected: list[tuple[int, TaskConfig]],
        cfg: AppConfig,
    ) -> list[TaskOutcome]:
        """依次执行勾选的任务（工作线程）。

        UI 更新一律走 ``report``：文字进度直接是字符串，
        行状态与进度条用 :class:`~..workers.Event`。
        """
        client = self.app.ctx.client()
        backend = get_backend(cfg.submit.backend, cfg.submit.app)
        outcomes: list[TaskOutcome] = []

        total = len(selected)
        for position, (index, task) in enumerate(selected, start=1):
            if is_cancelled():
                raise Cancelled()

            report(Event("start", index=index, position=position, total=total))
            report(f"[{position}/{total}] 开始：{task.name}")

            outcome = run_task(
                client,
                task,
                cfg,
                backend=backend,
                options=RunOptions(on_progress=report),
            )
            outcomes.append(outcome)

            if outcome.success:
                status, error = "成功", ""
            else:
                status, error = f"失败：{outcome.error}", outcome.error

            report(
                Event(
                    "done",
                    index=index,
                    position=position,
                    total=total,
                    status=status,
                    error=error,
                )
            )
            report(f"[{position}/{total}] {task.name} → {status}")

        return outcomes

    # ---------- 主线程回调 ----------

    def _on_progress(self, message: "Any") -> None:
        """分发工作线程的上报：Event 更新界面，字符串进日志。"""
        if isinstance(message, Event):
            # 注意：算进度只能用 position（本次执行里的第几个），
            # 不能用 index（下标）——见 Event 的文档。
            if not message.done:
                self._mark(message.index, "进行中", "busy")
                self._set_task_status(
                    f"进行中 {message.position}/{message.total}"
                )
            else:
                tone = "ok" if message.succeeded else "error"
                self._mark(message.index, _truncate(message.status), tone)
                self._errors[str(message.index)] = message.error
                self._progress.set_value(message.percent)
                self._set_task_status(f"{message.position}/{message.total}")
            self._update_summary()
            return
        self._log.append(message)

    def _set_task_status(self, text: str) -> None:
        """把进度同步到窗口底部状态栏。

        用户可以切到「历史」页去看之前的记录，状态栏是唯一一直可见的
        地方，在那里报进度就不用切回来盯。
        """
        setter = getattr(self.app, "set_task_progress", None)
        if setter:
            setter(text)

    def _mark(self, index: int, status: str, tone: str = "idle") -> None:
        """更新某一行的状态文案与配色，并同步汇总条。

        汇总放在这里更新而不是让调用方记得调：行状态一变，汇总里的
        成败计数就必须跟着变，分成两步迟早会忘，界面上就会出现
        「列表里明明有两个红的，汇总却说 0 失败」。
        """
        iid = str(index)
        if not self._tree.exists(iid):
            return
        values = list(self._tree.item(iid, "values"))
        if len(values) != len(COLUMNS):
            return
        values[-1] = status
        self._tones[iid] = tone
        # 每行只打一个 tag——避免多个 tag 的背景色互相打架
        self._tree.item(iid, values=values, tags=(tone,))
        self._update_summary()

    def _on_done(self, outcomes: list[TaskOutcome]) -> None:
        self._set_busy(False)
        self._active_worker = None
        ok = sum(1 for o in outcomes if o.success)
        self._progress.set_value(100)
        self._progress.set_text(f"完成 {ok}/{len(outcomes)}")
        self._set_task_status(f"完成 {ok}/{len(outcomes)}")
        self._log.append(f"全部结束：成功 {ok}/{len(outcomes)}")
        for outcome in outcomes:
            if outcome.success:
                self._log.append(f"  {outcome.bvid}  {outcome.url}")
        self._update_summary()

    def _on_error(self, exc: BaseException) -> None:
        self._set_busy(False)
        self._active_worker = None
        if isinstance(exc, Cancelled):
            self._revert_busy_rows("已取消")
            self._log.append("已取消")
            return
        # 非取消的异常（登录态失效、网络断了、ffmpeg 缺失抛的错）同样要收尾：
        # 不收的话「进行中」的行会永远停在那里、进度条也不复位，
        # 界面看着像还在跑，用户既不知道发生了什么也不知道怎么恢复。
        self._revert_busy_rows("已中断")
        if isinstance(exc, NotLoggedInError):
            self._log.append("登录态已失效，请到「登录」页重新扫码")
            # 登录态变了，状态栏那个「● 已登录」也得跟着改
            self._refresh_app_status()
        else:
            self._log.append(f"错误：{exc}")

    def _refresh_app_status(self) -> None:
        """让主窗口刷新登录态显示（没有这个方法时静默跳过）。"""
        refresh = getattr(self.app, "refresh_status", None)
        if refresh:
            refresh()

    def _revert_busy_rows(self, reason: str = "已取消") -> None:
        """把「进行中」的行恢复成待投稿，并复位进度条。

        不处理的话，那一批会永远停在「进行中」，进度条也不复位——
        看着像还在跑。取消和异常都要走这里，所以文案由调用方给。
        """
        pending = 0
        for iid in self._tree.get_children():
            if self._tones.get(iid) == "busy":
                self._mark(int(iid), "待投稿", "idle")
                pending += 1
        self._progress.reset()
        if pending:
            text = f"{reason}，{pending} 项未执行"
        else:
            text = reason
        self._progress.set_text(text)
        self._set_task_status(text)
        self._update_summary()

    def _on_double_click(self, event: tk.Event) -> None:
        """双击某一行：失败了看完整原因，否则改这个稿件的标题。

        「看错误」优先：这一行刚投失败时，用户双击想看的一定是失败原因，
        而不是去改一个已经投出去的稿件的标题。
        """
        if self._tree.identify_region(event.x, event.y) != "cell":
            return
        iid = self._tree.identify_row(event.y)
        if not iid:
            return

        error = self._errors.get(iid)
        if error:
            values = self._tree.item(iid, "values")
            name = values[1] if len(values) > 1 else iid
            messagebox.showerror(f"失败原因 · {name}", error)
            return

        if not self._editable():
            return
        self._edit_titles(iid)

    def _edit_titles(self, iid: str) -> None:
        """改某个稿件的标题（多 P 时连同各分 P 标题一起改）。

        自动分组给出的标题就是文件名/文件夹名，多数时候够用；但
        ``旅行_01`` 这种机器味的名字在播放器里不好看，所以留个口子让人改。
        单 P 任务也弹——它同样需要一个像样的标题，之前双击它没反应，
        想改标题只能去改文件名，那个绕路太远了。
        """
        index = int(iid)
        if index >= len(self._tasks):
            return
        task = self._tasks[index]
        files = task_files(task)
        if not files:
            return

        answer = self._ask_titles(
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

        self._tasks[index] = updated
        self._tree.set(iid, "title", archive_title or task.name)
        if len(files) > 1:
            self._log.append(
                f"已更新「{task.name}」的标题与 {len(files)} 个分P标题"
            )
        else:
            self._log.append(f"已更新「{task.name}」的标题")

    def _ask_titles(
        self,
        archive: str,
        files: list[Path],
        current: list[str],
        archive_title: str,
    ) -> tuple[str, list[str]] | None:
        """弹对话框要标题，取消返回 None。

        单独抽出来是因为对话框要跑模态事件循环，测试里替换掉这个
        方法就能验证 :meth:`_edit_titles` 的写入逻辑本身。
        """
        return PartTitlesDialog(self, archive, files, current, archive_title).show()

    def _set_busy(self, busy: bool) -> None:
        """切换运行态下的按钮可用性。

        汇总条里的「全选 / 只选失败项」也要一起锁——它们最终都落到
        :meth:`_apply_selection`，锁按钮和锁逻辑双保险，界面上不会
        出现「按了没反应」的按钮。
        """
        self._busy = busy
        self._run_button.state(["disabled"] if busy else ["!disabled"])
        self._retry_button.state(["disabled"] if busy else ["!disabled"])
        self._cancel_button.state(["!disabled"] if busy else ["disabled"])
        self._pick_button.state(["disabled"] if busy else ["!disabled"])
        self._load_button.state(["disabled"] if busy else ["!disabled"])
        self._config_button.state(["disabled"] if busy else ["!disabled"])
        self._summary.set_actions_enabled(not busy)

    # ---------- 汇总 ----------

    def _update_summary(self) -> None:
        """刷新汇总条：共多少、选了多少、成败各多少。"""
        total = len(self._tree.get_children())
        if not total:
            self._summary.set_stats("")
            self._retry_button.state(["disabled"])
            return

        picked = sum(1 for iid in self._tree.get_children() if self._picked.get(iid))
        tones = [self._tones.get(iid, "idle") for iid in self._tree.get_children()]
        parts = [f"共 {total}", f"已选 {picked}"]
        for label, tone_name in (("成功", "ok"), ("失败", "error")):
            count = tones.count(tone_name)
            if count:
                parts.append(f"{label} {count}")
        if self._missing:
            parts.append(f"缺失 {len(self._missing)}")

        self._summary.set_stats("  ·  ".join(parts))
        # 有失败项才让「重试失败项」可点；但运行中一律锁住——
        # 每完成一项 _mark 都会触发本方法，不在这里判 running 的话，
        # 执行途中出现第一个失败项时按钮就会被解开，而 _run 会因为
        # 「已有任务在运行」直接 return，变成按了没反应的假按钮。
        if tones.count("error") and self._editable():
            self._retry_button.state(["!disabled"])
        else:
            self._retry_button.state(["disabled"])


# ---------- 模块级辅助 ----------


def _scan_to_tasks(
    directory: Path, is_cancelled: "Any", mode: str = "none"
) -> tuple[AppConfig, list[TaskConfig]]:
    """扫目录并生成任务（工作线程执行）。

    生成的每个任务：标题默认取文件名（多 P 组取组名），投稿参数留空——
    顶部统一参数在启动执行时由 :meth:`TasksView.SharedSubmitValues.apply`
    填进去。

    Args:
        mode: 分 P 合并方式，取 ``none`` / ``prefix`` / ``folder``。
            后两者把"看起来是一套"的视频合成**一个**多 P 稿件。
            ``folder`` 会多扫一层子文件夹——不钻进去就看不到目录结构，
            所有文件都算根目录散落文件，等于什么都没分。
    """
    if is_cancelled():
        raise Cancelled()
    files = scan_video_files(directory, max_depth=2 if mode == "folder" else 1)
    if is_cancelled():
        raise Cancelled()
    if not files:
        raise ConfigError(f"目录里没有找到视频文件：{directory}")

    if mode == "none":
        tasks = [
            TaskConfig(name=path.stem, type="single", file=str(path), title=path.stem)
            for path in files
        ]
        return AppConfig(), tasks

    groups = group_files(files, mode, directory)
    if is_cancelled():
        raise Cancelled()
    return AppConfig(), [_group_to_task(item) for item in groups]


def _group_to_task(group: PartGroup) -> TaskConfig:
    """一个分 P 组 → 一个任务。单文件组退化成普通单 P 投稿。"""
    files = [str(path) for path in group.files]
    if len(files) == 1:
        return TaskConfig(
            name=group.name, type="single", file=files[0], title=group.name
        )
    return TaskConfig(name=group.name, type="multip", files=files, title=group.name)


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
        files: list[Path],
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
        SecondaryButton(buttons, "用文件名", self._reset).pack(
            side="left", padx=(0, theme.PAD_SM)
        )
        SecondaryButton(buttons, "取消", self._cancel).pack(
            side="left", padx=(0, theme.PAD_SM)
        )
        PrimaryButton(buttons, "确定", self._ok).pack(side="left")

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


def _parse_offset(text: str) -> float | None:
    """把「6」这样的输入转成小时数。空串表示立即发布。"""
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise BiliError(f"延时发布小时数不是数字: {raw}") from exc


def _fmt_offset(value: float) -> str:
    """小时数回填到输入框。整数就不带 ``.0``。"""
    return str(int(value)) if value == int(value) else str(value)


def tid_option(tid: int) -> str:
    """分区号 → 下拉框里的文字，认不出来的分区原样显示。"""
    for text in TID_OPTIONS:
        if parse_tid(text) == tid:
            return text
    return str(tid)
