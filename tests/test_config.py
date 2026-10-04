"""配置加载与校验测试。"""

import os
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilibili_submit.config import (  # noqa: E402
    AppConfig,
    UploadConfig,
    expand_tasks,
    load_config,
    scan_video_files,
)
from bilibili_submit.exceptions import ConfigError  # noqa: E402
from bilibili_submit.metadata import (  # noqa: E402
    ArchiveMeta,
    build_payload,
    normalize_tags,
    resolve_dtime,
)

MINIMAL = textwrap.dedent(
    """
    tasks:
      - name: "单文件"
        type: single
        file: "/tmp/whatever.mp4"
        title: "测试标题"
    """
)


def test_load_minimal(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(MINIMAL, encoding="utf-8")
    cfg = load_config(path)
    assert len(cfg.tasks) == 1
    assert cfg.tasks[0].name == "单文件"
    assert cfg.upload.concurrency == 3


def test_load_rejects_unknown_field(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(MINIMAL + "    bogus: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="未知字段"):
        load_config(path)


def test_load_requires_tasks(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("defaults:\n  tid: 21\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="至少要有"):
        load_config(path)


def test_single_task_missing_file(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(
        'tasks:\n  - name: "x"\n    type: single\n', encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="缺少 file"):
        load_config(path)


def test_concurrency_clamped():
    assert UploadConfig(concurrency=99).concurrency == 4
    assert UploadConfig(concurrency=0).concurrency == 1


def test_batch_expansion(tmp_path):
    folder = tmp_path / "series"
    folder.mkdir()
    (folder / "a.mp4").write_text("x")
    (folder / "b.mp4").write_text("x")
    (folder / "skip.mkv").write_text("x")

    path = tmp_path / "c.yaml"
    path.write_text(
        textwrap.dedent(
            f"""
            defaults:
              tid: 21
              tag: "默认标签"
            tasks:
              - name: "系列"
                type: batch
                dir: "{folder}"
                include: ["*.mp4"]
                title_template: "{{stem}} 第{{n}}期"
                tag: null
            """
        ),
        encoding="utf-8",
    )
    cfg = load_config(path)
    tasks = expand_tasks(cfg)
    assert len(tasks) == 2
    assert tasks[0].title == "a 第1期"
    assert tasks[1].title == "b 第2期"
    # tag 为 null 时继承 defaults
    assert all(t.tag == "默认标签" for t in tasks)
    assert all(t.tid == 21 for t in tasks)


def test_batch_dir_missing(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(
        f'tasks:\n  - name: "x"\n    type: batch\n    dir: "{tmp_path}/nope"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="目录不存在"):
        expand_tasks(load_config(path))


# ---------- 目录扫描（客户端免配置路径）----------


def _touch(path):
    path.write_text("x", encoding="utf-8")
    return path


def test_scan_only_video_files(tmp_path):
    _touch(tmp_path / "a.mp4")
    _touch(tmp_path / "b.mkv")
    _touch(tmp_path / "notes.txt")
    _touch(tmp_path / "cover.jpg")
    _touch(tmp_path / "noext")
    (tmp_path / "sub").mkdir()
    _touch(tmp_path / "sub" / "deep.mp4")

    found = scan_video_files(tmp_path)
    assert [p.name for p in found] == ["a.mp4", "b.mkv"]


def test_scan_accepts_uppercase_suffix(tmp_path):
    _touch(tmp_path / "A.MP4")
    _touch(tmp_path / "B.Mp4")
    assert [p.name for p in scan_video_files(tmp_path)] == ["A.MP4", "B.Mp4"]


def test_scan_is_not_recursive(tmp_path):
    """只扫第一层。递归下去用户误选大目录会一次生成上千任务。"""
    (tmp_path / "nested").mkdir()
    _touch(tmp_path / "nested" / "deep.mp4")
    assert scan_video_files(tmp_path) == []


def test_scan_sorted_casefold(tmp_path):
    for name in ("b.mp4", "A.mp4", "c.mp4"):
        _touch(tmp_path / name)
    assert [p.name for p in scan_video_files(tmp_path)] == ["A.mp4", "b.mp4", "c.mp4"]


def test_scan_empty_dir_returns_empty(tmp_path):
    assert scan_video_files(tmp_path) == []


def test_scan_missing_dir_raises(tmp_path):
    with pytest.raises(ConfigError, match="目录不存在"):
        scan_video_files(tmp_path / "nope")


# ---------- 元数据 ----------


def test_normalize_tags_dedup_and_limit():
    assert normalize_tags("a,b,a,,b") == "a,b"
    assert normalize_tags("a，b") == "a,b"  # 中文逗号
    assert len(normalize_tags(",".join(str(i) for i in range(20))).split(",")) == 10


def test_normalize_tags_total_length():
    tags = ["x" * 30 for _ in range(10)]
    result = normalize_tags(",".join(tags))
    assert len(result) <= 200


def test_title_truncated():
    meta = ArchiveMeta(title="x" * 200, videos=[{"filename": "f", "title": "t"}])
    assert len(meta.normalized().title) == 80


def test_reprint_requires_source():
    meta = ArchiveMeta(title="t", copyright=2, source="")
    with pytest.raises(ConfigError, match="source"):
        meta.normalized()


def test_dtime_must_be_future_and_over_4h():
    import time

    now = int(time.time())
    with pytest.raises(ConfigError, match="早于当前时间"):
        resolve_dtime(now - 100, None)
    with pytest.raises(ConfigError, match="至少 4 小时"):
        resolve_dtime(now + 3600, None)
    ok = resolve_dtime(None, 5)
    assert ok and ok - now >= 4 * 3600


def test_build_payload_basic():
    meta = ArchiveMeta(
        title="标题", tid=21, tag="a,b", desc="简介",
        videos=[{"filename": "abc123", "title": "P1", "desc": ""}],
    )
    payload = build_payload(meta, csrf="CSRF")
    assert payload["csrf"] == "CSRF"
    assert payload["videos"][0]["filename"] == "abc123"
    assert payload["tid"] == 21
    assert payload["web_os"] == 3
    assert "dtime" not in payload


def test_build_payload_without_videos():
    meta = ArchiveMeta(title="标题")
    with pytest.raises(ConfigError, match="videos 为空"):
        build_payload(meta, csrf="x")


# ---------- 多 P 任务 ----------


def test_task_files_picks_files_for_multip():
    from bilibili_submit.config import TaskConfig, task_files

    multip = TaskConfig(type="multip", files=["/v/a.mp4", "/v/b.mp4"])
    assert [p.name for p in task_files(multip)] == ["a.mp4", "b.mp4"]

    single = TaskConfig(type="single", file="/v/a.mp4")
    assert [p.name for p in task_files(single)] == ["a.mp4"]

    assert task_files(TaskConfig()) == []


def test_files_win_over_file_when_both_set():
    """两个都给了以 files 为准——否则顺序会变得不确定。"""
    from bilibili_submit.config import TaskConfig, task_files

    task = TaskConfig(type="multip", file="/v/z.mp4", files=["/v/a.mp4", "/v/b.mp4"])
    assert [p.name for p in task_files(task)] == ["a.mp4", "b.mp4"]


def test_part_titles_default_to_filename(tmp_path):
    from bilibili_submit.config import TaskConfig, task_part_titles

    files = [tmp_path / "旅行_01.mp4", tmp_path / "旅行_02.mp4"]
    task = TaskConfig(type="multip", files=[str(f) for f in files])
    assert task_part_titles(task, files) == ["旅行_01", "旅行_02"]


def test_part_titles_override_and_fall_back(tmp_path):
    """配了几个就用几个，没配到的回落到文件名而不是留空。"""
    from bilibili_submit.config import TaskConfig, task_part_titles

    files = [tmp_path / f"p{i}.mp4" for i in (1, 2, 3)]
    task = TaskConfig(type="multip", files=[str(f) for f in files], part_titles=["出发"])
    assert task_part_titles(task, files) == ["出发", "p2", "p3"]


def test_empty_part_title_also_falls_back(tmp_path):
    """标题填了空串等于没填，播放器里不能出现空白分P。"""
    from bilibili_submit.config import TaskConfig, task_part_titles

    files = [tmp_path / "a.mp4"]
    task = TaskConfig(type="multip", files=[str(files[0])], part_titles=["   "])
    assert task_part_titles(task, files) == ["a"]


def test_multip_task_requires_files(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("tasks:\n  - name: x\n    type: multip\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="缺少 files"):
        load_config(cfg)


def test_part_titles_without_files_is_rejected(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "tasks:\n  - name: x\n    type: single\n    file: a.mp4\n"
        "    part_titles: [\"p1\"]\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="part_titles"):
        load_config(cfg)


def test_multip_task_is_not_expanded(tmp_path):
    """multip 本身就是一个稿件，expand_tasks 不该把它拆开。"""
    for index in (1, 2):
        (tmp_path / f"v{index}.mp4").write_bytes(b"x")
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "tasks:\n"
        "  - name: 一套\n"
        "    type: multip\n"
        f"    files: [\"{tmp_path / 'v1.mp4'}\", \"{tmp_path / 'v2.mp4'}\"]\n",
        encoding="utf-8",
    )
    tasks = expand_tasks(load_config(cfg))
    assert len(tasks) == 1
    assert tasks[0].type == "multip"
    assert len(tasks[0].files) == 2
