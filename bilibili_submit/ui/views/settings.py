"""设置视图：代理、cookie 路径、主题、ffmpeg 自检。

改动即时写入 :class:`AppContext`，各视图下次操作时自动生效，
不需要「保存并重启」。

.. important::
   **主题是唯一的例外**：tk/ttk 把颜色写进控件，改色板不影响已建好的
   控件，所以主题改完要重启客户端。这一点在界面提示里也写明了——
   与其做个切一半的开关，不如把话说清楚。

**环境自检不在主线程跑。** 它要读 cookie 文件、探 ffmpeg 路径、必要时
启动子进程跑 ``ffmpeg -version``，放在主线程会把界面冻住——切到本页
点一下就卡住，用户看到的是程序死了而不是在忙。检测走
:mod:`.environment` + :class:`~..workers.Worker`，这里只负责把快照
填进控件。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import TYPE_CHECKING

from ... import __version__
from ...update import (
    ReleaseInfo,
    check_for_update,
    fetch_latest_release,
    hint_text,
    is_newer,
    mark_version_seen,
    open_release_page,
    should_notify,
)
from ..environment import EnvironmentSnapshot, probe_environment
from .. import layout, theme
from ..state import (
    AppUIState,
    THEME_MODE_LABELS,
    load_app_state,
    parse_theme_mode,
    save_app_state,
)
from ..workers import Worker
from ..widgets import (
    FormRow,
    KeyValueList,
    ScrollArea,
    SectionTitle,
    OutlinedButton,
)

__all__ = ["SettingsView"]

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查
    from ._host import AppHost

#: 下拉框选项顺序。跟随系统放第一个：它是默认值，也最不需要用户操心。
THEME_OPTIONS = [THEME_MODE_LABELS[key] for key in ("system", "light", "dark")]

#: 更新检查的结果。第二项是「到底查到了没有」——手动点的那一次要能
#: 区分「已是最新」和「网络不通」，否则断网时界面会说「已是最新」，
#: 那是骗人。自动检查不看这一项：它失败就该完全安静。
_UpdateResult = tuple[ReleaseInfo | None, bool]


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
        self._build()

    def _build(self) -> None:
        SectionTitle(
            self, "设置", "改动立即生效；主题一项需重启客户端。"
        ).grid(row=0, column=0, sticky="ew", pady=(0, theme.PAD_MD))

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
            "就写死了，改这里要**重启客户端**才生效。",
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

        # ③ 环境自检
        ttk.Label(env_box, text="环境自检", style="Heading.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, theme.PAD_SM)
        )
        self._env = KeyValueList(env_box, label_width=12)
        self._env.grid(row=1, column=0, sticky="w")

        # ④ 更新
        ttk.Label(update_box, text="更新", style="Heading.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, theme.PAD_SM)
        )
        row = FormRow(
            update_box,
            "检查更新",
            hint="每次启动自动查一次（24 小时内不重复请求）。有新版本才提示，"
            "没有就什么都不做。",
        )
        row.grid(row=1, column=0, sticky="ew", pady=(0, theme.PAD_SM))
        row.add(OutlinedButton, text="检查更新", command=self._on_check_update)
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

    def refresh(self) -> None:
        """切到本页时用当前设置回填表单，并异步跑一次环境自检。"""
        self._fill_form()
        self._start_env_probe()

    def _fill_form(self) -> None:
        """回填表单。纯内存操作，主线程做。"""
        ctx = self.app.ctx
        if not self._proxy_var.get():
            self._proxy_var.set(ctx.proxy or "")
        if not self._cookie_var.get():
            self._cookie_var.set(ctx.cookie_file)
        self._theme_var.set(THEME_MODE_LABELS[load_app_state().theme_mode])

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
        else:
            version = snapshot.ffmpeg_version
            if version and version != "未知":
                version = version.replace("ffmpeg version ", "").split(" ")[0]
            rows.append((f"ffmpeg（{info.source}）", version or "未知", "ok"))

        self._env.set_rows(rows)

    def _on_env_error(self, generation: int, exc: BaseException) -> None:
        """探测本身炸了要显示出来，不能让界面停在「检测中…」。"""
        if generation != self._probe_generation:
            return
        self._show_env_failed(str(exc))

    def _show_env_failed(self, reason: str) -> None:
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
        """
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

    def _show_update_failed(self, reason: str) -> None:
        self._update.set_rows(
            [
                ("当前版本", __version__, "idle"),
                ("最新版本", f"检查失败：{reason}", "idle"),
            ]
        )

    def _prompt_update(self, info: ReleaseInfo) -> None:
        """弹一次提示，问要不要去下载页。

        用 ``messagebox`` 而不是就地显示：启动自动检查时用户多半在别的页，
        就地显示一定会被错过。同一个版本只弹一次——记在
        ``mark_version_seen`` 里，不看用户选了什么：他看见了就够了。
        """
        if not should_notify(info.version):
            return
        title, body = hint_text(info)
        if messagebox.askyesno(title, f"{body}\n\n现在去下载页吗？", parent=self):
            open_release_page(info.url)
        mark_version_seen(info.version)
