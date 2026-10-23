"""Web 投稿：从「选中的目录」到「真的投出去」。

分两步是有意的：

1. :func:`build_preview` 只**扫目录、分组、起标题**，不碰网络也不写
   任何东西。前端把它渲染成可勾选、可改标题的列表，用户确认后才提交。
2. :func:`run_submit` 在后台线程跑，走核心层的
   :func:`~bilibili_submit.scheduler.run_all`。

为什么不自己写「逐个任务 + 写历史」的循环：那要把
``run_all`` 里的三条业务策略（后端选择、``stop_on_error``、投稿间隔）
在 Web 里再实现一遍。复制的时候看起来只有十几行，但以后任何一条
策略调整都会只改一处——而 Web 那条路径没有单测，只能靠人工点。

校验一律用 :mod:`bilibili_submit.ui.validation`
（标题 80 / 简介 2000 / 标签 10 / 定时下限 4 小时）。
那个模块虽然住在 ``ui/`` 下，但它是**纯函数、不碰 tkinter**——
它就是为「两处界面共用一套规则」才被抽出来的，Web 正是第二个调用方。
在入口拦住比依赖 :mod:`~bilibili_submit.metadata` 的静默截断要强得多：
截断后用户不知道自己的简介被改过。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..auth import build_client_from_cookies, load_cookies
from ..config import (
    AppConfig,
    DefaultsConfig,
    TaskConfig,
    scan_video_files,
)
from ..exceptions import BiliError, ConfigError, NotLoggedInError
from ..ffmpeg import ffmpeg_status
from ..metadata import COMMON_TIDS
from ..multipart import GROUP_MODES, group_files
from ..scheduler import RunOptions, TaskOutcome, run_all
from .events import Event
from .jobs import Job

__all__ = ["TaskDraft", "CommonFields", "build_preview", "run_submit"]

#: 分组模式。``none`` 是「每个文件一个稿件」，与界面上的说法一致。
GROUP_CHOICES = ("none", *GROUP_MODES)


@dataclass
class TaskDraft:
    """预览里的一行：一个待投稿的稿件。

    Attributes:
        name: 组名（单文件就是文件名去扩展名），用作稳定标识。
        files: 这个稿件要上传的视频绝对路径。
        title: 投稿标题，默认取 ``name``，前端可改。
        selected: 是否勾选。前端**整份列表回传**（含未勾选的），
            服务端自己过滤——这样事件里的 ``index`` 才能指回完整列表
            的行号，与桌面端 :class:`~bilibili_submit.ui.workers.Event`
            的语义一致。只回传勾选的话，行数会对不上。
    """

    name: str
    files: list[str] = field(default_factory=list)
    title: str = ""
    selected: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "files": self.files,
            "title": self.title or self.name,
            "parts": len(self.files),
            "selected": self.selected,
        }


@dataclass
class CommonFields:
    """列表顶部那套「统一填一次」的参数。

    字段刻意少：Web 版服务的是「选个目录批量投」这一件事，
    那些只在精细场景下有用的（mission_id、dynamic、no_reprint）
    留给配置文件和命令行，界面上不加——加了也没人填。
    """

    desc: str = ""
    tag: str = ""
    tid: int = 21
    cover: str = ""
    copyright: int = 1
    source: str = ""
    dtime_offset_hours: float | None = None
    up_close_reply: bool = False
    up_close_danmu: bool = False
    up_selection_reply: bool = False
    dolby: int = 0
    hires: int = 0
    interval_minutes: float = 0.0
    stop_on_error: bool = True

    @classmethod
    def from_dict(cls, data: dict | None) -> "CommonFields":
        data = data or {}
        known = {f.name for f in cls.__dataclass_fields__.values()}
        unknown = set(data) - known
        if unknown:
            raise ConfigError(f"投稿参数含未知字段: {sorted(unknown)}")
        return cls(**data)


# ---------- 预览 ----------


def build_preview(directory: str, group_by: str = "none") -> list[TaskDraft]:
    """扫一个目录，按分组方式给出待投稿列表。

    Args:
        group_by: ``none`` / ``prefix`` / ``folder`` / ``whole_dir``。
            ``folder`` 要多扫一层子目录——不钻进去就看不到目录结构。

    Raises:
        ConfigError: 目录不存在、不是目录，或里面没有视频。
    """
    from .browse import reject_path

    resolved = reject_path(directory)
    mode = group_by if group_by in GROUP_MODES else "none"
    files = scan_video_files(resolved, max_depth=2 if mode == "folder" else 1)
    if not files:
        raise ConfigError(f"目录里没有找到视频文件：{resolved}")

    if mode == "none":
        return [
            TaskDraft(name=path.stem, files=[str(path)], title=path.stem)
            for path in files
        ]

    groups = group_files(files, mode, resolved)
    return [
        TaskDraft(name=group.name, files=[str(p) for p in group.files],
                  title=group.name)
        for group in groups
    ]


def drafts_from_payload(items: list[dict]) -> list[TaskDraft]:
    """把前端回传的**完整列表**还原成 :class:`TaskDraft`。

    未勾选的也一起回传（见 :attr:`TaskDraft.selected`），这样执行时
    事件里的 ``index`` 还能指回预览列表的行号。

    文件存在性必须在服务端查：前端给的只是上次预览的快照，
    这期间文件可能已经被移走。等上传到一半才发现，
    用户看到的是「第 7 个失败」，而原因早就该在开始前说清楚。

    Raises:
        ConfigError: 列表为空，或某个文件不存在。
    """
    if not items:
        raise ConfigError("没有可投稿的任务")

    drafts: list[TaskDraft] = []
    missing: list[str] = []
    for item in items:
        files = [str(path) for path in (item.get("files") or [])]
        for path in files:
            if not Path(path).expanduser().is_file():
                missing.append(path)
        drafts.append(
            TaskDraft(
                name=str(item.get("name") or "").strip() or "未命名任务",
                files=files,
                title=str(item.get("title") or item.get("name") or "").strip(),
                selected=bool(item.get("selected", True)),
            )
        )

    if missing:
        raise ConfigError(f"视频文件不存在: {'、'.join(missing[:5])}")
    return drafts


def selected_pairs(drafts: list[TaskDraft]) -> list[tuple[int, TaskDraft]]:
    """``[(在完整列表里的下标, 稿件)]``，只含勾选的。

    保留下标是为了事件里的 ``index``——前端按它定位行。
    """
    return [(i, d) for i, d in enumerate(drafts) if d.selected]


def _to_task_config(draft: TaskDraft, common: CommonFields) -> TaskConfig:
    files = [str(Path(path).expanduser()) for path in draft.files]
    multip = len(files) > 1
    return TaskConfig(
        name=draft.name,
        type="multip" if multip else "single",
        file=None if multip else (files[0] if files else None),
        files=files if multip else [],
        title=draft.title or draft.name,
        tid=common.tid,
        tag=common.tag,
        desc=common.desc,
        cover=common.cover or None,
        copyright=common.copyright,
        source=common.source,
        dtime_offset_hours=common.dtime_offset_hours,
        up_close_reply=common.up_close_reply,
        up_close_danmu=common.up_close_danmu,
        up_selection_reply=common.up_selection_reply,
        dolby=common.dolby,
        hires=common.hires,
        per_task_interval_minutes=common.interval_minutes,
        stop_on_error=common.stop_on_error,
    )


# ---------- 执行 ----------


def run_submit(
    job: Job,
    drafts: list[TaskDraft],
    common: CommonFields,
    *,
    cookie_file: str,
    proxy: str | None = None,
    dry_run: bool = False,
    config: AppConfig | None = None,
) -> None:
    """在后台线程里把这一批投出去。

    所有进度都通过 ``job`` 上报；不抛业务异常，失败写成
    ``job.finish("error", ...)``——线程里抛出去了浏览器永远等不到
    ``end``，界面会一直转圈。

    Args:
        config: 可选的完整配置（给测试注入上传参数用）。不传则只按
            ``CommonFields`` 构造一份最小配置。
    """
    from ..ui.validation import (
        validate_desc,
        validate_offset_hours,
        validate_tag_text,
        validate_title,
    )

    try:
        cfg = config or AppConfig(
            defaults=DefaultsConfig(
                tid=common.tid,
                tag=common.tag,
                desc=common.desc,
                copyright=common.copyright,
                source=common.source,
            )
        )
        cfg.account.cookie_file = cookie_file
        cfg.account.proxy = proxy

        # 本地校验放在登录检查之前：参数填错不该让用户先去扫码
        for draft in drafts:
            validate_title(draft.title or draft.name)
        validate_desc(common.desc)
        validate_tag_text(common.tag)
        validate_offset_hours(common.dtime_offset_hours)
        if common.tid not in COMMON_TIDS:
            job.emit_text(
                f"警告：分区 {common.tid} 不在常用分区表里（仅告警，仍会提交）"
            )

        # 只跑勾选的，但事件里的 index 仍指回完整列表的行号
        pairs = selected_pairs(drafts)
        if not pairs:
            raise ConfigError("没有勾选任何任务")
        tasks = [_to_task_config(draft, common) for _i, draft in pairs]
        row_index = {k: i for k, (i, _d) in enumerate(pairs)}
        job.total = len(tasks)
        job.emit_text(f"共 {len(tasks)} 个投稿任务（已勾选 / 共 {len(drafts)} 个）")

        if dry_run:
            for position, task in enumerate(tasks, start=1):
                job.emit_event(
                    Event("start", index=row_index[position - 1], position=position,
                          total=len(tasks))
                )
                job.emit_text(f"[{position}/{len(tasks)}] 预览：{task.name}")
                job.emit_event(
                    Event("done", index=row_index[position - 1], position=position,
                          total=len(tasks), status="预览（未投稿）")
                )
            job.finish("done")
            return

        cookies = load_cookies(cookie_file)
        client = build_client_from_cookies(cookies, proxy=proxy)

        def on_start(position0: int, task: TaskConfig) -> None:
            job.emit_event(
                Event("start", index=row_index[position0], position=position0 + 1,
                      total=len(tasks))
            )
            job.emit_text(f"[{position0 + 1}/{len(tasks)}] 开始：{task.name}")

        def on_done(position0: int, task: TaskConfig, outcome: TaskOutcome) -> None:
            status = "成功" if outcome.success else f"失败：{outcome.error}"
            job.emit_event(
                Event(
                    "done",
                    index=row_index[position0],
                    position=position0 + 1,
                    total=len(tasks),
                    status=status,
                    error="" if outcome.success else outcome.error,
                )
            )
            job.emit_text(f"[{position0 + 1}/{len(tasks)}] {task.name} → {status}")
            job.results[task.name] = {
                "name": outcome.name,
                "success": outcome.success,
                "bvid": outcome.bvid,
                "url": outcome.url,
                "error": outcome.error,
            }

        options = RunOptions(
            on_progress=job.emit_text,
            on_task_start=on_start,
            on_task_done=on_done,
            should_stop=job.cancelled,
        )
        outcomes = run_all(client, tasks, cfg, options=options)

        ok = sum(1 for o in outcomes if o.success)
        job.emit_text(f"完成 {ok}/{len(outcomes)}")
        if job.cancelled:
            job.finish("cancelled", "已取消（当前任务会跑完，不再开始下一个）")
        else:
            job.finish("done")
    except NotLoggedInError as exc:
        job.finish("error", f"未登录：{exc}")
    except (BiliError, ConfigError) as exc:
        job.finish("error", str(exc))
    except Exception as exc:  # noqa: BLE001 - 线程里抛出浏览器就永远等不到结束
        job.finish("error", f"{type(exc).__name__}: {exc}")


def environment_snapshot(cookie_file: str, proxy: str | None = None) -> dict:
    """首页要显示的环境状态：登录态、ffmpeg、分区表。"""
    from ..auth import cookies_valid

    try:
        cookies = load_cookies(cookie_file)
        logged_in = cookies_valid(cookies)
        login_error = ""
    except BiliError as exc:
        logged_in = False
        login_error = str(exc)

    info = ffmpeg_status()
    return {
        "logged_in": logged_in,
        "login_error": login_error,
        "ffmpeg": info.source if info is not None else "",
        "ffmpeg_ready": info is not None,
        "tids": [{"id": tid, "name": name}
                 for tid, name in sorted(COMMON_TIDS.items())],
    }
