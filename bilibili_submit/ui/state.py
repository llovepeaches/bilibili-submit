"""界面偏好：记住批量任务页上次用的目录和统一参数。

存在 ``~/.config/bilibili_submit/ui-state.json``（与 cookie 同目录），
下次打开客户端自动回填并重新扫描，不用每次重新选文件夹。

刻意**不**存 exe 同目录：打包后那可能是 Program Files，只读；
源码运行时还会把仓库根目录弄脏。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from ..multipart import GROUP_MODES

logger = logging.getLogger(__name__)

__all__ = ["BatchUIState", "ui_state_path", "load_ui_state", "save_ui_state"]

#: 状态文件的结构版本。字段含义变了才递增，读取时不认就报问题。
SCHEMA_VERSION = 1

#: 分区回退值（日常）。文件里存了个不认识的分区号时用它，
#: 总比让用户对着一个空下拉框猜发生了什么强。
DEFAULT_TID = 21


@dataclass(frozen=True)
class BatchUIState:
    """批量任务页的跨次启动偏好。

    只存**用户填过的选择**，不存执行结果、勾选状态，
    更不存 cookie / 代理——那些是凭据或运行时状态，不该落到这种文件里。
    """

    directory: str = ""
    tid: int = DEFAULT_TID
    #: 投稿类型：1=自制 2=转载（B 站 ``copyright`` 字段）
    copyright: int = 1
    #: 转载来源。``copyright=2`` 时必填，缺了服务端会拒稿（21004）
    source: str = ""
    tag: str = ""
    desc: str = ""
    dtime_offset_hours: float | None = None
    #: 分 P 合并方式：``none`` / ``prefix`` / ``folder``
    #: （取值见 :data:`~bilibili_submit.multipart.GROUP_MODES`）。
    #: 早期版本这里是布尔 ``group_parts``，读取时做了兼容映射。
    group_mode: str = "none"
    #: 投稿标题模板，占位符 ``{name}``（文件名/文件夹名）与 ``{n}``（序号）。
    #: 留空表示不动标题——批量套用是覆盖操作，默认空才不会误伤。
    title_template: str = ""
    #: 互动设置
    close_reply: bool = False       # 关闭评论区
    close_danmu: bool = False       # 关闭弹幕
    selection_reply: bool = False   # 开启精选评论
    #: 音质增强。开了没效果多半是源文件本身不支持，不是程序坏了。
    dolby: bool = False
    hires: bool = False
    #: 「更多设置」折叠区上次是展开还是收起。
    #: 组件本身不记偏好，这里由界面层存——每次打开都重新收起会让人以为
    #: 设置丢了。
    advanced_opened: bool = False


def ui_state_path() -> Path:
    """状态文件位置。跟着 cookie 走，卸载时一起清掉。"""
    from ..config import DEFAULT_COOKIE_FILE

    return Path(DEFAULT_COOKIE_FILE).expanduser().parent / "ui-state.json"


def load_ui_state(path: Path | None = None) -> tuple[BatchUIState, str]:
    """读取偏好，返回 ``(状态, 问题描述)``。

    **「文件不存在」不是问题**，只是第一次用，返回默认值和空描述。
    但文件损坏或版本不认识必须如实报告——静默当成首次使用的话，
    用户会以为程序把自己的设置弄丢了，而界面上什么都看不出来。
    """
    target = path or ui_state_path()
    if not target.is_file():
        return BatchUIState(), ""

    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return BatchUIState(), f"偏好文件读取失败（{exc}）"
    if not isinstance(raw, dict):
        return BatchUIState(), "偏好文件格式不对（顶层不是对象）"

    version = raw.get("schema_version")
    if version != SCHEMA_VERSION:
        return BatchUIState(), f"偏好文件版本不兼容（{version}），已用默认值"

    batch = raw.get("batch")
    if not isinstance(batch, dict):
        return BatchUIState(), "偏好文件缺少 batch 段"

    return BatchUIState(
        directory=_as_str(batch.get("directory")),
        tid=_as_tid(batch.get("tid")),
        copyright=_as_copyright(batch.get("copyright")),
        source=_as_str(batch.get("source")),
        tag=_as_str(batch.get("tag")),
        desc=_as_str(batch.get("desc")),
        dtime_offset_hours=_as_offset(batch.get("dtime_offset_hours")),
        group_mode=_as_group_mode(batch.get("group_mode"), batch.get("group_parts")),
        title_template=_as_str(batch.get("title_template")),
        close_reply=_as_bool(batch.get("close_reply")),
        close_danmu=_as_bool(batch.get("close_danmu")),
        selection_reply=_as_bool(batch.get("selection_reply")),
        dolby=_as_bool(batch.get("dolby")),
        hires=_as_bool(batch.get("hires")),
        advanced_opened=_as_bool(batch.get("advanced_opened")),
    ), ""


def save_ui_state(state: BatchUIState, path: Path | None = None) -> None:
    """写入偏好。**同目录临时文件 + ``os.replace``**，保证不留半个文件。

    写失败只记日志不抛：保存偏好是锦上添花，不能因为它
    让「扫描文件夹」这种本来能成的操作整个失败掉。
    """
    target = path or ui_state_path()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "batch": asdict(state),
    }
    tmp = target.with_name(target.name + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, target)
    except OSError as exc:
        logger.debug("保存界面偏好失败: %s", exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _as_tid(value: object) -> int:
    """分区号必须是非零正整数，否则回退默认。"""
    if isinstance(value, bool):
        return DEFAULT_TID  # bool 是 int 的子类，先挡掉
    if isinstance(value, int) and value > 0:
        return value
    return DEFAULT_TID


def _as_bool(value: object) -> bool:
    """只认真布尔。字符串 "false" 也当假，免得手改过的文件把开关反过来。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() not in ("", "false", "0", "no", "off")
    return bool(value)


def _as_copyright(value: object) -> int:
    """投稿类型只认 1 / 2，其余一律回落自制。

    回落方向是刻意的：自制不要求填来源，不会让投稿当场失败；反过来
    认不出就当转载的话，用户会撞上「缺 source」被服务端打回，却分不清
    是自己没选还是程序弄错了。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return 1
    return value if value in (1, 2) else 1


def _as_group_mode(value: object, legacy: object = None) -> str:
    """分 P 合并方式。认不出来就当 ``none``（不合并）。

    分组本质是猜用户意图，猜不出来应当退回最保守的结果——按一个猜错的
    方式把几个不相干的视频投成同一稿件的分 P，要删稿重投。

    Args:
        legacy: 旧版写下的布尔 ``group_parts``。那时只有"按文件名前缀"
            一种分法，``true`` 就等价于今天的 ``prefix``。不认这个字段的话，
            老用户升级完会发现自己的选择被悄悄改回"不合并"——
            这正是宁可不升 schema 版本也要做兼容映射的原因。
    """
    text = value.strip().casefold() if isinstance(value, str) else ""
    if text in ("none", *GROUP_MODES):
        return text
    if _as_bool(legacy):
        return "prefix"
    return "none"


def _as_offset(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
