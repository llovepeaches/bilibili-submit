"""稿件投递。

B 站 Web 投稿接口 ``x/vu/web/add/v3`` 已被社区观察到不稳定（biliup-rs 将其
标注为 no longer working 并回退到 APP 接口）。这里把投递抽象成
``SubmitBackend``，web 失败时可以一行配置切到 app 后端，不必改动上层。

APP 后端需要 ``access_key``（OAuth 授权所得）+ ``appkey``/``appsec`` 做签名，
纯 cookie 登录拿不到，因此未配置时给出明确报错而不是静默失败。
"""

from __future__ import annotations

import hashlib
import logging
import random
import time
import urllib.parse
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .client import BiliClient
from .exceptions import BiliError, ConfigError
from .metadata import ArchiveMeta, build_payload

logger = logging.getLogger(__name__)

ADD_V3_URL = "https://member.bilibili.com/x/vu/web/add/v3"
ADD_V2_URL = "https://member.bilibili.com/x/vu/web/add"
APP_ADD_URL = "https://member.bilibili.com/x/vu/app/add"

#: 601 频控的阶梯退避（秒）。新号投稿更容易触发，间隔给得比较保守
DEFAULT_COOLDOWNS: tuple[int, ...] = (300, 600, 1200, 1800, 3600)

__all__ = [
    "SubmitResult",
    "SubmitBackend",
    "WebBackend",
    "AppBackend",
    "get_backend",
    "submit_archive",
]


@dataclass
class SubmitResult:
    """投稿成功的结果。"""

    aid: int
    bvid: str
    backend: str = ""

    def __str__(self) -> str:
        return f"{self.bvid} (av{self.aid})"

    @property
    def url(self) -> str:
        return f"https://www.bilibili.com/video/{self.bvid}"


class SubmitBackend(ABC):
    """投稿后端接口。"""

    name = "abstract"

    @abstractmethod
    def submit(self, client: BiliClient, meta: ArchiveMeta) -> SubmitResult:
        """投递稿件，返回 aid/bvid。"""


class WebBackend(SubmitBackend):
    """Web 端投稿。

    Args:
        use_v3: True 用 ``add/v3``（默认），False 退回旧版 ``x/vu/web/add``。
    """

    def __init__(self, use_v3: bool = True) -> None:
        self.use_v3 = use_v3
        self.name = "web/v3" if use_v3 else "web/v2"

    def submit(self, client: BiliClient, meta: ArchiveMeta) -> SubmitResult:
        payload = build_payload(meta, csrf=client.csrf)
        url = ADD_V3_URL if self.use_v3 else ADD_V2_URL
        data = client.post(url, json=payload, timeout=(10.0, 60.0))
        if not isinstance(data, dict):
            raise BiliError("投稿返回格式异常", raw=data)
        aid = int(data.get("aid") or 0)
        bvid = str(data.get("bvid") or "")
        if not aid or not bvid:
            raise BiliError("投稿未返回 aid/bvid", raw=data)
        return SubmitResult(aid=aid, bvid=bvid, backend=self.name)


class AppBackend(SubmitBackend):
    """APP 端投稿（``x/vu/app/add``），web 接口失效时的兜底。

    需要配置 ``access_key``，以及该 appkey 对应的 ``appsec``。APP 签名为：
    参数加 ``appkey``/``ts``/``access_key`` 后按 key 排序拼接，尾部拼 appsec 取 MD5。
    """

    name = "app"

    def __init__(
        self,
        access_key: str = "",
        appkey: str = "",
        appsec: str = "",
        **extra: Any,
    ) -> None:
        self.access_key = access_key
        self.appkey = appkey
        self.appsec = appsec
        self.extra = extra

    def _require_credentials(self) -> None:
        missing = [
            name
            for name, value in (
                ("access_key", self.access_key),
                ("appkey", self.appkey),
                ("appsec", self.appsec),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                f"APP 投稿后端缺少配置: {missing}。"
                "access_key 需通过 OAuth 授权获取，纯扫码登录拿不到；"
                "若不具条件请保持 submit.backend=web"
            )

    @staticmethod
    def sign(params: Mapping[str, Any], appkey: str, appsec: str) -> str:
        signed = dict(params)
        signed["appkey"] = appkey
        query = urllib.parse.urlencode(sorted(signed.items()))
        return hashlib.md5((query + appsec).encode("utf-8")).hexdigest()

    def submit(self, client: BiliClient, meta: ArchiveMeta) -> SubmitResult:
        self._require_credentials()
        meta = meta.normalized()
        params: dict[str, Any] = {
            "access_key": self.access_key,
            "ts": int(time.time()),
            "title": meta.title,
            "tid": meta.tid,
            "tag": meta.tag,
            "desc": meta.desc,
            "copyright": meta.copyright,
            "cover": meta.cover or "",
            "no_reprint": meta.no_reprint,
            "videos": ",".join(v.get("filename", "") for v in meta.videos),
        }
        if meta.source:
            params["source"] = meta.source
        if meta.dtime:
            params["dtime"] = meta.dtime
        params.update(self.extra)
        params["sign"] = self.sign(params, self.appkey, self.appsec)

        data = client.post(APP_ADD_URL, data=params, timeout=(10.0, 60.0))
        if not isinstance(data, dict):
            raise BiliError("APP 投稿返回格式异常", raw=data)
        aid = int(data.get("aid") or 0)
        bvid = str(data.get("bvid") or "")
        if not aid or not bvid:
            raise BiliError("APP 投稿未返回 aid/bvid", raw=data)
        return SubmitResult(aid=aid, bvid=bvid, backend=self.name)


def get_backend(
    name: str = "web", app_config: Mapping[str, Any] | None = None
) -> SubmitBackend:
    """按配置名构造后端。

    ``web`` / ``web/v2`` 走 Web 接口；``app`` 走 APP 接口（需 access_key 等）。
    """
    name = (name or "web").lower()
    if name in ("web", "web/v3", "v3"):
        return WebBackend(use_v3=True)
    if name in ("web/v2", "v2"):
        return WebBackend(use_v3=False)
    if name == "app":
        return AppBackend(**(app_config or {}))
    raise ConfigError(f"未知的投稿后端: {name}（可选 web / web/v2 / app）")


def submit_archive(
    client: BiliClient,
    meta: ArchiveMeta,
    backend: SubmitBackend | None = None,
    cooldowns: tuple[int, ...] = DEFAULT_COOLDOWNS,
    on_wait: Callable[[float, int], None] | None = None,
) -> SubmitResult:
    """投稿并处理 601 频控。

    601 是账号侧的临时频控（"您上传视频过快"），重试即可，改参数没用。
    重试前会刷新 WBI 密钥与 csrf，避免等待期间密钥轮换导致二次失败。
    """
    backend = backend or WebBackend()
    attempts = list(cooldowns) or [300]

    for index, wait in enumerate(attempts):
        try:
            return backend.submit(client, meta)
        except BiliError as exc:
            if exc.code != 601:
                raise
            if index == len(attempts) - 1:
                logger.error("已重试 %d 次仍被频控拦截，放弃本条", len(attempts))
                raise
            delay = wait + random.uniform(0, 60)
            logger.warning(
                "触发投稿频控（601），等待 %.0f 秒后第 %d 次重试",
                delay, index + 1,
            )
            if on_wait is not None:
                on_wait(delay, index + 1)
            time.sleep(delay)
            client.signer.invalidate()

    raise BiliError("投稿失败：重试耗尽")  # pragma: no cover - 循环内必定 return/raise
