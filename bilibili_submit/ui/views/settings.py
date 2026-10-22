"""设置视图：代理、cookie 路径、主题、界面缩放、ffmpeg 自检。

改动即时写入 :class:`AppContext`，各视图下次操作时自动生效，
不需要「保存并重启」。

.. important::
   **主题与界面缩放是例外**：tk/ttk 把颜色和控件尺寸在创建时就写进
   控件，改色板或改缩放都不影响已经建好的控件，所以这两项改完要重启
   客户端。这一点在界面提示里也写明了——与其做个切一半的开关，
   不如把话说清楚。

**环境自检不在主线程跑。** 它要读 cookie 文件、探 ffmpeg 路径、必要时
启动子进程跑 ``ffmpeg -version``，放在主线程会把界面冻住——切到本页
点一下就卡住，用户看到的是程序死了而不是在忙。检测走
:mod:`.environment` + :class:`~..workers.Worker`，这里只负责把快照
填进控件。
"""

from __future__ import annotations

import os
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING

from ... import __version__
from ...exceptions import BiliError
from ...ffmpeg import FfmpegInfo, install_imageio_ffmpeg
from ...update import (
    ReleaseInfo,
    UpdateCancelled,
    check_for_update,
    cleanup_stale_updates,
    download_update,
    fetch_latest_release,
    hint_text,
    is_newer,
    launch_installer,
    mark_version_seen,
    open_release_page,
    relaunch_app,
    release_form,
    replace_executable,
    should_notify,
)
from ..environment import EnvironmentSnapshot, probe_environment
from .. import layout, theme
from ..state import (
    AppUIState,
    THEME_MODE_LABELS,
    UI_SCALE_LABELS,
    load_app_state,
    parse_theme_mode,
    parse_ui_scale,
    save_app_state,
)
from ..workers import Worker
from ..widgets import (
    FormRow,
    KeyValueList,
    ScrollArea,
    PageHeader,
    OutlinedButton,
)

__all__ = ["SettingsView"]

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查
    from ._host import AppHost


def _error_detail(exc: BaseException) -> str:
    """把异常拼成一句人读的话。

    :class:`~bilibili_submit.exceptions.BiliError` 的 ``str()`` 只有标题
    （"安装超时（180 秒）"），真正能让人往下走的 ``hint`` 在另一个字段
    里——只显示标题等于什么都没说。
    """
    hint = getattr(exc, "hint", "")
    return f"{exc}\n\n{hint}" if hint else str(exc)


#: 下拉框选项顺序。跟随系统放第一个：它是默认值，也最不需要用户操心。
THEME_OPTIONS = [THEME_MODE_LABELS[key] for key in ("system", "light", "dark")]

#: 界面缩放下拉框选项，顺序同 :data:`~.state.APP_UI_SCALES`。
SCALE_OPTIONS = [UI_SCALE_LABELS[key] for key in ("auto", "1.0", "1.25", "1.5")]


def _cookie_stamp(path: str) -> tuple[int, int] | None:
    """cookie 文件的 ``(mtime_ns, 字节数)``，读不到就 None。

    只用来判断「探测的输入变没变」。文件不存在也是一个**稳定**值——
    不这样的话，首次使用前每次切页都要重跑一遍探测。
    """
    try:
        stat = os.stat(os.path.expanduser(path))
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


#: 更新检查的结果。第二项是「到底查到了没有」——手动点的那一次要能
#: 区分「已是最新」和「网络不通」，否则断网时界面会说「已是最新」，
#: 那是骗人。自动检查不看这一项：它失败就该完全安静。
_UpdateResult = tuple[ReleaseInfo | None, bool]


def _progress_text(done: int, total: int) -> str:
    """下载进度的一行文字。

    总字节未知时（服务器没给 Content-Length，GitHub 资产一般会给）
    只报已下载量——编一个总数比不报更糟。
    """
    if total > 0:
        return f"{done / 1e6:.1f} / {total / 1e6:.1f} MB（{done / total * 100:.0f}%）"
    return f"{done / 1e6:.1f} MB"


