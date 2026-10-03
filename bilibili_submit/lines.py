"""上传线路探测与择优。

``GET https://member.bilibili.com/preupload?r=probe`` 会返回可用的 upos 线路，
每条线路带一个探测地址。逐条 ping 取最快的一条，比硬编码线路可靠得多。

注意该接口**不是** B 站标准的 ``code``/``data`` 包装，顶层直接是
``{"OK":1, "lines":[...], "probe":{"post":1}}``，因此走 ``request_raw``。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable

import requests

from .client import BiliClient
from .exceptions import BiliError

logger = logging.getLogger(__name__)

PREUPLOAD_URL = "https://member.bilibili.com/preupload"

__all__ = ["UploadLine", "fetch_lines", "measure_line", "pick_line"]


@dataclass
class UploadLine:
    """一条上传线路。"""

    os: str = "upos"
    query: str = "probe_version=20250923&upcdn=bda2&zone=cs"
    probe_url: str = ""
    upcdn: str = ""
    zone: str = ""
    cost: float | None = None  # 探测耗时（秒）

    @property
    def name(self) -> str:
        return self.upcdn or self.zone or self.os

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "UploadLine":
        return cls(
            os=str(payload.get("os", "upos")),
            query=str(payload.get("query", "")),
            probe_url=str(payload.get("probe_url", "")),
            upcdn=str(payload.get("upcdn", "")),
            zone=str(payload.get("zone", "")),
        )


#: 接口不可用时的兜底线路（bda2 百度，国内通用性最好）
FALLBACK_LINES: tuple[UploadLine, ...] = (
    UploadLine(query="probe_version=20250923&upcdn=bda2&zone=cs", upcdn="bda2", zone="cs"),
    UploadLine(query="probe_version=20250923&upcdn=tx&zone=cs", upcdn="tx", zone="cs"),
    UploadLine(query="probe_version=20250923&upcdn=bldsa&zone=cs", upcdn="bldsa", zone="cs"),
)


def fetch_lines(client: BiliClient, timeout: float = 15.0) -> list[UploadLine]:
    """拉取可用线路，失败时返回兜底列表而不是中断。"""
    try:
        resp = client.request_raw(
            "GET", PREUPLOAD_URL, params={"r": "probe"}, timeout=(10.0, timeout)
        )
        payload = resp.json()
    except (BiliError, ValueError, requests.RequestException) as exc:
        logger.warning("线路探测失败，使用兜底线路: %s", exc)
        return list(FALLBACK_LINES)

    raw_lines = payload.get("lines")
    if not isinstance(raw_lines, list) or not raw_lines:
        logger.warning("线路探测未返回有效 lines，使用兜底线路")
        return list(FALLBACK_LINES)

    lines = [
        UploadLine.from_api(ln) for ln in raw_lines if isinstance(ln, dict)
    ]
    # probe.post=1 表示需要用 POST 探测，记在 client 上供 measure_line 使用
    return lines


def probe_method(client: BiliClient, timeout: float = 15.0) -> str:
    """探测请求该用 GET 还是 POST（由 probe 字段决定）。"""
    try:
        resp = client.request_raw(
            "GET", PREUPLOAD_URL, params={"r": "probe"}, timeout=(10.0, timeout)
        )
        probe = resp.json().get("probe") or {}
    except (BiliError, ValueError, requests.RequestException):
        return "POST"
    return "POST" if probe.get("post") else "GET"


def measure_line(
    client: BiliClient, line: UploadLine, method: str = "POST", timeout: float = 6.0
) -> float | None:
    """探测单条线路耗时（秒），失败返回 None。"""
    if not line.probe_url:
        return None
    url = line.probe_url
    if url.startswith("//"):
        url = "https:" + url
    elif not url.startswith("http"):
        url = "https://" + url

    started = time.perf_counter()
    try:
        if method == "POST":
            resp = client.request_raw("POST", url, data=b"", timeout=(5.0, timeout))
        else:
            resp = client.request_raw("GET", url, timeout=(5.0, timeout))
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - 探测失败只是排除该线路
        logger.debug("线路 %s 探测失败: %s", line.name, exc)
        return None
    return time.perf_counter() - started


def pick_line(
    client: BiliClient,
    prefer: str | None = None,
    candidates: Iterable[UploadLine] | None = None,
    max_cost: float = 3.0,
) -> UploadLine:
    """选一条线路。

    Args:
        prefer: 指定 upcdn 名（bda2/tx/estx/bldsa/akbd），为 None 或 "auto" 时自动择优。
        candidates: 候选线路，默认重新探测。
        max_cost: 超过此耗时的线路直接排除。
    """
    lines = list(candidates) if candidates is not None else fetch_lines(client)

    if prefer and prefer not in ("auto", ""):
        for line in lines:
            if line.upcdn == prefer or line.name == prefer:
                logger.info("使用指定线路: %s", prefer)
                return line
        logger.warning("未找到指定线路 %s，改为自动择优", prefer)

    method = probe_method(client)
    best: UploadLine | None = None
    for line in lines:
        if line.os != "upos":
            continue
        cost = measure_line(client, line, method=method)
        if cost is None or cost > max_cost:
            continue
        line.cost = cost
        logger.debug("线路 %s 耗时 %.3fs", line.name, cost)
        if best is None or cost < (best.cost or float("inf")):
            best = line

    if best is None:
        logger.warning("所有线路探测失败，回退到 bda2")
        return FALLBACK_LINES[0]

    logger.info("已选择线路 %s（%.3fs）", best.name, best.cost)
    return best
