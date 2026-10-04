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
from tkinter import filedialog, ttk

from ..environment import EnvironmentSnapshot, probe_environment
from .. import theme
from ..state import (
    AppUIState,
    THEME_MODE_LABELS,
    load_app_state,
    parse_theme_mode,
    save_app_state,
)
from ..workers import Worker
from ..widgets import (
    Card,
    FormRow,
    KeyValueList,
    SectionTitle,
    SecondaryButton,
)

__all__ = ["SettingsView"]

#: 下拉框选项顺序。跟随系统放第一个：它是默认值，也最不需要用户操心。
THEME_OPTIONS = [THEME_MODE_LABELS[key] for key in ("system", "light", "dark")]


class SettingsView(ttk.Frame):
    """设置页。"""

    def __init__(self, master: tk.Misc, app: "object") -> None:
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
        self._build()

    def _build(self) -> None:
        SectionTitle(
            self, "设置", "改动立即生效；主题一项需重启客户端。"
        ).grid(row=0, column=0, sticky="ew", pady=(0, theme.PAD_MD))

        card = Card(self)
        card.grid(row=1, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)

        # 代理
        self._proxy_var = tk.StringVar()
        row = FormRow(
            card,
            "代理",
            hint="例如 http://127.0.0.1:7890；境外网络或直连被风控时填。留空表示不走代理。",
        )
        row.grid(row=0, column=0, sticky="ew", pady=(0, theme.PAD_SM))
        row.add(ttk.Entry, textvariable=self._proxy_var)
        self._proxy_var.trace_add("write", self._on_proxy_change)

        # cookie 文件
        self._cookie_var = tk.StringVar()
        row = FormRow(
            card, "Cookie 文件", hint="含 SESSDATA，等同于账号凭据，请勿外传。"
        )
        row.grid(row=1, column=0, sticky="ew", pady=(0, theme.PAD_SM))
        row.add(ttk.Entry, textvariable=self._cookie_var, padx=(0, theme.PAD_SM))
        row.add(
            SecondaryButton, text="浏览…", command=self._pick_cookie, column=1, sticky="w"
        )

        ttk.Separator(card, orient="horizontal").grid(
            row=2, column=0, sticky="ew", pady=theme.PAD_MD
        )

        # 外观：主题模式
        ttk.Label(card, text="外观", style="Heading.TLabel").grid(
            row=3, column=0, sticky="w", pady=(0, theme.PAD_SM)
        )
        self._theme_var = tk.StringVar(value=THEME_OPTIONS[0])
        row = FormRow(
            card,
            "主题",
            hint="跟随系统即读 Windows 的浅色/深色设置。tk 控件的颜色在建界面时"
            "就写死了，改这里要**重启客户端**才生效。",
        )
        row.grid(row=4, column=0, sticky="ew", pady=(0, theme.PAD_SM))
        row.add(
            ttk.Combobox,
            textvariable=self._theme_var,
            values=THEME_OPTIONS,
            state="readonly",
            width=12,
        )
        self._theme_var.trace_add("write", self._on_theme_change)

        ttk.Separator(card, orient="horizontal").grid(
            row=5, column=0, sticky="ew", pady=theme.PAD_MD
        )

        # 环境自检
        ttk.Label(card, text="环境自检", style="Heading.TLabel").grid(
            row=6, column=0, sticky="w", pady=(0, theme.PAD_SM)
        )
        self._env = KeyValueList(card, label_width=12)
        self._env.grid(row=7, column=0, sticky="w")

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

    def _on_proxy_change(self, *_: "object") -> None:
        value = self._proxy_var.get().strip()
        self.app.ctx.proxy = value or None

    def _on_theme_change(self, *_: "object") -> None:
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
