"""界面偏好文件的测试。"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilibili_submit.ui.state import (  # noqa: E402
    BatchUIState,
    DEFAULT_TID,
    SCHEMA_VERSION,
    load_ui_state,
    save_ui_state,
)


def test_missing_file_is_not_a_problem(tmp_path):
    """第一次用：没文件是正常的，不能报成故障。"""
    state, problem = load_ui_state(tmp_path / "absent.json")
    assert state == BatchUIState()
    assert problem == ""


def test_roundtrip(tmp_path):
    path = tmp_path / "ui-state.json"
    original = BatchUIState(
        directory="D:/视频/待投稿",
        tid=138,
        tag="日常,记录",
        desc="统一简介",
        dtime_offset_hours=6.0,
    )
    save_ui_state(original, path)

    loaded, problem = load_ui_state(path)
    assert problem == ""
    assert loaded == original


def test_roundtrip_keeps_chinese(tmp_path):
    path = tmp_path / "ui-state.json"
    save_ui_state(
        BatchUIState(directory="/home/我 的 视频/合集", tag="日常,记录"), path
    )
    loaded, _ = load_ui_state(path)
    assert loaded.directory == "/home/我 的 视频/合集"
    assert loaded.tag == "日常,记录"


def test_corrupt_file_reports_problem(tmp_path):
    """损坏必须如实报告。静默当首次使用的话，用户以为程序把设置弄丢了。"""
    path = tmp_path / "ui-state.json"
    path.write_text("{ 这不是 json", encoding="utf-8")

    state, problem = load_ui_state(path)
    assert state == BatchUIState()
    assert "读取失败" in problem


def test_unsupported_version_reports_problem(tmp_path):
    path = tmp_path / "ui-state.json"
    path.write_text(
        json.dumps({"schema_version": 99, "batch": {}}), encoding="utf-8"
    )
    _, problem = load_ui_state(path)
    assert "版本不兼容" in problem


def test_top_level_not_object_reports_problem(tmp_path):
    path = tmp_path / "ui-state.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    _, problem = load_ui_state(path)
    assert "顶层不是对象" in problem


def test_missing_batch_section_reports_problem(tmp_path):
    path = tmp_path / "ui-state.json"
    path.write_text(
        json.dumps({"schema_version": SCHEMA_VERSION}), encoding="utf-8"
    )
    _, problem = load_ui_state(path)
    assert "batch" in problem


def test_wrong_field_types_fall_back(tmp_path):
    """字段类型不对时回退默认值，而不是崩给用户看。"""
    path = tmp_path / "ui-state.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "batch": {
                    "directory": 123,
                    "tid": "abc",
                    "tag": None,
                    "desc": [],
                    "dtime_offset_hours": "六",
                },
            }
        ),
        encoding="utf-8",
    )
    state, problem = load_ui_state(path)
    assert problem == ""
    assert state == BatchUIState()


def test_tid_true_is_not_tid_one(tmp_path):
    """bool 是 int 的子类——``True`` 不能被当成 tid=1。"""
    path = tmp_path / "ui-state.json"
    path.write_text(
        json.dumps(
            {"schema_version": SCHEMA_VERSION, "batch": {"tid": True}}
        ),
        encoding="utf-8",
    )
    state, _ = load_ui_state(path)
    assert state.tid == DEFAULT_TID


def test_negative_tid_falls_back(tmp_path):
    path = tmp_path / "ui-state.json"
    path.write_text(
        json.dumps(
            {"schema_version": SCHEMA_VERSION, "batch": {"tid": -5}}
        ),
        encoding="utf-8",
    )
    state, _ = load_ui_state(path)
    assert state.tid == DEFAULT_TID


def test_save_uses_os_replace(tmp_path, monkeypatch):
    """必须是「临时文件 + 原子替换」，不能直接覆盖目标。

    直接覆盖的话，写到一半崩了会留下半个 json，下次启动就读不出来——
    而这个文件正是「下次打开还在」的唯一凭据。
    """
    path = tmp_path / "ui-state.json"
    calls = []
    real_replace = os.replace

    def spy(src, dst, *args, **kwargs):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", spy)
    save_ui_state(BatchUIState(directory="D:/v"), path)

    assert len(calls) == 1
    src, dst = calls[0]
    assert src != dst
    assert dst == str(path)


def test_save_creates_parent_dir(tmp_path):
    path = tmp_path / "nested" / "deeper" / "ui-state.json"
    save_ui_state(BatchUIState(directory="D:/v"), path)
    assert path.is_file()


def test_save_failure_keeps_old_file(tmp_path):
    """写失败不能让已有的偏好烂掉。"""
    path = tmp_path / "ui-state.json"
    save_ui_state(BatchUIState(directory="D:/v"), path)

    original = BatchUIState(directory="D:/v")
    unwritable = tmp_path / "sub" / "ui-state.json"
    save_ui_state(original, unwritable)  # 建立目录
    unwritable.chmod(0o500)  # 只读目录，mkdir 之后写入会失败
    try:
        if os.access(unwritable.parent, os.W_OK):
            pytest.skip("以 root 运行，权限位不生效")
        save_ui_state(BatchUIState(directory="D:/other"), unwritable)
    finally:
        unwritable.chmod(0o700)

    assert load_ui_state(path)[0] == original


def test_no_tmp_left_behind(tmp_path):
    path = tmp_path / "ui-state.json"
    save_ui_state(BatchUIState(directory="D:/v"), path)
    assert not (tmp_path / "ui-state.json.tmp").exists()
