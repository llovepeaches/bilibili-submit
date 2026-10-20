"""投稿参数的统一校验，单文件页与批量页共用。

之前两页的校验各自为政：单文件页 :mod:`~.views.upload` 拦了标题 /
简介 / 标签长度，批量页 :mod:`~.views.tasks` 完全没拦——结果就是批量页
能提交超长简介，被服务端逐条打回（21015 / 21018）。这里把规则收口到一处。

定时发布的**下限**（距今至少 4 小时）与 :mod:`~bilibili_submit.metadata`
的 :func:`resolve_dtime` 保持一致：那里是提交时才拦，这里在界面上先拦，
省得用户整批跑完才发现。平台只规定了下限，没有界面需要关心的上限，所以
这里只校验下限。
"""

from __future__ import annotations

from ..exceptions import BiliError

#: 标题上限（字符数）。B 站硬性限制，单文件页原本在 upload.py 里也定义了同样的值。
MAX_TITLE = 80
#: 简介上限（字符数）。
MAX_DESC = 2000
#: 标签数量上限。
MAX_TAGS = 10
#: 定时发布下限（小时）。平台要求距今至少 4 小时，见 metadata.DTIME_MIN_OFFSET。
MIN_OFFSET_HOURS = 4.0


def validate_title(title: str) -> None:
    """标题长度校验，超限抛 :class:`BiliError`。"""
    if len(title) > MAX_TITLE:
        raise BiliError(f"标题超过 {MAX_TITLE} 字（当前 {len(title)} 字）")


def validate_desc(desc: str) -> None:
    """简介长度校验，超限抛 :class:`BiliError`。"""
    if len(desc) > MAX_DESC:
        raise BiliError(f"简介超过 {MAX_DESC} 字（当前 {len(desc)} 字）")


def validate_tag_list(tags: "list[str]") -> None:
    """标签数量校验（以列表传入，单文件页用）。"""
    if len(tags) > MAX_TAGS:
        raise BiliError(f"标签超过 {MAX_TAGS} 个（当前 {len(tags)} 个）")


def validate_tag_text(tag: str) -> None:
    """标签数量校验（以逗号 / 空白分隔的字符串传入，批量页用）。

    批量页的标签是普通文本框，用户用逗号（含中文逗号）或空白分隔，
    这里按同样的分隔方式数个数，和单文件页的列表计数保持同一标准。
    """
    if not tag:
        return
    parts = [t.strip() for t in tag.replace("，", ",").split(",") if t.strip()]
    validate_tag_list(parts)


def validate_offset_hours(offset: "float | None") -> None:
    """定时发布下限校验：必须距今至少 4 小时（与 resolve_dtime 一致）。"""
    if offset is None:
        return
    if offset < MIN_OFFSET_HOURS:
        raise BiliError(
            f"定时发布必须距今至少 {MIN_OFFSET_HOURS:g} 小时"
            f"（当前设定 {offset:g} 小时），请调大延时"
        )


def parse_offset(text: str) -> "float | None":
    """把「6」这样的输入转成小时数，空串表示立即发布。

    非数字、或不足 4 小时都会抛 :class:`BiliError`——前者是输入错误，
    后者在提交时必被 ``resolve_dtime`` 拦下，不如界面上先拦住。
    """
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise BiliError(f"延时发布小时数不是数字: {raw}") from exc
    validate_offset_hours(value)
    return value
