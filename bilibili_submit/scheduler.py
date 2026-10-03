"""任务执行引擎：把配置任务跑完，并记录投稿历史。"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .client import BiliClient
from .config import AppConfig, TaskConfig, task_dtime
from .cover import resolve_cover
from .exceptions import BiliError, ConfigError
from .lines import pick_line
from .metadata import ArchiveMeta, fetch_tids, validate_tid
from .submit import SubmitBackend, get_backend, submit_archive
from .upload import upload_video

logger = logging.getLogger(__name__)

DEFAULT_HISTORY_FILE = os.path.expanduser(
    "~/.config/bilibili_submit/history.json"
)

__all__ = [
    "TaskOutcome",
    "run_task",
    "run_all",
    "append_history",
    "read_history",
]


@dataclass
class TaskOutcome:
    name: str
    success: bool
    file: str = ""
    aid: int = 0
    bvid: str = ""
    url: str = ""
    error: str = ""


@dataclass
class RunOptions:
    dry_run: bool = False
    history_file: str = DEFAULT_HISTORY_FILE
    on_progress: Callable[[str], None] | None = None


def _emit(options: RunOptions, message: str) -> None:
    logger.info(message)
    if options.on_progress:
        options.on_progress(message)


def run_task(
    client: BiliClient,
    task: TaskConfig,
    cfg: AppConfig,
    backend: SubmitBackend | None = None,
    options: RunOptions | None = None,
) -> TaskOutcome:
    """执行单个投稿任务：上传 → 封面 → 投递。"""
    options = options or RunOptions()
    source = Path(task.file or "")
    outcome = TaskOutcome(name=task.name, success=False, file=str(source))

    if not source.is_file():
        outcome.error = f"视频文件不存在: {source}"
        return outcome

    if options.dry_run:
        _emit(options, f"[dry-run] 将投稿 {source}（tid={task.tid}）")
        outcome.success = True
        return outcome

    try:
        dtime = task_dtime(task)
        validate_tid(int(task.tid or 21))

        # 1) 上传视频
        line = None
        if cfg.upload.line and cfg.upload.line != "auto":
            line = pick_line(client, prefer=cfg.upload.line)
        result = upload_video(
            client,
            source,
            line=line,
            profile=cfg.upload.profile,
            concurrency=cfg.upload.concurrency,
            resume=cfg.upload.resume,
            chunk_retries=cfg.upload.chunk_retries,
            show_progress=cfg.upload.show_progress,
        )
        _emit(options, f"上传完成: {source.name} → filename={result.filename}")

        # 2) 封面
        cover_url = None
        if task.cover:
            cover_url = resolve_cover(client, task.cover, source)
            if cover_url:
                _emit(options, f"封面已上传: {cover_url}")

        # 3) 组装并投递
        meta = ArchiveMeta(
            title=task.title or source.stem,
            tid=int(task.tid or 21),
            tag=task.tag or "",
            desc=task.desc or "",
            cover=cover_url,
            copyright=int(task.copyright or 1),
            source=task.source or "",
            dtime=dtime,
            videos=[{"filename": result.filename, "title": source.stem, "desc": ""}],
        )
        submitted = submit_archive(
            client,
            meta,
            backend=backend,
            cooldowns=cfg.submit.cooldown_seconds,
            on_wait=lambda wait, attempt: _emit(
                options, f"被频控拦截，等待 {wait:.0f}s 后重试（第 {attempt} 次）"
            ),
        )
        outcome.success = True
        outcome.aid = submitted.aid
        outcome.bvid = submitted.bvid
        outcome.url = submitted.url
        _emit(options, f"投稿成功: {submitted}  {submitted.url}")
    except (BiliError, ConfigError) as exc:
        outcome.error = str(exc)
        logger.error("任务 %s 失败: %s", task.name, exc)
    except Exception as exc:  # noqa: BLE001 - 单条失败不应拖垮整批
        outcome.error = f"{type(exc).__name__}: {exc}"
        logger.exception("任务 %s 发生未预期错误", task.name)

    return outcome


def run_all(
    client: BiliClient,
    tasks: list[TaskConfig],
    cfg: AppConfig,
    options: RunOptions | None = None,
) -> list[TaskOutcome]:
    """按顺序执行任务，批量任务之间按配置间隔等待，规避频控。"""
    options = options or RunOptions()
    backend = get_backend(cfg.submit.backend, cfg.submit.app)
    outcomes: list[TaskOutcome] = []

    for index, task in enumerate(tasks):
        outcome = run_task(client, task, cfg, backend=backend, options=options)
        outcomes.append(outcome)

        if outcome.success and options.history_file and not options.dry_run:
            append_history(outcome, options.history_file)

        if not outcome.success and getattr(task, "stop_on_error", True):
            logger.error("任务 %s 失败，按 stop_on_error 停止后续任务", task.name)
            break

        interval = float(getattr(task, "per_task_interval_minutes", 0) or 0)
        if interval > 0 and index < len(tasks) - 1:
            seconds = interval * 60
            _emit(options, f"等待 {interval:.0f} 分钟以规避频控后继续下一条")
            if not options.dry_run:
                time.sleep(seconds)

    return outcomes


# ---------- 历史记录 ----------


def append_history(outcome: TaskOutcome, path: str = DEFAULT_HISTORY_FILE) -> None:
    """把成功记录追加到历史文件（只记成功，失败信息不落盘）。"""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = read_history(path)
    entries.append(
        {
            "name": outcome.name,
            "file": outcome.file,
            "bvid": outcome.bvid,
            "url": outcome.url,
            "time": time.time(),
        }
    )
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, path)


def read_history(path: str = DEFAULT_HISTORY_FILE) -> list[dict[str, Any]]:
    path = Path(path).expanduser()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def tids_for_check(client: BiliClient) -> dict[int, str]:
    """供 check 命令使用：拉取当前账号可用分区。"""
    return fetch_tids(client)
