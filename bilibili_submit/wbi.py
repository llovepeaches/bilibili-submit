"""B 站 WBI 签名（Web Broadcast Interface）。

B 站自 2023-03 起对大部分 Web 查询接口启用 WBI 风控签名，缺失或错误会返回
``code=-352``。签名依赖一对每天轮换的密钥（img_key / sub_key），可从
``GET https://api.bilibili.com/x/web-interface/nav`` 的 ``data.wbi_img`` 中匿名获取。

签名流程：
    1. 拼接 img_key + sub_key，按固定 64 位置换表重排后取前 32 位 → mixin_key
    2. 待签参数加入 wts（Unix 秒级时间戳）
    3. 按 key 升序排序，逐个过滤掉 !'()* 后做 URL 编码（大写十六进制、空格 %20）
    4. 用 & 连接成 query，尾部拼接 mixin_key
    5. 整体取 MD5 十六进制 → w_rid
"""

from __future__ import annotations

import hashlib
import re
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any, Mapping

__all__ = [
    "MIXIN_KEY_ENC_TAB",
    "WbiKey",
    "WbiSigner",
    "get_mixin_key",
    "signed_params",
]

# 固定置换表：重排 img_key+sub_key 得到 mixin_key（取前 32 位）
MIXIN_KEY_ENC_TAB: tuple[int, ...] = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)

# JS encodeURIComponent 不会转义这几个字符，签名前必须剔除
_CHR_FILTER = re.compile(r"[!'()*]")


@dataclass(frozen=True)
class WbiKey:
    """一对 WBI 密钥及其抓取时间。"""

    img_key: str
    sub_key: str
    fetched_at: float = 0.0

    @property
    def mixin_key(self) -> str:
        return get_mixin_key(self.img_key, self.sub_key)

    def is_stale(self, ttl: float = 3600.0) -> bool:
        """密钥每日轮换，本地缓存默认 1 小时后视为过期。"""
        return (time.time() - self.fetched_at) > ttl


def get_mixin_key(img_key: str, sub_key: str) -> str:
    """由 img_key 与 sub_key 派生 32 位 mixin_key。"""
    raw = img_key + sub_key
    return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]


def _encode(value: Any) -> str:
    """过滤特殊字符后做 URL 编码。

    ``urllib.parse.quote(s, safe="")`` 默认保留字母数字与 ``_.-~``，其余字符以
    大写十六进制输出、空格编码为 ``%20``，与 JS encodeURIComponent 行为一致。
    """
    cleaned = _CHR_FILTER.sub("", str(value))
    return urllib.parse.quote(cleaned, safe="", encoding="utf-8")


def signed_params(
    params: Mapping[str, Any],
    key: WbiKey,
    wts: int | None = None,
) -> dict[str, Any]:
    """返回附带 ``w_rid`` 与 ``wts`` 的新参数字典，不修改入参。"""
    signed = dict(params)
    signed["wts"] = int(time.time()) if wts is None else int(wts)

    query = "&".join(
        f"{_encode(k)}={_encode(signed[k])}" for k in sorted(signed)
    )
    signed["w_rid"] = hashlib.md5(
        (query + key.mixin_key).encode("utf-8")
    ).hexdigest()
    return signed


class WbiSigner:
    """持有当前 WBI 密钥，负责为请求参数签名。

    密钥的抓取（nav 接口）由调用方完成，本模块不直接发起网络请求，以便单元测试。
    """

    def __init__(self, key: WbiKey | None = None, ttl: float = 3600.0) -> None:
        self._key = key
        self._ttl = ttl

    @property
    def key(self) -> WbiKey | None:
        return self._key

    def is_ready(self) -> bool:
        return self._key is not None and not self._key.is_stale(self._ttl)

    def set_key(self, key: WbiKey) -> None:
        self._key = key

    def invalidate(self) -> None:
        """签名被拒（-352）时调用，强制下次重新抓取密钥。"""
        self._key = None

    def sign(self, params: Mapping[str, Any], wts: int | None = None) -> dict[str, Any]:
        if self._key is None:
            raise RuntimeError("WBI 密钥尚未初始化，请先调用 set_key()")
        return signed_params(params, self._key, wts)
