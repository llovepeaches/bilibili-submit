"""界面偏好文件的测试。"""

import json
import os
import sys
from pathlib import Path

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


# ---------- 分 P 合并方式 ----------


def _write(tmp_path, batch: dict) -> Path:
    path = tmp_path / "ui-state.json"
    path.write_text(
        json.dumps({"schema_version": SCHEMA_VERSION, "batch": batch}),
        encoding="utf-8",
    )
    return path


def test_group_mode_defaults_to_none(tmp_path):
    state, problem = load_ui_state(_write(tmp_path, {"directory": "/v"}))
    assert problem == ""
    assert state.group_mode == "none"


def test_group_mode_round_trips(tmp_path):
    path = _write(tmp_path, {"group_mode": "folder"})
    state, _ = load_ui_state(path)
    assert state.group_mode == "folder"

    save_ui_state(state, path)
    again, _ = load_ui_state(path)
    assert again.group_mode == "folder"


def test_legacy_group_parts_true_maps_to_prefix(tmp_path):
    """旧版布尔 group_parts: true 应读成 prefix。

    那时只有"按文件名前缀"一种分法。不认这个字段的话，老用户升级完会
    发现自己的选择被悄悄改回"不合并"——偏好丢失比功能缺失更招骂。
    """
    state, _ = load_ui_state(_write(tmp_path, {"group_parts": True}))
    assert state.group_mode == "prefix"


def test_legacy_group_parts_false_stays_none(tmp_path):
    state, _ = load_ui_state(_write(tmp_path, {"group_parts": False}))
    assert state.group_mode == "none"


def test_new_group_mode_wins_over_legacy_field(tmp_path):
    """两个字段都在时以新的为准，避免旧字段把新选择盖掉。"""
    state, _ = load_ui_state(
        _write(tmp_path, {"group_mode": "folder", "group_parts": True})
    )
    assert state.group_mode == "folder"


def test_unknown_group_mode_falls_back_to_none(tmp_path):
    """认不出的合并方式退回"不合并"。

    分组是猜意图，猜不出来按最保守的来——按一个错的方式把几个不相干的
    视频投成同一稿件的分 P，要删稿重投。
    """
    state, _ = load_ui_state(_write(tmp_path, {"group_mode": "瞎写的"}))
    assert state.group_mode == "none"


def test_older_state_file_without_the_new_options(tmp_path):
    """旧版偏好文件里没有这些字段，读出来应当是「全关」而不是报错。

    不升 schema 版本就是为了这个：老用户升级后目录、分区、分组方式
    全都还在，只有新开关取默认值。
    """
    path = tmp_path / "ui-state.json"
    path.write_text(
        json.dumps(
            {"schema_version": SCHEMA_VERSION, "batch": {"directory": "D:/v"}}
        ),
        encoding="utf-8",
    )
    state, problem = load_ui_state(path)
    assert problem == ""
    assert state.directory == "D:/v"
    assert state.dolby is False
    assert state.hires is False
    assert state.close_reply is False
    assert state.close_danmu is False
    assert state.selection_reply is False
    assert state.title_template == ""
    assert state.advanced_opened is False


def test_switches_read_strings_as_off_when_they_say_off(tmp_path):
    """手改过的偏好文件里写 "false" / "0" 要真的关掉。"""
    path = tmp_path / "ui-state.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "batch": {"dolby": "false", "hires": "0", "close_reply": "no"},
            }
        ),
        encoding="utf-8",
    )
    state, _ = load_ui_state(path)
    assert state.dolby is False
    assert state.hires is False
    assert state.close_reply is False


def test_older_state_file_defaults_to_self_made(tmp_path):
    """旧文件没有 copyright 字段 → 自制。

    不能因为缺这个字段就让整份偏好读不出来，也不能默认成转载——转载
    缺来源会被拒稿，等于把一次升级变成一次投稿失败。
    """
    path = tmp_path / "ui-state.json"
    path.write_text(
        json.dumps(
            {"schema_version": SCHEMA_VERSION, "batch": {"directory": "D:/v"}}
        ),
        encoding="utf-8",
    )
    state, problem = load_ui_state(path)
    assert problem == ""
    assert state.copyright == 1
    assert state.source == ""


def test_copyright_reads_only_one_or_two(tmp_path):
    """偏好文件里写了个离谱的类型值，回落自制而不是原样收下。"""
    state, _ = load_ui_state(_write(tmp_path, {"copyright": 9}))
    assert state.copyright == 1
    # 布尔是 int 的子类，别把 true 读成 1 蒙混过关
    state2, _ = load_ui_state(_write(tmp_path, {"copyright": True}))
    assert state2.copyright == 1


def test_reprint_source_survives_roundtrip(tmp_path):
    path = tmp_path / "ui-state.json"
    save_ui_state(BatchUIState(copyright=2, source="https://example.com/origin"), path)
    loaded, problem = load_ui_state(path)
    assert problem == ""
    assert loaded.copyright == 2
    assert loaded.source == "https://example.com/origin"
