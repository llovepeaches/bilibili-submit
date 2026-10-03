"""分片与上传相关算法测试（纯逻辑，不联网）。"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilibili_submit.upload import (  # noqa: E402
    ResumeState,
    UploadBucket,
    _chunks_of,
    _state_path,
)


def test_chunks_count_edges():
    cs = 10 * 1024 * 1024
    assert _chunks_of(0, cs) == 1
    assert _chunks_of(1, cs) == 1
    assert _chunks_of(cs, cs) == 1          # 整除
    assert _chunks_of(cs + 1, cs) == 2      # 余 1 字节也要多一片
    assert _chunks_of(cs * 3, cs) == 3


def test_last_chunk_is_remainder():
    """末片的 size 必须是实际余数，不能是 chunk_size。"""
    cs = 1000
    size = 2500
    chunks = _chunks_of(size, cs)
    assert chunks == 3
    last_len = size - (chunks - 1) * cs
    assert last_len == 500


def test_filename_extracted_from_upos_uri():
    bucket = UploadBucket(
        endpoint="//upos-cs-upcdnbda2.bilivideo.com",
        upos_uri="upos://bfs/archive/7cd084941338484aae1ad9425b84077c.mp4",
        auth="AUTH",
        biz_id=1,
        chunk_size=1024,
    )
    assert bucket.filename == "7cd084941338484aae1ad9425b84077c"
    assert bucket.path == "bfs/archive/7cd084941338484aae1ad9425b84077c.mp4"


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        ("//upos-cs.bilivideo.com", "https://upos-cs.bilivideo.com/bfs/x.mp4"),
        ("https://upos-cs.bilivideo.com", "https://upos-cs.bilivideo.com/bfs/x.mp4"),
        ("upos-cs.bilivideo.com", "https://upos-cs.bilivideo.com/bfs/x.mp4"),
    ],
)
def test_base_url_normalization(endpoint, expected):
    bucket = UploadBucket(
        endpoint=endpoint, upos_uri="upos://bfs/x.mp4", auth="", biz_id=0, chunk_size=1
    )
    assert bucket.base_url == expected


def test_resume_state_roundtrip():
    state = ResumeState(
        upload_id="UID", path="/tmp/a.mp4", size=100, chunks=3,
        done={0, 1}, parts=[{"partNumber": 1, "eTag": "e"}],
    )
    restored = ResumeState.from_json(state.to_json())
    assert restored.upload_id == "UID"
    assert restored.done == {0, 1}
    assert restored.parts == state.parts


def test_resume_state_rejects_size_change(tmp_path):
    """文件被替换后（size 不同）旧的续传状态不能复用。"""
    from bilibili_submit.upload import _load_state

    video = tmp_path / "a.mp4"
    video.write_bytes(b"x" * 100)
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    from bilibili_submit.upload import _save_state

    state = ResumeState(upload_id="U", path=str(video), size=100, chunks=1)
    _save_state(video, state_dir, state)
    assert _load_state(video, state_dir, ttl=9999) is not None

    video.write_bytes(b"y" * 200)  # 文件变了
    assert _load_state(video, state_dir, ttl=9999) is None


def test_resume_state_expiry(tmp_path):
    from bilibili_submit.upload import _load_state, _save_state

    video = tmp_path / "a.mp4"
    video.write_bytes(b"x" * 10)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    state = ResumeState(upload_id="U", path=str(video), size=10, chunks=1)
    _save_state(video, state_dir, state)

    # 先验证未过期可读（过期分支会删除状态文件，故放在后面）
    assert _load_state(video, state_dir, ttl=9999) is not None

    _save_state(video, state_dir, state)
    # ttl<=0 表示禁用续传（用 -1 而非 0：state 刚写入时时间差可能为 0.0）
    assert _load_state(video, state_dir, ttl=-1) is None
    assert not _state_path(video, state_dir).exists()  # 过期状态会被清理


def test_state_path_stable_per_file(tmp_path):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    state_dir = tmp_path / "state"
    p1 = _state_path(video, state_dir)
    p2 = _state_path(video, state_dir)
    assert p1 == p2
    assert p1.suffix == ".json"
