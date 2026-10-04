"""配置文件加载与校验。

用 dataclass 手写校验而不是引入 pydantic——字段不多，手写更轻，
且能把「缺失即报错」和「可默认值」区分清楚。
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .exceptions import ConfigError
from .metadata import COMMON_TIDS, resolve_dtime
from .multipart import GROUP_MODES, PartGroup, group_files

__all__ = [
    "AccountConfig",
    "UploadConfig",
    "SubmitConfig",
    "DefaultsConfig",
    "TaskConfig",
    "AppConfig",
    "VIDEO_SUFFIXES",
    "load_config",
    "expand_tasks",
    "scan_video_files",
    "task_files",
    "task_part_titles",
]

DEFAULT_COOKIE_FILE = os.path.expanduser("~/.config/bilibili_submit/cookie.json")

#: 客户端「选文件夹」时认的视频扩展名。
#:
#: 与 yaml 里 ``include: ["*.mp4"]`` 的通配符语义**分开**：那边的
#: pattern 由用户自己写，这里是给不想碰配置文件的人一个开箱可用的默认值。
VIDEO_SUFFIXES = frozenset(
    {
        ".mp4",
        ".mkv",
        ".flv",
        ".avi",
        ".mov",
        ".webm",
        ".wmv",
        ".m4v",
        ".mpeg",
        ".mpg",
        ".ts",
    }
)


@dataclass
class AccountConfig:
    cookie_file: str = DEFAULT_COOKIE_FILE
    proxy: str | None = None


@dataclass
class UploadConfig:
    line: str = "auto"                 # auto 或具体 upcdn（bda2/tx/estx/bldsa/akbd）
    profile: str = "ugcupos/bup"       # 海外改 ugcupos/bupfetch
    concurrency: int = 3
    resume: bool = True
    chunk_retries: int = 3
    show_progress: bool = True

    def __post_init__(self) -> None:
        self.concurrency = max(1, min(int(self.concurrency), 4))
        self.chunk_retries = max(1, int(self.chunk_retries))


@dataclass
class SubmitConfig:
    backend: str = "web"               # web / web/v2 / app
    cooldown_seconds: tuple[int, ...] = (300, 600, 1200, 1800, 3600)
    app: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.cooldown_seconds, (int, float)):
            self.cooldown_seconds = (int(self.cooldown_seconds),)
        self.cooldown_seconds = tuple(int(x) for x in self.cooldown_seconds) or (300,)


@dataclass
class DefaultsConfig:
    tid: int = 21
    copyright: int = 1
    source: str = ""
    tag: str = ""
    desc: str = ""
    cover: str | None = None
    no_reprint: int = 0
    mission_id: int | None = None
    dynamic: str = ""
    dtime: int | None = None
    dtime_offset_hours: float | None = None
    # 互动设置。字段名与 B 站接口一致，省掉一层翻译。
    up_close_reply: bool = False       # 关闭评论区
    up_close_danmu: bool = False       # 关闭弹幕
    up_selection_reply: bool = False   # 开启精选评论
    # 音质增强。``hires`` 在这里用用户听得懂的名字，投递时映射到接口
    # 字段 ``lossless_music``（B 站官方文档就叫这个，不叫 hires）。
    dolby: int = 0                     # 杜比音效
    hires: int = 0                     # Hi-Res 无损音质

    def __post_init__(self) -> None:
        # 写 "1" / true / yes 都认。不能直接 int(bool(...))：
        # bool("0") 是 True，手改配置时写 dolby: "0" 会被当成开启，正好弄反。
        self.no_reprint = _as_flag(self.no_reprint)
        self.dolby = _as_flag(self.dolby)
        self.hires = _as_flag(self.hires)
        self.up_close_reply = _as_bool(self.up_close_reply)
        self.up_close_danmu = _as_bool(self.up_close_danmu)
        self.up_selection_reply = _as_bool(self.up_selection_reply)


@dataclass
class TaskConfig:
    name: str = "未命名任务"
    type: str = "single"               # single | batch | multip
    # single
    file: str | None = None
    # multip：一个稿件挂多个分 P，files 的顺序就是 P1/P2/P3 的顺序
    files: list[str] = field(default_factory=list)
    part_titles: list[str] = field(default_factory=list)
    # batch
    dir: str | None = None
    group_by: str = "none"             # none | prefix | folder（仅 batch 生效）
    include: list[str] = field(default_factory=lambda: ["*.mp4"])
    exclude: list[str] = field(default_factory=list)
    sort: str = "name"                 # name | mtime
    max_items: int | None = None
    # 元数据（可被模板覆盖）
    title: str | None = None
    title_template: str | None = None
    desc: str | None = None
    desc_template: str | None = None
    tag: str | None = None
    cover: str | None = None
    tid: int | None = None
    copyright: int | None = None
    source: str | None = None
    no_reprint: int | None = None
    dynamic: str | None = None
    mission_id: int | None = None
    dtime: int | None = None
    dtime_offset_hours: float | None = None
    # 互动设置与音质增强。None 表示「没配」，走 defaults 的值——
    # 所以想显式关掉要写 false / 0，不能靠省略（省略等于听 defaults 的）。
    up_close_reply: bool | None = None
    up_close_danmu: bool | None = None
    up_selection_reply: bool | None = None
    dolby: int | None = None
    hires: int | None = None
    # 批量节奏控制
    per_task_interval_minutes: float = 0.0
    stop_on_error: bool = True
    # 运行时填充
    index: int = 0

    def merged(self, defaults: DefaultsConfig) -> "TaskConfig":
        """用 defaults 填补未设置的字段。"""
        out = TaskConfig(**{f.name: getattr(self, f.name) for f in fields(self)})
        for f in fields(DefaultsConfig):
            current = getattr(out, f.name, None)
            if current is None:
                setattr(out, f.name, getattr(defaults, f.name))
        return out


@dataclass
class AppConfig:
    account: AccountConfig = field(default_factory=AccountConfig)
    upload: UploadConfig = field(default_factory=UploadConfig)
    submit: SubmitConfig = field(default_factory=SubmitConfig)
    defaults: DefaultsConfig = field(default_factory=DefaultsConfig)
    tasks: list[TaskConfig] = field(default_factory=list)


#: 配置文件里表示「开」的写法。手改配置的人不会记得引号规则，
#: 见 :func:`_as_flag` 为什么不能直接 ``int(bool(value))``。
_TRUTHY = {"1", "true", "yes", "on", "y", "t"}


def _as_flag(value: object) -> int:
    """把开关类的配置值统一成 0/1，返回 int。"""
    if isinstance(value, str):
        return 1 if value.strip().lower() in _TRUTHY else 0
    return int(bool(value))


def _as_bool(value: object) -> bool:
    """把开关类的配置值统一成 bool。"""
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY
    return bool(value)


def _build(cls: Any, data: Mapping[str, Any] | None) -> Any:
    data = data or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{cls.__name__} 配置段应为字典，实际为 {type(data).__name__}")
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"{cls.__name__} 含未知字段: {sorted(unknown)}")
    return cls(**data)


def load_config(path: str | Path) -> AppConfig:
    """读取并校验配置文件。"""
    path = Path(path).expanduser()
    if not path.is_file():
        raise ConfigError(f"配置文件不存在: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件不是合法 YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("配置文件顶层应为字典")

    cfg = AppConfig(
        account=_build(AccountConfig, raw.get("account")),
        upload=_build(UploadConfig, raw.get("upload")),
        submit=_build(SubmitConfig, raw.get("submit")),
        defaults=_build(DefaultsConfig, raw.get("defaults")),
        tasks=[_build(TaskConfig, t) for t in (raw.get("tasks") or [])],
    )

    _validate_tasks(cfg)
    return cfg


def _validate_tasks(cfg: AppConfig) -> None:
    """逐条校验任务配置。

    抽成独立函数是因为 single / batch / multip 三种任务的必填项各不相同
    （还多出一条 part_titles 的联动约束），全挤在 :func:`load_config`
    里会让那个函数一眼读不完。
    """
    if not cfg.tasks:
        raise ConfigError("配置中没有 tasks，至少要有一个投稿任务")
    for task in cfg.tasks:
        if task.type not in ("single", "batch", "multip"):
            raise ConfigError(
                f"任务 {task.name!r} 的 type 必须是 single / batch / multip"
            )
        if task.type == "single" and not task.file:
            raise ConfigError(f"任务 {task.name!r} 是 single 类型但缺少 file")
        if task.type == "multip" and not (task.files or task.dir):
            raise ConfigError(
                f"任务 {task.name!r} 是 multip 类型但缺少 files"
                "（也可以给 dir，目录下所有视频即分 P）"
            )
        if task.type == "batch" and not task.dir:
            raise ConfigError(f"任务 {task.name!r} 是 batch 类型但缺少 dir")
        if task.part_titles and not task.files:
            raise ConfigError(
                f"任务 {task.name!r} 配了 part_titles 但没有 files"
                "（分 P 标题只对显式列出 files 的多 P 任务有意义，"
                "用 dir 的任务标题取文件名）"
            )
        if task.group_by not in ("none", *GROUP_MODES):
            raise ConfigError(
                f"任务 {task.name!r} 的 group_by 必须是 "
                f"none / {' / '.join(GROUP_MODES)}，实际是 {task.group_by!r}"
            )
        if task.group_by != "none" and task.type != "batch":
            raise ConfigError(
                f"任务 {task.name!r} 配了 group_by 但 type 是 {task.type!r}"
                "（分组只用于把 batch 目录展开成多个多 P 稿件）"
            )


def scan_video_files(directory: str | Path, max_depth: int = 1) -> list[Path]:
    """扫出目录里的视频文件，同目录内按文件名排序。

    客户端批量任务页用它代替 yaml 配置：选个文件夹就能出一批任务，
    不用先写 ``config.yaml``。

    .. important::
       **默认只扫第一层，不递归。** 用户在文件对话框里很容易指到
       「视频」这种大目录，递归下去可能一次生成上千个任务——
       真正想要多层目录时要显式把 ``max_depth`` 传大，而不是默认替他决定。
       按文件夹分 P 时会传 2（多钻一层子目录），上限也就到此为止。

    Args:
        max_depth: 扫几层目录。``1`` 只扫该目录本身；``2`` 再加上
            直接子文件夹。小于 1 的值会被抬回 1。

    扩展名大小写不敏感（``.MP4`` 也要认），排序用 ``casefold()``
    以免同一批文件在不同系统上顺序不同。

    排序键带上父目录：单层扫描时所有文件父目录相同，结果与既往一致；
    多层扫描时同一文件夹的文件会聚在一起，不会和其他文件夹的同名文件
    交错——分 P 组内顺序就是从这个顺序来的，交错会让 P1/P2 看起来很乱。
    """
    directory = Path(directory).expanduser()
    if not directory.is_dir():
        raise ConfigError(f"视频目录不存在: {directory}")

    depth = max(1, int(max_depth))
    found: list[Path] = []
    _collect_videos(directory, depth, 1, found)
    return sorted(
        found,
        key=lambda path: (str(path.parent).casefold(), path.name.casefold()),
    )


def _collect_videos(
    directory: Path, max_depth: int, depth: int, out: list[Path]
) -> None:
    """把 ``directory`` 下的视频收进 ``out``，目录最多再进 ``max_depth`` 层。"""
    for path in sorted(directory.iterdir(), key=lambda p: p.name.casefold()):
        if path.is_file():
            if path.suffix.lower() in VIDEO_SUFFIXES:
                out.append(path)
        elif path.is_dir() and depth < max_depth:
            _collect_videos(path, max_depth, depth + 1, out)


def expand_tasks(cfg: AppConfig) -> list[TaskConfig]:
    """把 batch 任务按目录展开成单个任务列表，并继承 defaults。"""
    out: list[TaskConfig] = []
    for task in cfg.tasks:
        # multip 本身就是一个稿件（内含多个分 P），不需要再展开
        if task.type in ("single", "multip"):
            out.append(task.merged(cfg.defaults))
            continue

        directory = Path(task.dir or ".").expanduser()
        if not directory.is_dir():
            raise ConfigError(f"任务 {task.name!r} 的目录不存在: {directory}")

        if task.group_by in GROUP_MODES:
            # 按前缀/文件夹分组：一个组出一个稿件（组内多个就是多 P）。
            # 分组要知道哪些文件属于根目录，所以扫到子文件夹一层。
            files = scan_video_files(directory, max_depth=2)
            if not files:
                raise ConfigError(
                    f"任务 {task.name!r} 目录下没有匹配的文件: {directory}"
                )
            groups = group_files(files, task.group_by, directory)
            if task.max_items:
                groups = groups[: int(task.max_items)]
            out.extend(
                _group_to_tasks(task, cfg.defaults, group, index)
                for index, group in enumerate(groups, start=1)
            )
            continue

        files = _scan_files(directory, task.include, task.exclude, task.sort)
        if task.max_items:
            files = files[: int(task.max_items)]
        if not files:
            raise ConfigError(f"任务 {task.name!r} 目录下没有匹配的文件: {directory}")

        for index, file in enumerate(files, start=1):
            merged = task.merged(cfg.defaults)
            merged.type = "single"
            merged.file = str(file)
            merged.index = index
            merged.name = f"{task.name}#{index}"
            if merged.title_template:
                merged.title = merged.title_template.format(
                    stem=file.stem, name=file.name, n=index, index=index
                )
            elif merged.title is None:
                merged.title = file.stem
            if merged.desc_template:
                merged.desc = merged.desc_template.format(
                    stem=file.stem, name=file.name, n=index, index=index
                )
            out.append(merged)
    return out


def _group_to_tasks(
    task: TaskConfig,
    defaults: DefaultsConfig,
    group: "PartGroup",
    index: int,
) -> TaskConfig:
    """一个分 P 组 → 一个任务。

    单文件组退化成普通单 P 投稿，但标题仍取组名（文件夹名）——
    那是用户自己起的名字，通常比 ``output.mp4`` 这种文件名像样。

    ``dir`` 展开完就置空：下游 :func:`task_files` 见到 dir 会再去扫一遍，
    而这里已经给出确切的文件列表了，重复扫既浪费又可能扫出别的东西。
    """
    merged = task.merged(defaults)
    merged.type = "single" if len(group.files) == 1 else "multip"
    if merged.type == "multip":
        merged.files = [str(path) for path in group.files]
    else:
        merged.file = str(group.files[0])
    merged.dir = None
    merged.group_by = "none"
    merged.index = index
    merged.name = f"{task.name}#{index}"
    if merged.title_template:
        merged.title = merged.title_template.format(
            stem=group.name, name=group.name, n=index, index=index
        )
    elif merged.title is None:
        merged.title = group.name
    if merged.desc_template:
        merged.desc = merged.desc_template.format(
            stem=group.name, name=group.name, n=index, index=index
        )
    return merged


def _scan_files(
    directory: Path,
    include: Iterable[str],
    exclude: Iterable[str],
    sort_by: str,
) -> list[Path]:
    from fnmatch import fnmatch

    patterns = list(include) or ["*.mp4"]
    excludes = list(exclude)
    found: list[Path] = []
    for pattern in patterns:
        for path in directory.glob(pattern):
            if not path.is_file():
                continue
            if any(fnmatch(path.name, ex) for ex in excludes):
                continue
            if path not in found:
                found.append(path)

    if sort_by == "mtime":
        found.sort(key=lambda p: p.stat().st_mtime)
    else:
        found.sort(key=lambda p: p.name)
    return found


def task_files(task: TaskConfig) -> list[Path]:
    """任务要上传的视频文件。

    多 P 任务：显式 ``files`` 优先（顺序即 P1/P2/P3 的顺序）；
    没给时若配了 ``dir``，就扫该目录下的视频——「这一个文件夹就是一套
    视频」是最省事的写法，不用把十几个文件名敲一遍。
    其余返回单个 ``file``。都没有时返回空列表，由调用方报错。
    """
    if task.files:
        return [Path(item).expanduser() for item in task.files]
    # 只有 multip 认 dir：batch 展开出来的 single 任务也带着 dir 字段
    # （历史行为），在这里跟着扫会把「每文件一个任务」变成「每任务都是
    # 整个目录」，所以这里必须限定 type。
    if task.type == "multip" and task.dir and not task.files:
        return scan_video_files(Path(task.dir).expanduser())
    if task.file:
        return [Path(task.file).expanduser()]
    return []


def task_part_titles(task: TaskConfig, files: list[Path]) -> list[str]:
    """每个分 P 的标题。

    优先用配置里的 ``part_titles``（按下标对应），没配或配短了的部分
    回落到文件名——分 P 标题空着会让播放器里显示一片空白，
    宁可显示文件名也不要留空。
    """
    configured = list(task.part_titles or [])
    titles: list[str] = []
    for index, path in enumerate(files):
        custom = configured[index].strip() if index < len(configured) else ""
        titles.append(custom or path.stem)
    return titles


def tid_known(tid: int) -> bool:
    return int(tid) in COMMON_TIDS


def task_dtime(task: TaskConfig) -> int | None:
    """解析任务的定时时间，含 4 小时下限校验。"""
    return resolve_dtime(task.dtime, task.dtime_offset_hours)
