"""HTTP 客户端：会话、签名、CSRF、响应归一化与网络层重试。

上层（upload / cover / submit）只与本模块交互，不直接接触 requests，
这样 UA、代理、重试、错误处理策略都集中在一处，接口变更时改动最小。
"""

from __future__ import annotations

import json
import logging
import random
import time
from typing import Any, Mapping

import requests

from .exceptions import (
    ApiChangedError,
    BiliError,
    NetworkError,
    NotLoggedInError,
    WbiSignatureError,
    raise_for_code,
)
from .wbi import WbiKey, WbiSigner

logger = logging.getLogger(__name__)

#: 伪装成桌面 Chrome，B 站对异常 UA 会提高风控概率
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

API_BASE = "https://api.bilibili.com"
PASSPORT_BASE = "https://passport.bilibili.com"
MEMBER_BASE = "https://member.bilibili.com"

__all__ = ["BiliClient", "USER_AGENT"]


class BiliClient:
    """封装好的 B 站 Web API 客户端。

    Attributes:
        signer: WBI 签名器，密钥按需从 nav 接口抓取并缓存。
        max_retries: 网络层（超时 / 5xx）最大重试次数。
        timeout: (连接超时, 读取超时)。上传大文件时由调用方单独放宽。
    """

    def __init__(
        self,
        cookies: Mapping[str, str] | None = None,
        proxy: str | None = None,
        max_retries: int = 3,
        timeout: tuple[float, float] = (10.0, 30.0),
        session: requests.Session | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Referer": "https://member.bilibili.com/platform/upload/video/frame",
                "Origin": "https://member.bilibili.com",
                "Accept": "application/json, text/plain, */*",
            }
        )
        if cookies:
            self.set_cookies(cookies)
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})
        self.max_retries = max_retries
        self.timeout = timeout
        self.signer = WbiSigner()

    # ---------- Cookie / CSRF ----------

    def set_cookies(self, cookies: Mapping[str, str]) -> None:
        for name, value in cookies.items():
            self.session.cookies.set(name, value, domain=".bilibili.com")

    def export_cookies(self) -> dict[str, str]:
        return {c.name: c.value for c in self.session.cookies}

    @property
    def csrf(self) -> str:
        """bili_jct，POST 请求必须携带。"""
        return self.session.cookies.get("bili_jct", "")

    @property
    def mid(self) -> int:
        try:
            return int(self.session.cookies.get("DedeUserID", "0"))
        except (TypeError, ValueError):
            return 0

    @property
    def sessdata(self) -> str:
        return self.session.cookies.get("SESSDATA", "")

    @property
    def is_logged_in(self) -> bool:
        return bool(self.sessdata and self.csrf)

    # ---------- WBI ----------

    def refresh_wbi(self) -> WbiKey:
        """从 nav 接口重新抓取 WBI 密钥（未登录时也能拿到）。"""
        resp = self.session.get(
            f"{API_BASE}/x/web-interface/nav", timeout=self.timeout[0]
        )
        payload = resp.json()
        wbi_img = (payload.get("data") or {}).get("wbi_img") or {}
        img_url = wbi_img.get("img_url", "")
        sub_url = wbi_img.get("sub_url", "")
        if not img_url or not sub_url:
            raise ApiChangedError(
                "nav 接口未返回 wbi_img，无法生成签名", raw=payload
            )
        key = WbiKey(
            img_key=_stem(img_url),
            sub_key=_stem(sub_url),
            fetched_at=time.time(),
        )
        self.signer.set_key(key)
        return key

    def _ensure_wbi(self) -> None:
        if not self.signer.is_ready():
            self.refresh_wbi()

    # ---------- 底层请求 ----------

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        sign: bool = False,
        with_csrf: bool = False,
        timeout: tuple[float, float] | None = None,
        raw: bool = False,
        **kwargs: Any,
    ) -> Any:
        """发请求并做归一化处理。

        Args:
            sign: 对 query 参数做 WBI 签名。
            with_csrf: 自动附加 csrf 参数。
            raw: 返回完整 ``Response`` 而不做 JSON 解析（上传接口需要）。
        """
        timeout = timeout or self.timeout
        base_params = dict(params or {})
        if with_csrf:
            base_params.setdefault("csrf", self.csrf)

        last_exc: Exception | None = None
        wbi_retried = False

        for attempt in range(self.max_retries + 1):
            query = dict(base_params)
            if sign:
                self._ensure_wbi()
                query = self.signer.sign(query)

            try:
                resp = self.session.request(
                    method, url, params=query, timeout=timeout, **kwargs
                )
            except requests.RequestException as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    raise NetworkError(f"请求 {url} 失败: {exc}") from exc
                self._sleep_backoff(attempt)
                continue

            if resp.status_code >= 500:
                last_exc = NetworkError(f"服务端 {resp.status_code}")
                if attempt >= self.max_retries:
                    raise last_exc
                self._sleep_backoff(attempt)
                continue

            if raw:
                return resp

            try:
                return self._parse(resp)
            except WbiSignatureError:
                # 密钥可能刚轮换：强制刷新后重放一次，避免无限循环
                if wbi_retried or not sign:
                    raise
                wbi_retried = True
                self.signer.invalidate()
                logger.debug("WBI 签名被拒，刷新密钥后重放请求")
                continue

        raise last_exc or NetworkError(f"请求 {url} 失败")

    @staticmethod
    def _parse(resp: requests.Response) -> Any:
        """把响应解析为 ``data`` 字段，非 0 code 抛对应异常。"""
        ctype = resp.headers.get("Content-Type", "")
        if "application/json" not in ctype:
            raise ApiChangedError(
                f"接口返回了非 JSON 内容（Content-Type: {ctype or 'empty'}）",
                raw=resp.text[:500],
            )
        try:
            payload = resp.json()
        except json.JSONDecodeError as exc:
            raise ApiChangedError(
                "响应不是合法 JSON", raw=resp.text[:500]
            ) from exc

        code = payload.get("code", 0)
        if code != 0:
            err = raise_for_code(code, payload.get("message", ""), raw=payload)
            raise err
        return payload.get("data")

    @staticmethod
    def _sleep_backoff(attempt: int) -> None:
        time.sleep(min(2 ** attempt, 30) + random.uniform(0, 1))

    # ---------- 便捷方法 ----------

    def get(self, url: str, **kwargs: Any) -> Any:
        return self._request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        return self._request("POST", url, with_csrf=True, **kwargs)

    def get_signed(self, url: str, params: Mapping[str, Any] | None = None, **kw: Any) -> Any:
        return self._request("GET", url, params=params, sign=True, **kw)

    def request_raw(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> requests.Response:
        """返回原始 ``Response``，用于 preupload / upos 这类非标准包装的接口。

        这类接口顶层直接是 ``{"OK":1, ...}``，没有 ``code``/``data`` 外壳，
        走 ``_parse`` 会把响应误判为异常。
        """
        return self._request(method, url, raw=True, **kwargs)

    # ---------- 账户信息 ----------

    def nav(self, signed: bool = True) -> dict[str, Any]:
        """当前登录态信息，未登录时 code=-101 但 wbi_img 仍可用。"""
        try:
            data = self._request(
                "GET", f"{API_BASE}/x/web-interface/nav", sign=signed
            )
            return data or {}
        except NotLoggedInError:
            return {}

    def require_login(self) -> dict[str, Any]:
        """校验登录态，失效则抛 NotLoggedInError。"""
        data = self.nav()
        if not data.get("isLogin"):
            raise NotLoggedInError("Cookie 已失效，请重新扫码登录")
        return data

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "BiliClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _stem(url: str) -> str:
    """从 ``https://i0.hdslb.com/bfs/wbi/7cd0849....png`` 取出文件名主体。"""
    name = url.rsplit("/", 1)[-1]
    return name.split(".", 1)[0]


def is_bili_error(exc: BaseException, *codes: int) -> bool:
    """判断异常是否为指定 code 的 BiliError，便于上层做针对性处理。"""
    return isinstance(exc, BiliError) and exc.code in codes
