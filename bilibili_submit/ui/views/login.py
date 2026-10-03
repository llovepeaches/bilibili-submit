"""登录视图：扫码登录。

CLI 的 :func:`~bilibili_submit.auth.login_interactive` 是**阻塞**的，
内部还直接 print，不适合 GUI。这里改为直接用两个原子 API
（``request_qrcode`` / ``poll_qrcode``）自己驱动轮询循环：

- 二维码画在 Canvas 上（不是终端字符画）
- 轮询在工作线程，状态变化回主线程更新界面
- 支持取消，不会把窗口卡死
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import ttk

import requests

from ...auth import (
    REQUIRED_COOKIES,
    ensure_buvid,
    poll_qrcode,
    request_qrcode,
    save_cookies,
)
from ...client import BiliClient
from ...exceptions import BiliError
from .. import theme
from ..qr import draw_placeholder, draw_qr
from ..widgets import Card, LogConsole, PrimaryButton, SecondaryButton, SectionTitle
from ..workers import Cancelled, Worker

__all__ = ["LoginView"]

POLL_INTERVAL = 2.0
DEFAULT_TIMEOUT = 180


class LoginView(ttk.Frame):
    """扫码登录页。"""

    def __init__(self, master: tk.Misc, app: "object") -> None:
        super().__init__(master, style="TFrame")
        self.app = app
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._worker = Worker(self)
        self._qr_url = ""

        self._build_header()
        self._build_body()

    # ---------- 布局 ----------

    def _build_header(self) -> None:
        SectionTitle(
            self,
            "扫码登录",
            "用手机 B 站 App 扫描下方二维码，扫完还要在手机上点「确认登录」。",
        ).grid(row=0, column=0, sticky="ew", pady=(0, theme.PAD_MD))

    def _build_body(self) -> None:
        card = Card(self)
        card.grid(row=1, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)

        # 二维码区
        qr_holder = ttk.Frame(card, style="Card.TFrame")
        qr_holder.grid(row=0, column=0, pady=(0, theme.PAD_MD))

        self._canvas = tk.Canvas(
            qr_holder,
            width=theme.QR_SIZE,
            height=theme.QR_SIZE,
            background=theme.SURFACE_ALT,
            highlightthickness=0,
        )
        self._canvas.pack()

        self._status = tk.Label(
            card,
            text="尚未获取二维码",
            font=theme.FONT_MEDIUM,
            background=theme.SURFACE,
            foreground=theme.TEXT_SECONDARY,
        )
        self._status.grid(row=1, column=0)

        # 按钮区
        buttons = ttk.Frame(card, style="Card.TFrame")
        buttons.grid(row=2, column=0, pady=theme.PAD_MD)

        self._start_button = PrimaryButton(buttons, "获取二维码", self._start)
        self._start_button.pack(side="left", padx=(0, theme.PAD_SM))

        self._cancel_button = SecondaryButton(buttons, "取消", self._cancel)
        self._cancel_button.pack(side="left")
        self._cancel_button.state(["disabled"])

        # 手动打开链接（终端/扫码不便时的退路）
        self._link = tk.Label(
            card,
            text="",
            font=theme.FONT_SMALL,
            background=theme.SURFACE,
            foreground=theme.PRIMARY_DARK,
            cursor="hand2",
            wraplength=520,
        )
        self._link.grid(row=3, column=0, pady=(0, theme.PAD_SM))
        self._link.bind("<Button-1>", self._copy_link)

        self._log = LogConsole(card, height=6)
        self._log.grid(row=4, column=0, sticky="ew")

        draw_placeholder(self._canvas, "点击「获取二维码」开始")

    # ---------- 行为 ----------

    def refresh(self) -> None:
        """切到本页时刷新登录态显示。"""
        if self.app.ctx.logged_in:
            self._status.configure(
                text="已登录，无需重复扫码（如需换账号可重新登录）",
                foreground=theme.SUCCESS,
            )

    def _start(self) -> None:
        if self._worker.running:
            return
        self._set_busy(True)
        self._log.clear()
        self._log.append("正在申请登录二维码…")
        self._worker.run(
            lambda report, is_cancelled: self._login_flow(report, is_cancelled),
            on_progress=self._log.append,
            on_done=self._on_done,
            on_error=self._on_error,
        )

    def _cancel(self) -> None:
        self._worker.cancel()
        self._log.append("已请求取消…")

    def _login_flow(self, report, is_cancelled) -> dict[str, str]:
        """完整登录流程（工作线程执行）。

        返回登录 cookie 字典。任何一步取消都抛 :class:`Cancelled`。
        """
        if is_cancelled():
            raise Cancelled()

        session = requests.Session()
        proxy = self.app.ctx.proxy
        if proxy:
            session.proxies.update({"http": proxy, "https": proxy})

        qr = request_qrcode(session)
        self._qr_url = qr.url
        # 二维码要立刻显示，不能等线程结束——用户得马上拿手机扫
        self.after(0, lambda: self._show_qr(qr.url))
        report("二维码已生成，请用手机 B 站 App 扫码")

        deadline = time.time() + DEFAULT_TIMEOUT
        last_code = -1
        while time.time() < deadline:
            if is_cancelled():
                raise Cancelled()

            status = poll_qrcode(qr.key, session)
            if status.code != last_code:
                last_code = status.code
                report(status.describe())

            if status.done:
                missing = [c for c in REQUIRED_COOKIES if not status.cookies.get(c)]
                if missing:
                    raise BiliError(f"登录返回缺少 cookie: {missing}，请重试")
                report("登录成功，正在补全设备指纹…")
                cookies = {c: status.cookies[c] for c in REQUIRED_COOKIES}
                client = BiliClient(cookies=cookies, proxy=proxy)
                cookies.update(ensure_buvid(client))
                return cookies
            if status.expired:
                raise BiliError("二维码已失效，请点击「获取二维码」重新生成")

            time.sleep(POLL_INTERVAL)

        raise BiliError(f"登录超时（{DEFAULT_TIMEOUT}s），请重试")

    def _show_qr(self, url: str) -> None:
        draw_qr(self._canvas, url)
        self._link.configure(text=f"扫码不便？点此复制链接：{url}")
        self._status.configure(text="等待扫码…", foreground=theme.TEXT_SECONDARY)

    def _copy_link(self, _event: "object" = None) -> None:
        if not self._qr_url:
            return
        self.clipboard_clear()
        self.clipboard_append(self._qr_url)
        self._log.append("登录链接已复制到剪贴板")

    def _on_done(self, cookies: dict[str, str]) -> None:
        path = save_cookies(cookies, self.app.ctx.cookie_file)
        self._set_busy(False)
        self._status.configure(text="登录成功", foreground=theme.SUCCESS)
        self._log.append(f"Cookie 已保存到 {path}（权限 0600，请勿外传）")
        self.app.refresh_status()

    def _on_error(self, exc: BaseException) -> None:
        self._set_busy(False)
        if isinstance(exc, Cancelled):
            self._status.configure(text="已取消", foreground=theme.TEXT_MUTED)
            return
        self._status.configure(text="登录失败", foreground=theme.DANGER)
        self._log.append(f"错误：{exc}")

    def _set_busy(self, busy: bool) -> None:
        self._start_button.state(["disabled"] if busy else ["!disabled"])
        self._cancel_button.state(["!disabled"] if busy else ["disabled"])
