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
from ..ffmpeg import ffmpeg_status
from . import theme
from .views import (
    HistoryView,
    LoginView,
    SettingsView,
    TasksView,
    UploadView,
)
from .workers import safe_after

logger = logging.getLogger(__name__)

__all__ = ["AppContext", "App", "launch"]


class AppContext:
    """各视图共享的状态。

    视图之间不直接互相引用，都通过 context 读配置路径、拿客户端——
    这样加新页不用改已有页。
    """

    def __init__(self) -> None:
        self.config_path: str = ""
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

        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self._build_nav()
        self._build_content()
        self._build_statusbar()

        # 首次进入刷新一次登录态，之后由各视图在需要时调用
        safe_after(self, 100, self.refresh_status)

    # ---------- 布局 ----------

    def _build_nav(self) -> None:
        nav = ttk.Frame(self, style="Surface.TFrame", width=theme.NAV_WIDTH)
        nav.grid(row=0, column=0, sticky="nsew")
        nav.grid_propagate(False)
        nav.rowconfigure(99, weight=1)

        ttk.Label(
            nav, text="B 站投稿", style="Title.TLabel", padding=(theme.PAD_MD, theme.PAD_LG)
        ).grid(row=0, column=0, sticky="w")

        self._nav_buttons: dict[str, ttk.Button] = {}
        self._views_info: list[tuple[str, type]] = []
        entries = [
            ("登录", LoginView),
            ("投稿", UploadView),
            ("批量任务", TasksView),
            ("历史", HistoryView),
            ("设置", SettingsView),
        ]
        for index, (label, view_cls) in enumerate(entries, start=1):
            button = ttk.Button(
                nav,
                text=label,
                style="Nav.TButton",
                command=lambda key=label: self.show(key),
            )
            button.grid(row=index, column=0, sticky="ew", padx=theme.PAD_SM, pady=2)
            self._nav_buttons[label] = button
            self._views_info.append((label, view_cls))

        # 底部版本号
        ttk.Label(
            nav,
            text=f"v{__version__}",
            style="Secondary.TLabel",
        ).grid(row=100, column=0, sticky="sw", padx=theme.PAD_MD, pady=theme.PAD_MD)

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
        bar = ttk.Frame(self, style="Surface.TFrame")
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        bar.columnconfigure(0, weight=1)

        self._status_login = tk.Label(
            bar, text="", font=theme.FONT_SMALL, background=theme.SURFACE,
            foreground=theme.TEXT_SECONDARY, anchor="w",
        )
        self._status_login.grid(row=0, column=0, sticky="w", padx=theme.PAD_MD, pady=theme.PAD_SM)

        self._status_ffmpeg = tk.Label(
            bar, text="", font=theme.FONT_SMALL, background=theme.SURFACE,
            foreground=theme.TEXT_SECONDARY, anchor="e",
        )
        self._status_ffmpeg.grid(row=0, column=1, sticky="e", padx=theme.PAD_MD)

    # ---------- 行为 ----------

    def show(self, key: str) -> None:
        """切换到指定视图，并刷新其数据。"""
        view = self._views.get(key)
        if view is None:
            return
        view.tkraise()
        for label, button in self._nav_buttons.items():
            button.state(["selected"] if label == key else ["!selected"])

        refresh: Callable[[], None] | None = getattr(view, "refresh", None)
        if refresh:
            refresh()

    def refresh_status(self) -> None:
        """刷新状态栏：登录态与 ffmpeg。"""
        if self.ctx.logged_in:
            self._status_login.configure(text="● 已登录", foreground=theme.SUCCESS)
        else:
            self._status_login.configure(text="○ 未登录", foreground=theme.TEXT_MUTED)

        info = ffmpeg_status()
        if info is None:
            self._status_ffmpeg.configure(
                text="ffmpeg 未找到（不影响投稿，仅自动抽帧不可用）",
                foreground=theme.WARNING,
            )
        else:
            self._status_ffmpeg.configure(
                text=f"ffmpeg 就绪（{info.source}）", foreground=theme.TEXT_SECONDARY
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
        import tkinter  # noqa: F401  # 只是确认可用，实际 import 在模块顶部
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

    style = ttk.Style(root)
    try:
        style.theme_use("clam")  # clam 在各平台上观感一致，且允许自定义配色
    except tk.TclError:
        pass  # 没有该主题就用默认
    theme.apply(style)
    _configure_nav_style(style)

    _set_window_icon(root)

    app = App(root)
    app.pack(fill="both", expand=True)

    # 窗口居中
    root.update_idletasks()
    _center(root)

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
    """导航按钮样式：选中时加粉色底和左侧竖条。"""
    style.configure(
        "Nav.TButton",
        background=theme.SURFACE,
        foreground=theme.TEXT,
        borderwidth=0,
        relief="flat",
        anchor="w",
        padding=(theme.PAD_LG, theme.PAD_SM + 2),
        font=theme.FONT_MEDIUM,
    )
    style.map(
        "Nav.TButton",
        background=[("selected", theme.PRIMARY_SOFT), ("active", theme.SURFACE_ALT)],
        foreground=[("selected", theme.PRIMARY_DARK)],
    )
