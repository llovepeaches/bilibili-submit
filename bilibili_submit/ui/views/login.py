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
from typing import TYPE_CHECKING

from ...auth import (
    REQUIRED_COOKIES,
    ensure_buvid,
    new_session,
    poll_qrcode,
    request_qrcode,
    save_cookies,
)
from ...client import BiliClient
from ...exceptions import BiliError
from .. import theme
from ..qr import draw_placeholder, draw_qr
from ..widgets import Card, LogConsole, FilledButton, OutlinedButton, PageHeader
from ..workers import Cancelled, Worker

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查
    from ._host import AppHost

__all__ = ["LoginView"]

POLL_INTERVAL = 2.0
DEFAULT_TIMEOUT = 180


class _PendingQrCode:
    """内部哨兵，借进度通道把「二维码已就绪」送回主线程。

    :attr:`~..workers.Worker.run` 的 ``report`` 接受 ``str | Event``，
    但二维码 URL 得等拿到了才知道，没法预先拼进日志文案。这里用哨兵
    对象当信号，主线程收到后再从 :attr:`LoginView._qr_url` 取真正的 URL。
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return "<pending-qr>"


class LoginView(ttk.Frame):
    """扫码登录页。"""

    #: 见 :class:`_PendingQrCode`
    _PENDING_QR = _PendingQrCode()

    def __init__(self, master: tk.Misc, app: "AppHost") -> None:
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
        PageHeader(
            self,
            "扫码登录",
            "用手机 B 站 App 扫描下方二维码，扫完还要在手机上点「确认登录」。",
        ).grid(row=0, column=0, sticky="ew", pady=(0, theme.PAD_LG))

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
            background=theme.PAPER_ALT,
            highlightthickness=0,
        )
        self._canvas.pack()

        self._status = tk.Label(
            card,
            text="尚未获取二维码",
            font=theme.font("subtitle"),
            background=theme.PAPER,
            foreground=theme.INK_SECOND,
        )
        self._status.grid(row=1, column=0)

        # 按钮区
        buttons = ttk.Frame(card, style="Card.TFrame")
        buttons.grid(row=2, column=0, pady=theme.PAD_MD)

        self._start_button = FilledButton(buttons, "获取二维码", self._start)
        self._start_button.pack(side="left", padx=(0, theme.PAD_SM))

        self._cancel_button = OutlinedButton(buttons, "取消", self._cancel)
        self._cancel_button.pack(side="left")
        self._cancel_button.state(["disabled"])

        # 手动打开链接（终端/扫码不便时的退路）
        self._link = tk.Label(
            card,
            text="",
            font=theme.font("caption"),
            background=theme.PAPER,
            foreground=theme.PINK_DEEP,
            cursor="hand2",
            wraplength=520,
        )
        self._link.grid(row=3, column=0, pady=(0, theme.PAD_SM))
        self._link.bind("<Button-1>", self._copy_link)

        self._log = LogConsole(card, height=6)
        self._log.grid(row=4, column=0, sticky="ew")
        # 预先写一行：空的日志框在浅色界面上是一大块无意义的色块，
        # 看着像界面坏了。一句话说明「这里会出现什么」就够了——
        # 用户关心的是流程输出，不是具体每一步的技术细节
        self._log.append(
            "点「获取二维码」后，这里会显示登录流程的每一步："
            "申请二维码 → 等待扫码 → 手机确认 → 保存 cookie"
        )

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
            # 二维码要立刻显示，不能等线程结束——用户得马上拿手机扫。
            # 走 on_progress 通道回主线程，不在工作线程里直接调 after。
            on_progress=self._on_progress,
            on_done=self._on_done,
            on_error=self._on_error,
        )

    def _cancel(self) -> None:
        self._worker.cancel()
        self._log.append("已请求取消…")

    def _on_progress(self, message: str) -> None:
        """处理工作线程上报的进度。

        二维码本身不是文字进度，用一个内部的哨兵对象搭桥：
        ``_qr_url`` 借 :attr:`_PENDING_QR` 这个标记传回来，
        这样就不用在 worker 线程里直接操作 Tk 控件。
        """
        if message is self._PENDING_QR:
            self._show_qr(self._qr_url)
            return
        self._log.append(message)

    def _login_flow(self, report, is_cancelled) -> dict[str, str]:
        """完整登录流程（工作线程执行）。

        返回登录 cookie 字典。任何一步取消都抛 :class:`Cancelled`。
        """
        if is_cancelled():
            raise Cancelled()

        # 必须用 new_session()：它的 UA 伪装成桌面浏览器。
        # 裸 requests.Session() 的 UA 是 python-requests/x.y.z，
        # 会被 B 站风控返回 HTTP 412 的 HTML 页，表现为看不懂的 JSONDecodeError。
        session = new_session(self.app.ctx.proxy)

        qr = request_qrcode(session)
        self._qr_url = qr.url
        report(self._PENDING_QR)
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
                client = BiliClient(cookies=cookies, proxy=self.app.ctx.proxy)
                cookies.update(ensure_buvid(client))
                return cookies
            if status.expired:
                raise BiliError("二维码已失效，请点击「获取二维码」重新生成")

            time.sleep(POLL_INTERVAL)

        raise BiliError(f"登录超时（{DEFAULT_TIMEOUT}s），请重试")

    def _show_qr(self, url: str) -> None:
        draw_qr(self._canvas, url)
        self._link.configure(text=f"扫码不便？点此复制链接：{url}")
        self._status.configure(text="等待扫码…", foreground=theme.INK_SECOND)

    def _copy_link(self, _event: object = None) -> None:
        if not self._qr_url:
            return
        self.clipboard_clear()
        self.clipboard_append(self._qr_url)
        self._log.append("登录链接已复制到剪贴板")

    def _on_done(self, cookies: dict[str, str]) -> None:
        # **先解锁再干活**。save_cookies 会 makedirs/open/os.replace/os.chmod，
        # 路径不可写时抛 OSError；这个回调是 Worker 排进 Tk 主循环的，
        # 抛出去没人接——按钮就永久停在 disabled，用户看着「登录成功」
        # 却既不能重试也不能取消，只能重启进程。
        # _on_error 也是先 _set_busy(False) 同理，两边顺序必须一致。
        self._set_busy(False)
        try:
            path = save_cookies(cookies, self.app.ctx.cookie_file)
        except OSError as exc:
            self._status.configure(text="登录成功，但保存失败", foreground=theme.WARNING)
            self._log.append(f"错误：cookie 写入 {self.app.ctx.cookie_file} 失败：{exc}")
            self._log.append("登录态在本次进程内有效，但重启后需要重新扫码")
            return
        self._status.configure(text="登录成功", foreground=theme.SUCCESS)
        self._log.append(f"Cookie 已保存到 {path}（权限 0600，请勿外传）")
        self._refresh_app_status()

    def _refresh_app_status(self) -> None:
        """刷新状态栏登录态。

        ``refresh_status`` 会去读 cookie 文件，文件损坏时
        ``AppContext.logged_in`` 会抛 ``BiliError``。登录刚成功时抛这个
        就很荒唐了，所以这里兜住——登录态的显示更新不值得让整个回调炸掉。
        """
        try:
            self.app.refresh_status()
        except BiliError as exc:
            self._log.append(f"状态栏刷新失败：{exc}")

    def _on_error(self, exc: BaseException) -> None:
        self._set_busy(False)
        if isinstance(exc, Cancelled):
            self._status.configure(text="已取消", foreground=theme.INK_MUTED)
            return
        # 二维码没出来是最常见的故障，而日志区在卡片最下方，
        # 用户未必会往下看。状态标签紧挨着二维码，必须在这里给出可读原因。
        self._status.configure(text="获取二维码失败", foreground=theme.DANGER)
        # BiliError 会把 message + hint 一起拼进 str(exc)，
        # 所以 hint 已经在错误行里了，别再单独打一遍「建议」。
        self._log.append(f"错误：{exc}")

    def _set_busy(self, busy: bool) -> None:
        self._start_button.state(["disabled"] if busy else ["!disabled"])
        self._cancel_button.state(["!disabled"] if busy else ["disabled"])
