"""投稿视图：选文件、填元数据、看进度。

这是 GUI 的主功能页。表单字段与 CLI 的 ``upload`` 命令一一对应，
提交时构造 :class:`~bilibili_submit.config.TaskConfig` 走同一条
``run_task`` 链路——GUI 不自己实现投稿，避免两套逻辑走偏。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk
from pathlib import Path

from ...config import AppConfig, TaskConfig
from ...exceptions import BiliError, NotLoggedInError
from ...metadata import COMMON_TIDS
from ...scheduler import RunOptions, run_task
from ...submit import get_backend
from .. import theme
from ..widgets import (
    FormRow,
    LogConsole,
    PrimaryButton,
    ProgressBar,
    ScrollArea,
    SecondaryButton,
    SectionTitle,
)
from ..workers import Cancelled, Worker

__all__ = ["UploadView", "parse_tid", "TID_OPTIONS"]

#: 标签上限，B 站硬性限制
MAX_TAGS = 10

#: 分区下拉的选项，格式 ``"21 - 日常"``。投稿页和批量任务页共用同一份，
#: 免得两个页面的分区列表哪天不一样，用户要重新适应。
TID_OPTIONS = [f"{tid} - {name}" for tid, name in sorted(COMMON_TIDS.items())]


def parse_tid(text: str, default: int = 21) -> int:
    """从「21 - 日常」这样的下拉项里取出分区号。"""
    head = (text or "").split("-")[0].strip()
    try:
        return int(head)
    except (ValueError, IndexError):
        return default


class UploadView(ttk.Frame):
    """单文件投稿页。"""

    def __init__(self, master: tk.Misc, app: "object") -> None:
        super().__init__(master, style="TFrame")
        self.app = app
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._worker = Worker(self)
        self._build()

    # ---------- 布局 ----------

    def _build(self) -> None:
        SectionTitle(
            self, "投稿", "选择一个视频文件，填写标题和分区后提交。"
        ).grid(row=0, column=0, sticky="ew", pady=(0, theme.PAD_MD))

        # 窗口压到最小尺寸时表单装不下，用滚动区兜住，
        # 否则「开始投稿」会被挤出可视范围。
        area = ScrollArea(self)
        area.grid(row=1, column=0, sticky="nsew")
        card = area.body

        form = ttk.Frame(card, style="Card.TFrame")
        form.grid(row=0, column=0, sticky="ew")
        # FormRow 全部 grid 在第 0 列，权重给这一列，窗口拉宽时输入框才跟着变宽
        form.columnconfigure(0, weight=1)

        # 视频文件：输入框 + 「选择文件…」按钮
        self._file_var = tk.StringVar()
        row = FormRow(form, "视频文件", hint="支持 mp4 / flv / mov / mkv 等常见格式")
        row.grid(row=0, column=0, sticky="ew", pady=4)
        row.add(ttk.Entry, textvariable=self._file_var, padx=(0, theme.PAD_SM))
        row.add(
            SecondaryButton,
            text="选择文件…",
            command=self._pick_file,
            column=1,
            sticky="w",
        )

        # 标题
        self._title_var = tk.StringVar()
        row = FormRow(form, "标题", hint="留空则自动取文件名")
        row.grid(row=1, column=0, sticky="ew", pady=4)
        row.add(ttk.Entry, textvariable=self._title_var)

        # 分区
        self._tid_var = tk.StringVar()
        row = FormRow(form, "分区", hint="B 站投稿分区，决定稿件出现在哪里")
        row.grid(row=2, column=0, sticky="ew", pady=4)
        row.add(
            ttk.Combobox,
            textvariable=self._tid_var,
            values=TID_OPTIONS,
            state="readonly",
        )
        self._tid_var.set("21 - 日常")

        # 标签
        self._tag_var = tk.StringVar()
        row = FormRow(form, "标签", hint=f"逗号分隔，最多 {MAX_TAGS} 个")
        row.grid(row=3, column=0, sticky="ew", pady=4)
        row.add(ttk.Entry, textvariable=self._tag_var)

        # 简介
        self._desc_var = tk.StringVar()
        row = FormRow(form, "简介", hint="可留空")
        row.grid(row=4, column=0, sticky="ew", pady=4)
        row.add(ttk.Entry, textvariable=self._desc_var)

        # 定时发布
        self._dtime_var = tk.StringVar()
        row = FormRow(
            form,
            "延时发布",
            hint="距今多少小时后发布，需大于 4；留空为立即发布",
        )
        row.grid(row=5, column=0, sticky="ew", pady=4)
        row.add(ttk.Entry, textvariable=self._dtime_var)

        # 操作区
        actions = ttk.Frame(card, style="Card.TFrame")
        actions.grid(row=1, column=0, sticky="ew", pady=(theme.PAD_MD, theme.PAD_SM))

        self._submit_button = PrimaryButton(actions, "开始投稿", self._submit)
        self._submit_button.pack(side="left", padx=(0, theme.PAD_SM))

        self._cancel_button = SecondaryButton(actions, "取消", self._cancel)
        self._cancel_button.pack(side="left")
        self._cancel_button.state(["disabled"])

        self._preview_button = SecondaryButton(actions, "预览（不实际投稿）", self._dry_run)
        self._preview_button.pack(side="left", padx=(theme.PAD_SM, 0))

        self._progress = ProgressBar(card)
        self._progress.grid(row=2, column=0, sticky="ew")

        # 滚动容器里不能用 weight=1 让日志区「吃掉剩余空间」——
        # 父容器高度就是内容高度，权重不会带来额外空间，反而会压缩按钮。
        # 固定高度 + 内容超出时日志自己滚。
        self._log = LogConsole(card, height=7)
        self._log.grid(row=3, column=0, sticky="ew", pady=(theme.PAD_SM, 0))

    # ---------- 行为 ----------

    def refresh(self) -> None:
        """切到本页时不需要额外加载，留空实现保持接口一致。"""

    def _pick_file(self) -> None:
        path = filedialog.askopenfilename(
            title="选择视频文件",
            filetypes=[
                ("视频文件", "*.mp4 *.flv *.mov *.mkv *.avi *.wmv"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        self._file_var.set(path)
        # 标题空着的话顺手填上文件名，省一次输入
        if not self._title_var.get().strip():
            self._title_var.set(Path(path).stem)

    def _collect(self) -> TaskConfig:
        """从表单收集并校验，返回任务配置。"""
        raw = self._file_var.get().strip()
        if not raw:
            raise BiliError("请先选择视频文件")
        file = Path(raw).expanduser()
        if not file.is_file():
            raise BiliError(f"视频文件不存在: {file}")

        tid = parse_tid(self._tid_var.get(), default=21)

        offset_text = self._dtime_var.get().strip()
        offset = None
        if offset_text:
            try:
                offset = float(offset_text)
            except ValueError as exc:
                raise BiliError(f"延时发布小时数不是数字: {offset_text}") from exc

        return TaskConfig(
            name=file.stem,
            type="single",
            file=str(file),
            title=self._title_var.get().strip() or None,
            tid=tid,
            tag=self._tag_var.get().strip() or None,
            desc=self._desc_var.get().strip() or None,
            dtime_offset_hours=offset,
        )

    def _run(self, dry_run: bool) -> None:
        if self._worker.running:
            return
        try:
            task = self._collect()
        except BiliError as exc:
            self._log.append(f"输入有误：{exc}")
            return

        self._set_busy(True)
        self._log.clear()
        self._progress.reset()
        self._progress.start_indeterminate()
        self._progress.set_text("准备中…")

        self._worker.run(
            lambda report, is_cancelled: self._do_upload(task, dry_run, report, is_cancelled),
            on_progress=self._on_progress,
            on_done=self._on_done,
            on_error=self._on_error,
        )

    def _submit(self) -> None:
        if not self.app.ctx.logged_in:
            self._log.append("尚未登录，请先到「登录」页扫码")
            return
        self._run(dry_run=False)

    def _dry_run(self) -> None:
        self._run(dry_run=True)

    def _cancel(self) -> None:
        self._worker.cancel()
        self._log.append("已请求取消…")

    def _do_upload(self, task: TaskConfig, dry_run: bool, report, is_cancelled):
        """投稿主流程（工作线程执行）。"""
        if is_cancelled():
            raise Cancelled()

        cfg = AppConfig()
        cfg.account.cookie_file = self.app.ctx.cookie_file
        cfg.account.proxy = self.app.ctx.proxy

        client = self.app.ctx.client(need_login=not dry_run)
        report(f"{'[预览] ' if dry_run else ''}开始处理：{task.file}")

        outcome = run_task(
            client,
            task,
            cfg,
            backend=get_backend(cfg.submit.backend, cfg.submit.app),
            options=RunOptions(dry_run=dry_run, on_progress=report),
        )
        return outcome

    # ---------- 回调（主线程） ----------

    def _on_progress(self, message: str) -> None:
        self._log.append(message)
        self._progress.set_text(message)

    def _on_done(self, outcome) -> None:
        self._set_busy(False)
        self._progress.stop_indeterminate()
        if outcome.success:
            self._progress.set_value(100)
            self._progress.set_text("完成")
            self._log.append(f"投稿成功：{outcome.bvid}\n{outcome.url}")
        else:
            self._progress.set_text("失败")
            self._log.append(f"投稿失败：{outcome.error}")

    def _on_error(self, exc: BaseException) -> None:
        self._set_busy(False)
        self._progress.stop_indeterminate()
        self._progress.set_text("出错")
        if isinstance(exc, Cancelled):
            self._log.append("已取消")
            return
        if isinstance(exc, NotLoggedInError):
            self._log.append("登录态已失效，请到「登录」页重新扫码")
            return
        self._log.append(f"错误：{exc}")

    def _set_busy(self, busy: bool) -> None:
        state = ["disabled"] if busy else ["!disabled"]
        self._submit_button.state(state)
        self._preview_button.state(state)
        self._cancel_button.state(["!disabled"] if busy else ["disabled"])
