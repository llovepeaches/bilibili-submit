"""稿件元数据处理：标题/标签/分区/定时校验与 payload 组装。

B 站对这几个字段有硬性限制，超限会直接拒稿（62004 等），因此在这里
统一做截断与校验，而不是等服务端报错。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from .client import BiliClient
from .exceptions import BiliError, ConfigError

logger = logging.getLogger(__name__)

TID_URL = "https://member.bilibili.com/x/vupre/web/tid"

TITLE_MAX = 80          # 标题上限（字符）
TAG_MAX_COUNT = 10      # 标签最多 10 个
TAG_MAX_TOTAL = 200     # 标签总长度上限
DESC_MAX = 2000         # 简介上限，B 站实际约 2000

#: 定时投稿要求距今至少 4 小时
DTIME_MIN_OFFSET = 4 * 3600

__all__ = [
    "ArchiveMeta",
    "fetch_tids",
    "validate_tid",
    "normalize_tags",
    "build_payload",
    "resolve_dtime",
]

#: 常用分区速查，离线也能用；以服务端返回的列表为准
COMMON_TIDS: dict[int, str] = {
    21: "日常", 122: "野生技术协会", 188: "科技数码", 217: "科普",
    36: "科学科普", 39: "设计创意", 160: "生活", 138: "搞笑",
    4: "游戏", 17: "单机游戏", 171: "电子竞技", 3: "音乐",
    129: "舞蹈", 5: "娱乐", 181: "影视", 23: "电影",
    11: "电视剧", 177: "纪录片", 119: "鬼畜", 155: "时尚",
    202: "资讯", 211: "美食", 76: "美食制作", 75: "野生动物",
    165: "运动", 234: "汽车", 223: "知识分享", 26: "计算机学习",
}


@dataclass
class ArchiveMeta:
    """一个待投稿件的元数据。"""

    title: str
    tid: int = 21
    tag: str = ""
    desc: str = ""
    cover: str | None = None
    copyright: int = 1          # 1=自制 2=转载
    source: str = ""            # 转载来源，copyright=2 时必填
    no_reprint: int = 0         # 0=允许转载 1=禁止
    dtime: int | None = None
    mission_id: int | None = None
    dynamic: str = ""
    subtitle_open: int = 0
    subtitle_lan: str = ""
    up_close_reply: bool = False       # 关闭评论区
    up_close_danmu: bool = False       # 关闭弹幕
    up_selection_reply: bool = False   # 开启精选评论
    # 音视频增强。字段名按 B 站官方参数表来：Hi-Res 叫 lossless_music，
    # 不叫 hires——猜错名字的话接口会静默忽略，开着开关却没效果。
    dolby: int = 0                     # 杜比音效 0=否 1=是
    lossless_music: int = 0            # Hi-Res 无损音质 0=否 1=是
    videos: list[dict[str, str]] = field(default_factory=list)

    def normalized(self) -> "ArchiveMeta":
        """按 B 站限制做裁剪，返回新对象。"""
        title = self.title.strip()
        if len(title) > TITLE_MAX:
            logger.warning("标题超 %d 字，已截断", TITLE_MAX)
            title = title[:TITLE_MAX]

        desc = self.desc or ""
        if len(desc) > DESC_MAX:
            logger.warning("简介超 %d 字，已截断", DESC_MAX)
            desc = desc[:DESC_MAX]

        if self.copyright == 2 and not self.source.strip():
            raise ConfigError("copyright=2（转载）时必须填写 source（转载来源）")

        return ArchiveMeta(
            title=title,
            tid=int(self.tid),
            tag=normalize_tags(self.tag),
            desc=desc,
            cover=self.cover,
            copyright=self.copyright,
            source=self.source.strip(),
            no_reprint=int(bool(self.no_reprint)),
            dtime=self.dtime,
            mission_id=self.mission_id,
            dynamic=self.dynamic,
            subtitle_open=self.subtitle_open,
            subtitle_lan=self.subtitle_lan,
            up_close_reply=self.up_close_reply,
            up_close_danmu=self.up_close_danmu,
            up_selection_reply=self.up_selection_reply,
            # 允许传 True/False，统一成 0/1 B 站才认
            dolby=int(bool(self.dolby)),
            lossless_music=int(bool(self.lossless_music)),
            videos=list(self.videos),
        )


def normalize_tags(tag: str | Iterable[str]) -> str:
    """标签规范化：去重、去空、限 10 个、总长 < 200。"""
    if isinstance(tag, str):
        items = [t.strip() for t in tag.replace("，", ",").split(",")]
    else:
        items = [str(t).strip() for t in tag]
    items = [t for t in items if t]

    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)

    if len(unique) > TAG_MAX_COUNT:
        logger.warning("标签超过 %d 个，仅保留前 %d 个", TAG_MAX_COUNT, TAG_MAX_COUNT)
        unique = unique[:TAG_MAX_COUNT]

    result = ",".join(unique)
    if len(result) > TAG_MAX_TOTAL:
        while unique and len(",".join(unique)) > TAG_MAX_TOTAL:
            unique.pop()
        logger.warning("标签总长超过 %d，已裁剪", TAG_MAX_TOTAL)
        result = ",".join(unique)
    return result


def fetch_tids(client: BiliClient, timeout: float = 15.0) -> dict[int, str]:
    """拉取全部分区。失败时回退到内置速查表，不阻断流程。"""
    try:
        data = client.get(f"{TID_URL}?t={int(time.time())}", timeout=(10.0, timeout))
    except BiliError as exc:
        logger.warning("拉取分区列表失败，使用内置速查表: %s", exc)
        return dict(COMMON_TIDS)

    mapping: dict[int, str] = {}
    rows = data if isinstance(data, list) else (data or {}).get("list", [])
    for row in rows:
        if not isinstance(row, dict):
            continue
        tid = row.get("id") or row.get("tid")
        name = row.get("name") or row.get("typename")
        if tid and name:
            mapping[int(tid)] = str(name)
        # 部分分区带二级列表
        for child in row.get("children") or []:
            if isinstance(child, dict) and child.get("id"):
                mapping[int(child["id"])] = str(child.get("name", ""))
    return mapping or dict(COMMON_TIDS)


def validate_tid(tid: int, tids: dict[int, str] | None = None) -> None:
    """校验分区 ID。已知分区表命中不了时只告警——新分区可能不在本地表里。"""
    tid = int(tid)
    if tids and tid in tids:
        return
    if tid in COMMON_TIDS:
        return
    logger.warning(
        "分区 tid=%s 不在已知列表中，若投稿失败请核对分区 ID（可用 `check` 命令查看）",
        tid,
    )


def resolve_dtime(dtime: int | None, offset_hours: float | None) -> int | None:
    """把绝对时间戳或相对小时数统一成绝对时间戳，并校验 >= 4 小时。"""
    if dtime is not None:
        value = int(dtime)
    elif offset_hours is not None:
        value = int(time.time()) + int(float(offset_hours) * 3600)
    else:
        return None

    now = int(time.time())
    if value <= now:
        raise ConfigError(
            f"定时时间 {value} 早于当前时间，请检查 dtime（需为 Unix 秒级时间戳）"
        )
    if value - now < DTIME_MIN_OFFSET:
        raise ConfigError(
            "定时投稿必须距今至少 4 小时"
            f"（当前设定仅 {(value - now) / 3600:.1f} 小时），请调大 dtime_offset_hours"
        )
    return value


def build_payload(meta: ArchiveMeta, csrf: str, desc_format_id: int = 9999) -> dict[str, Any]:
    """组装 ``x/vu/web/add/v3`` 的请求体。"""
    meta = meta.normalized()
    if not meta.videos:
        raise ConfigError("稿件至少需要一个视频分 P（videos 为空）")

    payload: dict[str, Any] = {
        "videos": [
            {
                "filename": v.get("filename", ""),
                "title": v.get("title", "")[:TITLE_MAX],
                "desc": v.get("desc", ""),
            }
            for v in meta.videos
        ],
        "title": meta.title,
        "copyright": meta.copyright,
        "tid": meta.tid,
        "tag": meta.tag,
        "desc_format_id": desc_format_id,
        "desc": meta.desc,
        "recreate": -1,
        "dynamic": meta.dynamic,
        "interactive": 0,
        "act_reserve_create": 0,
        "no_disturbance": 0,
        "no_reprint": meta.no_reprint,
        "subtitle": {"open": meta.subtitle_open, "lan": meta.subtitle_lan},
        "web_os": 3,
        "csrf": csrf,
    }
    if meta.cover:
        payload["cover"] = meta.cover
    if meta.source:
        payload["source"] = meta.source
    if meta.dtime:
        payload["dtime"] = meta.dtime
    if meta.mission_id:
        payload["mission_id"] = meta.mission_id
    if meta.up_close_reply:
        payload["up_close_reply"] = True
    if meta.up_close_danmu:
        payload["up_close_danmu"] = True
    if meta.up_selection_reply:
        payload["up_selection_reply"] = True
    # 这两个官方参数表标了「必要」，0 也要带上，不能只在开启时写
    payload["dolby"] = meta.dolby
    payload["lossless_music"] = meta.lossless_music
    return payload
