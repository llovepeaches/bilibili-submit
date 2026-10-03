"""异常体系与 B 站错误码映射。

B 站接口无论 HTTP 状态码如何，业务错误都通过 JSON 里的 ``code`` 字段表达，
因此这里统一把 code 翻译成带可操作建议的异常，供上层决定重试还是放弃。
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "BiliError",
    "NotLoggedInError",
    "WbiSignatureError",
    "RiskControlError",
    "RateLimitedError",
    "DailyLimitError",
    "ArchiveRejectedError",
    "ApiChangedError",
    "NetworkError",
    "UploadError",
    "MergeFailedError",
    "ConfigError",
    "raise_for_code",
]


class BiliError(Exception):
    """所有 B 站业务异常的基类。"""

    #: 是否需要重试（由子类覆盖）
    retryable = False

    def __init__(
        self,
        message: str,
        code: int | None = None,
        hint: str = "",
        raw: Any = None,
    ) -> None:
        self.code = code
        self.message = message
        self.hint = hint
        self.raw = raw
        super().__init__(self._compose())

    def _compose(self) -> str:
        parts = []
        if self.code is not None:
            parts.append(f"[code={self.code}]")
        parts.append(self.message)
        if self.hint:
            parts.append(f"→ {self.hint}")
        return " ".join(parts)


class NotLoggedInError(BiliError):
    """``-101`` Cookie 失效或未登录。"""

    retryable = False

    def __init__(self, message: str = "未登录或登录态已过期", **kw: Any) -> None:
        kw.setdefault("code", -101)
        kw.setdefault("hint", "请执行 `bili-submit login` 重新扫码登录")
        super().__init__(message, **kw)


class WbiSignatureError(BiliError):
    """``-352`` WBI 签名缺失或错误。"""

    retryable = True

    def __init__(self, message: str = "WBI 签名校验失败", **kw: Any) -> None:
        kw.setdefault("code", -352)
        kw.setdefault("hint", "密钥可能已轮换，将重新抓取后重试")
        super().__init__(message, **kw)


class RiskControlError(BiliError):
    """``-412`` IP 或账号被风控拦截。"""

    retryable = False

    def __init__(self, message: str = "请求被风控拦截", **kw: Any) -> None:
        kw.setdefault("code", -412)
        kw.setdefault("hint", "通常源于 IP 信誉或高频请求，建议更换网络/代理后重试")
        super().__init__(message, **kw)


class RateLimitedError(BiliError):
    """``601`` 投稿过于频繁（新号尤其容易触发）。"""

    retryable = True

    def __init__(self, message: str = "投稿过于频繁", **kw: Any) -> None:
        kw.setdefault("code", 601)
        kw.setdefault("hint", "账号侧临时频控，等待冷却后自动重试，无需改动参数")
        super().__init__(message, **kw)


class DailyLimitError(BiliError):
    """``200009`` 当日投稿数量已达上限。"""

    retryable = False

    def __init__(self, message: str = "今日投稿数量已达上限", **kw: Any) -> None:
        kw.setdefault("code", 200009)
        kw.setdefault("hint", "非正式会员单日上限较低，可到主站答题转为正式会员")
        super().__init__(message, **kw)


class ArchiveRejectedError(BiliError):
    """稿件内容被拒（62002 存在未过审稿件 / 62004 标题封面违规 等）。"""

    retryable = False

    def __init__(self, message: str, code: int = 62002, **kw: Any) -> None:
        kw.setdefault("hint", "需人工处理：检查是否有稿件正在审核，或标题/封面是否违规")
        super().__init__(message, code=code, **kw)


class ApiChangedError(BiliError):
    """接口返回了非预期内容（如 HTML 错误页），通常意味着接口已变更。"""

    retryable = False

    def __init__(self, message: str = "接口返回了非 JSON 内容", **kw: Any) -> None:
        kw.setdefault("hint", "接口可能已变更或被风控拦截，请检查网络或更新程序")
        super().__init__(message, **kw)


class NetworkError(BiliError):
    """网络层异常：连接失败、超时、5xx。"""

    retryable = True

    def __init__(self, message: str, **kw: Any) -> None:
        kw.setdefault("hint", "网络不稳定，将退避重试")
        super().__init__(message, **kw)


class UploadError(BiliError):
    """视频分片上传失败。"""

    retryable = True


class MergeFailedError(BiliError):
    """分片合并失败，通常需要整文件重传。"""

    retryable = True

    def __init__(self, message: str = "分片合并失败", **kw: Any) -> None:
        kw.setdefault("hint", "服务端未返回 OK=1，将丢弃断点状态重新上传")
        super().__init__(message, **kw)


class ConfigError(Exception):
    """配置文件缺失、字段非法或校验不通过。"""


#: code → 异常类。未列出的 code 走通用 BiliError。
_CODE_MAP: dict[int, type[BiliError]] = {
    -101: NotLoggedInError,
    -111: BiliError,          # csrf 校验失败
    -352: WbiSignatureError,
    -403: BiliError,          # 权限不足
    -412: RiskControlError,
    -509: BiliError,          # 请求过于频繁
    601: RateLimitedError,
    62002: ArchiveRejectedError,
    62004: ArchiveRejectedError,
    200009: DailyLimitError,
}

#: 为没有独立异常类的 code 补充提示文案
_CODE_HINTS: dict[int, str] = {
    -111: "csrf 与 Cookie 中的 bili_jct 不一致，请重新登录",
    -403: "权限不足，请确认账号已完成实名并具备投稿权限",
    -509: "请求过于频繁，请降低频率后重试",
}


def raise_for_code(code: int, message: str = "", raw: Any = None) -> BiliError:
    """按 code 构造对应异常（不抛出，交给调用方决定）。"""
    cls = _CODE_MAP.get(code, BiliError)
    hint = _CODE_HINTS.get(code, "")
    text = message or f"接口返回错误 {code}"
    if cls is BiliError:
        return BiliError(text, code=code, hint=hint, raw=raw)
    return cls(text, code=code, hint=hint, raw=raw)
