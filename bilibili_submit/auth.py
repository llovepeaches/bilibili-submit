"""扫码登录与 Cookie 持久化。

登录流程（Web 端二维码）：
    1. ``GET  /x/passport-login/web/qrcode/generate`` → qrcode_key + 登录 url
    2. 终端打印二维码，用户用手机 B 站 App 扫码并**确认**
    3. ``GET  /x/passport-login/web/qrcode/poll`` 轮询，直到拿到 SESSDATA

注意 poll 是**双层错误码**：外层 HTTP 响应始终 ``code=0``，真实状态在
``data.code``（0 成功 / 86038 二维码失效 / 86090 已扫码待确认 / 86101 未扫码）。
"""

from __future__ import annotations

import json
import logging
import os
import stat
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Mapping

import requests

from .client import USER_AGENT, BiliClient
from .exceptions import ApiChangedError, BiliError, NetworkError, NotLoggedInError

logger = logging.getLogger(__name__)

PASSPORT_BASE = "https://passport.bilibili.com"
QRCODE_GENERATE = f"{PASSPORT_BASE}/x/passport-login/web/qrcode/generate"
QRCODE_POLL = f"{PASSPORT_BASE}/x/passport-login/web/qrcode/poll"
SPI_URL = "https://api.bilibili.com/x/frontend/finger/spi"

DEFAULT_COOKIE_FILE = os.path.expanduser(
    "~/.config/bilibili_submit/cookie.json"
)

#: 登录成功所需的 cookie
REQUIRED_COOKIES = ("SESSDATA", "bili_jct", "DedeUserID")

__all__ = [
    "LoginStatus",
    "QrCode",
    "login_interactive",
    "poll_qrcode",
    "request_qrcode",
    "save_cookies",
    "load_cookies",
    "cookies_valid",
    "ensure_buvid",
    "build_client_from_cookies",
    "new_session",
    "DEFAULT_COOKIE_FILE",
]

# poll 返回的 data.code
QR_SUCCESS = 0
QR_EXPIRED = 86038
QR_SCANNED = 86090
QR_NOT_SCANNED = 86101


@dataclass
class QrCode:
    """一次二维码登录会话。"""

    key: str
    url: str


@dataclass
class LoginStatus:
    """poll 的一次结果。"""

    code: int
    message: str
    cookies: dict[str, str]

    @property
    def done(self) -> bool:
        return self.code == QR_SUCCESS

    @property
    def expired(self) -> bool:
        return self.code == QR_EXPIRED

    @property
    def scanned(self) -> bool:
        """已扫码但尚未在手机上点确认。"""
        return self.code == QR_SCANNED

    @property
    def pending(self) -> bool:
        return self.code == QR_NOT_SCANNED

    def describe(self) -> str:
        return {
            QR_SUCCESS: "登录成功",
            QR_EXPIRED: "二维码已失效",
            QR_SCANNED: "已扫码，请在手机上点「确认登录」",
            QR_NOT_SCANNED: "等待扫码",
        }.get(self.code, f"未知状态 {self.code}: {self.message}")


# ---------- 二维码 ----------


def _json_or_raise(resp: requests.Response, action: str) -> dict[str, Any]:
    """解析 JSON 响应，把非 JSON 的响应翻译成可操作的异常。

    B 站风控返回的是 **HTML 页面**而非 JSON（HTTP 412）。直接 ``.json()``
    会抛 ``JSONDecodeError: Expecting value``，用户完全看不出是风控。
    这里按状态码给出对应的排查建议。
    """
    if resp.status_code != 200:
        hint = {
            412: "被 B 站风控拦截。程序已带浏览器 UA，若仍失败请在「设置」页配置代理或换网络",
            403: "请求被拒绝，通常是访问过于频繁，稍后重试",
        }.get(resp.status_code, "请检查网络连通性与代理设置")
        raise NetworkError(
            f"{action}失败: HTTP {resp.status_code}",
            code=resp.status_code,
            hint=hint,
        )
    try:
        payload = resp.json()
    except ValueError as exc:
        raise ApiChangedError(
            f"{action}失败: 返回的不是 JSON（{exc}）",
            hint="通常是风控拦截或接口变更；程序已带浏览器 UA，可尝试配置代理后重试",
        ) from exc
    if not isinstance(payload, dict):
        raise ApiChangedError(
            f"{action}失败: 返回的 JSON 结构异常（顶层是 "
            f"{type(payload).__name__}，应为对象）",
        )
    return payload


