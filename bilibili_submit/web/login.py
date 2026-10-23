"""扫码登录的会话状态。

为什么不能直接用 :func:`bilibili_submit.auth.login_interactive`：
那个函数内部 ``time.sleep`` 轮询 + ``print`` 到终端，是给命令行写的，
在 Web 请求里调它等于把整个进程卡三分钟。

所以这里只用它的两个**非阻塞原语**——``request_qrcode`` 和
``poll_qrcode``——轮询节奏交给浏览器（前端定时来问一次）。

.. important::
   **申请二维码和轮询必须共用同一个** ``requests.Session``。
   cookie 是 poll 时由服务端 ``Set-Cookie`` 下发的，存在 session 里；
   ``poll_qrcode`` 就是从这个 session 取 cookie 的（见 ``auth.py``）。
   每次请求新建 session 的话，登录成功也拿不到 cookie——
   界面会一直显示"等待扫码"，而服务端其实早就成了。

二维码有效期 180 秒，与 :func:`~bilibili_submit.auth.login_interactive`
的默认 timeout 一致。过期后前端会拿到 ``expired`` 并自己换新码。
"""

from __future__ import annotations

import threading
import time

import requests

from ..auth import (
    REQUIRED_COOKIES,
    poll_qrcode,
    request_qrcode,
    save_cookies,
)
from ..exceptions import BiliError

__all__ = ["LoginSession", "QR_TTL_SECONDS"]

#: 二维码有效期（秒）。服务端三分钟失效，提前 10 秒判定，
#: 免得前端刚好在失效瞬间来问，拿到一个马上就过期的码。
QR_TTL_SECONDS = 180 - 10


class LoginSession:
    """一次扫码登录。用完（成功或过期）调用方应当丢弃它。"""

    def __init__(self, cookie_file: str, proxy: str | None = None) -> None:
        from ..auth import new_session

        self.cookie_file = cookie_file
        self._session: requests.Session = new_session(proxy)
        self._lock = threading.Lock()
        self._key = ""
        self._url = ""
        self._created = 0.0
        self._state = "idle"  # idle / pending / scanned / done / expired / failed
        self._message = ""
        self._error = ""

    # ---------- 查询 ----------

    @property
    def state(self) -> str:
        return self._state

    @property
    def message(self) -> str:
        return self._message

    @property
    def error(self) -> str:
        return self._error

    @property
    def url(self) -> str:
        return self._url

    @property
    def expires_at(self) -> float:
        return self._created + QR_TTL_SECONDS if self._created else 0.0

    def status(self) -> dict[str, object]:
        """给前端的一次状态快照。"""
        return {
            "state": self._state,
            "message": self._message,
            "error": self._error,
            "url": self._url,
            "expires_at": self.expires_at,
        }

    def expired(self) -> bool:
        return self._created > 0 and time.time() > self.expires_at

    # ---------- 动作 ----------

    def start(self) -> dict[str, object]:
        """申请一个新的二维码。"""
        with self._lock:
            try:
                qr = request_qrcode(self._session)
            except BiliError as exc:
                self._state = "failed"
                self._error = str(exc)
                return self.status()
            self._key = qr.key
            self._url = qr.url
            self._created = time.time()
            self._state = "pending"
            self._message = "等待扫码"
            self._error = ""
            return self.status()

    def poll(self) -> dict[str, object]:
        """轮询一次。登录成功会把 cookie 落盘。

        落到 ``done`` / ``expired`` / ``failed`` 之后就是终态，
        再 poll 也只是把同一个结果重发一遍。
        """
        with self._lock:
            if self._state in ("done", "failed"):
                return self.status()
            if self._state == "idle":
                self._state = "failed"
                self._error = "还没有申请二维码"
                return self.status()
            if self._state == "expired":
                return self.status()
            if self.expired():
                self._state = "expired"
                self._message = "二维码已失效，请重新获取"
                return self.status()

            try:
                status = poll_qrcode(self._key, self._session)
            except BiliError as exc:
                self._state = "failed"
                self._error = str(exc)
                return self.status()

            if status.done:
                missing = [c for c in REQUIRED_COOKIES if not status.cookies.get(c)]
                if missing:
                    self._state = "failed"
                    self._error = f"登录返回缺少 cookie: {missing}，请重试"
                    return self.status()
                cookies = {c: status.cookies[c] for c in REQUIRED_COOKIES}
                try:
                    save_cookies(cookies, self.cookie_file)
                except OSError as exc:
                    self._state = "failed"
                    self._error = f"cookie 保存失败: {exc}"
                    return self.status()
                self._state = "done"
                self._message = "登录成功"
                return self.status()

            if status.expired:
                self._state = "expired"
                self._message = "二维码已失效，请重新获取"
                return self.status()

            self._state = "scanned" if status.scanned else "pending"
            self._message = status.describe()
            return self.status()
