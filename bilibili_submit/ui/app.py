"""GUI 主窗口：侧边导航 + 内容区 + 底部状态栏。

窗口结构::

    ┌──────────┬──────────────────────────┐
    │  导航    │                          │
    │  登录    │        内容区            │
    │  投稿    │   （各视图叠放切换）     │
    │  任务    │                          │
    │  历史    │                          │
    │  设置    │                          │
    ├──────────┴──────────────────────────┤
    │ 状态栏：登录态 · ffmpeg · 版本      │
    └─────────────────────────────────────┘

多页切换用「所有视图叠在同一 grid 单元格，切换时 tkraise」——
比销毁重建快，也能保住每个页已经填好的表单内容。
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from typing import Callable

from .. import __version__
from ..auth import (
    DEFAULT_COOKIE_FILE,
    build_client_from_cookies,
    cookies_valid,
    load_cookies,
)
from ..client import BiliClient
from . import layout, theme, win_effects
from .environment import EnvironmentSnapshot, probe_environment
from .state import load_app_state, resolve_theme_mode
from .widgets import BrandMark, NavRailItem
from .views import (
    HistoryView,
    LoginView,
    SettingsView,
    TasksView,
    UploadView,
)
from .workers import Worker, safe_after

logger = logging.getLogger(__name__)

__all__ = ["AppContext", "App", "launch"]


class AppContext:
    """各视图共享的状态。

    视图之间不直接互相引用，都通过 context 读 cookie 路径、代理，
    拿客户端——这样加新页不用改已有页。

    这里只有「当前设置」和「构造客户端」两件事。像「上次选了哪个
    视频文件夹」那种页面级状态不放这里：那是任务来源页自己的偏好，
    存 :mod:`.state`，别把 context 变成什么都往里塞的抽屉。
    """

    def __init__(self) -> None:
        self.cookie_file: str = DEFAULT_COOKIE_FILE
        self.proxy: str | None = None

    @property
    def logged_in(self) -> bool:
        return cookies_valid(load_cookies(self.cookie_file))

    def client(self, need_login: bool = True) -> BiliClient:
        """按当前设置构造客户端。未登录时抛 ``NotLoggedInError``。"""
        cookies = load_cookies(self.cookie_file)
        client = build_client_from_cookies(cookies, proxy=self.proxy)
        if need_login:
            client.require_login()
        return client


class App(ttk.Frame):
    """主界面框架。"""

    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, style="TFrame")
        self.master = master
        self.ctx = AppContext()

        #: 状态栏探测用。每次刷新一个新 Worker，配 ``_status_generation``
        #: 丢弃晚到的过期结果。理由见 :meth:`refresh_status`。
        self._status_worker: Worker[EnvironmentSnapshot] | None = None
        self._status_generation = 0

        #: 当前布局档位。``None`` 表示还没定过档，见 :meth:`_apply_layout`。
        self._tier: str | None = None
        self._last_height = 0
        #: 重入闸。改侧栏宽度会引发一串 ``<Configure>``，递归进来就死循环
        self._applying_layout = False

        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self._build_nav()
        self._build_content()
        self._build_statusbar()

        # 断点响应绑在**根窗口**上：Tk 的事件不冒泡，绑在 self 上收不到
        # root 的尺寸变化。放在所有视图建完之后——档位要分发给它们。
        self.master.bind("<Configure>", self._on_root_configure, add="+")
        # 首帧先定一次档：此时窗口可能还没映射（winfo_width() 是 1），
        # _apply_layout 会用 DEFAULT_WIDTH 兜住，不该按最窄档闪一帧。
        self._apply_layout(force=True)

        # 首次进入刷新一次登录态，之后由各视图在需要时调用
        safe_after(self, 100, self.refresh_status)
        # 更新检查压后：首屏绘制和 ffmpeg 探测都在抢这一两秒，
        # 跟它们挤在起跑线上只会让窗口看起来卡了一下。
        safe_after(self, 1500, self._auto_check_update)

    # ---------- 布局 ----------

    #: 导航项图标。
    #:
    #: **用单个汉字而不是符号**。试过 ``⚙``(U+2699)、``◷``(U+25F7) 这类
    #: Misc/Geometric 符号，Microsoft YaHei 对它们的字形覆盖不确定，
    #: Linux 上很容易渲染成方框；emoji 更不必说。汉字是 CJK 字体的
    #: 本体，100% 能显示，在中文界面里语义也直观。
    NAV_ICONS = {
        "登录": "登",
        "投稿": "投",
        "批量任务": "批",
        "历史": "历",
        "设置": "设",
    }

    def _build_nav(self) -> None:
        self._nav = ttk.Frame(self, style="Nav.TFrame", width=theme.NAV_WIDTH)
        nav = self._nav
        nav.grid(row=0, column=0, sticky="nsew")
        nav.grid_propagate(False)
        nav.columnconfigure(0, weight=1)
        nav.rowconfigure(99, weight=1)

        self._build_brand(nav)

        self._nav_buttons: dict[str, NavRailItem] = {}
        self._views_info: list[tuple[str, type]] = []
        entries = [
            ("登录", LoginView),
            ("投稿", UploadView),
            ("批量任务", TasksView),
            ("历史", HistoryView),
            ("设置", SettingsView),
        ]
        for index, (label, view_cls) in enumerate(entries, start=1):
            item = NavRailItem(
                nav,
                text=label,
                icon=self.NAV_ICONS.get(label, "●"),
                command=lambda key=label: self.show(key),
            )
            item.grid(
                row=index, column=0, sticky="ew",
                pady=(0, theme.NAV_ITEM_GAP),
            )
            self._nav_buttons[label] = item
            self._views_info.append((label, view_cls))

        # 底部版本号。折叠成图标栏时藏起来——56px 里塞不下版本号，
        # 而版本号在设置页也有，藏掉不丢信息。
        self._nav_version = ttk.Label(
            nav,
            text=f"v{__version__}",
            style="Nav.Secondary.TLabel",
        )
        self._nav_version.grid(
            row=100, column=0, sticky="sw", padx=theme.PAD_LG, pady=theme.PAD_MD
        )

    def _build_brand(self, nav: tk.Misc) -> None:
        """品牌区：主色方块 + 应用名。

        导航顶部有个视觉锚点，整块侧栏才不像一串裸按钮。
        """
        self._brand = ttk.Frame(nav, style="Nav.TFrame")
        brand = self._brand
        brand.grid(row=0, column=0, sticky="ew", pady=(theme.PAD_LG, theme.PAD_MD))
        brand.columnconfigure(1, weight=1)

        self._brand_mark = BrandMark(brand, "B")
        self._brand_mark.grid(
            row=0, column=0, padx=(theme.PAD_LG, theme.PAD_SM)
        )

        self._brand_text = ttk.Frame(brand, style="Nav.TFrame")
        self._brand_text.grid(row=0, column=1, sticky="w")
        tk.Label(
            self._brand_text, text="哔哩投稿", font=theme.font("body-strong"),
            background=theme.NAV_BG, foreground=theme.NAV_FG,
        ).pack(anchor="w")
        tk.Label(
            self._brand_text, text="自动投稿工具", font=theme.font("caption"),
            background=theme.NAV_BG, foreground=theme.INK_MUTED,
        ).pack(anchor="w")

    def _build_content(self) -> None:
        holder = ttk.Frame(self, style="TFrame")
        holder.grid(row=0, column=1, sticky="nsew", padx=theme.PAD_MD, pady=theme.PAD_MD)
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)

        self._views: dict[str, ttk.Frame] = {}
        for label, view_cls in self._views_info:
            view = view_cls(holder, self)
            view.grid(row=0, column=0, sticky="nsew")
            self._views[label] = view

        self.show("登录")

    def _build_statusbar(self) -> None:
        # 墨色条：和底部 ActionBar 同一套配色。状态栏和操作条一深一浅地
        # 夹住内容区，视线自然被上下两条边「收」住——这是用底色分层
        # 代替阴影的又一处应用。
        bar = ttk.Frame(self, style="Ink.TFrame")
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        bar.columnconfigure(0, weight=1)

        # 顶部分隔线：状态栏和上方内容区要有明确分界
        ttk.Separator(bar, orient="horizontal").grid(
            row=0, column=0, columnspan=3, sticky="ew"
        )

        cells = ttk.Frame(bar, style="Ink.TFrame")
        cells.grid(row=1, column=0, columnspan=3, sticky="ew")
        cells.columnconfigure(1, weight=1)

        self._status_login = tk.Label(
            cells, text="", font=theme.font("caption"), background=theme.INK_SURFACE,
            foreground=theme.INK_SURFACE_FG, anchor="w",
        )
        self._status_login.grid(row=0, column=0, sticky="w", padx=theme.PAD_MD, pady=theme.PAD_SM)

        # 中间段留给任务进度，由批量任务页通过 set_task_progress 写入
        self._status_task = tk.Label(
            cells, text="", font=theme.font("caption"), background=theme.INK_SURFACE,
            foreground=theme.INK_SURFACE_MUTED, anchor="center",
        )
        self._status_task.grid(row=0, column=1, sticky="ew")

        self._status_ffmpeg = tk.Label(
            cells, text="", font=theme.font("caption"), background=theme.INK_SURFACE,
            foreground=theme.INK_SURFACE_MUTED, anchor="e",
        )
        self._status_ffmpeg.grid(row=0, column=2, sticky="e", padx=theme.PAD_MD)

    # ---------- 响应式 ----------

    def _on_root_configure(self, event: tk.Event) -> None:
        """窗口尺寸变了就重新定档。

        四道闸，缺一道拖窗口时界面就会抖成一团：

        1. ``<Configure>`` 绑在 root 上，但 Tk 在**子控件**几何变化时
           也会往上层派发——只认 root 自己那一份。
        2. 窗口还没映射时 ``winfo_width()`` 是 1，拿它判档会先按最窄档
           布一帧再跳档，用户看得见闪一下。
        3. 档位没变、高度变化也不到四格时不动手——和
           ``TasksView._resize_columns`` 同样的道理，无脑重排会抖。
        4. 重入闸：改侧栏宽度本身就会引发一串 Configure。
        """
        if event.widget is not self.master or self._applying_layout:
            return
        width = self.master.winfo_width()
        if width < 100:
            return
        height = self.master.winfo_height()
        if (
            layout.tier_for(width) == self._tier
            and abs(height - self._last_height) < theme.GRID * 4
        ):
            return
        self._apply_layout()

    def _apply_layout(self, force: bool = False) -> None:
        """按当前窗口宽度定档，并把档位分给各视图。

        .. important::
           这里**只做几何**。视图的 ``apply_layout`` 里不许刷数据
           （扫描目录、环境探测、更新检查）——拖一次窗口就重扫一遍
           目录是这类钩子最容易犯的错。``App.show()`` 本来就会调
           ``view.refresh()``，这里不需要再踢一脚。
        """
        width = self.master.winfo_width()
        spec = layout.layout_for(width if width >= 100 else theme.DEFAULT_WIDTH)
        if not force and spec.tier == self._tier:
            return
        self._tier = spec.tier
        self._last_height = self.master.winfo_height()
        self._applying_layout = True
        try:
            self._apply_nav(spec)
            for view in self._views.values():
                # 可选协议：视图不实现就跳过。和 _auto_check_update 一样
                # 用 getattr 而不是抽象基类——加一个新页不该被迫实现
                # 它用不上的钩子。
                hook = getattr(view, "apply_layout", None)
                if hook is not None:
                    hook(spec)
        finally:
            self._applying_layout = False

    def _apply_nav(self, spec: "layout.Layout") -> None:
        """侧栏折叠：改宽度 + 导航项只留图标 + 藏掉品牌名与版本号。

        宽度直接 ``configure``：``_build_nav`` 里已经开了
        ``grid_propagate(False)``，否则改了也会被内容顶回去。

        折叠时品牌区只剩 32px 的 ``BrandMark``，靠把第 0 列的权重给满
        让它居中——比手算 padx 稳（图标宽度会随字体变）。
        """
        self._nav.configure(width=spec.nav_width)
        for item in self._nav_buttons.values():
            item.set_collapsed(spec.nav_collapsed)

        if spec.nav_collapsed:
            self._brand_text.grid_remove()
            self._nav_version.grid_remove()
            self._brand.columnconfigure(0, weight=1)
            self._brand.columnconfigure(1, weight=0)
            self._brand_mark.grid_configure(padx=0)
        else:
            self._brand_text.grid()
            self._nav_version.grid()
            self._brand.columnconfigure(0, weight=0)
            self._brand.columnconfigure(1, weight=1)
            self._brand_mark.grid_configure(padx=(theme.PAD_LG, theme.PAD_SM))

    def set_task_progress(self, text: str) -> None:
        """在状态栏中段显示任务进度，供批量任务页调用。

        执行任务时用户可能切到别的页看历史，状态栏是唯一一直可见的
        地方——把进度放这里，不用切回来也知道跑到第几个了。
        """
        self._status_task.configure(text=text)

    # ---------- 行为 ----------

    def show(self, key: str) -> None:
        """切换到指定视图，并刷新其数据。"""
        view = self._views.get(key)
        if view is None:
            return
        view.tkraise()
        for label, item in self._nav_buttons.items():
            item.set_active(label == key)

        refresh: Callable[[], None] | None = getattr(view, "refresh", None)
        if refresh:
            refresh()

    def _auto_check_update(self) -> None:
        """启动时静默查一次更新。

        动作本身在设置页里（它持有弹窗和限频），这里只负责踢一脚——
        用 ``getattr`` 而不是直接调用，是因为更新检查对界面是可选的：
        哪天设置页不想要这个入口了，删掉方法即可，主窗口不用跟着改。
        """
        view = self._views.get("设置")
        runner = getattr(view, "auto_check_update", None)
        if runner is not None:
            runner()

    def refresh_status(self) -> None:
        """刷新状态栏：登录态与 ffmpeg。

        **探测在后台跑**，这里只负责先摆个「检测中」再排回主线程更新。
        它有 4 个调用方（启动、换 cookie 路径、登录成功后、批量页
        发现登录态失效），每个都在主线程——同步探测的话，ffmpeg 定位
        慢一点就会让整窗卡住，而登录页刚扫码完那一下最需要界面活着。

        签名和返回值都不变，调用方不用等检测结果。

        .. important::
           不要用「已有探测在跑就 return」来防重入。用户刚换完 cookie
           路径就又点了刷新，第二次探测必须能发出去，否则界面会永远
           停在**上一个路径**的登录态上——而用户恰恰是在确认新路径
           有没有登录成功。
        """
        self._status_generation += 1
        generation = self._status_generation
        ctx = self.ctx

        login_fg, _bg = theme.tone("busy")
        self._status_login.configure(text="◌ 登录态检测中…", foreground=login_fg)
        self._status_ffmpeg.configure(text="◌ ffmpeg 检测中…", foreground=login_fg)

        worker: Worker[EnvironmentSnapshot] = Worker(self)
        self._status_worker = worker
        try:
            worker.run(
                # 状态栏只要知道 ffmpeg 在不在，不需要版本号——
                # 读版本要启动子进程，那是最贵的一步
                lambda _report, _cancelled: probe_environment(
                    ctx.cookie_file, include_ffmpeg_version=False
                ),
                on_done=lambda snap: self._apply_status(generation, snap),
                on_error=lambda exc: self._apply_status_failed(generation, exc),
            )
        except RuntimeError as exc:
            self._apply_status_failed(generation, str(exc))

    def _apply_status(
        self, generation: int, snapshot: EnvironmentSnapshot
    ) -> None:
        # 过期结果丢弃：晚到的属于上一次刷新的 cookie 路径
        if generation != self._status_generation:
            return

        # 墨底上要用亮色版状态色（theme.tone 的深色前景压上来会糊）
        fg = theme.tone_on_ink("ok" if snapshot.logged_in else "idle")
        if snapshot.login_error:
            self._status_login.configure(
                text="○ 未登录（cookie 读取失败）", foreground=fg
            )
        else:
            self._status_login.configure(
                text="● 已登录" if snapshot.logged_in else "○ 未登录",
                foreground=fg,
            )

        info = snapshot.ffmpeg
        if info is None:
            self._status_ffmpeg.configure(
                text="ffmpeg 未找到（不影响投稿，仅自动抽帧不可用）",
                foreground=theme.tone_on_ink("warn"),
            )
        else:
            self._status_ffmpeg.configure(
                text=f"ffmpeg 就绪（{info.source}）",
                foreground=theme.INK_SURFACE_MUTED,
            )

    def _apply_status_failed(self, generation: int, reason: str) -> None:
        """探测炸了要显示出来。状态栏不能永远停在「检测中…」。"""
        if generation != self._status_generation:
            return
        idle_fg = theme.tone_on_ink("idle")
        self._status_login.configure(text="○ 登录态未知", foreground=idle_fg)
        self._status_ffmpeg.configure(
            text=f"环境检测失败：{reason}", foreground=idle_fg
        )


def _set_dpi_aware() -> None:
    """Windows 高分屏下让文字保持清晰。

    不设的话系统会按 96 DPI 渲染再拉伸，界面发虚。
    必须在创建 Tk 根窗口之前调用，之后调用无效。
    """
    try:
        from ctypes import windll  # type: ignore[attr-defined]

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001 - 非 Windows 或已设置过，忽略
        pass


def _set_window_icon(root: tk.Tk) -> None:
    """给窗口设图标，找不到就跳过（图标缺失不该阻止启动）。"""
    from ..ffmpeg import app_dir

    for candidate in (
        app_dir() / f"{'bilibili-submit'}.ico",
        Path(__file__).resolve().parent.parent.parent / "assets" / "bilibili-submit.ico",
    ):
        if candidate.is_file():
            try:
                root.iconbitmap(str(candidate))
                return
            except tk.TclError:
                continue


def launch() -> int:
    """启动 GUI，返回进程退出码（正常关闭为 0）。

    Raises:
        RuntimeError: 环境缺少 tkinter（精简版 Python 常见）。
    """
    try:
        import tkinter  # noqa: F401  # pylint: disable=unused-import
        # 只是确认可用，实际 import 在模块顶部
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "当前 Python 未附带 tkinter，无法启动界面。\n"
            "Windows/macOS 官方安装包默认带；Linux 需装 python3-tk。"
        ) from exc

    _set_dpi_aware()

    root = tk.Tk()
    root.title(f"哔哩哔哩自动投稿程序 {__version__}")
    root.geometry(f"{theme.DEFAULT_WIDTH}x{theme.DEFAULT_HEIGHT}")
    root.minsize(theme.MIN_WIDTH, theme.MIN_HEIGHT)
    # 窗口底色跟着主题走，避免默认灰和导航栏/内容区接不上
    root.configure(background=theme.BG)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")  # clam 在各平台上观感一致，且允许自定义配色
    except tk.TclError:
        pass  # 没有该主题就用默认

    # 主题必须在建界面之前定下来：tk/ttk 把颜色写进控件，之后改色板
    # 不会影响已建好的控件，所以切换主题要重启（设置页里也这么写的）
    mode = resolve_theme_mode(load_app_state().theme_mode, win_effects.system_prefers_light())
    theme.apply(style, mode)
    _configure_nav_style(style)

    _set_window_icon(root)

    app = App(root)
    app.pack(fill="both", expand=True)

    # 窗口居中
    root.update_idletasks()
    _center(root)

    # 系统级效果要等窗口真正创建完（拿得到 HWND）才能设
    win_effects.apply_window_effects(root, dark=mode == "dark")

    root.mainloop()
    return 0


def _center(root: tk.Tk) -> None:
    """把窗口挪到屏幕中央。"""
    root.update_idletasks()
    width = root.winfo_width()
    height = root.winfo_height()
    x = (root.winfo_screenwidth() // 2) - (width // 2)
    y = (root.winfo_screenheight() // 3) - (height // 2)
    root.geometry(f"+{max(0, x)}+{max(0, y)}")


def _configure_nav_style(style: ttk.Style) -> None:
    """导航按钮样式：只补一个缺省 state 的边框色。

    其余（底色、前景、悬停、选中）都在 :func:`~.theme.apply` 里统一配了，
    这里重复配一遍迟早会两边不一致。
    """
    style.configure("Nav.TButton", bordercolor=theme.NAV_BG)
