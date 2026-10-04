"""多 P 投稿的执行测试。

核心要验证的是**一次投稿**：三个分 P 要上传三次，但只调一次投递接口，
payload 的 ``videos`` 数组里按顺序挂着三个 ``filename``。
如果变成分三次投稿，用户会拿到三个 av 号——那就不叫多 P 了。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilibili_submit import scheduler  # noqa: E402
from bilibili_submit.config import AppConfig, TaskConfig  # noqa: E402
from bilibili_submit.metadata import ArchiveMeta  # noqa: E402
from bilibili_submit.scheduler import RunOptions  # noqa: E402
from bilibili_submit.submit import SubmitResult  # noqa: E402


class _UploadResult:
    def __init__(self, filename: str) -> None:
        self.filename = filename


def _make_video(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_bytes(b"\x00" * 512)
    return path


@pytest.fixture
def recorder(monkeypatch):
    """打桩上传与投递，记录调用过程。"""
    log: dict[str, object] = {"uploaded": [], "submits": []}

    def fake_upload(client, video, **kwargs):
        log["uploaded"].append(Path(video).name)
        return _UploadResult(f"upos-{Path(video).stem}")

    def fake_submit(client, meta, **kwargs):
        log["submits"].append(meta)
        return SubmitResult(aid=100, bvid="BV1xx")

    monkeypatch.setattr(scheduler, "upload_video", fake_upload)
    monkeypatch.setattr(scheduler, "submit_archive", fake_submit)
    return log


def _task(files, **kwargs) -> TaskConfig:
    return TaskConfig(
        name="旅行",
        type="multip",
        files=[str(path) for path in files],
        title="旅行",
        **kwargs,
    )


# ---------- 一次投稿 ----------


def test_multipart_submits_once_with_all_parts(tmp_path, recorder):
    files = [_make_video(tmp_path, f"旅行_0{i}.mp4") for i in (1, 2, 3)]

    outcome = scheduler.run_task(None, _task(files), AppConfig())

    assert outcome.success
    assert recorder["uploaded"] == ["旅行_01.mp4", "旅行_02.mp4", "旅行_03.mp4"]
    assert len(recorder["submits"]) == 1, "三个分P只该投一次稿"

    meta: ArchiveMeta = recorder["submits"][0]
    assert [v["filename"] for v in meta.videos] == [
        "upos-旅行_01",
        "upos-旅行_02",
        "upos-旅行_03",
    ]
    assert outcome.parts == 3
    assert outcome.bvid == "BV1xx"


def test_part_order_follows_files_not_sorting(tmp_path, recorder):
    """分 P 顺序就是 files 的原序——B 站按数组下标排，传反了改不回来。"""
    files = [
        _make_video(tmp_path, "b.mp4"),
        _make_video(tmp_path, "a.mp4"),
        _make_video(tmp_path, "c.mp4"),
    ]

    scheduler.run_task(None, _task(files), AppConfig())

    meta: ArchiveMeta = recorder["submits"][0]
    assert [v["filename"] for v in meta.videos] == ["upos-b", "upos-a", "upos-c"]


def test_part_titles_default_to_filename(tmp_path, recorder):
    files = [_make_video(tmp_path, "旅行_01.mp4"), _make_video(tmp_path, "旅行_02.mp4")]

    scheduler.run_task(None, _task(files), AppConfig())

    meta: ArchiveMeta = recorder["submits"][0]
    assert [v["title"] for v in meta.videos] == ["旅行_01", "旅行_02"]


def test_part_titles_can_be_overridden(tmp_path, recorder):
    files = [_make_video(tmp_path, "旅行_01.mp4"), _make_video(tmp_path, "旅行_02.mp4")]

    scheduler.run_task(
        None, _task(files, part_titles=["出发", "到达"]), AppConfig()
    )

    meta: ArchiveMeta = recorder["submits"][0]
    assert [v["title"] for v in meta.videos] == ["出发", "到达"]


def test_short_part_titles_fall_back_to_filename(tmp_path, recorder):
    """只给一个标题时，剩下的不该变成空白 P。"""
    files = [
        _make_video(tmp_path, "旅行_01.mp4"),
        _make_video(tmp_path, "旅行_02.mp4"),
        _make_video(tmp_path, "旅行_03.mp4"),
    ]

    scheduler.run_task(None, _task(files, part_titles=["出发"]), AppConfig())

    meta: ArchiveMeta = recorder["submits"][0]
    assert [v["title"] for v in meta.videos] == ["出发", "旅行_02", "旅行_03"]


# ---------- 出错路径 ----------


def test_missing_one_part_blocks_the_whole_archive(tmp_path, recorder):
    """多 P 缺一个文件就投不完整，必须在上传之前就拦下来。

    跑到第二个文件才发现不存在的话，第一个已经传上去了——
    白白占了一次上传配额，用户还得自己清理。
    """
    keep = _make_video(tmp_path, "旅行_01.mp4")
    gone = tmp_path / "旅行_02.mp4"

    outcome = scheduler.run_task(None, _task([keep, gone]), AppConfig())

    assert not outcome.success
    assert "不存在" in outcome.error
    assert recorder["uploaded"] == [], "一个分P缺失就不该开始上传"
    assert recorder["submits"] == []


def test_task_without_any_file_reports_error(recorder):
    outcome = scheduler.run_task(
        None, TaskConfig(name="空", type="multip"), AppConfig()
    )
    assert not outcome.success
    assert "没有配置视频文件" in outcome.error


def test_dry_run_does_not_upload(tmp_path, recorder):
    files = [_make_video(tmp_path, "旅行_01.mp4"), _make_video(tmp_path, "旅行_02.mp4")]

    outcome = scheduler.run_task(
        None, _task(files), AppConfig(), options=RunOptions(dry_run=True)
    )

    assert outcome.success
    assert recorder["uploaded"] == []
    assert outcome.parts == 2


# ---------- 单 P 回归 ----------


def test_single_file_task_still_works(tmp_path, recorder):
    """加了多 P 之后，原来的单文件投稿不能变样。"""
    video = _make_video(tmp_path, "教程.mp4")
    task = TaskConfig(name="教程", type="single", file=str(video), title="教程")

    outcome = scheduler.run_task(None, task, AppConfig())

    assert outcome.success
    assert recorder["uploaded"] == ["教程.mp4"]
    meta: ArchiveMeta = recorder["submits"][0]
    assert len(meta.videos) == 1
    assert outcome.parts == 1


def test_progress_messages_are_numbered(tmp_path, recorder):
    """多 P 上传耗时长，进度里要能看出在传第几个。"""
    files = [_make_video(tmp_path, f"p{i}.mp4") for i in (1, 2)]
    messages: list[str] = []

    scheduler.run_task(
        None,
        _task(files),
        AppConfig(),
        options=RunOptions(on_progress=messages.append),
    )

    assert any(m.startswith("[1/2]") for m in messages)
    assert any(m.startswith("[2/2]") for m in messages)
    assert not any(m.startswith("[1/1]") for m in messages), "单P不该带序号前缀"