class SettingsView(ttk.Frame):
    """设置页。"""

    def __init__(self, master: tk.Misc, app: "AppHost") -> None:
        super().__init__(master, style="TFrame")
        self.app = app
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        #: 探测是短生命周期任务，每次刷新一个新 Worker——用单个长期
        #: Worker 的话，用户连点刷新时第二次会因为「已有任务在运行」
        #: 直接被丢掉，界面上就一直留着上一次的过期结果。
        self._probe_worker: Worker[EnvironmentSnapshot] | None = None
        #: 探测代号。每次刷新 +1，回包时先比对：旧代号的结果一律丢弃。
        #: 没有它的话，「旧 cookie 路径的探测慢、新路径的快」这种情况
        #: 下慢的会后到，把新的覆盖回去，界面显示的登录态是另一个文件
        #: 读出来的。
        self._probe_generation = 0
        #: 更新检查同样是一次性的短任务，理由与上面一致；和探测分开两个
        #: Worker 是因为它们可以随时各来一次，共用一个会互相把对方丢掉。
        self._update_worker: Worker[_UpdateResult] | None = None
        self._update_generation = 0
        #: 下载更新：安装包有几十 MB，是比检查更长的任务，同样单独一个
        #: Worker——下载途中用户又点了「检查更新」的话，两个任务不该
        #: 互相把对方丢掉。
        self._download_worker: Worker[Path] | None = None
        #: 正在下载的版本号。用于「自动检查又弹了一次同一个版本」时
        #: 认出来：那次不理会，让它走完正在进行的下载。
        self._downloading_version = ""
        #: 装 ffmpeg 也是一次性短任务，但和探测分开两个 Worker：
        #: 点「一键获取」时上一次探测可能还在跑，共用一个的话
        #: 第二次会因为「已有任务在运行」直接被丢掉。
        self._ffmpeg_worker: Worker[FfmpegInfo] | None = None
        #: 上一次探测**成功**的结果，以及它对应的输入指纹。见 :meth:`refresh`。
        #: 探测失败不写这里——失败的状态不该被缓存成「已知结果」。
        self._env_snapshot: EnvironmentSnapshot | None = None
        self._env_key: tuple | None = None
        self._build()

    def _build(self) -> None:
        PageHeader(
            self, "设置", "改动立即生效；主题与界面缩放两项需重启客户端。"
        ).grid(row=0, column=0, sticky="ew", pady=(0, theme.PAD_LG))

        # 最小窗口下这一页装不下（实测 reqh=730 vs 可用 595），底部
        # 「更新」那一块会被整个裁掉。包进滚动区兜住。
        #
        # ``ScrollArea.body`` 本身就是 ``Card.TFrame`` + 同样的 padding，
        # 所以把 ``card`` 直接绑到它上面，下面几十处引用一行都不用改
        # ——投稿页（upload.py）已经是这个写法。
        self._area = ScrollArea(self)
        self._area.grid(row=1, column=0, sticky="nsew")
        card = self._area.body
        card.columnconfigure(0, weight=1)

        # 四段各装一个分组 frame，宽屏（wide 档）排成两列、窄屏竖排。
        #
        # 分组 frame 是**静态**的：切换只改 grid 配置，不重建控件。
        # 重建会丢掉用户已填的表单内容，也会让异步探测回来的结果
        # 无处可写（探测回来时那个 KeyValueList 可能已经换了新实例）。
        #
        # 顺带去掉了原先挂在段间的三条 Separator：分组标题 + 间距
        # 已经足够分层，而横线在双列时会横跨两列、反而切断阅读。
        self._groups: list[ttk.Frame] = []
        for index in range(4):
            group = ttk.Frame(card, style="Card.TFrame")
            group.columnconfigure(0, weight=1)
            group.grid(row=index, column=0, sticky="ew", pady=(0, theme.PAD_MD))
            self._groups.append(group)
        basic, look, env_box, update_box = self._groups

        # ① 连接：代理与 cookie 路径
        ttk.Label(basic, text="连接", style="Heading.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, theme.PAD_SM)
        )
        self._proxy_var = tk.StringVar()
        row = FormRow(
            basic,
            "代理",
            hint="例如 http://127.0.0.1:7890；境外网络或直连被风控时填。留空表示不走代理。",
        )
        row.grid(row=1, column=0, sticky="ew", pady=(0, theme.PAD_SM))
        row.add(ttk.Entry, textvariable=self._proxy_var)
        self._proxy_var.trace_add("write", self._on_proxy_change)

        # cookie 文件
        self._cookie_var = tk.StringVar()
        row = FormRow(
            basic, "Cookie 文件", hint="含 SESSDATA，等同于账号凭据，请勿外传。"
        )
        row.grid(row=2, column=0, sticky="ew")
        row.add(ttk.Entry, textvariable=self._cookie_var, padx=(0, theme.PAD_SM))
        row.add(
            OutlinedButton, text="浏览…", command=self._pick_cookie, column=1, sticky="w"
        )

        # ② 外观：主题模式
        ttk.Label(look, text="外观", style="Heading.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, theme.PAD_SM)
        )
        self._theme_var = tk.StringVar(value=THEME_OPTIONS[0])
        row = FormRow(
            look,
            "主题",
            hint="跟随系统即读 Windows 的浅色/深色设置。tk 控件的颜色在建界面时"
            "就写死了，改这里要重启客户端才生效。",
        )
        row.grid(row=1, column=0, sticky="ew")
        row.add(
            ttk.Combobox,
            textvariable=self._theme_var,
            values=THEME_OPTIONS,
            state="readonly",
            width=12,
        )
        self._theme_var.trace_add("write", self._on_theme_change)

        # 界面缩放：字小了看不清的唯一自救入口。跟随系统通常已经对
        # （Windows 按 DPI 自动放大），但它只认主显示器——小屏笔记本、
        # 虚拟机、远程桌面，以及就是觉得中文小的用户，需要手动档。
        self._scale_var = tk.StringVar(value=SCALE_OPTIONS[0])
        row = FormRow(
            look,
            "界面缩放",
            hint="整个界面（字号、间距、窗口默认尺寸）一起放大或缩小。"
            "控件尺寸在建界面时就定死了，改这里和主题一样要重启客户端才生效。",
        )
        row.grid(row=2, column=0, sticky="ew")
        row.add(
            ttk.Combobox,
            textvariable=self._scale_var,
            values=SCALE_OPTIONS,
            state="readonly",
            width=12,
        )
        self._scale_var.trace_add("write", self._on_scale_change)

        # ③ 环境自检
        ttk.Label(env_box, text="环境自检", style="Heading.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, theme.PAD_SM)
        )
        self._env = KeyValueList(env_box, label_width=12)
        self._env.grid(row=1, column=0, sticky="w")
        # 有了结果缓存就得给用户一个手动重测的入口，否则装完 ffmpeg
        # 想让它重新认一次就只能重启客户端
        row = FormRow(
            env_box,
            "环境变了",
            hint="换代理、换新 cookie 文件、或刚装好 ffmpeg 时点这里。"
            "切到本页不会自动重跑——没必要每次都等一遍探测。",
        )
        row.grid(row=2, column=0, sticky="ew", pady=(theme.PAD_SM, 0))
        row.add(OutlinedButton, text="重新检测", command=self._start_env_probe)

        # ffmpeg 缺失时的补救入口。默认藏起来——绝大多数包自带 ffmpeg，
        # 常驻一个「一键获取」只会让人怀疑自己是不是漏装了什么。
        # 只有探测明确说「没找到」时才放出来（见 :meth:`_render_env`）。
        fix = FormRow(
            env_box,
            "自动抽帧",
            hint="ffmpeg 只影响 cover: auto 自动抽帧，其他功能不依赖它。"
            "点下面的按钮会装 imageio-ffmpeg（约 30MB），装完自动重新检测。",
        )
        fix.grid(row=3, column=0, sticky="ew", pady=(theme.PAD_SM, 0))
        self._ffmpeg_fix = fix
        self._ffmpeg_button = fix.add(
            OutlinedButton, text="一键获取 ffmpeg", command=self._on_install_ffmpeg
        )
        fix.grid_remove()

        # ④ 更新
        ttk.Label(update_box, text="更新", style="Heading.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, theme.PAD_SM)
        )
        row = FormRow(
            update_box,
            "版本",
            hint="每次启动自动查一次（24 小时内不重复请求）。有新版本才提示，"
            "没有就什么都不做。",
        )
        row.grid(row=1, column=0, sticky="ew", pady=(0, theme.PAD_SM))
        self._update_button = row.add(
            OutlinedButton, text="检查更新", command=self._on_check_update
        )
        self._update = KeyValueList(update_box, label_width=12)
        self._update.grid(row=2, column=0, sticky="w")
        self._update.set_rows([("当前版本", __version__, "idle")])

    def apply_layout(self, spec: "layout.Layout") -> None:
        """按档位把四段排成一列或两列。

        只 ``grid_configure`` 已建好的分组 frame——不重建、不换父容器。

        .. important::
           两列都要拿到 ``columnconfigure`` 权重，否则右列会被压成
           0 宽：只改 ``column`` 不改 ``weight`` 是这类切换最常见的
           失效方式，而且**不报错**，界面只是「右半边空白」。
        """
        two = spec.columns > 1
        card = self._area.body
        card.columnconfigure(1, weight=1 if two else 0)
        for index, group in enumerate(self._groups):
            column, row = (index % 2, index // 2) if two else (0, index)
            group.grid_configure(
                row=row,
                column=column,
                sticky="ew",
                padx=(0, spec.gutter) if (two and column == 0) else 0,
                pady=(0, theme.PAD_MD),
            )
        # 列数变了，canvas 的滚动区不会自己重算
        self._area.refresh()

    # ---------- 行为 ----------

    def refresh(self, force: bool = False) -> None:
        """切到本页时回填表单，只有必要时才重跑环境自检。

        自检**不是**每次切页都跑。``App.show()`` 每次切过来都会调一次
        ``refresh()``，而探测的第一步是把三行摆成「检测中…」，再等线程
        回来——ffmpeg 版本号要启动子进程读，慢的时候那三行就亮着
        「检测中」停一下，用户看到的就是「进来又重新加载了一遍」。

        输入没变就直接用上次的结果，连过渡态都不摆。输入 = cookie 文件
        的内容 + 代理：换 cookie 路径、改代理、扫码登录成功（cookie 文件
        被重写）这三种情况指纹都会变，剩下的切页一律复用。

        ``force=True`` 留给「重新检测」按钮，也留给换完 cookie 路径后的
        主动重测。
        """
        self._fill_form()
        if (
            not force
            and self._env_snapshot is not None
            and self._probe_key() == self._env_key
        ):
            self._render_env(self._env_snapshot)
            return
        self._start_env_probe()

    def _fill_form(self) -> None:
        """回填表单。纯内存操作，主线程做。"""
        ctx = self.app.ctx
        if not self._proxy_var.get():
            self._proxy_var.set(ctx.proxy or "")
        if not self._cookie_var.get():
            self._cookie_var.set(ctx.cookie_file)
        # 值没变就别 set：StringVar 上挂着 trace，set 一次就写一次
        # 用户偏好文件——每次切页都白写一遍磁盘
        state = load_app_state()
        theme_label = THEME_MODE_LABELS[state.theme_mode]
        if self._theme_var.get() != theme_label:
            self._theme_var.set(theme_label)
        scale_label = UI_SCALE_LABELS[state.ui_scale]
        if self._scale_var.get() != scale_label:
            self._scale_var.set(scale_label)

    def _probe_key(self) -> tuple:
        """环境自检的输入指纹：cookie 文件的内容标识 + 代理。

        登录成功就是往 cookie 文件里写东西，所以文件一变（扫码登录、
        换了路径）指纹必变；代理是探测的另一半输入。
        """
        return (_cookie_stamp(self.app.ctx.cookie_file), self.app.ctx.proxy)

    def _on_proxy_change(self, *_: object) -> None:
        value = self._proxy_var.get().strip()
        self.app.ctx.proxy = value or None

    def _on_theme_change(self, *_: object) -> None:
        """存下主题选择。

        只写文件，**不重画界面**：tk/ttk 的颜色在创建控件时就写进去了，
        改色板对已经建好的控件无效。要即时生效得把整棵控件树重建，
        代价太大也不稳——提示里写清楚「重启生效」比做个切一半的开关诚实。
        """
        save_app_state(AppUIState(theme_mode=parse_theme_mode(self._theme_var.get())))

    def _on_scale_change(self, *_: object) -> None:
        """存下界面缩放选择。和主题一样只写文件、重启生效。

        不尝试热切换是有原因的：缩放改的是**几十个模块级像素常量**
        加字阶，控件尺寸在创建时就从这些常量读走了。真要热切换得把
        整棵控件树按新度量重建——那相当于把「重启客户端」做进了一个
        按钮里，而它该有的可靠性（表单内容不丢、滚动位置还原）一项
        都没有。写文件 + 明说重启，比做个会丢数据的开关诚实。
        """
        save_app_state(
            AppUIState(
                theme_mode=parse_theme_mode(self._theme_var.get()),
                ui_scale=parse_ui_scale(self._scale_var.get()),
            )
        )

    def _pick_cookie(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Cookie 保存位置",
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if path:
            self._cookie_var.set(path)
            self.app.ctx.cookie_file = path
            self.app.refresh_status()
            # 状态栏刷新了，本页的登录态也得跟着重测——换的正是它读的文件
            self._start_env_probe()

    # ---------- 环境自检（异步） ----------

    def _start_env_probe(self) -> None:
        """先把界面摆成「检测中」，再把探测丢到工作线程。

        顺序不能反：先起线程再设过渡态的话，探测足够快的话结果会在
        过渡态之前落地，界面就变成「先闪一下检测中、再跳到结果」——
        闪得快的还只是难看，闪得慢（ffmpeg 版本读取慢）就是明确的
        「点了没反应」。
        """
        self._probe_generation += 1
        generation = self._probe_generation
        ctx = self.app.ctx
        # 记下**发起时**的输入：探测期间用户可能又改了代理，回来时再算
        # 的话这锅结果会被算成「属于新输入」，改完代理反而看不到更新
        self._env_key = self._probe_key()

        self._env.set_rows(
            [
                ("登录态", "检测中…", "busy"),
                ("代理", ctx.proxy or "未设置", "ok" if ctx.proxy else "idle"),
                ("ffmpeg", "检测中…", "busy"),
            ]
        )

        worker: Worker[EnvironmentSnapshot] = Worker(self)
        self._probe_worker = worker
        try:
            worker.run(
                lambda _report, _cancelled: probe_environment(
                    ctx.cookie_file, include_ffmpeg_version=True
                ),
                on_done=lambda snap: self._on_env_done(generation, snap),
                on_error=lambda exc: self._on_env_error(generation, exc),
            )
        except RuntimeError as exc:
            # 线程没起来不会有 on_done/on_error，这里必须自己收尾，
            # 否则界面永远停在「检测中…」
            self._show_env_failed(str(exc))

    def _on_env_done(
        self, generation: int, snapshot: EnvironmentSnapshot
    ) -> None:
        # 过期结果直接丢：晚到的那份属于另一个 cookie 路径，
        # 写进界面就成了「明明换好了却显示没登录」
        if generation != self._probe_generation:
            return

        self._env_snapshot = snapshot
        self._render_env(snapshot)

    def _render_env(self, snapshot: EnvironmentSnapshot) -> None:
        """把一份快照填进自检列表。

        和探测分开：命中缓存时直接调它，不用再摆一次「检测中」的过渡态。
        """
        ctx = self.app.ctx
        if snapshot.login_error:
            login_value, login_tone = f"读取失败：{snapshot.login_error}", "error"
        else:
            login_value = "已登录" if snapshot.logged_in else "未登录"
            login_tone = "ok" if snapshot.logged_in else "warn"

        rows = [
            ("登录态", login_value, login_tone),
            ("代理", ctx.proxy or "未设置", "ok" if ctx.proxy else "idle"),
        ]

        info = snapshot.ffmpeg
        if info is None:
            rows.append(("ffmpeg", "未找到（仅影响自动抽帧）", "warn"))
            # 补救入口只在**明确没找到**时露出来。探测失败走
            # :meth:`_show_env_failed` 那条路，那里不露——失败多半是
            # cookie 读不了，和 ffmpeg 无关，露出「一键获取」只会误导。
            self._reset_ffmpeg_button()
            self._ffmpeg_fix.grid()
        else:
            version = snapshot.ffmpeg_version
            if version and version != "未知":
                version = version.replace("ffmpeg version ", "").split(" ")[0]
            rows.append((f"ffmpeg（{info.source}）", version or "未知", "ok"))
            self._ffmpeg_fix.grid_remove()

        self._env.set_rows(rows)

    # ---------- 一键获取 ffmpeg（异步） ----------

    def _reset_ffmpeg_button(self) -> None:
        """把「一键获取」恢复成可点的默认样子。

        装过一次之后按钮可能停在「获取中…」的禁用态——下次再露出来
        （比如探测又报未找到）时如果不管它，用户会看到一个点不动的按钮。
        """
        button = self._ffmpeg_button
        if str(button.cget("text")) != "一键获取 ffmpeg":
            button.configure(text="一键获取 ffmpeg")
        button.state(["!disabled"])

    def _on_install_ffmpeg(self) -> None:
        """装 imageio-ffmpeg。必须走工作线程——pip 会阻塞几十秒。"""
        if self._ffmpeg_worker is not None and self._ffmpeg_worker.running:
            return  # 已在装了；再点一次没有意义
        proxy = self.app.ctx.proxy
        self._ffmpeg_button.configure(text="获取中…")
        self._ffmpeg_button.state(["disabled"])

        worker: Worker[FfmpegInfo] = Worker(self)
        self._ffmpeg_worker = worker
        try:
            worker.run(
                lambda _report, _cancelled: install_imageio_ffmpeg(proxy=proxy),
                on_done=lambda info: self._on_ffmpeg_ready(info),
                on_error=lambda exc: self._on_ffmpeg_failed(exc),
            )
        except RuntimeError as exc:
            # 线程没起来不会有 on_error，得自己收尾
            self._reset_ffmpeg_button()
            self._show_ffmpeg_error(str(exc))

    def _on_ffmpeg_ready(self, info: FfmpegInfo) -> None:
        """装好了：丢掉「未找到」那份缓存，重测一遍让界面自己更新。

        不弹「安装成功」——自检那一行会从「未找到」变成
        ``ffmpeg（imageio） 7.0.2``，那就是反馈。失败才值得打断用户。
        """
        self._ffmpeg_worker = None
        self._env_snapshot = None
        self._start_env_probe()

    def _on_ffmpeg_failed(self, exc: BaseException) -> None:
        self._ffmpeg_worker = None
        self._reset_ffmpeg_button()
        self._show_ffmpeg_error(_error_detail(exc))

    def _show_ffmpeg_error(self, detail: str) -> None:
        """失败要弹窗：原因通常有好几行（pip 的报错），一行塞不下。"""
        messagebox.showerror("获取 ffmpeg 失败", detail, parent=self)

    def _on_env_error(self, generation: int, exc: BaseException) -> None:
        """探测本身炸了要显示出来，不能让界面停在「检测中…」。"""
        if generation != self._probe_generation:
            return
        self._show_env_failed(str(exc))

    def _show_env_failed(self, reason: str) -> None:
        # 失败的结果不进缓存：下次切页还能重试，而不是把「检测失败」
        # 当成既定事实一直显示下去
        self._env_snapshot = None
        ctx = self.app.ctx
        self._env.set_rows(
            [
                ("登录态", f"检测失败：{reason}", "error"),
                ("代理", ctx.proxy or "未设置", "ok" if ctx.proxy else "idle"),
                ("ffmpeg", "检测失败", "error"),
            ]
        )

    # ---------- 检查更新（异步） ----------

    def _on_check_update(self) -> None:
        """手动点按钮。不受限频约束——用户主动点，一天也点不了几次。"""
        self._start_update_check(manual=True)

    def auto_check_update(self) -> None:
        """启动时的静默检查，由 :class:`~..app.App` 延迟调用。

        失败**完全不显示**：这是后台动作，用户没要求知道，断网时不该
        让界面上多一行「检查失败」。

        顺手收掉上次升级的残留：便携版更新时旧 exe 被改名成 ``.old``，
        删除它得等自己退出之后，所以留到这里——每次启动清一次，
        用户永远看不到目录里躺着一个用不了的旧文件。
        """
        cleanup_stale_updates()
        self._start_update_check(manual=False)

    def _start_update_check(self, *, manual: bool) -> None:
        self._update_generation += 1
        generation = self._update_generation
        proxy = self.app.ctx.proxy

        if manual:
            # 先摆过渡态，理由同 _start_env_probe：先起线程再摆的话，
            # 结果可能比过渡态先落地，变成「点了没反应」
            self._update.set_rows(
                [
                    ("当前版本", __version__, "idle"),
                    ("最新版本", "检查中…", "busy"),
                ]
            )

        if manual:

            def task(_report: object, _cancelled: object) -> _UpdateResult:
                info = fetch_latest_release(proxy=proxy)
                if info is None:
                    return None, False
                return (info if is_newer(info.tag, __version__) else None), True

        else:

            def task(_report: object, _cancelled: object) -> _UpdateResult:
                return check_for_update(proxy=proxy), True

        worker: Worker[_UpdateResult] = Worker(self)
        self._update_worker = worker
        try:
            worker.run(
                task,
                on_done=lambda result: self._on_update_done(
                    generation, result, manual=manual
                ),
                on_error=lambda exc: self._on_update_error(
                    generation, exc, manual=manual
                ),
            )
        except RuntimeError as exc:
            # 线程没起来不会有回调，手动的那次得自己收尾
            if manual:
                self._show_update_failed(str(exc))

    def _on_update_done(
        self, generation: int, result: _UpdateResult, *, manual: bool
    ) -> None:
        if generation != self._update_generation:
            return
        info, succeeded = result

        if manual:
            if not succeeded:
                self._show_update_failed("没查到（网络不通？）")
                return
            if info is None:
                self._update.set_rows(
                    [
                        ("当前版本", __version__, "idle"),
                        ("最新版本", f"已是最新（{__version__}）", "ok"),
                    ]
                )
                return
            self._update.set_rows(
                [
                    ("当前版本", __version__, "idle"),
                    ("最新版本", info.version, "warn"),
                ]
            )

        if info is not None:
            self._prompt_update(info)

    def _on_update_error(
        self, generation: int, exc: BaseException, *, manual: bool
    ) -> None:
        if generation != self._update_generation:
            return
        if manual:
            self._show_update_failed(str(exc))

    def _show_update_failed(self, reason: str, *, prefix: str = "检查失败") -> None:
        self._update.set_rows(
            [
                ("当前版本", __version__, "idle"),
                ("最新版本", f"{prefix}：{reason}", "idle"),
            ]
        )

    def _prompt_update(self, info: ReleaseInfo) -> None:
        """弹一次提示，问要不要就地更新。

        用 ``messagebox`` 而不是就地显示：启动自动检查时用户多半在别的页，
        就地显示一定会被错过。同一个版本只弹一次——记在
        ``mark_version_seen`` 里，不看用户选了什么：他看见了就够了。

        **不再跳浏览器**：安装版和便携版都直接下载并在客户端内完成
        （见 :meth:`_on_download_update`）。源码形态没有「装」这回事，
        才保留「去下载页」这条路——那是兜底，不是默认。
        """
        if not should_notify(info.version):
            return
        if (
            self._download_worker is not None
            and self._download_worker.running
            and self._downloading_version == info.version
        ):
            return  # 正在下的就是它：别为同一个版本再弹一次
        title, body = hint_text(info)
        if release_form() == "source":
            question = "现在去下载页看看吗？"
        else:
            question = "现在下载并更新吗？"
        if messagebox.askyesno(title, f"{body}\n\n{question}", parent=self):
            if release_form() == "source":
                open_release_page(info.url)
            else:
                self._on_download_update(info)
        mark_version_seen(info.version)

    # ---------- 下载并应用更新（异步） ----------

    def _reset_update_button(self) -> None:
        """把更新按钮恢复成「检查更新」的默认样子。

        下载期间它被改成「取消下载」，成功、失败、取消三条路收尾时
        都要走这里——不复原的话，下一次更新用户会看到一个点不动的
        「取消下载」。
        """
        button = self._update_button
        if str(button.cget("text")) != "检查更新":
            button.configure(text="检查更新")
        button.configure(command=self._on_check_update)
        button.state(["!disabled"])

    def _on_download_update(self, info: ReleaseInfo) -> None:
        """下载新版。必须走工作线程——安装包几十 MB，会跑好几分钟。"""
        if self._download_worker is not None and self._download_worker.running:
            return  # 正在下载；按钮此时就是「取消下载」，不会走到这
        if not info.asset_url:
            # 这个 release 没带可用资产（打包配置改了名之类）。让用户
            # 自己去下载页看，比在这儿干等着强。
            messagebox.showinfo(
                "无法自动更新",
                f"这个版本没有可供自动下载的文件。\n\n请手动打开：{info.url}",
                parent=self,
            )
            open_release_page(info.url)
            return

        proxy = self.app.ctx.proxy
        self._downloading_version = info.version
        self._update_button.configure(text="取消下载", command=self._on_cancel_download)

        self._update.set_rows(
            [
                ("当前版本", __version__, "idle"),
                ("最新版本", info.version, "warn"),
                ("更新进度", "下载中… 0%", "busy"),
            ]
        )

        # 节流：每 250ms 最多上报一次。KeyValueList 是整表重建的，
        # 64KB 一个 chunk 全上报等于每秒十几次重建；进度是人看的，
        # 4 次/秒和 40 次/秒没有区别。
        last_report = [0.0]

        def task(report: object, is_cancelled: object) -> Path:
            def on_progress(done: int, total: int) -> None:
                now = time.monotonic()
                if done < total and now - last_report[0] < 0.25:
                    return
                last_report[0] = now
                report(_progress_text(done, total))

            return download_update(
                info,
                proxy=proxy,
                on_progress=on_progress,
                is_cancelled=is_cancelled,
            )

        worker: Worker[Path] = Worker(self)
        self._download_worker = worker
        try:
            worker.run(
                task,
                on_progress=self._on_download_progress,
                on_done=lambda local: self._on_update_downloaded(info, local),
                on_error=lambda exc: self._on_download_error(exc),
            )
        except RuntimeError as exc:
            # 线程没起来不会有 on_error，复用同一条收尾路径
            self._on_download_error(exc)

    def _on_cancel_download(self) -> None:
        """取消下载。任务自己在下一个 chunk 边界检查标志位后退出。"""
        if self._download_worker is None:
            return
        self._download_worker.cancel()
        self._update_button.state(["disabled"])

    def _on_download_progress(self, message: str) -> None:
        """把进度行刷成最新文字。

        KeyValueList 是整表重建的，所以另外两行也要一起摆回去——
        当前版本照旧，最新版本沿用检查时定下的那行。
        """
        rows = [("当前版本", __version__, "idle")]
        for key, value in self._update.rows():
            if key == "最新版本：":
                rows.append(("最新版本", value, "warn"))
        rows.append(("更新进度", message, "busy"))
        self._update.set_rows(rows)

    def _on_update_downloaded(self, info: ReleaseInfo, local: Path) -> None:
        """下载完成：按形态确认下一步，然后完成更新。

        安装版走安装器（它提权替换 Program Files 下的文件），便携版
        自己换自己。两条路最后都要退出客户端——文件被自己占着，
        替换和安装都进行不下去。
        """
        self._download_worker = None
        self._downloading_version = ""
        self._reset_update_button()
        self._update.set_rows(
            [
                ("当前版本", __version__, "idle"),
                ("最新版本", info.version, "warn"),
                ("更新进度", "下载完成，等待确认", "ok"),
            ]
        )

        size_mb = local.stat().st_size / 1e6
        if release_form() == "portable":
            detail = (
                f"新版已下载完成（{size_mb:.0f} MB）。\n\n"
                "点「确定」后客户端会替换自己并自动重启；"
                "旧版本会备份成 .old，确认无误后可以删掉它。"
            )
        else:
            detail = (
                f"安装包已下载完成（{size_mb:.0f} MB）。\n\n"
                "点「确定」后客户端会退出并运行安装向导，"
                "按提示点几下即可完成升级；cookie 和投稿历史不受影响。"
            )
        if not messagebox.askyesno("更新就绪", detail, parent=self):
            # 用户改主意了：文件已下载好，告诉他去哪儿取，别让这
            # 几十 MB 白下
            messagebox.showinfo(
                "更新已取消", f"新版已下载到：\n{local}\n\n随时可以手动运行它。",
                parent=self,
            )
            return

        if release_form() == "portable":
            self._apply_portable(local)
        else:
            self._apply_installed(local)

    def _apply_portable(self, local: Path) -> None:
        """便携版：替换自己 → 启动新版 → 退出。"""
        try:
            replace_executable(local)
        except BiliError as exc:
            self._show_update_failed(_error_detail(exc), prefix="更新失败")
            return
        # 替换之后 sys.executable 指的就是新版了。启动失败**不退出**：
        # 当前进程跑的是改名后的 .old，还好好地活着，让用户先用着，
        # 手动双击一样能开新版。
        try:
            relaunch_app(Path(sys.executable))
        except OSError as exc:
            self._show_update_failed(
                f"新版已就位，但自动启动失败（{exc}），请手动重新打开程序",
                prefix="更新失败",
            )
            return
        self._quit()

    def _apply_installed(self, local: Path) -> None:
        """安装版：跑安装器（它提权）→ 退出，把文件让出来。"""
        try:
            launch_installer(local)
        except (OSError, BiliError) as exc:
            self._show_update_failed(_error_detail(exc), prefix="更新失败")
            return
        self._quit()

    def _quit(self) -> None:
        """退出客户端——更新流程的最后一步。"""
        self.winfo_toplevel().quit()

    def _on_download_error(self, exc: BaseException) -> None:
        """下载失败。取消不算失败：安静复位，不弹错误框。"""
        self._download_worker = None
        self._downloading_version = ""
        self._reset_update_button()
        if isinstance(exc, UpdateCancelled):
            self._update.set_rows(
                [
                    ("当前版本", __version__, "idle"),
                    ("最新版本", "下载已取消", "idle"),
                ]
            )
            return
        messagebox.showerror("下载更新失败", _error_detail(exc), parent=self)
        self._update.set_rows(
            [
                ("当前版本", __version__, "idle"),
                ("最新版本", "下载失败，可重试", "idle"),
            ]
        )