def request_qrcode(session: requests.Session | None = None) -> QrCode:
    """申请一个登录二维码。"""
    sess = session or new_session()
    resp = sess.get(QRCODE_GENERATE, timeout=15)
    payload = _json_or_raise(resp, "获取二维码")
    if payload.get("code") != 0:
        raise BiliError(
            f"获取二维码失败: {payload.get('message')}",
            code=payload.get("code"),
            raw=payload,
        )
    data = payload["data"]
    return QrCode(key=data["qrcode_key"], url=data["url"])


def poll_qrcode(
    qrcode_key: str, session: requests.Session | None = None
) -> LoginStatus:
    """轮询一次二维码状态。成功时从 Set-Cookie 与返回 url 中提取 cookie。"""
    sess = session or new_session()
    resp = sess.get(QRCODE_POLL, params={"qrcode_key": qrcode_key}, timeout=15)
    payload = _json_or_raise(resp, "轮询登录状态")
    if payload.get("code") != 0:
        raise BiliError(
            f"轮询登录状态失败: {payload.get('message')}",
            code=payload.get("code"),
            raw=payload,
        )
    data = payload.get("data") or {}
    inner = data.get("code")
    if inner is None:
        raise BiliError("poll 返回缺少 data.code", raw=payload)

    cookies = {c.name: c.value for c in sess.cookies}
    if inner == QR_SUCCESS:
        # 跨域场景下 session 可能没吃到 cookie，从返回 url 兜底解析
        cookies.update(_parse_cookies_from_url(data.get("url", "")))

    return LoginStatus(code=inner, message=data.get("message", ""), cookies=cookies)


def _parse_cookies_from_url(url: str) -> dict[str, str]:
    """从 crossDomain 跳转链接的 query 里提取登录 cookie。"""
    if not url:
        return {}
    query = urllib.parse.urlparse(url).query
    params = urllib.parse.parse_qs(query)
    out: dict[str, str] = {}
    for name in REQUIRED_COOKIES:
        values = params.get(name)
        if values:
            out[name] = values[0]
    return out


# ---------- 交互式登录 ----------


def login_interactive(
    session: requests.Session | None = None,
    timeout: int = 180,
    interval: float = 2.0,
    render: bool = True,
) -> dict[str, str]:
    """完整的扫码登录流程，返回登录 cookie 字典。

    Args:
        timeout: 总等待秒数，超时抛 BiliError。
        interval: 轮询间隔。
        render: 是否在终端打印二维码；False 时只打印 url。
    """
    sess = session or new_session()
    qr = request_qrcode(sess)

    if render and not _render_qrcode(qr.url):
        print(
            "提示：当前终端无法渲染二维码字符"
            "（Windows 控制台建议用 Windows Terminal，或在 cmd 执行 chcp 65001）"
        )
    print(f"\n请用手机 B 站 App 扫码（或手动打开）：\n{qr.url}\n")
    print("注意：扫码后必须在手机上点「确认登录」才算完成。")

    deadline = time.time() + timeout
    last_state = -1
    while time.time() < deadline:
        status = poll_qrcode(qr.key, sess)
        if status.code != last_state:
            print(f"  {status.describe()}")
            last_state = status.code
        if status.done:
            missing = [c for c in REQUIRED_COOKIES if not status.cookies.get(c)]
            if missing:
                raise BiliError(
                    f"登录返回缺少 cookie: {missing}，请重试", raw=status.cookies
                )
            print("登录成功。")
            return {c: status.cookies[c] for c in REQUIRED_COOKIES}
        if status.expired:
            raise BiliError("二维码已失效，请重新执行 login")
        time.sleep(interval)

    raise BiliError(f"登录超时（{timeout}s），请重试")


