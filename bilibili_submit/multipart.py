"""分 P 稿件：把多个视频文件合成**一个**稿件的多个分 P。

B 站的多 P 是一个 av/bv 号下面挂 P1、P2、P3……，播放器里可以连续切换。
投稿接口本来就支持：payload 的 ``videos`` 是个数组，每一项有自己的
``filename`` 和 ``title``。所以多 P 的关键不在接口，而在**决定哪些文件
算同一组**——本模块只解决这一件事，上传和投递交给 :mod:`.scheduler`。

分组规则刻意做得**保守且可预测**：

- 只认文件名**尾部**的序号，中间的编号不动
  （``第3集 正片.mp4`` 里的 3 不参与分组）。
- 剥序号前先剥分辨率后缀（``_1080p``/``_4k``），否则
  ``旅行_01_1080p.mp4`` 和 ``旅行_02_1080p.mp4`` 会被当成两个不同前缀。
- 组名归一化时忽略大小写和分隔符差异，但**显示**用组内第一个文件的原文，
  免得用户看到被改过字的标题。

分组可能出错（自动规则总有反例），所以调用方要把结果**展示给用户并允许
改回去**——本模块不做"猜错了就硬合并"的事。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

__all__ = ["PartGroup", "group_by_prefix", "natural_key", "strip_part_marker"]

#: 文件名里常见的分隔符（含全角括号，中文用户粘贴的文件名里很常见）
_SEP = r"[\s_\-\.()\[\]（）【】]*"

#: 分辨率后缀。下载站和录屏软件爱往文件名尾部挂，不剥掉就分不成组。
_RESOLUTION_RE = re.compile(
    rf"{_SEP}(?:\d{{3,4}}p|4k|2k|hd|fhd|uhd)(?:60|50)?$", re.IGNORECASE
)

#: ``第3集``/``第3话``——中文连载最常见的序号写法
_EPISODE_RE = re.compile(
    rf"{_SEP}第\s*(\d{{1,4}})\s*[集话期章节篇]{_SEP}$", re.IGNORECASE
)

#: 尾部序号：``_01`` / ``-2`` / ``(3)`` / ``P4`` / ``part5`` / ``ep06``
_TAIL_NUM_RE = re.compile(
    rf"{_SEP}(?:p|part|ep|episode|no|#)?\s*(\d{{1,4}}){_SEP}$", re.IGNORECASE
)


@dataclass(frozen=True)
class PartGroup:
    """一个分 P 组：组名 + 组内文件（已按自然序排好）。"""

    name: str
    files: tuple[Path, ...]

    @property
    def is_single(self) -> bool:
        """组内只有一个文件时，它就是一个普通单 P 稿件。"""
        return len(self.files) <= 1


def natural_key(text: str) -> tuple:
    """自然排序键：``a2`` 排在 ``a10`` 前面。

    纯字典序会把 ``P10`` 排到 ``P2`` 前面，分 P 顺序就乱了，
    所以数字段按数值比较，非数字段按大小写无关比较。
    """
    parts = re.findall(r"\d+|\D+", text)
    return tuple(
        (1, int(seg), "") if seg.isdigit() else (0, 0, seg.casefold())
        for seg in parts
    )


def strip_part_marker(stem: str) -> tuple[str, int | None]:
    """去掉文件名尾部的序号，返回 ``(去掉序号后的名字, 序号)``。

    没有序号时原样返回、序号为 ``None``。

    >>> strip_part_marker("旅行_01")
    ('旅行', 1)
    >>> strip_part_marker("教程_a")
    ('教程_a', None)
    >>> strip_part_marker("第3集")
    ('', 3)
    """
    text = stem.strip()

    # 分辨率后缀先剥：旅行_01_1080p 的尾部是 1080p，不是序号
    stripped = _RESOLUTION_RE.sub("", text)
    if stripped:
        text = stripped

    match = _EPISODE_RE.search(text)
    if match:
        return text[: match.start()].strip(), int(match.group(1))

    match = _TAIL_NUM_RE.search(text)
    if match:
        return text[: match.start()].strip(), int(match.group(1))

    return text, None


def _group_key(base: str) -> str:
    """组名的比较键：忽略大小写与分隔符差异。

    ``旅行-01`` 和 ``旅行_02`` 去掉序号后是 ``旅行`` 对 ``旅行``，
    但若用户一个用连字符一个用下划线（``旅 行``/``旅行_``），
    去分隔符后仍应算同一组。
    """
    return re.sub(r"[\s_\-\.()\[\]（）【】]+", "", base).casefold()


def _starts_at_one(nums: list[int | None]) -> bool:
    """纯序号组的额外约束：必须是从 1 开始的连续数列。

    ``01.mp4``/``02.mp4`` 显然是同一套；但 ``2023.mp4``/``2024.mp4``
    也"连续"，却是两个不相干的年度视频——名字里除了数字没有别的信息，
    无从判断关联，只能靠"从 1 开始"这条硬指标兜底。

    误合并的代价（两个无关视频被投成同一个稿件的两 P，要删稿重投）
    远大于漏合并（多占一个 av 号），所以这里宁可不合并。
    """
    if any(num is None for num in nums):
        return False
    return sorted(nums) == list(range(1, len(nums) + 1))


def group_by_prefix(files: list[Path]) -> list[PartGroup]:
    """按文件名前缀把视频文件分成若干分 P 组。

    同名的（去掉尾部序号后一致）归为一组，组内按自然序排列——
    ``P2`` 会排在 ``P10`` 前面。没有序号的文件自成一个单文件组。

    返回的组顺序按组内第一个文件的自然序，保证列表稳定、可预期。

    .. note::
       本函数**不做**"少于 N 个就不成组"的判断：单文件组照样返回。
       要不要按多 P 投递由调用方决定（见 :attr:`PartGroup.is_single`），
       分组本身只回答"哪些文件看起来是一套"。
    """
    buckets: dict[str, list[tuple[Path, int | None]]] = {}
    display: dict[str, str] = {}

    for path in files:
        base, num = strip_part_marker(path.stem)
        key = _group_key(base)
        buckets.setdefault(key, []).append((path, num))
        # 显示名用第一次遇到的原文，保留用户自己的大小写和分隔符
        display.setdefault(key, base or "")

    groups: list[PartGroup] = []
    for key, members in buckets.items():
        ordered = sorted(members, key=lambda item: natural_key(item[0].name))
        paths = [path for path, _ in ordered]

        if len(paths) == 1:
            # 单文件就是普通投稿，组名用文件名而不是下面的目录名
            groups.append(PartGroup(name=paths[0].stem, files=(paths[0],)))
            continue

        if not key and not _starts_at_one([num for _, num in ordered]):
            # 纯序号又不是从 1 连续：不敢合并，各自独立投稿
            for path in paths:
                groups.append(PartGroup(name=path.stem, files=(path,)))
            continue

        name = display[key]
        if not name:
            # 纯序号命名（01.mp4 / 第3集.mp4）：拿目录名当组名，
            # 否则列表里会冒出一堆空标题
            name = paths[0].parent.name or "分P稿件"
        groups.append(PartGroup(name=name, files=tuple(paths)))

    groups.sort(key=lambda g: natural_key(g.files[0].name))
    return groups
