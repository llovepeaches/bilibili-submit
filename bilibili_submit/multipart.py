"""分 P 稿件：把多个视频文件合成**一个**稿件的多个分 P。

B 站的多 P 是一个 av/bv 号下面挂 P1、P2、P3……，播放器里可以连续切换。
投稿接口本来就支持：payload 的 ``videos`` 是个数组，每一项有自己的
``filename`` 和 ``title``。所以多 P 的关键不在接口，而在**决定哪些文件
算同一组**——本模块只解决这一件事，上传和投递交给 :mod:`.scheduler`。

分组有三种依据，按 :data:`GROUP_MODES` 里指定的模式选一种：

- ``prefix``——文件名前缀（``旅行_01.mp4`` / ``旅行_02.mp4`` 是一套）；
- ``folder``——所在文件夹（``旅行/`` 底下的都是一套）；
- ``whole_dir``——整个目录下的所有文件合成一个稿件。

前缀模式的规则刻意做得**保守且可预测**：

- 只认文件名**尾部**的序号，中间的编号不动
  （``第3集 正片.mp4`` 里的 3 不参与分组）。
- 剥序号前先剥分辨率后缀（``_1080p``/``_4k``），否则
  ``旅行_01_1080p.mp4`` 和 ``旅行_02_1080p.mp4`` 会被当成两个不同前缀。
- 组名归一化时忽略大小写和分隔符差异，但**显示**用组内第一个文件的原文，
  免得用户看到被改过字的标题。

文件夹模式更省心——目录结构是用户自己建的，比文件名可靠；但**根目录
第一层的散落文件不合并**：它们没有"属于某个文件夹"的语义，硬按目录名
合成一个稿件，会把几个互不相干的视频投成同一稿件的分 P。

分组可能出错（自动规则总有反例），所以调用方要把结果**展示给用户并允许
改回去**——本模块不做"猜错了就硬合并"的事。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "GROUP_MODES",
    "PartGroup",
    "group_by_folder",
    "group_by_prefix",
    "group_by_whole_dir",
    "group_files",
    "natural_key",
    "strip_part_marker",
]

#: 可用的分组依据。UI 下拉框与配置校验共用这一份，
#: 免得两边各写一套字符串、改一处漏一处。
GROUP_MODES = ("prefix", "folder", "whole_dir")

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


def group_by_folder(files: list[Path], root: Path | None = None) -> list[PartGroup]:
    """按所在文件夹分组：同一个子文件夹里的视频合成一个稿件。

    组名取文件夹名——那是用户自己起的名字，通常比文件名有意义
    （``教程/output.mp4`` 里的 ``教程`` 才是他想要的标题）。

    Args:
        root: 扫描的根目录。**该目录第一层的散落文件不合并**，各自
            独立投稿。它们没有被放进任何子文件夹，说明用户没把它们
            当成一套；硬按目录名合成一个稿件，就是把几个不相干的
            视频投成同一稿件的分 P——误合并要删稿重投，代价远高于
            多占一个 av 号。子文件夹则无论有几个文件都成组。

    .. note::
       组顺序按**文件夹名**排，不按组内文件名排。后者会让不同文件夹
       里同名的文件（``旅行/01.mp4`` 与 ``教程/01.mp4``）互相干扰，
       排出来的顺序跟目录结构对不上。
    """
    buckets: dict[Path, list[Path]] = {}
    for path in files:
        buckets.setdefault(path.parent.resolve(), []).append(path)

    root_key = Path(root).resolve() if root is not None else None

    groups: list[PartGroup] = []
    for folder, paths in buckets.items():
        ordered = sorted(paths, key=lambda path: natural_key(path.name))

        if root_key is not None and folder == root_key:
            # 根目录散落文件：没有"一套"的语义，各自独立
            for path in ordered:
                groups.append(PartGroup(name=path.stem, files=(path,)))
            continue

        # 子文件夹里即使只有一个文件也成组：文件夹名是用户起的标题
        groups.append(
            PartGroup(name=folder.name or "分P稿件", files=tuple(ordered))
        )

    groups.sort(key=lambda g: natural_key(g.name))
    return groups


def group_by_whole_dir(files: list[Path], root: Path | None = None) -> list[PartGroup]:
    """把整个目录下的所有文件合成一个分 P 稿件。

    组名取根目录名（用户选的那个文件夹名），没有 root 时取第一个文件
    的父目录名。组内按自然序排。

    这适合用户已经把一套视频整整齐齐放在一个文件夹里的场景——
    不需要再建一层子文件夹，直接按当前目录合并。
    """
    if not files:
        return []
    ordered = sorted(files, key=lambda path: natural_key(path.name))
    name = root.name if root is not None else ordered[0].parent.name
    return [PartGroup(name=name or "分P稿件", files=tuple(ordered))]


def group_files(
    files: list[Path], mode: str, root: Path | None = None
) -> list[PartGroup]:
    """按给定模式分组，是三种模式的统一入口。

    ``mode`` 取 :data:`GROUP_MODES` 之一；不认识的模式返回"每个文件
    自成一组"（等价于不合并），而不是抛异常——分组只是猜用户意图，
    猜不出来退回最保守的结果，比让整次扫描失败好。
    """
    if mode == "folder":
        return group_by_folder(files, root)
    if mode == "prefix":
        return group_by_prefix(files)
    if mode == "whole_dir":
        return group_by_whole_dir(files, root)
    return [PartGroup(name=path.stem, files=(path,)) for path in files]
