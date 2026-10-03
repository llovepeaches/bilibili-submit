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

__all__ = [
    "AccountConfig",
    "UploadConfig",
    "SubmitConfig",
    "DefaultsConfig",
    "TaskConfig",
    "AppConfig",
    "load_config",
]

DEFAULT_COOKIE_FILE = os.path.expanduser("~/.config/bilibili_submit/cookie.json")


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


@dataclass
class TaskConfig:
    name: str = "未命名任务"
    type: str = "single"               # single | batch
    # single
    file: str | None = None
    # batch
    dir: str | None = None
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

    if not cfg.tasks:
        raise ConfigError("配置中没有 tasks，至少要有一个投稿任务")
    for task in cfg.tasks:
        if task.type not in ("single", "batch"):
            raise ConfigError(f"任务 {task.name!r} 的 type 必须是 single 或 batch")
        if task.type == "single" and not task.file:
            raise ConfigError(f"任务 {task.name!r} 是 single 类型但缺少 file")
        if task.type == "batch" and not task.dir:
            raise ConfigError(f"任务 {task.name!r} 是 batch 类型但缺少 dir")

    return cfg


def expand_tasks(cfg: AppConfig) -> list[TaskConfig]:
    """把 batch 任务按目录展开成单个任务列表，并继承 defaults。"""
    out: list[TaskConfig] = []
    for task in cfg.tasks:
        if task.type == "single":
            out.append(task.merged(cfg.defaults))
            continue

        directory = Path(task.dir or ".").expanduser()
        if not directory.is_dir():
            raise ConfigError(f"任务 {task.name!r} 的目录不存在: {directory}")

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


def tid_known(tid: int) -> bool:
    return int(tid) in COMMON_TIDS


def task_dtime(task: TaskConfig) -> int | None:
    """解析任务的定时时间，含 4 小时下限校验。"""
    return resolve_dtime(task.dtime, task.dtime_offset_hours)