def _render_qrcode(url: str) -> bool:
    """在终端打印二维码，返回是否成功渲染。

    两种情况会返回 False（调用方据此提示用户手动复制链接）：
    缺少 qrcode 库，或当前终端编码无法表示方块字符（Windows GBK 控制台常见）。
    """
    from .console import supports_block_chars

    if not supports_block_chars():
        logger.debug("当前终端不支持二维码方块字符，跳过渲染")
        return False

    try:
        import qrcode  # type: ignore[import-untyped]
    except ImportError:
        return False

    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make(fit=True)
    try:
        try:
            # invert=True 让二维码在深色终端上也清晰
            qr.print_ascii(invert=True)
        except TypeError:  # 旧版 qrcode 无 invert 参数
            qr.print_ascii()
    except UnicodeEncodeError:
        return False
    return True


# ---------- Cookie 持久化 ----------


def save_cookies(cookies: Mapping[str, str], path: str = DEFAULT_COOKIE_FILE) -> str:
    """保存 cookie 到 JSON，文件权限 0600（含 SESSDATA，别泄露）。"""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    payload = {
        "saved_at": time.time(),
        "mid": int(cookies.get("DedeUserID", 0)),
        "cookies": dict(cookies),
    }
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def load_cookies(path: str = DEFAULT_COOKIE_FILE) -> dict[str, str]:
    """读取 cookie；文件不存在或格式不对时返回空字典。"""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise BiliError(f"Cookie 文件读取失败: {exc}") from exc
    cookies = payload.get("cookies", {}) if isinstance(payload, dict) else {}
    if not isinstance(cookies, dict):
        return {}
    return {str(k): str(v) for k, v in cookies.items() if v}


def cookies_valid(cookies: Mapping[str, str]) -> bool:
    return all(cookies.get(name) for name in REQUIRED_COOKIES)


# ---------- 设备指纹 ----------


def ensure_buvid(client: BiliClient) -> dict[str, str]:
    """获取并写入 buvid3/buvid4/b_nut，缺失会显著提高风控命中率。

    返回值可直接并入 cookie 一起持久化。
    """
    try:
        data = client.session.get(SPI_URL, timeout=15).json().get("data") or {}
    except Exception as exc:  # noqa: BLE001 - 指纹获取失败不该中断登录
        logger.debug("获取 buvid 失败: %s", exc)
        return {}

    out: dict[str, str] = {}
    if data.get("b_3"):
        out["buvid3"] = data["b_3"]
        client.session.cookies.set("buvid3", data["b_3"], domain=".bilibili.com")
    if data.get("b_4"):
        out["buvid4"] = data["b_4"]
        client.session.cookies.set("buvid4", data["b_4"], domain=".bilibili.com")
    if out:
        out["b_nut"] = str(int(time.time()))
        client.session.cookies.set("b_nut", out["b_nut"], domain=".bilibili.com")
    return out


def new_session(proxy: str | None = None) -> requests.Session:
    """建一个伪装成桌面浏览器的会话。

    .. warning::
       **不要**用裸 :class:`requests.Session` 请求 B 站接口。
       裸 Session 的 UA 是 ``python-requests/x.y.z``，一眼就被认成脚本；
       ``passport.bilibili.com`` 会直接返回 **HTTP 412** 的 HTML 风控页
       （而不是 JSON），调用方的 ``.json()`` 会炸成一句难以理解的
       ``JSONDecodeError: Expecting value: line 1 column 1``。

       实测结论（2026-10）：UA 是决定性因素，Referer 非必需，
       但两者都带上更稳妥，也和 :class:`~bilibili_submit.client.BiliClient` 一致。

       历史踩坑：GUI 登录页自己 ``requests.Session()`` 造会话，
       结果命令行能登录、图形界面永远拿不到二维码。

    Args:
        proxy:形如 ``http://127.0.0.1:7890``；None 表示直连。
    """
    sess = requests.Session()
    sess.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Referer": "https://passport.bilibili.com/login",
        }
    )
    if proxy:
        sess.proxies.update({"http": proxy, "https": proxy})
    return sess


def build_client_from_cookies(
    cookies: Mapping[str, str], proxy: str | None = None, **kwargs: Any
) -> BiliClient:
    """用已保存的 cookie 构造客户端，并校验关键字段齐全。"""
    if not cookies_valid(cookies):
        missing = [c for c in REQUIRED_COOKIES if not cookies.get(c)]
        raise NotLoggedInError(f"Cookie 缺少必要字段: {missing}")
    return BiliClient(cookies=cookies, proxy=proxy, **kwargs)
