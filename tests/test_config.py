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
